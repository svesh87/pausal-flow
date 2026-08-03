#!/usr/bin/env python3
"""Реестр исходящих платежей поставщикам из PDF счетов и динарской выписки.

    python3 scripts/payments_registry.py

Пишет `finance/payments/registry.md`: строка — счёт поставщика (номер, дата, состав,
сумма) и дата его оплаты. Номер, дату и состав берёт из PDF, дату и сумму оплаты —
из `finance/bank/transactions_rsd.csv` (category=supplier).

Раньше этот реестр правился текстом, поэтому мог разъехаться с данными, а сверку
«реестр сходится с платежами до динара» приходилось делать глазами. Теперь сходится
по построению, а расхождения печатаются в конце файла:

  - счёт без оплаты — нормально в начале месяца, пока платёж не ушёл;
  - оплата без счёта — повод найти PDF: платёж есть, документа под него нет.

Сопоставление оплаты со счётом — по номеру счёта в назначении платежа. Суммы
не сравниваются напрямую: банк списывает ровно «UKUPNO ZA UPLATU», а в счёте есть
и сумма до скидки.
"""
import csv
import os
import re
import subprocess
import sys
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reqs  # noqa: E402

DATA = reqs.data_root()         # корень данных: finance/, months/, signatures/
PAYMENTS = os.path.join(DATA, 'finance', 'payments')
CSV_RSD = os.path.join(DATA, 'finance', 'bank', 'transactions_rsd.csv')


def pdftext(path):
    r = subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True)
    assert r.returncode == 0, f'{path}: pdftotext не смог прочитать файл'
    return r.stdout.decode('utf-8', 'replace')


def money(s):
    """`8.775,00` -> Decimal('8775.00') — сербский формат чисел в счетах поставщика."""
    return Decimal(s.replace('.', '').replace(',', '.'))


def fmt(d):
    return f'{d:,.2f}'.replace(',', '\x00').replace('.', ',').replace('\x00', '.')


def parse_invoice(path):
    """Номер, дата, позиции и сумма к оплате из счёта поставщика."""
    txt = pdftext(path)
    m = re.search(r'Faktura:.*?\n\s*(\S+/\d{4})\s+(\d{2}\.\d{2}\.\d{4})', txt, re.S)
    assert m, (f'{os.path.relpath(path, DATA)}: не найдены номер и дата счёта '
               '(блок «Faktura: … Datum fakture») — форма счёта изменилась')
    total = re.search(r'UKUPNO ZA UPLATU \(RSD\)\s+([\d.,]+)', txt)
    assert total, (f'{os.path.relpath(path, DATA)}: нет строки «UKUPNO ZA UPLATU (RSD)» — '
                   'форма счёта изменилась')
    return dict(no=m.group(1), date=m.group(2), total=money(total.group(1)),
                composition=composition(txt), file=os.path.relpath(path, PAYMENTS))


# строка позиции: единица, количество, цена, рабат, итог — числа в колонках
ROW = re.compile(r'(Komad|Usluga|Sat)\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)\s+[\d.,]+\s*$')


def composition(txt):
    """Состав счёта словами поставщика.

    В PDF название позиции разбито переносами и перемежается числовыми колонками
    («Usluge kancelarije za» / числа / «VII/2026»), поэтому имя собирается из текстовых
    обрывков, а числовые строки только считаются: их количество — число позиций,
    ненулевой рабат — скидка. Формулировки не переписываются: язык счёта — поставщика.
    """
    body = txt.split('VRSTA USLUGE')[-1].split('UKUPNO (RSD)')[0]
    words, rows, discounts = [], 0, []
    for line in body.splitlines():
        t = ' '.join(line.split())
        if not t:
            continue
        r = ROW.search(t)
        if r:
            rows += 1
            if money(r.group(2)) > 0:
                discounts.append(r.group(2).rstrip('0').rstrip(',') + '%')
            t = t[:r.start()].strip()          # перед числами может стоять начало имени
        if not t or re.fullmatch(r'[\d.,%\s]+', t):
            continue
        if 'JEDINICA' in t or 'KOLI' in t:     # шапка таблицы позиций
            continue
        if t not in words:
            words.append(t)
    name = ' '.join(words) or '—'
    if rows > 1:
        name += f' ×{rows}'
    if discounts:
        name += f' (скидка {", ".join(sorted(set(discounts)))})'
    return name


def supplier_payments():
    """[(дата, сумма, назначение, поставщик)] — все платежи поставщикам из выписки."""
    out = []
    assert os.path.exists(CSV_RSD), f'нет {CSV_RSD} — сначала scripts/parse_izvod.py'
    with open(CSV_RSD, newline='') as f:
        for r in csv.DictReader(f):
            if r['category'] != 'supplier':
                continue
            sup = reqs.supplier_by_account(r['counterparty_account'])
            out.append(dict(date=r['date'], amount=Decimal(r['amount']),
                            purpose=r['purpose'], supplier=sup))
    return out


