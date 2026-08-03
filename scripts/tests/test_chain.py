"""Цепочка остатков: закрытый пропуск izvod-а против настоящего разрыва.

Разница определяет, что делать: закрытый пропуск — данные полные, PDF просто не пришёл;
открытый разрыв — в CSV нет операций, и суммы отчёта занижены. Спутать их значит либо
зря дёргать владельца, либо молча потерять деньги из отчёта.
"""
from decimal import Decimal

import parse_izvod as pi


def izvod(date, no, prethodno, novo):
    """Строка цепочки: (дата, номер, prethodno stanje, novo stanje, файл)."""
    return (date, no, Decimal(prethodno), Decimal(novo), f'{date}.pdf')


def test_gap_closed_when_arithmetic_adds_up():
    """Реальный случай: izvod 48 не пришёл, но его операции добраны из выгрузки."""
    a = izvod('2025-08-12', 47, '308501.33', '302626.33')
    b = izvod('2025-08-15', 49, '296751.33', '216751.33')
    g = pi.classify_gap('rsd', a, b, Decimal('-5875.00'), ['2025-08-13'])
    assert g['closed'] is True
    assert g['delta'] == '-5875.00'
    assert g['days'] == ['2025-08-13']


def test_gap_open_when_operations_are_missing():
    """Ничего не добрано — сальдо не сходится, значит операций в CSV действительно нет."""
    a = izvod('2025-08-12', 47, '308501.33', '302626.33')
    b = izvod('2025-08-15', 49, '296751.33', '216751.33')
    g = pi.classify_gap('rsd', a, b, Decimal('0'), [])
    assert g['closed'] is False
    assert g['filled'] == '0'


def test_gap_open_when_filling_is_partial():
    """Добрано, но не всё: это опаснее пустого добора, потому что похоже на порядок."""
    a = izvod('2025-08-12', 47, '308501.33', '302626.33')
    b = izvod('2025-08-15', 49, '296751.33', '216751.33')
    g = pi.classify_gap('rsd', a, b, Decimal('-5000.00'), ['2025-08-13'])
    assert g['closed'] is False


def test_gap_open_when_csv_absent():
    """CSV ещё не собран — судить не о чем, считаем открытым и говорим вслух."""
    a = izvod('2025-08-12', 47, '308501.33', '302626.33')
    b = izvod('2025-08-15', 49, '296751.33', '216751.33')
    g = pi.classify_gap('rsd', a, b, None, [])
    assert g['closed'] is False
    assert g['filled'] is None


def test_ops_between_sums_signed(monkeypatch, rsd_csv):
    """Сальдо между izvod-ами: приход плюсом, расход минусом, границы не включаются."""
    root = rsd_csv([
        'ACC,2025-08-12,debit,1000.00,RSD,,,,граница не считается,supplier,x.pdf',
        'ACC,2025-08-13,debit,5850.00,RSD,,,,аренда,supplier,x.pdf',
        'ACC,2025-08-13,debit,25.00,RSD,,,,комиссия,bank_fee,x.pdf',
        'ACC,2025-08-14,credit,100.00,RSD,,,,приход,income,x.pdf',
        'ACC,2025-08-15,debit,80000.00,RSD,,,,граница не считается,cash,x.pdf',
    ])
    monkeypatch.setattr(pi, "DATA", root)
    total, days = pi.ops_between('rsd', '2025-08-12', '2025-08-15')
    assert total == Decimal('-5775.00')
    assert days == ['2025-08-13', '2025-08-14']
