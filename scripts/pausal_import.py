#!/usr/bin/env python3
"""Генерация файлов импорта для pausal.rs по данным месяца.

    python3 scripts/pausal_import.py YYYY_MM [--place «…»] [--out DIR]

Кладёт в `months/YYYY_MM/pausal/` два файла:

  articles_YYYY_MM.xlsx   артикул (позиция счёта) — импортировать ПЕРВЫМ
  invoices_YYYY_MM.xlsx   сам счёт

Порядок обязателен: импорт счетов артикул не создаёт, а сверяет по названию,
поэтому сначала загружается артикул, потом счёт.

Данные собираются из реестра месяца и инвойса клиента, с кросс-проверкой номера,
даты и суммы по трём источникам (реестр, `invoiceN.pdf`, бланк банка). При
расхождении скрипт падает — как подписыватели, лучше упасть, чем внести
неверную сумму в KPO.

Название артикула и место оборота берутся **дословно** из инвойса клиента. В названии
уже есть номер и дата акта, а язык, которым клиент пишет месяц, меняется от инвойса к инвойсу
(`No 17 date Febrero 2, 2026`) — собирать такую строку самому нельзя, иначе она не
совпадёт с документом, который ушёл в банк. Место оборота — адрес клиента, и в счетах
паушала записана та же строка с задвоенной «Valencia,Valencia», что и в инвойсе.

Место оборота — это **не** место выдачи. По статье 12 Закона о НДС местом оборота услуг,
оказанных налогоплательщику, считается место нахождения получателя, то есть Валенсия;
Гроцка идёт в отдельное поле «место выдачи», которое приложение берёт из твоих реквизитов.
Поставить сюда Гроцку значило бы заявить, что услуга оказана в Сербии.

Заполнение полей повторяет то, что уже внесено в паушал руками (проверено
по карточке товара и по счёту, сгенерированному приложением):

| Артикул | Значение |
|---|---|
| Naziv | строка позиции из инвойса |
| Jedinica mere | `komad` |
| Cena | сумма счёта |
| Valuta | `EUR` |
| Tip | `Usluga` |
| GTIN | пусто |
| Poreska stopa | `А` — **кириллическая** А (U+0410), 0%, вне системы НДС |

| Счёт | Значение |
|---|---|
| Префикс / Номер | `26-8-LC` → `26-` и `8`; старые номера вида `17` → префикс пустой |
| Дата счёта / Дата оказания услуг | дата инвойса, обе одинаковые |
| Место оказания услуг | адрес клиента, дословно из инвойса (можно задать `--place`) |
| Налоговый номер клиента | `client.tax_id` из `profile.json` |
| Тип счёта / Валюта | `Foreign` / `EUR` |
| Название товара | то же, что Naziv артикула |
| Общая сумма | сумма счёта |
| Дата оплаты | дата зачисления из банковского уведомления |
"""
import re, sys, os, shutil, zipfile, subprocess, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reqs  # noqa: E402

DATA = reqs.data_root()         # корень данных: months/ с реестрами и документами
# Шаблоны импорта — часть движка, а не данных: их корень считается от самого файла
TEMPLATES = os.path.join(reqs.CODE_ROOT, 'scripts', 'templates', 'pausal')
CLIENT_TAX = reqs.get('client.tax_id')
VAT_RATE = 'А'          # кириллическая А — латинская не пройдёт валидацию
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def pdftext(path, layout=False):
    cmd = ['pdftotext'] + (['-layout'] if layout else []) + [path, '-']
    return subprocess.run(cmd, capture_output=True).stdout.decode('utf-8', 'replace')


def ru_date(s):
    """`01.07.2026` -> `2026-07-01`."""
    d, m, y = s.split('.')
    return f'{y}-{m}-{d}'


