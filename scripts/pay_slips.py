#!/usr/bin/env python3
"""Уплатницы паушальных налогов за месяц — «налог за уплату» (образац 1) с NBS IPS QR.

    python3 scripts/pay_slips.py                  # за прошлый месяц
    python3 scripts/pay_slips.py --month 2026-10
    python3 scripts/pay_slips.py --cashless       # шифра 2xx, как в образцах решений

Пишет `finance/taxes/slips/ГГГГ-ММ.pdf`: четыре листа A4 — порез, ПИО, здравствено, НЗС, —
на каждом по два одинаковых бланка с линией отреза: один остаётся в меняльнице, второй
с её печатью — у плательщика. В сврхе уплате — месяц, за который платится. Нужны там,
где налог платится не из своего банка: в меняльнице или на почте наличными.

**Реквизиты берутся из образцов уплатниц, которые налоговая печатает в самих решениях**
(`taxes/statements/ГГГГ/PAUS-RESPOR-*.pdf`, `PAUS-RESDOP-*.pdf`): счёт, модель и позив
на број, сврха, прималац. Руками они не набираются — позив на број за следующий год там
уже другой, и ошибка в нём означает, что деньги придут, а обязанность останется. Суммы —
из тех же решений: месячная аконтация пореза и строка месяца в таблице доприноса.

Шифра плаћања в образцах — 253, безналичный платёж. Первая цифра шифры — способ оплаты:
1 — наличными, 2 — безналично, 3 — обрачунски. Для меняльницы нужен налог за уплату
с 153; остальные две цифры (основание — «уплата јавних прихода») не меняются.
"""
import os, re, sys, glob, shutil, argparse, datetime, subprocess, tempfile
from decimal import Decimal
from html import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reqs  # noqa: E402
import tax_registry as tax  # noqa: E402

DATA = reqs.data_root()
ORDER = ('porez', 'pio', 'zdro', 'nes')       # порядок бланков — как в выписке и реестре
MONTHS = ('Јануар', 'Фебруар', 'Март', 'Април', 'Мај', 'Јун', 'Јул', 'Август',
          'Септембар', 'Октобар', 'Новембар', 'Децембар')
MONEY = r'\d{1,3}(?:\.\d{3})*,\d{2}'           # в решениях Пореске управе — «23.947,02»
COPIES = 2                                     # бланков одного налога на листе
CHROME = ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser')


def money(s):
    return Decimal(s.replace('.', '').replace(',', '.'))


def sample_slips(txt):
    """Образцы уплатниц из текста решения: [{year, purpose, recipient, code, account, ref}].

    pdftotext -layout отдаёт бланк столбцом: шифра на строке под «уплатилац», счёт —
    справа от «сврха уплате», сврха — строкой под «модел и позив», затем модель и позив,
    затем «прималац» и его имя. Год берётся из сврхе: в одном решении образцы на два года.
    """
    out = []
    for block in txt.split('НАЛОГ ЗА УПЛАТУ')[1:]:
        code = re.search(r'^\s*(\d{3})\s+дин\.', block, re.M)
        account = re.search(r'сврха уплате\s+(\d{3}-\d+-\d{2})', block)
        tail = re.search(r'модел и позив на број \(одобрење\)\s*\n\s*(.+?)\s*\n\s*(\d{2})\s+(\d+)'
                         r'\s*\n\s*прималац\s*\n\s*\n?\s*(.+?)\s*\n', block)
        if not (code and account and tail):
            continue
        year = re.search(r'\b(20\d{2})\.', tail.group(1))
        out.append({'year': int(year.group(1)) if year else None, 'purpose': tail.group(1),
                    'recipient': tail.group(4), 'code': code.group(1),
                    'account': account.group(1), 'model': tail.group(2), 'ref': tail.group(3)})
    return out


def porez_amount(txt, year):
    """Месячная аконтация пореза за год из решения о порезе."""
    m = re.search(rf'Месечна\s+аконтациона\s+обавеза\s+пореза\s+у\s+{year}\.\s+години\s+'
                  rf'износи\s+({MONEY})', txt)
    return money(m.group(1)) if m else None


def doprinos_amounts(txt, month):
    """(ПИО, здравствено, НЗС) за месяц из таблицы решения о доприносах."""
    m = re.search(rf'^\s*{MONTHS[month - 1]}\s+{MONEY}\s+({MONEY})\s+({MONEY})\s+({MONEY})\s*$',
                  txt, re.M)
    return tuple(money(x) for x in m.groups()) if m else None


def month_purpose(purpose, year, month):
    """«Допринос за ПИО за 2026. годину» → «Допринос за ПИО за октобар 2026.».

    В образце год без месяца: решение одно на год. На уплатнице месяц нужен самому
    плательщику — по квитанции видно, за какой месяц она, — а зачёт идёт по позиву.
    """
    return re.sub(rf'\bза\s+{year}\.(\s+годину)?', f'за {MONTHS[month - 1].lower()} {year}.',
                  purpose)


