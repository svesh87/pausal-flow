#!/usr/bin/env python3
"""Реестр уплаченных налогов из bank/transactions_rsd.csv (category=tax).

    python3 scripts/tax_registry.py

Пишет taxes/registry.md: строка — период (месяц, за который начислено), колонки —
четыре паушальных платежа (порез, ПИО, здравоохранение, НЗС) и эко-такса. Период
берётся из назначения платежа («Doprinos za PIO za 07/2025»); эко-такса начисляется
годовым решением и приходует по дате уплаты. Суммы начислений для сверки — в
решениях налоговой (`taxes/statements/ГГГГ/PAUS-RES*.pdf`).
"""
import os, re, csv, sys
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reqs  # noqa: E402

DATA = reqs.data_root()         # корень данных: finance/, months/, signatures/
KINDS = [('porez', 'Порез'), ('pio', 'ПИО'), ('zdro', 'Здрав'), ('nes', 'НЗС'), ('eko', 'Эко')]


ACCOUNTS = {  # уплатные счета публичных приходов (18 цифр) — главный признак
    '840000071112284332': 'porez', '840000072131384374': 'pio',
    '840000072132584361': 'zdro', '840000072133184306': 'nes',
    '840000071456284356': 'eko'}


def kind_of(purpose, account=''):
    digits = re.sub(r'\D', '', account)
    if len(digits) == 14:                      # запись без ведущих нулей: 840-711122843-32
        digits = digits[:3] + '0000' + digits[3:]
    k = ACCOUNTS.get(digits)
    if k:
        return k
    p = purpose.upper()
    if 'EUPRAVA' in p:  # оплата картой через LPA-портал — локальный налог, т.е. эко-такса
        return 'eko'    # (в сумме комиссия портала 50 RSD)
    if 'PIO' in p:
        return 'pio'
    if 'ZDRO' in p or 'ZDRAV' in p:
        return 'zdro'
    if re.search(r'\b(NES|NZS|NEZ)', p):  # границы слов: NES иначе ловится в BUSSINES
        return 'nes'
    if 'POREZ' in p or 'PAU' in p:
        return 'porez'
    if 'NAKNADA' in p or 'TITU' in p or 'IVOTNE' in p:  # заштиту животне средине
        return 'eko'
    return None


def fmt(d):
    return '—' if d == 0 else f'{d:,.2f}'.replace(',', ' ')


def main():
    csv_path = os.path.join(DATA, 'finance', 'bank', 'transactions_rsd.csv')
    rows = [r for r in csv.DictReader(open(csv_path)) if r['category'] == 'tax']
    periods = defaultdict(lambda: defaultdict(Decimal))
    pay_dates = defaultdict(set)
    unknown = []
    for r in rows:
        k = kind_of(r['purpose'], r['counterparty_account'])
        if k is None:
            unknown.append(r)
            continue
        m = re.search(r'za\s+(\d{2})/(\d{4})', r['purpose'])
        if m and k != 'eko':
            per = f'{m.group(2)}-{m.group(1)}'
        else:
            per = r['date'][:7]      # эко и нераспознанные периоды — по дате уплаты
        periods[per][k] += Decimal(r['amount'])
        pay_dates[per].add(r['date'])

    out = ['# Реестр уплаченных налогов', '',
           f'Генерируется `{reqs.script_ref("tax_registry.py")}` '
           'из `bank/transactions_rsd.csv` — команда',
           '«Обнови налоги». Период — месяц, за который начислен паушальный платёж; эко-такса',
           '(накнада за заштиту и унапређивање животне средине) — годовое решение, показана',
           'в месяце уплаты. Начисления для сверки — решения в `taxes/statements/ГГГГ/`.', '',
           '| Период | ' + ' | '.join(t for _, t in KINDS) + ' | Всего | Уплачено |',
           '|---|' + '---:|' * (len(KINDS) + 1) + '---|']
    total = defaultdict(Decimal)
    years = defaultdict(lambda: defaultdict(Decimal))
    for per in sorted(periods):
        vals = [periods[per][k] for k, _ in KINDS]
        s = sum(vals)
        for (k, _), v in zip(KINDS, vals, strict=True):
            total[k] += v
            years[per[:4]][k] += v
        years[per[:4]]['sum'] += s
        total['sum'] += s
        dates = sorted(pay_dates[per])
        out.append(f'| {per} | ' + ' | '.join(fmt(v) for v in vals) +
                   f' | {fmt(s)} | {", ".join(dates)} |')
    out.append('| **итого** | ' + ' | '.join(f'**{fmt(total[k])}**' for k, _ in KINDS) +
               f' | **{fmt(total["sum"])}** | |')
    out += ['', '## По годам', '',
            '| Год | ' + ' | '.join(t for _, t in KINDS) + ' | Всего |',
            '|---|' + '---:|' * (len(KINDS) + 1)]
    for y in sorted(years):
        out.append(f'| {y} | ' + ' | '.join(fmt(years[y][k]) for k, _ in KINDS) +
                   f' | {fmt(years[y]["sum"])} |')
    out.append('')
    if unknown:
        out += ['## Нераспознанные налоговые платежи — разобрать руками', '']
        out += [f'- {r["date"]} {r["amount"]} {r["purpose"][:120]}' for r in unknown] + ['']

    path = os.path.join(DATA, 'finance', 'taxes', 'payments.md')
    os.makedirs(os.path.dirname(path), exist_ok=True)   # в свежем дереве данных папки ещё нет
    open(path, 'w').write('\n'.join(out))
    print(f'{os.path.relpath(path, DATA)}: {len(periods)} периодов, '
          f'{len(rows)} платежей, всего {fmt(total["sum"])} RSD'
          + (f', НЕРАСПОЗНАННЫХ: {len(unknown)}' if unknown else ''))


if __name__ == '__main__':
    main()
