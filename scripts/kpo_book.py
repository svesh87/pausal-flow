#!/usr/bin/env python3
"""Сборка книги KPO (knjiga o ostvarenom prometu) по данным репозитория.

    python3 scripts/kpo_book.py [--year 2026] [--offline] [--allow-past]

Кладёт в `finance/kpo/`:

  kpo_YYYY.pdf     книга за год — официальный сербский образец КПО, на сербском
  entries.md       рабочий реестр записей: счёт, EUR, курс, RSD, итоги по годам

Кэш курсов НБС — в `.cache/nbs_rates.json` (вне git: пересобирается сам).
Реквизиты шапки книги — из `profile.json`, в коде их нет.

## Что переписывается

Только то, что действительно изменилось. Книга за год собирается в HTML и сверяется
с уже лежащим PDF; совпало — файл не трогается. Иначе каждый запуск переписывал бы
все книги: LibreOffice кладёт в PDF дату сборки, и файл прошлого года выглядел бы
изменённым без единой правки в данных. То же для `entries.md` и кэша курсов.

**Книги закрытых лет не перегенерируются.** По члану 13 правилника закрытая книга
заверена подписью обвезника, это сданный документ. Если данные за прошлый год
разошлись с книгой, скрипт про это сообщает и завершается с кодом 1, но файл
не пишет: сначала нужно понять, почему разошлось. Осознанная перегенерация —
`--allow-past` (и лучше вместе с `--year`), с разрешения пользователя.

Форма и порядок заполнения — по «Правилнику о пословним књигама и исказивању
финансијског резултата по систему простог књиговодства», члан 7: колонка 1 —
редни број од почетка године, 2 — датум и опис књижења, 3 и 4 — приход од продаје
производа и од извршених услуга, 5 — укупан приход као збир 3 и 4. По члану 13
закључена књига оверава се потписом обвезника, поэтому в подвале есть строка подписи.

PDF собирается из HTML через LibreOffice: кириллица и A4 без ручной работы со шрифтами.

С `--offline` курсы берутся только из кэша — когда сети нет или нужна воспроизводимость.

## Как считается

Доход попадает в книгу **по дате счёта** (datum prometa), пересчитанный в динары
по **среднему курсу НБС на эту дату**, округление до двух знаков (half-up).
Дата поступления денег и дата валютной конверсии не влияют ни на дату записи,
ни на сумму: банк покупает валюту по своему курсу и в свой день, к книге это
отношения не имеет.

Проверено на всех записях: расчёт совпадает с книгой, которую ведёт бухгалтерский сервис,
до копейки. То есть книга здесь — независимая перепроверка, а не догадка о чужой логике.

Год записи определяется годом **счёта**: инвойс за декабрь выставляется в январе
и попадает в следующий год. Поэтому в первом году работы записей меньше, чем отработанных
месяцев, — декабрь уезжает в следующую книгу.

## Источник курсов

`https://kurs.resenje.org/api/v1/currencies/eur/rates/ГГГГ-ММ-ДД` — публичный JSON-API
официальных курсов НБС, без авторизации, поле `exchange_middle`. Сверялся с курсом,
по которому запись посчитал бухгалтерский сервис, — совпал до четвёртого знака.

Курсы ЕЦБ и любых банков **не подходят**: НБС публикует свой средний курс, он другой.
Курс покупки валюты банком (в выписке `OTKUP DEVIZA … KURS:115`) — тоже не тот.
"""
import re, os, sys, glob, json, argparse, datetime, subprocess, urllib.request
from decimal import Decimal, ROUND_HALF_UP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reqs  # noqa: E402

DATA = reqs.data_root()         # корень данных: months/ с реестрами, finance/kpo/ с книгами
KPO_DIR = os.path.join(DATA, 'finance', 'kpo')
CACHE = os.path.join(DATA, '.cache', 'nbs_rates.json')
API = 'https://kurs.resenje.org/api/v1/currencies/eur/rates/{}'

# поля шапки — дословно как в официальном бланке КПО, значения из profile.json
HEADER = [
    ('ПИБ', reqs.get('entrepreneur.pib')),
    ('Обвезник', reqs.get('entrepreneur.name')),
    ('Фирма - радње', reqs.get('entrepreneur.firm')),
    ('Седиште', reqs.get('entrepreneur.address')),
    ('Шифра пореског обвезника', reqs.get('entrepreneur.tax_code')),
    ('Шифра делатности', reqs.get('entrepreneur.activity_code')),
]
CLIENT = reqs.get('client.name')
# книгу составляет и заверяет сам обвезник: у паушальца без сотрудников
# «Саставио» и «Одговорно лице» — одно лицо (члан 13 правилника)
SIGNER = reqs.get('entrepreneur.name')


