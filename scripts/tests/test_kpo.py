"""Книга КПО: что попадает в запись, по какому курсу и что не переписывается.

Здесь ошибка стоит дороже всего: книга — сданный документ, а расхождение с паушалом
означает расхождение с налоговой. Плюс отдельный случай — закрытый год: его нельзя
пересобирать молча, даже если данные поехали.
"""
import os
from decimal import Decimal

import kpo_book
import pytest

MONTH_FILE = """# Задачи за месяц

## Реестр

- Инвойс: № 26-8-LC от 03.08.2026 (`invoice23.pdf`)
- Оплата: 03.08.2026, EUR 4,100.00 (`Obavestenje o prilivu 0743-0183436-MS.pdf`)
- Акт: `acceptance_23.pdf` → `acceptance_23_signed.pdf`
"""

UNPAID_MONTH = """# Задачи за месяц

## Реестр

- Инвойс: № 26-9-LC от 02.09.2026 (`invoice24.pdf`)
- Оплата: —
- Акт: —
"""


def _month(root, name, text):
    d = os.path.join(root, 'months', name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, 'tasks.md'), 'w').write(text)


def test_record_taken_from_invoice_not_payment(monkeypatch, tmp_path):
    """Дата записи — дата счёта: деньги могли прийти в другой день и даже в другом году."""
    _month(str(tmp_path), '2026_07', MONTH_FILE)
    monkeypatch.setattr(kpo_book, "DATA", str(tmp_path))
    rows = kpo_book.read_months()
    assert len(rows) == 1
    assert rows[0]['date'] == '2026-08-03'
    assert rows[0]['no'] == '26-8-LC'
    assert rows[0]['eur'] == Decimal('4100.00')
    assert rows[0]['month'] == '2026_07'


def test_month_without_payment_is_skipped(monkeypatch, tmp_path):
    """Счёт выставлен, оплаты нет — в книгу пока не идёт."""
    _month(str(tmp_path), '2026_08', UNPAID_MONTH)
    monkeypatch.setattr(kpo_book, "DATA", str(tmp_path))
    assert kpo_book.read_months() == []


def test_rate_comes_from_cache_in_offline_mode():
    cache = {'2026-08-03': 117.3863}
    assert kpo_book.nbs_rate('2026-08-03', cache, offline=True) == Decimal('117.3863')


def test_offline_mode_refuses_to_guess_missing_rate():
    """Без курса запись нельзя посчитать — падаем, а не берём соседний день."""
    with pytest.raises(AssertionError) as e:
        kpo_book.nbs_rate('2026-08-04', {}, offline=True)
    assert '--offline' in str(e.value)


def test_rsd_amount_rounds_half_up():
    """Пересчёт в динары — до копейки, округление half-up, как в паушале."""
    rate = Decimal('117.3863')
    rsd = (Decimal('4100.00') * rate).quantize(Decimal('0.01'), rounding='ROUND_HALF_UP')
    assert rsd == Decimal('481283.83')


def test_book_not_rewritten_when_content_matches(monkeypatch, tmp_path):
    """Сверка идёт по тексту, а не по байтам: LibreOffice пишет в PDF дату сборки."""
    path = tmp_path / 'kpo_2026.pdf'
    path.write_bytes(b'%PDF-1.4 stub')
    monkeypatch.setattr(kpo_book, 'pdf_tokens', lambda p: ['a', 'b'])
    monkeypatch.setattr(kpo_book, 'html_tokens', lambda h: ['a', 'b'])
    assert kpo_book.same_as_on_disk('<html/>', str(path)) is True
    monkeypatch.setattr(kpo_book, 'html_tokens', lambda h: ['a', 'b', 'c'])
    assert kpo_book.same_as_on_disk('<html/>', str(path)) is False


def test_missing_book_is_never_considered_same(tmp_path):
    assert kpo_book.same_as_on_disk('<html/>', str(tmp_path / 'нет.pdf')) is False


def test_write_if_changed_leaves_identical_file_alone(tmp_path):
    """Иначе в коммит попадали бы файлы, в которых ничего не поменялось."""
    p = tmp_path / 'entries.md'
    p.write_text('одно и то же')
    assert kpo_book.write_if_changed(str(p), 'одно и то же') is False
    assert kpo_book.write_if_changed(str(p), 'другое') is True
    assert p.read_text() == 'другое'