def read_registry(month):
    p = os.path.join(DATA, 'months', month, 'tasks.md')
    assert os.path.exists(p), f'нет файла месяца {p}'
    txt = open(p).read()
    mi = re.search(r'^- Инвойс:\s*№\s*(\S+)\s+от\s+(\d{2}\.\d{2}\.\d{4})\s*\(`([^`]+)`\)',
                   txt, re.M)
    assert mi, ('в реестре месяца нет строки «- Инвойс: № … от … (`invoiceN.pdf`)» — '
                'счёт ещё не выпущен или реестр не заполнен')
    mp = re.search(r'^- Оплата:\s*(\d{2}\.\d{2}\.\d{4}),\s*EUR\s*([\d.,]+)\s*\(`([^`]+)`\)',
                   txt, re.M)
    assert mp, ('в реестре месяца нет строки «- Оплата: … EUR … (`Obavestenje…`)» — '
                'платёж ещё не пришёл, дата оплаты в паушале обязательна для статуса «Оплачено»')
    return dict(inv_no=mi.group(1), inv_date=mi.group(2), inv_file=mi.group(3),
                pay_date=mp.group(1), amount=mp.group(2), bank_file=mp.group(3))


def read_invoice(path):
    txt = pdftext(path)
    m = re.search(r'INVOICE\s*#\s*(\S+)', txt)
    assert m, f'{path}: не найден номер (INVOICE #)'
    md = re.search(r'DATE\s+(\d{2}\.\d{2}\.\d{4})', txt)
    assert md, f'{path}: не найдена дата (DATE)'
    mt = re.search(r'TOTAL[\s\S]{0,40}?([\d][\d.,]*)', txt)
    assert mt, f'{path}: не найдена сумма (TOTAL)'
    lines = [ln.strip() for ln in txt.splitlines()]
    desc = None
    for i, ln in enumerate(lines):
        if ln.startswith('Works in accordance'):
            desc = ln
            if i + 1 < len(lines) and lines[i+1] and not lines[i+1].startswith('TOTAL'):
                desc += ' ' + lines[i+1]
            break
    assert desc, (f'{path}: не найдена строка позиции «Works in accordance …» — '
                  'формулировка в инвойсе изменилась, остановиться и разобрать руками')
    # адрес клиента = место оборота; тоже берём дословно, чтобы совпало с уже внесённым
    place = None
    for i, ln in enumerate(lines):
        if ln.startswith('Address of company:'):
            place = ln.split(':', 1)[1].strip()
            if i + 1 < len(lines) and lines[i+1]:
                place = (place + ' ' + lines[i+1]).strip()
            break
    assert place, (f'{path}: не найден адрес клиента («Address of company:») — '
                   'разобрать руками или задать флагом --place')
    return dict(no=m.group(1), date=md.group(1), total=mt.group(1), desc=desc, place=place)


def split_number(no):
    """`26-8-LC` -> (`26-`, `8`); `17` -> (``, `17`)."""
    if re.fullmatch(r'\d{1,6}', no):
        return '', no
    m = re.fullmatch(r'(\d{1,5}-)(\d{1,6})(?:-[A-Za-z]{1,4})?', no)
    assert m, (f'номер счёта «{no}» не раскладывается на префикс и цифры: в паушале '
               'номер — только цифры (максимум 6), буквы и дефис допустимы лишь в префиксе '
               '(максимум 6 знаков). Остановиться и решить, как нумеровать.')
    return m.group(1), m.group(2)


def to_num(s):
    """`4,100.00` -> 4100 (int, если без дробной части)."""
    v = float(s.replace(',', ''))
    return int(v) if v == int(v) else v


def _esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _cell(ref, value, numeric=False):
    if value is None or value == '':
        return ''
    if numeric:
        return f'<c r="{ref}"><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_esc(value)}</t></is></c>'


