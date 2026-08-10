#!/usr/bin/env python3
"""Реестр уплаченных налогов из bank/transactions_rsd.csv (category=tax).

    python3 scripts/tax_registry.py

Пишет taxes/payments.md: строка — период (месяц, за который начислено), колонки —
четыре паушальных платежа (порез, ПИО, здравоохранение, НЗС) и эко-такса. Период
берётся из назначения платежа («Doprinos za PIO za 07/2025»); эко-такса начисляется
годовым решением и приходует по дате уплаты. Суммы начислений для сверки — в
решениях налоговой (`taxes/statements/ГГГГ/PAUS-RES*.pdf`).

**Эко-такса живёт не по месячному ритму, поэтому её сроки скрипт сторожит сам.** Накнада
за заштиту и унапређивање животне средине начисляется годовым решением, платится
тромесечно и подтверждается годовой пријавом — три разных срока, ни одного ежемесячного,
и пропуск виден только по опомене с каматом. Скрипт читает решения
(`taxes/statements/ГГГГ/012_*.pdf`), раскладывает уплаченное по кварталам, пишет секцию
«Эко-такса по кварталам» и печатает тревоги: просроченный квартал, ближайший срок,
отсутствие решения за текущий год, ненайденная пријава. Пока решения за год нет,
начислением считается аконтација — обязанность последнего квартала прошлого года,
как предписывает само решение.
"""
import os, re, csv, sys, glob, datetime, subprocess
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reqs  # noqa: E402

DATA = reqs.data_root()         # корень данных: finance/, months/, signatures/
KINDS = [('porez', 'Порез'), ('pio', 'ПИО'), ('zdro', 'Здрав'), ('nes', 'НЗС'), ('eko', 'Эко')]

# Сроки квартальных платежей эко-таксы: «у року од 15 дана од дана истека тромесечја»
# (решение о накнади). Четвёртый квартал поэтому доспева уже в январе следующего года —
# из-за этого январский платёж относится к прошлогодней обязанности, см. eco_ledger().
ECO_DUE = ((4, 15), (7, 15), (10, 15), (1, 15))
ECO_PRIJAVA_DUE = (4, 30)       # пријава — до 30 апреля года, за который определяется накнада
ECO_PRIJAVA_FROM = (3, 1)       # раньше марта напоминать не о чем: год только начался
ECO_DECISION_FROM = 9           # решение за год выставляют осенью — до сентября его и не ждём

# Сумма в решении: две дробные цифры обязательны, разряды через пробел или точку. Форма
# нужна именно такая узкая: в строке «Укупно за период 01.01.2025 - 31.12.2025.  1 220,44»
# рядом с суммой стоят даты, и жадный `[\d.,]+` склеивал их в одно число.
ECO_MONEY = r'\d{1,3}(?:[ \xa0.]\d{3})*[,.]\d{2}'


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


def fmt0(d):
    """Как fmt, но нуль печатается нулём: в тревоге «уплачено —» читается как ошибка."""
    return f'{d:,.2f}'.replace(',', ' ')


def ru_date(d):
    return d.strftime('%d.%m.%Y')


def pdftext(path):
    r = subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True)
    assert r.returncode == 0, f'{path}: pdftotext не смог прочитать файл'
    return r.stdout.decode('utf-8', 'replace')


def eco_money(s):
    """Сумма из решения об эко-таксе.

    В одном документе два формата: годовая накнада печатается по сербскому образцу
    («1 220,44» — пробел разряды, запятая дробная часть), а квартальные разбивки —
    с точкой («300.93»). Перепутать разделители значит ошибиться в тысячу раз, поэтому
    формат выбирается по наличию запятой, а не угадывается.
    """
    s = s.replace('\xa0', ' ').strip()
    if ',' in s:
        return Decimal(s.replace(' ', '').replace('.', '').replace(',', '.'))
    return Decimal(s.replace(' ', ''))


