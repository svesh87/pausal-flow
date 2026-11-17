"""Эко-такса: разбор решения, кварталы, сроки и напоминания.

Единственный платёж без месячного ритма: годовое решение, четыре квартальных срока
и годовая пријава. Пропуск не виден нигде — ни в выписке, ни в реестре по месяцам, — пока
не придёт опомена с каматом. Поэтому здесь проверяется не столько разбор PDF, сколько
арифметика зачёта и то, что просрочка действительно называется просрочкой.

Текст решения синтетический: форма настоящая, реквизиты выдуманы. Публичные уплатные
счета оставлены как есть — они одинаковы у всех налогоплательщиков.
"""
import datetime
from decimal import Decimal

import tax_registry as tax

# Фрагмент решения об эко-таксе так, как его отдаёт pdftotext -layout: квартальные суммы
# в отдельной строке под шапкой, фраза про уплату разорвана переносом строки, а ниже —
# второй счёт с другим позивом на број (административная такса на случай жалобы).
DECISION_TXT = """
                                РЕШЕЊЕ
      УТВРЂУЈЕ СЕ висина годишњег износа накнаде за заштиту и унапређивање животне
средине за 2025. годину,
 Р.     Степен            Укупaн приход    Годишња накнада     Период      Утврђена
 бр. негативног утицаја  паушално у 2024.     Прописана        у 2025.     накнада
  1       Мали               305 110,10        1 220,44     01.01 -31.12.  1 220,44
                     Укупно за период 01.01.2025 - 31.12.2025.            1 220,44

    Утврђени износ накнаде обвезник је дужан да плаћа тромесечно, сразмерно броју дана у
тромесечју, у року од 15 дана од дана истека тромесечја и то:
      Прво тромесечје      Друго тромесечје     Треће тромесечје   Четврто тромесечје
             300.93               304.27               307.62             307.62

     Накнада за заштиту и унапређивање животне средине уплаћује се на рачун број
840-714562843-56 са позивом на
број 97 78012000000000.
      До доспелости решењa за 2026. годину, обвезник је дужан да плаћа аконтацију накнаде
у висини обавезе за последње тромесечје претходне године.
       Жалба се таксира са 610,00 динара административне таксе и уплаћује на рачун број
840-742221843-57 - Републичке административне таксе, са позивом на број 97 7501262398.
"""

# Опомена приходит тем же кодом документа `012_`, говорит о той же накнади и тоже содержит
# суммы — но это долг с каматом, а не начисление.
OPOMENA_TXT = """
                                                            ОПОМЕНУ
    Пореском обвезнику: ПРЕЗИМЕ ИМЕ
I Према књиговодственом стању имате доспелу а неизмирену обавезу по основу:
        Накнада за заштиту и унапређивање
 1.                                           840-714562843-56    307.62    5.71    313.33
                 животне средине
III ... поступак принудне наплате доношењем решења о принудној наплати ...
"""

DECISION = {2025: {**tax.eco_decision(DECISION_TXT), 'file': 'x'}}
TODAY = datetime.date(2026, 8, 4)


def test_decision_gives_year_and_quarters():
    """Год и разбивка по кварталам — то, из чего потом считаются сроки."""
    d = tax.eco_decision(DECISION_TXT)
    assert d['year'] == 2025
    assert d['quarters'] == [Decimal('300.93'), Decimal('304.27'),
                             Decimal('307.62'), Decimal('307.62')]
    assert sum(d['quarters']) == d['total']


def test_decision_money_formats_differ_in_one_document():
    """В одном решении годовая сумма — `1 220,44`, квартальная — `300.93`."""
    assert tax.eco_money('1 220,44') == Decimal('1220.44')
    assert tax.eco_money('300.93') == Decimal('300.93')
    assert tax.eco_decision(DECISION_TXT)['total'] == Decimal('1220.44')


def test_payment_details_are_not_taken_from_appeal_fee():
    """Счёт и позив на број — из фразы про накнаду, а не из таксы на жалобу ниже."""
    d = tax.eco_decision(DECISION_TXT)
    assert d['account'] == '840-714562843-56'
    assert d['ref'] == '97 78012000000000'


def test_opomena_is_not_a_decision():
    """Опомена с тем же префиксом `012_` начислением не считается."""
    assert tax.eco_decision(OPOMENA_TXT) is None