def qr_purpose(purpose, month, year):
    """Сврха для IPS QR — не длиннее 35 знаков: длинная сокращается до «… ММ/ГГГГ»."""
    if len(purpose) <= 35:
        return purpose
    return re.sub(r'\s+за\s+\S+\s+\d{4}\.$', f' {month:02d}/{year}', purpose)[:35]


def cash_code(code):
    """253 → 153: первая цифра шифры — способ оплаты, 1 — наличными."""
    return '1' + code[1:]


def account18(account):
    """840-711122843-32 → 840000071112284332: банк, партия до 13 цифр, контрольный."""
    bank, party, check = account.split('-')
    return bank + party.zfill(13) + check


def ips_payload(slip, payer):
    """Строка NBS IPS QR (K:PR — платёжный налог): поля и пределы длины — по стандарту НБС."""
    amount = f'{slip["amount"]:.2f}'.replace('.', ',')
    return '|'.join([
        'K:PR', 'V:01', 'C:1', f'R:{account18(slip["account"])}',
        f'N:{slip["recipient"][:70]}', f'I:RSD{amount}', f'P:{payer[:70]}',
        f'SF:{slip["code"]}', f'S:{slip.get("qr_purpose", slip["purpose"])[:35]}',
        f'RO:{slip["model"]}{slip["ref"]}'])


def qr_svg(payload):
    """SVG с QR (qrencode, уровень M, как требует стандарт); без qrencode — пусто."""
    if not shutil.which('qrencode'):
        return ''
    r = subprocess.run(['qrencode', '-t', 'SVG', '-l', 'M', '-m', '0', '--svg-path', '-o', '-',
                        payload], capture_output=True, check=True)
    svg = r.stdout.decode()
    return svg[svg.index('<svg'):]


def statement(year, prefix):
    found = sorted(glob.glob(os.path.join(DATA, 'finance', 'taxes', 'statements', '*',
                                          f'{prefix}-*.pdf')))
    for path in reversed(found):          # свежее решение — последнее по номеру
        txt = tax.pdftext(path)
        if re.search(rf'за\s+{year}\.\s+годину', txt):
            return path, txt
    sys.exit(f'нет решения {prefix} за {year} год в finance/taxes/statements/ — '
             'положить его туда («Прими документы»)')


def collect(year, month, cashless=False):
    """Четыре бланка месяца: реквизиты из образцов, суммы из решений."""
    _, respor = statement(year, 'PAUS-RESPOR')
    _, resdop = statement(year, 'PAUS-RESDOP')
    amounts = {'porez': porez_amount(respor, year)}
    dop = doprinos_amounts(resdop, month)
    if amounts['porez'] is None or dop is None:
        sys.exit(f'в решениях за {year} не нашлись суммы за {MONTHS[month - 1].lower()} — '
                 'форма документа изменилась?')
    amounts.update(zip(('pio', 'zdro', 'nes'), dop, strict=True))
    slips = {}
    for s in sample_slips(respor) + sample_slips(resdop):
        kind = tax.kind_of('', s['account'])
        if s['year'] == year and kind in ORDER:
            purpose = month_purpose(s['purpose'], year, month)
            slips[kind] = {**s, 'kind': kind, 'amount': amounts[kind], 'purpose': purpose,
                           'qr_purpose': qr_purpose(purpose, month, year),
                           'code': s['code'] if cashless else cash_code(s['code'])}
    missing = [k for k in ORDER if k not in slips]
    if missing:
        sys.exit(f'в решениях за {year} нет образцов уплатниц для: {", ".join(missing)}')
    return [slips[k] for k in ORDER]


CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
body { margin: 0; font: 9pt 'DejaVu Sans', 'Liberation Sans', sans-serif; color: #000; }
.page { height: 297mm; page-break-after: always; overflow: hidden; }
.page:last-child { page-break-after: auto; }
.slip { position: relative; width: 210mm; height: 99mm; padding: 6mm 8mm;
        border-bottom: 0.3mm dashed #888; }
.left { position: absolute; left: 8mm; top: 12mm; width: 92mm; }
.right { position: absolute; left: 106mm; top: 6mm; width: 96mm; }
.title { font-weight: bold; font-size: 11pt; text-align: right; margin-bottom: 2mm; }
.lbl { font-size: 6.5pt; margin: 1.6mm 0 0.6mm; }
.box { border: 0.25mm solid #000; padding: 1.2mm 1.8mm; font-size: 9.5pt; }
.big { height: 15mm; }
.row { display: flex; gap: 2mm; }
.row > div { flex: none; }
.sign { position: absolute; left: 8mm; bottom: 6mm; width: 92mm; font-size: 6.5pt; }
.sign .line { border-top: 0.25mm solid #000; margin-top: 7mm; padding-top: 0.6mm; }
.sign .pair { display: flex; gap: 6mm; } .sign .pair > div { flex: 1; }
.qr { position: absolute; right: 8mm; bottom: 7mm; width: 26mm; height: 26mm; }
.qr svg { width: 100%; height: 100%; }
.qrl { position: absolute; right: 8mm; bottom: 3mm; width: 26mm; text-align: center;
       font-size: 5.5pt; }
.note { position: absolute; left: 106mm; bottom: 7mm; width: 60mm; font-size: 6.5pt;
        color: #444; }
"""


def slip_html(s, payer_lines, due):
    amount = f'{s["amount"]:,.2f}'.replace(',', ' ').replace('.', ',').replace(' ', '.')
    payer = '<br>'.join(escape(x) for x in payer_lines)
    qr = qr_svg(ips_payload(s, ', '.join(payer_lines)))
    return f"""<div class="slip">
  <div class="left">
    <div class="lbl">уплатилац</div><div class="box big">{payer}</div>
    <div class="lbl">сврха уплате</div><div class="box big">{escape(s['purpose'])}</div>
    <div class="lbl">прималац</div><div class="box big">{escape(s['recipient'])}</div>
  </div>
  <div class="right">
    <div class="title">НАЛОГ ЗА УПЛАТУ</div>
    <div class="row">
      <div><div class="lbl">шифра плаћања</div>
        <div class="box" style="width:17mm;text-align:center">{s['code']}</div></div>
      <div><div class="lbl">валута</div>
        <div class="box" style="width:14mm;text-align:center">RSD</div></div>
      <div><div class="lbl">износ</div>
        <div class="box" style="width:61mm;text-align:right">= {amount}</div></div>
    </div>
    <div class="lbl">рачун примаоца</div><div class="box">{s['account']}</div>
    <div class="lbl">модел и позив на број (одобрење)</div>
    <div class="row">
      <div class="box" style="width:12mm;text-align:center">{s['model']}</div>
      <div class="box" style="width:82mm">{s['ref']}</div>
    </div>
  </div>
  <div class="sign">
    <div class="line">печат и потпис уплатиоца</div>
    <div class="pair"><div class="line">место и датум пријема</div>
      <div class="line">датум валуте</div></div>
  </div>
  <div class="note">рок уплате — {due}</div>
  {f'<div class="qr">{qr}</div><div class="qrl">NBS IPS QR</div>' if qr else ''}
</div>"""


def write_pdf(html, dst):
    chrome = next((c for c in CHROME if shutil.which(c)), None)
    if not chrome:
        sys.exit('нужен Chrome или Chromium для печати HTML в PDF: '
                 + ', '.join(CHROME) + ' — ни одного не нашлось')
    tmp = tempfile.mkdtemp()
    try:
        src = os.path.join(tmp, 'slips.html')
        open(src, 'w').write(html)
        out = os.path.join(tmp, 'slips.pdf')
        subprocess.run([chrome, '--headless=new', '--disable-gpu', '--no-pdf-header-footer',
                        f'--print-to-pdf={out}', 'file://' + src], capture_output=True)
        if not os.path.exists(out):
            sys.exit(f'{chrome} не собрал PDF')
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(out, dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--month', help='ГГГГ-ММ, за который платится (по умолчанию — прошлый)')
    ap.add_argument('--cashless', action='store_true',
                    help='шифра 2xx из образцов — для оплаты переводом, а не наличными')
    a = ap.parse_args()
    if a.month:
        year, month = map(int, a.month.split('-'))
    else:
        first = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)
        year, month = first.year, first.month
    slips = collect(year, month, a.cashless)
    payer = [reqs.get('entrepreneur.firm'), reqs.get('entrepreneur.address')]
    label = f'{MONTHS[month - 1].lower()} {year}'
    nxt = datetime.date(year + month // 12, month % 12 + 1, 15)
    due = nxt.strftime('%d.%m.%Y.')
    html = ('<!doctype html><html><head><meta charset="utf-8"><style>' + CSS
            + '</style></head><body>'
            + ''.join('<div class="page">' + slip_html(s, payer, due) * COPIES + '</div>'
                      for s in slips) + '</body></html>')
    dst = os.path.join(DATA, 'finance', 'taxes', 'slips', f'{year}-{month:02d}.pdf')
    write_pdf(html, dst)
    print(f'{os.path.relpath(dst, DATA)}: {len(slips)} листа по {COPIES} уплатнице за {label}, '
          f'всего {sum(s["amount"] for s in slips):,.2f} RSD')
    for s in slips:
        print(f'  {s["code"]}  {s["account"]}  {s["model"]} {s["ref"]}  '
              f'{s["amount"]:>10,.2f}  {s["purpose"]}')


if __name__ == '__main__':
    main()