def eco_decision(txt):
    """Решение об эко-таксе из текста PDF или None, если это не решение.

    Кодом документа `012_` Секретариат за јавне приходе присылает и решение, и опомену
    о недоимке, поэтому вид определяется содержимым: у решения есть заголовок «РЕШЕЊЕ»
    и год, за который утверждена накнада, у опомене — «ОПОМЕНУ» и доспела неизмирена
    обавеза. Спутать нельзя: суммы в опомене — долг с каматом, а не начисление.
    """
    if 'ОПОМЕНУ' in txt or not re.search(r'^\s*РЕШЕЊЕ\s*$', txt, re.M):
        return None
    # Пробелы между словами — \s+: pdftotext рвёт фразы по строкам там, где ему удобно,
    # и одна и та же форма приходит то одной строкой, то двумя.
    year = re.search(r'животне\s+средине\s+за\s+(\d{4})\.\s+годину', txt)
    total = re.search(rf'Укупно\s+за\s+период[^\n]*?({ECO_MONEY})\s*$', txt, re.M)
    quarters = re.search(r'Прво\s+тромесечје.*?\n([^\n]+)', txt, re.S)
    if not (year and total and quarters):
        return None
    amounts = [eco_money(m) for m in re.findall(ECO_MONEY, quarters.group(1))]
    if len(amounts) != 4:
        return None
    # Реквизиты уплаты берутся строго из фразы про саму накнаду: ниже в решении есть второй
    # счёт и позив на број — административная такса на случай жалобы, и по общему шаблону
    # «рачун број …» находится именно он. Пробелы через \s+: pdftotext рвёт фразу по строкам.
    pay = re.search(r'уплаћује се на рачун број\s+(\d{3}-\d+-\d+)\s+са\s+позивом\s+на'
                    r'\s+број\s+(\d{2}\s+\d+)', txt)
    return {'year': int(year.group(1)), 'total': eco_money(total.group(1)),
            'quarters': amounts, 'account': pay.group(1) if pay else '',
            'ref': pay.group(2) if pay else ''}


def eco_decisions():
    """{год: решение} по всем решениям в taxes/statements/ГГГГ/012_*.pdf.

    Свежее дерево данных и одно-древный режим решений не содержат: пустой словарь —
    законное состояние, дальше проверка кварталов просто молчит.
    """
    out = {}
    pattern = os.path.join(DATA, 'finance', 'taxes', 'statements', '*', '012_*.pdf')
    for path in sorted(glob.glob(pattern)):
        d = eco_decision(pdftext(path))
        if d:
            d['file'] = os.path.relpath(path, DATA)
            out[d['year']] = d
    return out


def eco_expected(year, decisions):
    """Квартальные начисления года и словами — откуда они взялись.

    Есть решение за год — его разбивка. Нет — аконтација: «До доспелости решења
    за следећу годину, обвезник је дужан да плаћа аконтацију накнаде у висини обавезе
    за последње тромесечје претходне године». Нет и прошлогоднего решения — считать
    не от чего, начисления неизвестны.
    """
    if year in decisions:
        return decisions[year]['quarters'], f'решение {year}'
    prev = decisions.get(year - 1)
    if prev:
        return [prev['quarters'][3]] * 4, f'аконтација по решению {year - 1}'
    return [None] * 4, 'решения нет'


def eco_due(year, q):
    month, day = ECO_DUE[q - 1]
    return datetime.date(year + 1 if q == 4 else year, month, day)


def _status(amount, paid, due, today):
    if amount is None:
        return 'сумма неизвестна'
    if paid >= amount:
        return 'уплачен'
    if paid > 0:
        return 'частично, ПРОСРОЧЕН' if today > due else 'частично'
    return 'ПРОСРОЧЕН' if today > due else 'ожидается'


