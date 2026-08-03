"""Реестр кейсов и тесты обязаны совпадать.

Это и есть гейт покрытия: вместо процента строк — список бизнес-кейсов, где каждая строка
имеет живой тест, а каждый тест описан кейсом. Без такой проверки список кейсов через месяц
станет украшением: тесты уедут вперёд, а строки останутся про то, чего уже нет.
"""
import os
import re

CASES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'CASES.md')
TESTS_DIR = os.path.dirname(CASES)


def listed_cases():
    """{(файл, тест): описание кейса} из таблицы CASES.md."""
    out = {}
    for line in open(CASES):
        m = re.match(r'\|\s*(.+?)\s*\|\s*`(test_[\w.]+\.py)::(test_\w+)`\s*\|', line)
        if m:
            out[(m.group(2), m.group(3))] = m.group(1)
    return out


def existing_tests():
    """{(файл, тест)} — все тесты набора, кроме самого этого файла."""
    out = set()
    for name in sorted(os.listdir(TESTS_DIR)):
        if not (name.startswith('test_') and name.endswith('.py')):
            continue
        if name == os.path.basename(__file__):
            continue
        for m in re.finditer(r'^def (test_\w+)\(', open(os.path.join(TESTS_DIR, name)).read(),
                             re.M):
            out.add((name, m.group(1)))
    return out


def test_registry_is_not_empty():
    assert len(listed_cases()) > 10, 'CASES.md разобрался пустым — формат таблицы изменился?'


def test_every_case_has_a_living_test():
    missing = sorted(set(listed_cases()) - existing_tests())
    assert not missing, ('в CASES.md есть кейсы без тестов: '
                         + ', '.join(f'{f}::{t}' for f, t in missing))


def test_every_test_is_described_by_a_case():
    extra = sorted(existing_tests() - set(listed_cases()))
    assert not extra, ('тесты есть, а кейсов под них нет — дописать в CASES.md: '
                       + ', '.join(f'{f}::{t}' for f, t in extra))
