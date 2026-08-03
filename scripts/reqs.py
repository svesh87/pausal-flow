#!/usr/bin/env python3
"""Реквизиты из `profile.json` и два корня: код и данные.

Модуль назван `reqs`, а не `profile`: последнее — имя модуля стандартной библиотеки,
и файл рядом со скриптами затенял бы его.

Скрипты и навыки не содержат ни номеров счетов, ни ПИБ, ни имён контрагентов:
всё берётся отсюда, поэтому движок (`.claude/skills/`, `CLAUDE.md`, `README.md`,
`docs/`, `scripts/`) живёт отдельным публичным репозиторием, а данные — своим
приватным.

    from reqs import get, data_root, integration, supplier_by_account
    get('accounts.rsd')                 # значение или падение с понятным текстом
    get('limits.pausal_rsd_year', 0)    # значение по умолчанию, если поля нет
    integration('chat')                 # чем закрыта роль «канал с клиентом»
    data_root()                         # где лежат months/, finance/, signatures/

## Два корня

`CODE_ROOT` — где лежит сам движок: шаблоны `scripts/templates/`, фикстуры тестов,
`profile.example.json`. Считается от файла и никогда не зависит от того, откуда
запущен скрипт.

`data_root()` — где лежат данные: `profile.json`, `months/`, `finance/`, `signatures/`.
Ищется по порядку:

1. переменная окружения `TASKS_DATA` — явно, для прогонов на копиях;
2. каталог файла из `TASKS_PROFILE`, если она задана — так работают тесты;
3. ближайший вверх от текущего каталога каталог с `profile.json` — обычный режим,
   когда движок подключён к приватному репозиторию симлинком;
4. путь из файла `.tasks_data` в корне движка, если он есть — так гейты и пре-коммит,
   которые гоняются **из каталога движка**, всё же видят настоящие реквизиты владельца
   и проверяют, не утекли ли они. Файл локальный и в git не идёт: у каждого свой;
5. `CODE_ROOT` — одно-деревной режим: движок сам по себе, без данных. На нём стоят
   тесты, синтетические примеры и первое знакомство нового владельца.

Пункт 5 означает, что скрипты не падают в клоне без данных: они честно сообщают,
что данных нет, и предлагают завести их по образцу.
"""
import json
import os

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(CODE_ROOT, 'profile.example.json')
# Локальная подсказка «где мои данные» для запусков из каталога движка. Одна строка с путём,
# в git не идёт: движок публичный, а путь у каждого свой.
DATA_HINT = os.path.join(CODE_ROOT, '.tasks_data')
_cache = {}
# Корни кэшируются по ключу «окружение + текущий каталог»: поиск вверх делают почти все
# скрипты, а подмена переменных в тестах обязана срабатывать сразу, без сброса кэша.
_roots = {}


def data_root():
    """Корень данных: где `profile.json`, `months/`, `finance/`, `signatures/`."""
    env_data = os.environ.get('TASKS_DATA')
    env_profile = os.environ.get('TASKS_PROFILE')
    key = (env_data, env_profile, os.getcwd())
    if key not in _roots:
        _roots[key] = _find_data_root(env_data, env_profile)
    return _roots[key]


def _find_data_root(env_data, env_profile):
    if env_data:
        return os.path.abspath(env_data)
    if env_profile:
        return os.path.dirname(os.path.abspath(env_profile))
    d = os.path.abspath(os.getcwd())
    while True:
        if os.path.exists(os.path.join(d, 'profile.json')):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    if os.path.exists(DATA_HINT):
        hint = open(DATA_HINT).read().strip()
        # Путь может быть относительным — тогда он считается от корня движка: так подсказка
        # переживает переезд обоих каталогов, если они лежат рядом.
        if hint:
            hint = os.path.abspath(os.path.join(CODE_ROOT, os.path.expanduser(hint)))
            if os.path.exists(os.path.join(hint, 'profile.json')):
                return hint
    return CODE_ROOT