def eco_ledger(payments, decisions, today):
    """Кварталы эко-таксы: начислено, срок, уплачено, статус.

    `payments` — [(дата ISO, сумма)] платежей вида eko из динарской выписки. Квартал платежа
    в выписке не написан, поэтому уплаченное раскладывается по годовой обязанности: платёж
    закрывает кварталы своего года по порядку, а излишек (камата по опомене, 50 RSD комиссии
    ЛПА-портала) гаснет и будущий квартал не оплачивает — иначе комиссия притворялась бы
    предоплатой. Годовой платёж одной суммой закрывает все четыре квартала, это законный
    случай: так закрыт 2025 год после решения, вынесенного в августе.

    Внутри года платёж закрывает самый ранний незакрытый квартал: недоимку налоговая
    зачитывает от старой к новой, и опомена приходит именно на неё. Январский платёж
    по той же причине относится к прошлому году, если у того остались незакрытые
    кварталы: четвёртый доспева 15 января, и деньги за него уходят уже в новом году.
    """
    years = {int(d[:4]) for d, _ in payments} | set(decisions)
    if not years:
        return []
    amounts = {y: eco_expected(y, decisions)
               for y in range(min(years), max(max(years), today.year) + 1)}
    paid = defaultdict(Decimal)
    for date, amount in sorted(payments):
        y = int(date[:4])
        quarters, _ = amounts.get(y - 1, ([None] * 4, ''))
        outstanding = (all(q is not None for q in quarters)
                       and sum(paid[(y - 1, q)] for q in (1, 2, 3, 4)) < sum(quarters))
        if date[5:7] == '01' and outstanding:
            y -= 1
        left, spill = Decimal(amount), False
        for q, due_amount in enumerate(amounts.get(y, ([None] * 4, ''))[0], 1):
            if due_amount is None or left <= 0:
                break
            short = due_amount - paid[(y, q)]
            if short <= 0:                 # квартал уже закрыт — идём к следующему
                continue
            if spill and left < short:
                break        # на следующий квартал переходит только полная его сумма: остаток
            take = min(left, short)        # платежа — это камата и комиссия портала, а не аванс
            paid[(y, q)] += take
            left -= take
            spill = True
    return [{'year': y, 'q': q, 'amount': amounts[y][0][q - 1], 'source': amounts[y][1],
             'due': eco_due(y, q), 'paid': paid[(y, q)],
             'status': _status(amounts[y][0][q - 1], paid[(y, q)], eco_due(y, q), today)}
            for y in sorted(amounts) for q in (1, 2, 3, 4)]


def eco_prijava_years():
    """Годы, за которые пријава лежит в данных: statements/ГГГГ/prijava_ГГГГ*."""
    pattern = os.path.join(DATA, 'finance', 'taxes', 'statements', '*', 'prijava_*')
    years = set()
    for path in glob.glob(pattern):
        m = re.search(r'prijava_(\d{4})', os.path.basename(path))
        if m:
            years.add(int(m.group(1)))
    return years


def eco_alerts(ledger, decisions, prijava_years, today):
    """Строки о сроках эко-таксы — и в реестр, и в вывод скрипта, и в сводку навыка."""
    alerts = []
    for r in ledger:
        if 'ПРОСРОЧЕН' in r['status']:
            alerts.append(f'ПРОСРОЧЕНО: {r["year"]} Q{r["q"]} — срок {ru_date(r["due"])}, '
                          f'начислено {fmt0(r["amount"])}, уплачено {fmt0(r["paid"])}')
    nxt = next((r for r in ledger if r['status'] == 'ожидается'), None)
    if nxt:
        acting = decisions.get(nxt['year']) or decisions.get(nxt['year'] - 1) or {}
        where = ', '.join(x for x in (f'счёт {acting.get("account")}' if acting.get('account')
                                      else '', f'позив на број {acting["ref"]}'
                                      if acting.get('ref') else '') if x)
        alerts.append(f'следующий платёж: {nxt["year"]} Q{nxt["q"]} — {fmt0(nxt["amount"])} '
                      f'до {ru_date(nxt["due"])} ({nxt["source"]})'
                      + (f'; {where}' if where else ''))
    if ledger and today.year not in decisions and today.month >= ECO_DECISION_FROM:
        alerts.append(f'решения за {today.year} год в данных нет, а его уже выставляют — '
                      'проверить портал ЛПА; до него платится аконтација')
    prijava_due = datetime.date(today.year, *ECO_PRIJAVA_DUE)
    if ledger and today.year not in decisions and today.year not in prijava_years:
        # решение за год само означает, что пријава дошла: молчим, не выдумывая просрочку
        if today >= datetime.date(today.year, *ECO_PRIJAVA_FROM):
            when = ('срок истёк' if today > prijava_due else f'срок {ru_date(prijava_due)}')
            alerts.append(f'пријава за {today.year} год в данных не отмечена ({when}) — '
                          f'если сдана, положить prijava_{today.year}.pdf '
                          f'в finance/taxes/statements/{today.year}/')
    return alerts