def match(invoices, payments):
    """Оплата привязывается к счёту по его номеру в назначении платежа."""
    paid, used = {}, set()
    for inv in invoices:
        num = inv['no'].split('/')[0]
        for i, pay in enumerate(payments):
            if i in used:
                continue
            # в назначении номер приходит и как `183/2025`, и как `00183-2025`
            if re.search(rf'(^|\D)0*{num}[-/]{inv["no"].split("/")[1]}(\D|$)', pay['purpose']):
                paid[inv['no']] = pay
                used.add(i)
                break
    orphans = [p for i, p in enumerate(payments) if i not in used]
    return paid, orphans


def main():
    # Счетов поставщиков может не быть вовсе — у нового владельца или у того, у кого
    # поставщиков нет. Это не ошибка: реестр просто не собирается, и пересборка целиком
    # (`rebuild_all.sh`) на этом не останавливается.
    if not os.path.isdir(PAYMENTS):
        print(f'{os.path.relpath(PAYMENTS, DATA)}: папки нет — счетов поставщиков пока нет')
        return
    files = sorted(f for y in sorted(os.listdir(PAYMENTS))
                   if os.path.isdir(os.path.join(PAYMENTS, y))
                   for f in [os.path.join(PAYMENTS, y, n)
                             for n in sorted(os.listdir(os.path.join(PAYMENTS, y)))]
                   if f.endswith('.pdf'))
    if not files:
        print(f'{os.path.relpath(PAYMENTS, DATA)}: PDF счетов пока нет — реестр не собирается')
        return
    invoices = [parse_invoice(f) for f in files]
    invoices.sort(key=lambda i: (i['date'][6:], i['date'][3:5], i['date'][:2]))
    payments = supplier_payments()
    paid, orphans = match(invoices, payments)

    by_supplier = defaultdict(Decimal)
    lines = ['# Реестр исходящих платежей поставщикам', '',
             f'Генерируется `{reqs.script_ref("payments_registry.py")}` из PDF счетов '
             'в `finance/payments/` и динарской выписки '
             '(`finance/bank/transactions_rsd.csv`, category=supplier).',
             'Руками не правится: перезапись затрёт правки.', '',
             'Состав берётся из счёта дословно, поэтому язык и формулировки — поставщика.', '',
             '| № | Дата счёта | Состав | Сумма RSD | Оплачен | Файл |',
             '|---|---|---|---:|---|---|']
    for inv in invoices:
        pay = paid.get(inv['no'])
        when = '.'.join(reversed(pay['date'].split('-'))) if pay else '—'
        sup = (pay or {}).get('supplier')
        if sup:
            by_supplier[sup['name']] += pay['amount']
        lines.append(f'| {inv["no"]} | {inv["date"]} | {inv["composition"]} | '
                     f'{fmt(inv["total"])} | '
                     f'{when} | `{inv["file"]}` |')
    total_inv = sum((i['total'] for i in invoices), Decimal(0))
    total_paid = sum((p['amount'] for p in paid.values()), Decimal(0))
    lines += ['', f'Счетов: {len(invoices)} на {fmt(total_inv)} RSD; '
                  f'оплачено {len(paid)} на {fmt(total_paid)} RSD.', '']
    if by_supplier:
        lines += ['## Оплачено по поставщикам', '', '| Поставщик | RSD |', '|---|---:|']
        lines += [f'| {n} | {fmt(v)} |' for n, v in sorted(by_supplier.items())]
        lines.append('')
    unpaid = [i['no'] for i in invoices if i['no'] not in paid]
    if unpaid:
        lines += ['## Счета без оплаты', '',
                  'Нормально в начале месяца, пока платёж не ушёл.', '',
                  *[f'- {n}' for n in unpaid], '']
    if orphans:
        lines += ['## Оплаты без счёта', '',
                  'Платёж есть, PDF под него нет — найти документ '
                  'и положить в `finance/payments/`.',
                  '', *[f'- {p["date"]}, {fmt(p["amount"])} RSD — {p["purpose"][:80]}'
                        for p in orphans], '']
    out = os.path.join(PAYMENTS, 'registry.md')
    open(out, 'w').write('\n'.join(lines))
    print(f'{os.path.relpath(out, DATA)}: счетов {len(invoices)}, оплачено {len(paid)}, '
          f'без оплаты {len(unpaid)}, оплат без счёта {len(orphans)}')


if __name__ == '__main__':
    main()