def data_path(*parts):
    """Путь внутри данных: `data_path('finance', 'report.md')`."""
    return os.path.join(data_root(), *parts)


def script_ref(name):
    """Как назвать скрипт движка в файле, который лежит в данных.

    Генерируемые реестры и отчёт пишут в шапке, чем они собраны. Читать их будут из корня
    данных, а движок там подключён симлинком `engine`, поэтому путь `scripts/…` был бы
    неверным. Если симлинка нет (одно-деревной режим, чужой клон) — обычный `scripts/…`.
    """
    if os.path.isdir(os.path.join(data_root(), 'engine', 'scripts')):
        return f'engine/scripts/{name}'
    return f'scripts/{name}'


def rel(p):
    """Путь для сообщений — от корня данных, если он внутри, иначе как есть."""
    p = os.path.abspath(p)
    root = data_root()
    return os.path.relpath(p, root) if p.startswith(root + os.sep) else p


def has_data():
    """Есть ли вообще данные: заполненный `profile.json` в корне данных."""
    return os.path.exists(os.path.join(data_root(), 'profile.json'))


def path():
    """Путь к файлу реквизитов."""
    return os.environ.get('TASKS_PROFILE') or os.path.join(data_root(), 'profile.json')


def load():
    """Разобранный profile.json. Кэшируется по пути, чтобы тесты могли подменять."""
    p = path()
    if p not in _cache:
        assert os.path.exists(p), (
            f'нет файла реквизитов {p} — скопировать {os.path.relpath(EXAMPLE, CODE_ROOT)} '
            'в profile.json рядом с данными и заполнить своими данными; каталог данных '
            'задаётся переменной TASKS_DATA или находится поиском вверх от текущего')
        try:
            _cache[p] = json.load(open(p))
        except json.JSONDecodeError as e:
            raise AssertionError(f'{p}: не разбирается как JSON — {e}') from e
    return _cache[p]


_MISSING = object()


def get(dotted, default=_MISSING):
    """Значение по пути вида `client.slack_channel`."""
    node = load()
    for part in dotted.split('.'):
        if isinstance(node, dict) and part in node:
            node = node[part]
            continue
        if default is not _MISSING:
            return default
        raise AssertionError(
            f'в {rel(path())} нет поля «{dotted}» — заполнить по образцу '
            f'{os.path.relpath(EXAMPLE, CODE_ROOT)}')
    return node


def integration(role):
    """Чем закрыта роль интеграции: `tasks`, `chat`, `mail`, `accounting`.

    Возвращает словарь с `kind` (какой инструмент) и, где есть, `mcp` (имя сервера).
    Навыки читают роль отсюда, а не помнят наизусть: у другого владельца на месте
    YouTrack может стоять Jira, а на месте Slack — Telegram.
    """
    node = get(f'integrations.{role}', None)
    assert isinstance(node, dict) and node.get('kind'), (
        f'в {rel(path())} не заполнена роль «integrations.{role}» — чем она закрыта, '
        f'смотреть в docs/integrations.md, образец в {os.path.relpath(EXAMPLE, CODE_ROOT)}')
    return node


def suppliers():
    return get('suppliers')


def supplier_by_account(account):
    """Поставщик по номеру его жиро-счёта; None, если такого нет."""
    digits = ''.join(c for c in str(account) if c.isdigit())
    for s in suppliers():
        if digits and digits == ''.join(c for c in s.get('account', '') if c.isdigit()):
            return s
    return None


if __name__ == '__main__':
    # Корни нужны и оболочке: `rebuild_all.sh` и `check_clean.sh` спрашивают их здесь,
    # чтобы правило поиска жило в одном месте, а не повторялось на bash.
    import sys
    args = sys.argv[1:]
    if args == ['--data-root']:
        print(data_root())
    elif args == ['--code-root']:
        print(CODE_ROOT)
    elif args == ['--has-data']:
        sys.exit(0 if has_data() else 1)
    else:
        print(__doc__.strip())
        sys.exit(2)