def write_xlsx(template, out, cells):
    """Копия шаблона с перезаписанной строкой 2 (в шаблонах она — образец).

    Копируем, а не собираем файл с нуля: так сохраняются выпадающие списки,
    правила валидации и подсказки к колонкам.
    """
    shutil.copyfile(template, out)
    zin = zipfile.ZipFile(out)
    items = {n: zin.read(n) for n in zin.namelist()}
    zin.close()
    xml = items['xl/worksheets/sheet1.xml'].decode('utf-8')
    row = '<row r="2">' + ''.join(_cell(*c) for c in cells) + '</row>'
    new, n = re.subn(r'<row r="2"[^>]*>.*?</row>', row, xml, count=1, flags=re.S)
    if n == 0:
        new, n = re.subn(r'<row r="2"[^>]*/>', row, xml, count=1)
    assert n == 1, f'{template}: не найдена строка 2 — шаблон изменился, разобрать руками'
    items['xl/worksheets/sheet1.xml'] = new.encode('utf-8')
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zout:
        for name, data in items.items():
            zout.writestr(name, data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('month', help='папка месяца, например 2026_06')
    ap.add_argument('--place', default=None,
                    help='место оборота (колонка E); по умолчанию адрес клиента из инвойса')
    ap.add_argument('--out', default=None,
                    help='каталог для файлов (по умолчанию months/YYYY_MM/pausal)')
    a = ap.parse_args()

    reg = read_registry(a.month)
    inv_path = os.path.join(DATA, 'months', a.month, reg['inv_file'])
    assert os.path.exists(inv_path), f'нет инвойса {inv_path}'
    inv = read_invoice(inv_path)

    # кросс-проверки: реестр против инвойса
    assert inv['no'] == reg['inv_no'], \
        f'номер расходится: реестр {reg["inv_no"]}, инвойс {inv["no"]}'
    assert inv['date'] == reg['inv_date'], \
        f'дата расходится: реестр {reg["inv_date"]}, инвойс {inv["date"]}'
    assert to_num(inv['total']) == to_num(reg['amount']), \
        f'сумма расходится: реестр {reg["amount"]}, инвойс {inv["total"]}'

    # и против бланка банка, если он в папке месяца
    bank = os.path.join(DATA, 'months', a.month, reg['bank_file'])
    if os.path.exists(bank):
        bt = pdftext(bank, layout=True)
        mb = re.search(r'Iznos\s+EUR\s*([\d.,]+)', bt)
        assert mb, f'{bank}: не найдена сумма (Iznos)'
        assert to_num(mb.group(1)) == to_num(reg['amount']), \
            f'сумма расходится: реестр {reg["amount"]}, бланк банка {mb.group(1)}'

    place = a.place or inv['place']
    prefix, number = split_number(reg['inv_no'])
    amount = to_num(reg['amount'])
    outdir = a.out or os.path.join(DATA, 'months', a.month, 'pausal')
    os.makedirs(outdir, exist_ok=True)

    art = os.path.join(outdir, f'articles_{a.month}.xlsx')
    write_xlsx(os.path.join(TEMPLATES, 'articles_import_template.xlsx'), art, [
        ('A2', inv['desc']), ('B2', 'komad'), ('C2', amount, True),
        ('D2', 'EUR'), ('E2', 'Usluga'), ('G2', VAT_RATE),
    ])

    fak = os.path.join(outdir, f'invoices_{a.month}.xlsx')
    write_xlsx(os.path.join(TEMPLATES, 'invoice_import_template.xlsx'), fak, [
        ('A2', prefix), ('B2', number, True),
        ('C2', ru_date(reg['inv_date'])), ('D2', ru_date(reg['inv_date'])),
        ('E2', place), ('F2', CLIENT_TAX), ('G2', 'Foreign'), ('H2', 'EUR'),
        ('I2', inv['desc']), ('J2', amount, True), ('K2', ru_date(reg['pay_date'])),
    ])

    print(f'месяц       : {a.month}')
    print(f'счёт        : {reg["inv_no"]} -> префикс «{prefix}» + номер {number}')
    print(f'даты        : счёт {ru_date(reg["inv_date"])}, оплата {ru_date(reg["pay_date"])}')
    print(f'сумма       : {amount} EUR (сверено: реестр, инвойс' +
          (', бланк банка)' if os.path.exists(bank) else ')'))
    print(f'позиция     : {inv["desc"]}')
    print(f'место оборота: {place}')
    print(f'файлы       : {art}')
    print(f'              {fak}')
    print('порядок     : сначала articles, потом invoices')


if __name__ == '__main__':
    main()