def load_cache():
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    return {}


def nbs_rate(date, cache, offline=False):
    if date in cache:
        return Decimal(str(cache[date]))
    assert not offline, f'курса на {date} нет в кэше, а запрошен режим --offline'
    try:
        j = json.load(urllib.request.urlopen(API.format(date), timeout=20))
    except Exception as e:
        raise AssertionError(f'не удалось получить курс НБС на {date}: {e}. '
                             'Проверить доступность kurs.resenje.org или задать курс в кэше '
                             f'{CACHE} вручную') from e
    r = j.get('exchange_middle')
    assert r, (f'в ответе API нет exchange_middle на {date} — НБС не публикует курс '
               'на нерабочий день; проверить дату счёта')
    cache[date] = r
    return Decimal(str(r))


def read_months():
    """[(месяц, № счёта, дата счёта ISO, сумма EUR)] по всем папкам месяцев."""
    out = []
    for p in sorted(glob.glob(os.path.join(DATA, 'months', '20*', 'tasks.md'))):
        txt = open(p).read()
        mi = re.search(r'^- Инвойс:\s*№\s*(\S+)\s+от\s+(\d{2})\.(\d{2})\.(\d{4})', txt, re.M)
        mp = re.search(r'^- Оплата:.*?EUR\s*([\d.,]+)', txt, re.M)
        if not (mi and mp):
            continue                      # счёт ещё не выпущен или платёж не пришёл
        out.append(dict(month=os.path.basename(os.path.dirname(p)),
                        no=mi.group(1),
                        date=f'{mi.group(4)}-{mi.group(3)}-{mi.group(2)}',
                        eur=Decimal(mp.group(1).replace(',', ''))))
    return out


def fmt(d):
    """1158221.61 -> `1.158.221,61` — как в официальной книге."""
    s = f'{d:,.2f}'
    return s.replace(',', '\x00').replace('.', ',').replace('\x00', '.')


def fmt_rate(r):
    """НБС публикует курс с четырьмя знаками: 117.385 -> `117,3850`."""
    return f'{r:.4f}'.replace('.', ',')


HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@page {{ size: A4 landscape; margin: 8mm 14mm; }}
body {{ font-family: "Liberation Serif", "Times New Roman", serif; font-size: 10pt; color: #000; }}
</style></head><body>
<table cellspacing="0" cellpadding="0" width="100%"><tr>
<td width="80%" style="font-size:9pt; vertical-align:top">{header}</td>
<td width="20%" style="text-align:right; vertical-align:top;
    font-weight:bold; font-size:11pt">КПО</td>
</tr></table>
<div style="text-align:center; font-weight:bold; font-size:10pt;
     line-height:1.35; margin:4mm 0 3.5mm">
КЊИГА О ОСТВАРЕНОМ ПРОМЕТУ<br>ПАУШАЛНО ОПОРЕЗОВАНИХ ОБВЕЗНИКА ЗА {year}. ГОДИНУ</div>
<table cellspacing="0" cellpadding="4" width="100%" style="border-collapse:collapse; font-size:9pt">
<tr>
<td width="6%" rowspan="2" style="{c}; text-align:center; vertical-align:middle">
<p style="{p}">Редни<br>број</p></td>
<td width="30%" rowspan="2" style="{c}; text-align:center; vertical-align:middle">
<p style="{p}">Датум и опис књижења</p></td>
<td width="38%" colspan="2" style="{c}; text-align:center">
<p style="{p}">ПРИХОД ОД ДЕЛАТНОСТИ</p></td>
<td width="26%" rowspan="2" style="{c}; text-align:center; vertical-align:middle">
<p style="{p}">СВЕГА ПРИХОДИ ОД<br>ДЕЛАТНОСТИ (3 + 4)</p></td>
</tr>
<tr>
<td style="{c}; text-align:center"><p style="{p}">од продаје производа</p></td>
<td style="{c}; text-align:center"><p style="{p}">од извршених услуга</p></td>
</tr>
<tr>
<td style="{c}; text-align:center"><p style="{p}">1</p></td>
<td style="{c}; text-align:center"><p style="{p}">2</p></td>
<td style="{c}; text-align:center"><p style="{p}">3</p></td>
<td style="{c}; text-align:center"><p style="{p}">4</p></td>
<td style="{c}; text-align:center"><p style="{p}">5</p></td>
</tr>
{rows}
<tr>
<td colspan="2" style="{c}; text-align:right; font-weight:bold"><p style="{p}">УКУПНО (RSD)</p></td>
<td style="{c}; text-align:right"><p style="{p}">0,00</p></td>
<td style="{c}; text-align:right; font-weight:bold"><p style="{p}">{total}</p></td>
<td style="{c}; text-align:right; font-weight:bold"><p style="{p}">{total}</p></td>
</tr>
</table>
<p style="margin-top:0pt; margin-bottom:0pt; font-size:22pt">&nbsp;</p>
<table cellspacing="0" cellpadding="0" width="100%" style="font-size:9pt">
<tr><td width="32%" style="text-align:center"><p style="{p}">Саставио</p></td>
<td width="36%"></td>
<td width="32%" style="text-align:center"><p style="{p}">Одговорно лице</p></td></tr>
<tr><td style="border-bottom:1px solid #000; height:9mm"></td>
<td></td>
<td style="border-bottom:1px solid #000; height:9mm"></td></tr>
<tr><td style="text-align:center"><p style="{p}">{signer}</p></td>
<td></td>
<td style="text-align:center"><p style="{p}">{signer}</p></td></tr>
</table>
</body></html>"""

CELL = 'border:1px solid #000'
# LibreOffice добавляет отбивку абзаца в каждую ячейку и понимает только явные 0pt:
# без этого шаг строки 26 pt вместо 12 и год не влезает на лист
P = 'margin-top:0pt; margin-bottom:0pt; line-height:100%'


def build_year(year, rows, cache, offline):
    """HTML книги за год по официальному образцу КПО (paragraf.rs/obrasci/KPO.pdf)."""
    hdr = ''.join(f'<p style="{P}"><b>{k}</b> {v}</p>' for k, v in HEADER)
    body, total = [], Decimal('0')
    for i, r in enumerate(rows, 1):
        rate = nbs_rate(r['date'], cache, offline)
        rsd = (r['eur'] * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        total += rsd
        d = f'{r["date"][8:]}.{r["date"][5:7]}.{r["date"][:4]}'
        body.append(
            f'<tr><td style="{CELL}; text-align:center"><p style="{P}">{i}</p></td>'
            f'<td style="{CELL}"><p style="{P}">{CLIENT} {d}</p></td>'
            f'<td style="{CELL}; text-align:right"><p style="{P}">0,00</p></td>'
            f'<td style="{CELL}; text-align:right"><p style="{P}">{fmt(rsd)}</p></td>'
            f'<td style="{CELL}; text-align:right"><p style="{P}">{fmt(rsd)}</p></td></tr>')
    html = HTML.format(year=year, header=hdr, c=CELL, p=P, signer=SIGNER,
                       rows='\n'.join(body), total=fmt(total))
    return html, total


def build_registry(years, cache, offline):
    """Сводный реестр по всем годам: счёт, евро, курс, динары, итоги."""
    lines = ['# Реестр счетов и пересчёта в динары', '',
             'Собирается вместе с книгами KPO из реестров месяцев. Период работ и год записи',
             'в книге не совпадают: инвойс за декабрь выставляется в январе и попадает',
             'в следующий год.', '',
             '| Период работ | Счёт | Дата счёта | EUR | Курс НБС | RSD |',
             '|---|---|---|---:|---:|---:|']
    grand_eur = grand_rsd = Decimal('0')
    for y in sorted(years):
        sub_eur = sub_rsd = Decimal('0')
        for r in years[y]:
            rate = nbs_rate(r['date'], cache, offline)
            rsd = (r['eur'] * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            sub_eur += r['eur']; sub_rsd += rsd
            d = f'{r["date"][8:]}.{r["date"][5:7]}.{r["date"][:4]}'
            lines.append(f'| {r["month"].replace("_", "-")} | {r["no"]} | {d} | '
                         f'{fmt(r["eur"])} | {fmt_rate(rate)} | {fmt(rsd)} |')
        lines.append(f'| **итого {y}** | | | **{fmt(sub_eur)}** | | **{fmt(sub_rsd)}** |')
        grand_eur += sub_eur; grand_rsd += sub_rsd
    lines += [f'| **всего** | | | **{fmt(grand_eur)}** | | **{fmt(grand_rsd)}** |', '']
    return '\n'.join(lines)


def html_tokens(html):
    """Слова книги из HTML — для сверки с тем, что уже лежит в PDF."""
    h = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.S)
    h = re.sub(r'<[^>]+>', ' ', h).replace('&nbsp;', ' ')
    return sorted(h.split())


def pdf_tokens(path):
    """Слова из готового PDF. Порядок игнорируется: pdftotext читает по колонкам,
    а не в порядке HTML, поэтому сверяется мультимножество слов — этого хватает,
    чтобы поймать любую изменившуюся сумму, дату или добавленную строку."""
    r = subprocess.run(['pdftotext', path, '-'], capture_output=True)
    assert r.returncode == 0, f'pdftotext не прочитал {path}: {r.stderr.decode()[:200]}'
    return sorted(r.stdout.decode('utf-8', 'replace').split())


def same_as_on_disk(html, path):
    return os.path.exists(path) and pdf_tokens(path) == html_tokens(html)


def write_if_changed(path, text):
    """Текстовый файл переписывается только при изменении содержимого."""
    if os.path.exists(path) and open(path).read() == text:
        return False
    open(path, 'w').write(text)
    return True


def write_pdf(html, base):
    """HTML -> PDF через LibreOffice: кириллица и A4 без ручной работы со шрифтами."""
    import tempfile, shutil
    tmp = tempfile.mkdtemp()
    try:
        src = os.path.join(tmp, base + '.html')
        open(src, 'w').write(html)
        r = subprocess.run(['soffice', '--headless', '--convert-to', 'pdf',
                            '--outdir', tmp, src], capture_output=True)
        out = os.path.join(tmp, base + '.pdf')
        assert os.path.exists(out), (f'LibreOffice не собрал PDF: {r.stderr.decode()[:300]}. '
                                     'Проверить, что soffice установлен '
                                     'и не запущен в другой сессии')
        dst = os.path.join(KPO_DIR, base + '.pdf')
        shutil.move(out, dst)
        return dst
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, default=None, help='только один год')
    ap.add_argument('--offline', action='store_true', help='курсы брать только из кэша')
    ap.add_argument('--allow-past', action='store_true',
                    help='разрешить перегенерацию книг закрытых лет (только осознанно)')
    a = ap.parse_args()

    os.makedirs(KPO_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    cache = load_cache()
    entries = read_months()
    assert entries, 'ни в одном месяце не заполнен реестр (инвойс + оплата)'

    years = {}
    for e in entries:
        years.setdefault(int(e['date'][:4]), []).append(e)
    for y in years:
        years[y].sort(key=lambda r: r['date'])

    this_year = datetime.date.today().year
    blocked = []
    for y in sorted(years):
        if a.year and y != a.year:
            continue
        html, total = build_year(y, years[y], cache, a.offline)
        dst = os.path.join(KPO_DIR, f'kpo_{y}.pdf')
        tail = f'записей: {len(years[y]):2}  итого: {fmt(total)} RSD'
        if same_as_on_disk(html, dst):
            print(f'{dst}  без изменений, не переписана  ({tail})')
            continue
        if y < this_year and not a.allow_past:
            blocked.append(y)
            continue
        print(f'{write_pdf(html, f"kpo_{y}")}  {tail}')

    rp = os.path.join(KPO_DIR, 'entries.md')
    reg = build_registry(years, cache, a.offline)
    print(f'{rp}  ' + ('сводный реестр по всем годам' if write_if_changed(rp, reg)
                       else 'без изменений'))

    dump = json.dumps(cache, indent=1, sort_keys=True)
    write_if_changed(CACHE, dump)
    print(f'курсов в кэше: {len(cache)} ({CACHE})')

    if blocked:
        years_s = ', '.join(str(y) for y in blocked)
        print(f'\nОСТАНОВЛЕНО: книга за {years_s} расходится с данными репозитория, '
              f'но это закрытый год — файл не переписан.\n'
              f'Разобраться, почему расхождение, и только с разрешения пользователя: '
              f'python3 scripts/kpo_book.py --year {blocked[0]} --allow-past', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
