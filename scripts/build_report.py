#!/usr/bin/env python3
"""Сводный финансовый отчёт по месяцам и годам из bank/transactions_*.csv.

    python3 scripts/build_report.py

Пишет finance/report.md: по каждому году таблица по месяцам, внизу итог года
и общий итог. Колонки:

  Приход EUR      выручка на девизный счёт (category=income, EUR)
  Приход RSD      динарская противостоимость проданной валюты (category=income, RSD)
  Налоги          уплаты на счета 840-… (category=tax)
  Поставщики      счета поставщиков (category=supplier), разбивка по каждому — ниже таблиц
  Банк            провизии и наknade (category=bank_fee)
  Личный счёт     переводы на личный счёт владельца (category=personal)
  Наличные        снятия бизнес-картой: ATM банка и сеть MULTICARD (category=cash)
  Карта           покупки бизнес-картой (category=card)

Месяц — календарный, по дате операции в выписке (не по периоду работ, в отличие
от months/). Перед таблицами — оговорки: стартовый остаток и пропуски в цепочке
выписок из parse_izvod.balance_chain(). Категории fx, founding и other в отчёт
не идут: первая — зеркало продажи валюты, вторая — взнос при открытии счёта,
третья — то, чего категоризатор ещё не знает.
"""
import os, csv, sys
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_izvod import balance_chain  # noqa: E402
import reqs  # noqa: E402

DATA = reqs.data_root()         # корень данных: finance/, months/, signatures/

COLS = [('income_eur', 'Приход EUR'), ('income_rsd', 'Приход RSD'), ('tax', 'Налоги'),
        ('supplier', 'Поставщики'), ('bank_fee', 'Банк'), ('personal', 'Личный счёт'),
        ('cash', 'Наличные'), ('card', 'Карта')]
MONTHS = {1: 'янв', 2: 'фев', 3: 'мар', 4: 'апр', 5: 'май', 6: 'июн',
          7: 'июл', 8: 'авг', 9: 'сен', 10: 'окт', 11: 'ноя', 12: 'дек'}


def fmt(d):
    if d == 0:
        return '—'
    s = f'{d:,.2f}'
    return s.replace(',', ' ')


def load():
    """({(год, месяц): {колонка: сумма}}, {(поставщик, год): сумма})"""
    data = defaultdict(lambda: defaultdict(Decimal))
    by_supplier = defaultdict(Decimal)
    for name in ('transactions_eur.csv', 'transactions_rsd.csv'):
        with open(os.path.join(DATA, 'finance', 'bank', name)) as f:
            for r in csv.DictReader(f):
                y, m = int(r['date'][:4]), int(r['date'][5:7])
                cat = r['category']
                if cat == 'income':
                    col = 'income_eur' if r['currency'] == 'EUR' else 'income_rsd'
                elif cat in dict(COLS):
                    col = cat
                else:
                    continue  # fx (зеркало продажи валюты) и other в отчёт не идут
                data[(y, m)][col] += Decimal(r['amount'])
                if cat == 'supplier':
                    sup = reqs.supplier_by_account(r['counterparty_account'])
                    by_supplier[(sup['name'] if sup else r['counterparty'] or '—', y)] += \
                        Decimal(r['amount'])
    return data, by_supplier


def table(rows_keys, data, label_of):
    lines = ['| Месяц | ' + ' | '.join(t for _, t in COLS) + ' |',
             '|---|' + '---:|' * len(COLS)]
    total = defaultdict(Decimal)
    for key in rows_keys:
        cells = []
        for col, _ in COLS:
            v = data[key][col]
            total[col] += v
            cells.append(fmt(v))
        lines.append(f'| {label_of(key)} | ' + ' | '.join(cells) + ' |')
    lines.append('| **итого** | ' + ' | '.join(f'**{fmt(total[c])}**' for c, _ in COLS) + ' |')
    return lines, total