def main():
    csv_path = os.path.join(DATA, 'finance', 'bank', 'transactions_rsd.csv')
    rows = [r for r in csv.DictReader(open(csv_path)) if r['category'] == 'tax']
    periods = defaultdict(lambda: defaultdict(Decimal))
    pay_dates = defaultdict(set)
    unknown = []
    eco_payments = []
    for r in rows:
        k = kind_of(r['purpose'], r['counterparty_account'])
        if k is None:
            unknown.append(r)
            continue
        if k == 'eko':
            eco_payments.append((r['date'], Decimal(r['amount'])))
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

    today = datetime.date.today()
    decisions = eco_decisions()
    ledger = eco_ledger(eco_payments, decisions, today)
    alerts = eco_alerts(ledger, decisions, eco_prijava_years(), today)
    if ledger:
        acting = ', '.join(sorted({r['source'] for r in ledger if r['year'] == today.year}))
        out += ['## Эко-такса по кварталам', '',
                'Накнада за заштиту и унапређивање животне средине платится тромесечно —',
                'в течение 15 дней после истечения квартала, — а подтверждается годовой',
                'пријавом до 30 апреля года, за который определяется накнада. Начисления —',
                'из решений `taxes/statements/ГГГГ/012_*.pdf`; пока решения за год нет,',
                'действует аконтација в размере обязанности последнего квартала прошлого',
                f'года. За {today.year} год действует: {acting}.', '',
                '| Год | Квартал | Начислено | Срок | Уплачено | Статус |',
                '|---|---|---:|---|---:|---|']
        for r in ledger:
            amount = fmt(r['amount']) if r['amount'] is not None else '—'
            out.append(f'| {r["year"]} | Q{r["q"]} | {amount} | {ru_date(r["due"])} | '
                       f'{fmt(r["paid"])} | {r["status"]} |')
        out.append('')
        if alerts:
            out += ['Сроки на сегодня:', ''] + [f'- {a}' for a in alerts] + ['']
    if unknown:
        out += ['## Нераспознанные налоговые платежи — разобрать руками', '']
        out += [f'- {r["date"]} {r["amount"]} {r["purpose"][:120]}' for r in unknown] + ['']

    path = os.path.join(DATA, 'finance', 'taxes', 'payments.md')
    os.makedirs(os.path.dirname(path), exist_ok=True)   # в свежем дереве данных папки ещё нет
    open(path, 'w').write('\n'.join(out))
    print(f'{os.path.relpath(path, DATA)}: {len(periods)} периодов, '
          f'{len(rows)} платежей, всего {fmt(total["sum"])} RSD'
          + (f', НЕРАСПОЗНАННЫХ: {len(unknown)}' if unknown else ''))
    for a in alerts:      # эко-такса вне месячного ритма: её сроки видны только здесь
        print(f'эко-такса: {a}')


if __name__ == '__main__':
    main()
