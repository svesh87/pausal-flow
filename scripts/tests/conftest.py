"""Общие фикстуры: подставные реквизиты, мини-CSV, синтетический PDF.

Ничего настоящего в тестах нет — ни реквизитов, ни документов. Реквизиты подменяются
через `TASKS_PROFILE` (переменная окружения, которую понимает `reqs.py`), PDF собирается
кодом, CSV пишется в тесте. Причина не в чистоплюйстве: настоящие документы содержат
именно те данные, которые этот репозиторий выносит в `profile.json`, и тащить их
в фикстуры значит вернуть их обратно в переносимую часть.
"""
import os
import sys

import pytest

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(TESTS))
FIXTURE_PROFILE = os.path.join(TESTS, 'fixtures', 'profile.json')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))


def pytest_configure():
    """Подменить реквизиты ДО импорта тестовых модулей.

    Скрипты читают конфиг на уровне модуля (`RSD_ACC = reqs.get(...)`, `SUPPLIER_ACCS = …`),
    поэтому подмена внутри теста уже поздна: константы посчитаны при импорте. `pytest_configure`
    выполняется до сбора тестов — это единственное место, где подмена успевает.
    """
    os.environ['TASKS_PROFILE'] = FIXTURE_PROFILE


@pytest.fixture
def make_pdf(tmp_path):
    """Собрать минимальный PDF с заданными строками текста.

    Нужен там, где проверяется работа с настоящим файлом: разбор структуры, подпись,
    отказ перезаписать результат. Сам построитель живёт в `pdfobj.build_simple` — он же
    собирает синтетические примеры для клона без данных, и держать две копии одного
    генератора не за чем.
    """
    import pdfobj

    def build(name='doc.pdf', lines=(('Hello', 72, 700),)):
        path = tmp_path / name
        pdfobj.build_simple(str(path), lines)
        return str(path)
    return build


@pytest.fixture
def rsd_csv(tmp_path):
    """Записать динарский CSV операций и вернуть путь к папке finance/bank."""
    header = ('account,date,direction,amount,currency,counterparty,counterparty_account,'
              'code,purpose,category,source_file')

    def build(rows, eur_rows=()):
        bank = tmp_path / 'finance' / 'bank'
        bank.mkdir(parents=True, exist_ok=True)
        (bank / 'transactions_rsd.csv').write_text('\n'.join([header, *rows]) + '\n')
        (bank / 'transactions_eur.csv').write_text('\n'.join([header, *eur_rows]) + '\n')
        return str(tmp_path)
    return build