def main():
    data, by_supplier = load()
    assert data, 'finance/bank/transactions_*.csv пусты — сначала scripts/parse_izvod.py'
    gaps, spans = balance_chain()

    out = ['# Финансовый отчёт по счетам ИП', '',
           f'Собирается скриптом `{reqs.script_ref("build_report.py")}` из '
           '`finance/bank/transactions_*.csv` (они — из выписок в `finance/bank/raw/`, '
           f'см. `{reqs.script_ref("parse_izvod.py")}`). Месяц — календарный по дате операции. '
           '«Приход RSD» — динарская противостоимость проданной валюты, поэтому месяцы '
           'прихода EUR и RSD могут расходиться на несколько дней.', '', '## Оговорки', '']
    for kind, s in spans.items():
        opening = f'{Decimal(s["opening"]):,.2f}'.replace(',', ' ')
        closing = f'{Decimal(s["closing"]):,.2f}'.replace(',', ' ')
        out.append(f'- Выписки {kind.upper()}: {s["first"]} — {s["last"]}, входящий остаток '
                   f'{opening}, исходящий {closing}.')
    out.append('- Операции до первой выписки (открытие счёта: завод денег, комиссии) '
               'в отчёт не входят — от них остался только входящий остаток.')
    # закрыт ли пропуск, решает арифметика цепочки в balance_chain(), а не наличие
    # каких-нибудь операций из выгрузки в этих числах
    for g in gaps:
        if g['closed']:
            out.append(f'- Пропуск izvod-а {g["kind"].upper()} между {g["after"]} '
                       f'(izvod {g["izvod_a"]}) и {g["before"]} (izvod {g["izvod_b"]}): '
                       f'операции за {", ".join(g["days"])} добраны из выгрузки '
                       f'«Promet po računu», сальдо {fmt(Decimal(g["delta"]))} сходится — '
                       f'данные полные, в отчёте учтены.')
        else:
            out.append(f'- Разрыв {g["kind"].upper()}: между {g["after"]} (izvod {g["izvod_a"]}) '
                       f'и {g["before"]} (izvod {g["izvod_b"]}) не хватает izvod-а, дельта '
                       f'{fmt(Decimal(g["delta"]))} — эти операции в отчёте НЕ учтены, '
                       f'выгрузить период из eBank в bank/raw/ и прогнать «Обнови банк».')
    out.append('')

    years = sorted({y for y, _ in data})
    for y in years:
        out += [f'## {y}', '']
        months = sorted(m for yy, m in data if yy == y)
        lines, _ = table([(y, m) for m in months], data, lambda k: MONTHS[k[1]])
        out += lines + ['']

    if by_supplier:
        sup_years = sorted({y for _, y in by_supplier})
        out += ['## Затраты по поставщикам', '',
                '| Поставщик | ' + ' | '.join(str(y) for y in sup_years) + ' | итого |',
                '|---|' + '---:|' * (len(sup_years) + 1)]
        for name in sorted({n for n, _ in by_supplier}):
            cells = [fmt(by_supplier[(name, y)]) for y in sup_years]
            total = sum((by_supplier[(name, y)] for y in sup_years), Decimal(0))
            out.append(f'| {name} | ' + ' | '.join(cells) + f' | **{fmt(total)}** |')
        out.append('')

    out += ['## Итоги по годам', '']
    lines, grand = table([y for y in years], defaultdict(lambda: defaultdict(Decimal), {
        y: {c: sum((data[(yy, m)][c] for yy, m in data if yy == y), Decimal(0)) for c, _ in COLS}
        for y in years}), str)
    lines[0] = lines[0].replace('| Месяц |', '| Год   |', 1)
    out += lines + ['']

    path = os.path.join(DATA, 'finance', 'report.md')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w').write('\n'.join(out))
    print(f'{os.path.relpath(path, DATA)}: {len(years)} лет, {len(data)} месяцев')
    print('всего: ' + ', '.join(f'{t} {fmt(grand[c])}' for c, t in COLS))


if __name__ == '__main__':
    main()
