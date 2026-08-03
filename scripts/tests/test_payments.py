"""Реестр платежей поставщикам: состав счёта, привязка оплат, расхождения.

Реестр — единственное место, где видно, за что заплачено. Пропущенная привязка выглядит
как неоплаченный счёт (пойдут искать платёж, которого нет), а лишняя — как оплаченный,
которого не было.
"""
from decimal import Decimal

import payments_registry as pay

# Фрагмент текста счёта поставщика: название позиции разбито переносами и перемежается
# числовыми колонками — ровно так его отдаёт pdftotext -layout.
INVOICE_TXT = """
                Faktura:                                Datum fakture     Datum prometa
                116/2026                                02.07.2026        02.07.2026

                VRSTA USLUGE          JEDINICA   KOLIČINA    CENA   RABAT %    UKUPNO

                Usluge kancelarije za
                                      Komad          1,00  5.850,00   0,00   5.850,00
                VII/2026

                Usluge kancelarije za
                                      Komad          1,00  5.850,00  50,00   2.925,00
                VII/2026

                UKUPNO (RSD)                                                11.700,00
                UKUPNO ZA UPLATU (RSD)                                       8.775,00
"""


def test_composition_keeps_supplier_wording():
    """Формулировки не переписываются: язык счёта — поставщика, наше дело — не потерять."""
    assert pay.composition(INVOICE_TXT) == 'Usluge kancelarije za VII/2026 ×2 (скидка 50%)'


def test_composition_drops_table_header_and_numbers():
    """Шапка таблицы и числовые колонки в состав не попадают."""
    c = pay.composition(INVOICE_TXT)
    assert 'JEDINICA' not in c and 'KOLI' not in c
    assert '5.850,00' not in c


def test_payment_matched_by_invoice_number_in_purpose():
    """Номер счёта в назначении приходит и как `183/2025`, и как `00183-2025`."""
    invoices = [dict(no='183/2025'), dict(no='116/2026')]
    payments = [dict(date='2025-08-13', amount=Decimal('5850.00'),
                     purpose='NAKNADA 00183-2025 GRAD', supplier=None),
                dict(date='2026-07-04', amount=Decimal('8775.00'),
                     purpose='placanje po fakturi 116/2026', supplier=None)]
    paid, orphans = pay.match(invoices, payments)
    assert set(paid) == {'183/2025', '116/2026'}
    assert paid['183/2025']['date'] == '2025-08-13'
    assert orphans == []


def test_invoice_without_payment_is_not_matched():
    """Счёт выставлен, платёж ещё не ушёл — нормально в начале месяца."""
    paid, orphans = pay.match([dict(no='200/2026')], [])
    assert paid == {}
    assert orphans == []


def test_payment_without_invoice_becomes_orphan():
    """Платёж есть, PDF под него нет — повод найти документ, а не молча пройти мимо."""
    payments = [dict(date='2026-08-05', amount=Decimal('8775.00'),
                     purpose='placanje po fakturi 140/2026', supplier=None)]
    paid, orphans = pay.match([dict(no='116/2026')], payments)
    assert paid == {}
    assert len(orphans) == 1


def test_one_payment_is_not_reused_for_two_invoices():
    """Две одинаковые суммы подряд — не повод привязать один платёж к обоим счетам."""
    invoices = [dict(no='97/2026'), dict(no='116/2026')]
    payments = [dict(date='2026-07-04', amount=Decimal('8775.00'),
                     purpose='fakture 97/2026 i 116/2026', supplier=None)]
    paid, orphans = pay.match(invoices, payments)
    assert len(paid) == 1
    assert orphans == []
