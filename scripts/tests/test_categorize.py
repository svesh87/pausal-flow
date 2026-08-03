"""Категоризация операций: по одному тесту на каждую категорию.

Ошибка здесь тихая: операция уезжает не в ту колонку отчёта или вовсе выпадает из него
(`fx`, `founding` и `other` в отчёт не идут), а суммы при этом выглядят правдоподобно.
"""
from decimal import Decimal

import parse_izvod as pi


def op(direction='debit', currency='RSD', amount='100.00', purpose='', counterparty='',
       account='', blob=''):
    return dict(direction=direction, currency=currency, amount=Decimal(amount),
                purpose=purpose, counterparty=counterparty, counterparty_account=account,
                _blob=blob)


def test_supplier_by_account():
    """Счёт поставщика из profile.json — точное совпадение по цифрам, без дефисов."""
    assert pi.categorize(op(account='222222222222222222')) == 'supplier'
    assert pi.categorize(op(account='333-3333333333333-33')) == 'supplier'


def test_supplier_by_name_when_account_unknown():
    """У поставщика мог поменяться счёт, но имя в назначении остаётся."""
    assert pi.categorize(op(purpose='PLACANJE LANDLORD PR FIRM')) == 'supplier'
    assert pi.categorize(op(purpose='NAKNADA ZA LANDLORD OLD NAME')) == 'supplier'


def test_tax_by_public_revenue_account():
    assert pi.categorize(op(account='840-1234567890123-45')) == 'tax'


def test_tax_via_euprava_portal():
    """Оплата налога картой на портале: счёт получателя ничего не говорит."""
    assert pi.categorize(op(purpose='CHIP CARD_EUPRAVA NAPLATA')) == 'tax'


def test_personal_transfer():
    assert pi.categorize(op(account='111000100011111111')) == 'personal'


def test_founding_deposit_is_not_income():
    """Взнос при открытии счёта: не выручка и не `other` — отдельная категория."""
    assert pi.categorize(op(direction='credit', purpose='PRILIV PO REF 03800-007100002')) \
        == 'founding'


def test_income_rsd_is_currency_conversion():
    assert pi.categorize(op(direction='credit', purpose='PROTIVVREDNOST PRODATE VALUTE')) \
        == 'income'


def test_income_and_fx_on_eur_account():
    assert pi.categorize(op(direction='credit', currency='EUR',
                            purpose='NALOG ZA NAPLATU')) == 'income'
    assert pi.categorize(op(direction='debit', currency='EUR',
                            purpose='OTKUP DEVIZA')) == 'fx'


def test_cash_and_card_are_told_apart():
    """Обе операции идут по счёту карточного центра — различает только текст."""
    atm = op(purpose='KARTICA 1234 : ATM BEOGRAD')
    shop = op(purpose='KARTICA 1234 : IKEA BEOGRAD')
    assert pi.categorize(atm) == 'cash'
    assert pi.categorize(shop) == 'card'
    assert pi.categorize(op(purpose='KARTICA 1234 : MULTICARD KIOSK')) == 'cash'


def test_bank_fee():
    assert pi.categorize(op(purpose='OBRACUN PROVIZIJE ZA DAN')) == 'bank_fee'
    assert pi.categorize(op(purpose='NAPLATA KARTICE')) == 'bank_fee'


def test_unknown_stays_other():
    """`other` — сигнал «категоризатор этого не знает», поэтому он должен оставаться пустым."""
    assert pi.categorize(op(purpose='NESTO SASVIM NOVO')) == 'other'