def test_akontacija_falls_back_to_last_quarter():
    """Решения за год нет — платится обязанность последнего квартала прошлого года."""
    quarters, source = tax.eco_expected(2026, DECISION)
    assert quarters == [Decimal('307.62')] * 4
    assert source == 'аконтација по решению 2025'
    assert tax.eco_expected(2025, DECISION)[1] == 'решение 2025'


def test_overdue_quarter_is_reported():
    """Срок истёк, платежа нет — квартал просрочен, и это видно в тревогах."""
    ledger = tax.eco_ledger([('2026-01-10', Decimal('307.62'))], DECISION, TODAY)
    q1 = next(r for r in ledger if (r['year'], r['q']) == (2026, 1))
    assert q1['due'] == datetime.date(2026, 4, 15) and q1['status'] == 'ПРОСРОЧЕН'
    alerts = tax.eco_alerts(ledger, DECISION, {2026}, TODAY)
    assert any('ПРОСРОЧЕНО: 2026 Q1' in a for a in alerts)


def test_lump_payment_covers_whole_year():
    """Год, уплаченный одной суммой, закрывает все четыре квартала."""
    ledger = tax.eco_ledger([('2025-09-03', Decimal('1220.44'))], DECISION, TODAY)
    assert [r['status'] for r in ledger if r['year'] == 2025] == ['уплачен'] * 4


def test_january_payment_closes_last_years_quarter():
    """Четвёртый квартал доспева 15 января: январский платёж — за прошлый год."""
    payments = [('2025-09-03', Decimal('912.82')),   # Q1..Q3 2025 одной суммой
                ('2026-01-12', Decimal('307.62'))]   # Q4 2025, срок 15.01.2026
    ledger = tax.eco_ledger(payments, DECISION, TODAY)
    paid = {(r['year'], r['q']): r['paid'] for r in ledger}
    assert paid[(2025, 4)] == Decimal('307.62') and paid[(2026, 1)] == 0


def test_overpay_does_not_prepay_future_quarter():
    """Камата по опомене и комиссия портала — не аванс за следующий квартал."""
    payments = [('2026-05-30', Decimal('313.33')),   # 307.62 + камата 5.71
                ('2026-07-06', Decimal('357.62'))]   # 307.62 + 50.00 комиссии портала
    ledger = tax.eco_ledger(payments, DECISION, TODAY)
    paid = {(r['year'], r['q']): r['paid'] for r in ledger}
    assert paid[(2026, 1)] == paid[(2026, 2)] == Decimal('307.62')
    assert paid[(2026, 3)] == 0
    nxt = [a for a in tax.eco_alerts(ledger, DECISION, {2026}, TODAY) if 'следующий' in a]
    assert nxt and '2026 Q3' in nxt[0] and '15.10.2026' in nxt[0]


def test_prijava_reminder_stops_at_marker():
    """Напоминание о пријаве гаснет от файла в данных или от решения за год."""
    ledger = tax.eco_ledger([('2025-09-03', Decimal('1220.44'))], DECISION, TODAY)

    def prijava_alerts(prijava_years, decisions=DECISION, today=TODAY):
        return [a for a in tax.eco_alerts(ledger, decisions, prijava_years, today)
                if 'пријава' in a]

    assert prijava_alerts(set())                      # за 2026 ни пријаве, ни решения
    assert not prijava_alerts({2026})                 # пријава лежит в statements/2026/
    assert not prijava_alerts(set(), {**DECISION, 2026: DECISION[2025]})  # решение пришло
    assert not prijava_alerts(set(), today=datetime.date(2026, 2, 1))     # рано напоминать


def test_no_decisions_is_quiet():
    """Без решений в данных проверка молчит: клон без данных и свежее дерево — норма."""
    assert tax.eco_ledger([], {}, TODAY) == []
    assert tax.eco_alerts([], {}, set(), TODAY) == []


