"""Разбор чисел, дат и номеров — то, на чём стоят все суммы в отчётности.

Форматы разные и легко путаются: банк пишет `1,234.56`, паушал — `1.234,56`, книга КПО
печатает `1.158.221,61`. Ошибка в разделителях меняет сумму в тысячу раз и при этом
выглядит как правдоподобное число.
"""
from decimal import Decimal

import kpo_book
import parse_izvod as pi
import pausal_import as pau
import payments_registry as pay
import tax_registry as tax


def test_bank_amount_format():
    """Банк: запятая — разделитель тысяч, точка — дробная часть."""
    assert pi.amount('1,234.56') == Decimal('1234.56')
    assert pi.amount('302,626.33') == Decimal('302626.33')


def test_supplier_invoice_amount_format():
    """Счета поставщика: наоборот — точка тысячная, запятая дробная."""
    assert pay.money('8.775,00') == Decimal('8775.00')
    assert pay.money('45.000,00') == Decimal('45000.00')


def test_iso_date_from_serbian():
    assert pi.iso('13.08.2025') == '2025-08-13'
    assert pau.ru_date('01.07.2026') == '2026-07-01'


def test_kpo_money_and_rate_formats():
    """В книге КПО суммы печатаются по сербскому образцу, курс — с четырьмя знаками."""
    assert kpo_book.fmt(Decimal('1158221.61')) == '1.158.221,61'
    assert kpo_book.fmt_rate(Decimal('117.385')) == '117,3850'


def test_report_money_format_uses_spaces():
    """В реестре налогов разряды разделены пробелом — иначе таблица не читается."""
    assert tax.fmt(Decimal('873412.06')) == '873 412.06'


def test_invoice_number_split_for_bookkeeping():
    """Сервис принимает только цифры; буквы допустимы лишь в префиксе."""
    assert pau.split_number('26-8-LC') == ('26-', '8')
    assert pau.split_number('17') == ('', '17')


def test_tax_kind_by_payment_account():
    """Вид налога определяется уплатным счётом 840-…, а не текстом назначения."""
    assert tax.kind_of('', '840-0000711122843-32') == 'porez'
    assert tax.kind_of('', '840-0000721313843-74') == 'pio'


def test_tax_account_without_leading_zeros():
    """В выписке счёт приходит и в 14 цифр — без ведущих нулей после кода 840."""
    assert tax.kind_of('', '840-711122843-32') == 'porez'


def test_tax_kind_falls_back_to_purpose():
    """Счёт неизвестен — разбираем по назначению; совсем непонятное остаётся нераспознанным."""
    assert tax.kind_of('UPLATA PIO DOPRINOS', '') == 'pio'
    assert tax.kind_of('RANDOM TRANSFER', '') is None


def test_tax_purpose_heuristic_is_greedy():
    """Разбор по словам ловит лишнее: «NESTO» попадает под шаблон взноса за незанятость.

    Поэтому счёт — главный признак, а назначение только резерв. Тест закрепляет
    известную нестрогость, чтобы её не приняли за поломку.
    """
    assert tax.kind_of('NESTO DRUGO', '') == 'nes'
