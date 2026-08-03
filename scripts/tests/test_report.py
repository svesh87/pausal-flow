"""Сводный отчёт: что попадает в колонки и как считается разбивка по поставщикам.

Проверяется на двух поставщиках, хотя сейчас в работе один: разбивка обязана быть готовой
к появлению второго, иначе её придётся переделывать в момент, когда счёт уже пришёл.
"""
from decimal import Decimal

import build_report


def test_categories_that_must_not_reach_the_report(monkeypatch, tmp_path, rsd_csv):
    """`fx`, `founding` и `other` в отчёт не идут: первое — зеркало продажи валюты,
    второе — взнос при открытии счёта, третье — то, чего категоризатор не знает."""
    root = rsd_csv([
        'ACC,2026-07-01,credit,2000.00,RSD,,,,взнос при открытии,founding,x.pdf',
        'ACC,2026-07-02,debit,999.00,RSD,,,,непонятное,other,x.pdf',
        'ACC,2026-07-03,debit,8775.00,RSD,,222-2222222222222-22,,аренда,supplier,x.pdf',
    ], eur_rows=['EUR,2026-07-01,debit,4100.00,EUR,,,,продажа валюты,fx,y.pdf'])
    monkeypatch.setattr(build_report, "DATA", root)
    data, by_supplier = build_report.load()
    assert data[(2026, 7)]['supplier'] == 8775
    assert 'founding' not in data[(2026, 7)]
    assert 'other' not in data[(2026, 7)]
    assert 'fx' not in data[(2026, 7)]


def test_supplier_breakdown_splits_by_account(monkeypatch, tmp_path, rsd_csv):
    """Поставщик определяется по его счёту из profile.json, а не по тексту назначения."""
    root = rsd_csv([
        'ACC,2026-06-02,debit,5850.00,RSD,,222-2222222222222-22,,аренда,supplier,x.pdf',
        'ACC,2026-07-04,debit,8775.00,RSD,,222-2222222222222-22,,аренда,supplier,x.pdf',
        'ACC,2026-07-10,debit,1200.00,RSD,,333-3333333333333-33,,хостинг,supplier,x.pdf',
    ])
    monkeypatch.setattr(build_report, "DATA", root)
    data, by_supplier = build_report.load()
    assert by_supplier[('Landlord PR Firm', 2026)] == 14625
    assert by_supplier[('Hoster Doo', 2026)] == 1200
    assert data[(2026, 7)]['supplier'] == 9975


def test_unknown_supplier_falls_back_to_counterparty(monkeypatch, tmp_path, rsd_csv):
    """Счёт не из конфига — платёж не теряется, показывается под именем из выписки."""
    root = rsd_csv([
        'ACC,2026-07-05,debit,700.00,RSD,Nekakav Doo,999-9999999999999-99,,услуга,supplier,x.pdf',
    ])
    monkeypatch.setattr(build_report, "DATA", root)
    _, by_supplier = build_report.load()
    assert by_supplier[('Nekakav Doo', 2026)] == 700


def test_income_split_by_currency(monkeypatch, tmp_path, rsd_csv):
    """Приход EUR и его динарская противостоимость — разные колонки одной категории."""
    root = rsd_csv(
        ['ACC,2026-08-03,credit,481283.83,RSD,,,,противостоимость,income,x.pdf'],
        eur_rows=['EUR,2026-08-03,credit,4100.00,EUR,,,,naplata,income,y.pdf'])
    monkeypatch.setattr(build_report, "DATA", root)
    data, _ = build_report.load()
    assert data[(2026, 8)]['income_eur'] == 4100
    assert data[(2026, 8)]['income_rsd'] == Decimal('481283.83')