def test_offbank_payments_are_read_from_data(tmp_path, monkeypatch):
    """Налоги, уплаченные не со счёта ИП, берутся из файла; период паушала — из колонки."""
    taxes = tmp_path / 'finance' / 'taxes'
    taxes.mkdir(parents=True)
    (taxes / 'offbank.csv').write_text('date,kind,amount,period,note\n'
                                       '2026-10-05,eko,307.62,,квитанция\n'
                                       '2026-11-14,pio,23947.02,2026-11,карта\n')
    monkeypatch.setattr(tax, 'DATA', str(tmp_path))
    got = tax.offbank()
    assert [(r['kind'], r['amount'], r['period']) for r in got] == [
        ('eko', Decimal('307.62'), '2026-10'), ('pio', Decimal('23947.02'), '2026-11')]


def test_offbank_unknown_kind_fails(tmp_path, monkeypatch):
    """Опечатка в виде налога — падение, а не молча потерянный платёж."""
    taxes = tmp_path / 'finance' / 'taxes'
    taxes.mkdir(parents=True)
    (taxes / 'offbank.csv').write_text('date,kind,amount,period,note\n2026-10-05,eco,1.00,,\n')
    monkeypatch.setattr(tax, 'DATA', str(tmp_path))
    try:
        tax.offbank()
    except SystemExit as e:
        assert 'eco' in str(e)
    else:
        raise AssertionError('неизвестный вид налога прошёл молча')


def test_offbank_file_is_optional(tmp_path, monkeypatch):
    """Без файла платежей не со счёта ИП — пусто, а не ошибка."""
    monkeypatch.setattr(tax, 'DATA', str(tmp_path))
    assert tax.offbank() == []


def test_quarters_without_thousands_separator():
    """«1073.58» в квартальной разбивке — тысяча, а не 73.58."""
    txt = (DECISION_TXT.replace('1 220,44', '4 354,00')
           .replace('300.93               304.27               307.62             307.62',
                    '1073.58              1085.52              1097.45            1097.45'))
    d = tax.eco_decision(txt)
    assert d['quarters'] == [Decimal('1073.58'), Decimal('1085.52'),
                             Decimal('1097.45'), Decimal('1097.45')]


def test_quarters_must_add_up_to_total():
    """Кварталы, не сложившиеся в годовую сумму, — сломанный разбор, а не данные."""
    try:
        tax.eco_decision(DECISION_TXT.replace('300.93', '200.93'))
    except SystemExit:
        return
    raise AssertionError('кривая разбивка прошла молча')


def test_opomena_payment_counts_principal_only():
    """Платёж по опомене гасит главный долг; камата квартал не оплачивает."""
    o = tax.eco_opomena(OPOMENA_TXT.replace('ОПОМЕНУ', 'дана 29.05.2026. године, издаје\nОПОМЕНУ'))
    assert o['principal'] == Decimal('307.62') and o['total'] == Decimal('313.33')
    assert tax.eco_principal('2026-05-30', Decimal('313.33'), [o]) == Decimal('307.62')
    assert tax.eco_principal('2026-05-30', Decimal('307.62'), [o]) == Decimal('307.62')


def test_late_decision_moves_due_date():
    """Кварталы, доспевшие до решения, доплачиваются в 15 дней от него — не просрочка."""
    late = {2026: {**DECISION[2025], 'year': 2026, 'issued': datetime.date(2026, 10, 2)}}
    assert tax.eco_due(2026, 1, late) == datetime.date(2026, 10, 17)
    assert tax.eco_due(2026, 4, late) == datetime.date(2027, 1, 15)
    ledger = tax.eco_ledger([('2026-05-30', Decimal('307.62'))], late,
                            datetime.date(2026, 10, 10))
    assert not any('ПРОСРОЧЕН' in r['status'] for r in ledger if r['year'] == 2026)


def test_fully_paid_year_still_names_next_payment():
    """Год уплачен целиком — строка про эко-таксу всё равно есть: срок уже в следующем году."""
    ledger = tax.eco_ledger([('2025-09-03', Decimal('1220.44'))], DECISION,
                            datetime.date(2025, 11, 1))
    alerts = tax.eco_alerts(ledger, DECISION, {2025}, datetime.date(2025, 11, 1))
    assert any('2026 Q1' in a and '307.62' in a and '15.04.2026' in a for a in alerts)


def test_portal_fee_is_two_percent_with_minimum():
    """Комиссия портала — 2 %, но не меньше 50: обе ветки по настоящим квитанциям."""
    assert tax.eco_without_fee(Decimal('357.62')) == Decimal('307.62')
    assert tax.eco_without_fee(Decimal('3499.76')) == Decimal('3431.14')
