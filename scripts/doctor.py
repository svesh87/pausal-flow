#!/usr/bin/env python3
"""Готовность окружения одним взглядом: что есть, чего нет, чем починить.

    python3 scripts/doctor.py

Печатает чек-лист и завершается ненулевым кодом, если чего-то обязательного не хватает.
Смысл — чтобы настройка с нуля не превращалась в перебор ошибок по одной: сначала видно
весь список, потом чинится.

Проверяется четыре слоя:

**Инструменты.** `pdftotext` и `pdftoppm` (poppler-utils) — разбор и рендер PDF, `soffice`
(LibreOffice) — сборка книг КПО, `gpg` — образцы подписи, `git` — история правок.

**Окружение разработки.** Виртуальное окружение с линтером и тестами: без него гейты
не прогнать.

**Данные.** Найден ли корень данных, есть ли в нём `profile.json`, заполнены ли обязательные
поля, на месте ли контейнер образцов подписи, включён ли пре-коммит.

**Интеграции.** Какие роли закрыты в конфиге. Живость MCP-серверов отсюда не проверяется:
они поднимаются агентом в сессии, а не этим скриптом, — здесь видно только то, что заявлено
в конфиге.

Всё, чего не хватает, печатается вместе с командой или ссылкой на документ, где написано,
как это получить.
"""
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reqs  # noqa: E402

EXAMPLE = os.path.join(reqs.CODE_ROOT, 'profile.example.json')

# (команда, зачем, чем ставится) — пакеты называются как в Debian/Ubuntu, в остальных
# дистрибутивах имена свои, но по названию бинарника они находятся.
TOOLS = [
    ('pdftotext', 'чтение текста из PDF: разбор выписок, сверка подписанного', 'poppler-utils'),
    ('pdftoppm', 'рендер PDF в картинку: проверка подписанного и поиск линий', 'poppler-utils'),
    ('pdfinfo', 'даты и число страниц PDF: проверка подписанного', 'poppler-utils'),
    ('soffice', 'сборка книг КПО из HTML в PDF', 'libreoffice-calc / libreoffice'),
    ('gpg', 'шифрование контейнера образцов подписи', 'gnupg'),
    ('git', 'история правок данных и движка', 'git'),
]
# Поля, без которых не работает ни один шаг: остальные проверяются по месту, когда
# понадобятся, и падают с понятным текстом.
REQUIRED_FIELDS = ['entrepreneur.name', 'entrepreneur.firm', 'entrepreneur.pib',
                   'accounts.rsd', 'accounts.eur', 'accounts.eur_iban',
                   'mail.mailbox', 'client.name', 'client.chat_languages',
                   'client.act_languages', 'gpg.recipient']
ROLES = ['tasks', 'chat', 'mail', 'accounting']

OK, WARN, BAD = 'есть', 'внимание', 'НЕТ'


class Report:
    """Чек-лист: печатает строку сразу и считает, чего не хватает.

    Печать на месте, а не в конце: строки идут под своими заголовками разделов, и по ходу
    видно, на чём проверка встала, если что-то из инструментов подвиснет.
    """

    WIDTH = 26                              # ширина колонки с названием проверки

    def __init__(self):
        self.bad = 0

    def add(self, status, what, note=''):
        if status == BAD:
            self.bad += 1
        line = f'  [{status:^8}] {what:<{self.WIDTH}}'
        print(f'{line}  {note}' if note else line)


def placeholders():
    """Значения-заглушки — прямо из `profile.example.json`, а не списком в коде.

    Список в коде разошёлся бы с образцом при первой же правке образца, и доктор перестал
    бы замечать незаполненные поля. Заодно эти строки не выглядят реквизитами для проверки
    чистоты движка: они и есть образец.
    """
    out = set()

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and node:
            out.add(node)

    with open(EXAMPLE) as f:
        walk(json.load(f))
    return out


def check_tools(r):
    print('Инструменты')
    for cmd, why, pkg in TOOLS:
        if shutil.which(cmd):
            r.add(OK, cmd, why)
        else:
            r.add(BAD, cmd, f'{why} — поставить пакет {pkg}')


def check_venv(r):
    print('\nОкружение разработки')
    venv = os.path.join(reqs.CODE_ROOT, '.venv', 'bin')
    for tool in ('ruff', 'pytest'):
        p = os.path.join(venv, tool)
        if os.path.exists(p):
            r.add(OK, f'.venv/bin/{tool}', 'гейты можно гонять')
        else:
            r.add(WARN, f'.venv/bin/{tool}',
                  'нужен только для правок движка: python3 -m venv .venv && '
                  '.venv/bin/pip install -r requirements-dev.txt')


def check_data(r):
    print('\nДанные')
    root = reqs.data_root()
    if not reqs.has_data():
        r.add(BAD, 'корень данных',
              f'profile.json не найден (искал в {root}) — как завести: docs/setup.md')
        return None
    r.add(OK, 'корень данных', root)
    example = placeholders()
    filled, unfilled, missing = 0, [], []
    for field in REQUIRED_FIELDS:
        value = reqs.get(field, None)
        if not value:
            missing.append(field)
        elif str(value) in example:
            unfilled.append(field)
        else:
            filled += 1
    r.add(OK if not missing else BAD, 'обязательные поля конфига',
          f'заполнено {filled} из {len(REQUIRED_FIELDS)}'
          + (f', пусто: {", ".join(missing)}' if missing else ''))
    if unfilled:
        r.add(WARN, 'заглушки из образца', ', '.join(unfilled))

    container = os.path.join(root, 'signatures', 'samples.tar.gz.gpg')
    if os.path.exists(container):
        r.add(OK, 'контейнер образцов подписи', 'signatures/samples.tar.gz.gpg')
    else:
        r.add(WARN, 'контейнер образцов подписи',
              'без него не подписать документы — как собрать: docs/signatures.md')

    for name in ('months', 'finance'):
        p = os.path.join(root, name)
        r.add(OK if os.path.isdir(p) else WARN, f'{name}/',
              'на месте' if os.path.isdir(p) else 'появится на первом же месяце')
    return root


def check_hooks(r, root):
    print('\nПре-коммит')
    for label, path in (('движок', reqs.CODE_ROOT), ('данные', root)):
        if not path or not os.path.isdir(os.path.join(path, '.git')):
            r.add(WARN, f'хук в репозитории ({label})', 'это не корень репозитория git')
            continue
        out = subprocess.run(['git', 'config', 'core.hooksPath'],
                             capture_output=True, cwd=path)
        value = out.stdout.decode().strip()
        if value:
            r.add(OK, f'хук в репозитории ({label})', f'core.hooksPath = {value}')
        else:
            r.add(WARN, f'хук в репозитории ({label})',
                  'не включён: scripts/install_hooks.sh')


def check_integrations(r, root):
    print('\nИнтеграции (по конфигу; живость серверов проверяется в сессии через /mcp)')
    if not root:
        r.add(WARN, 'роли интеграций', 'нет конфига — проверять нечего')
        return
    for role in ROLES:
        node = reqs.get(f'integrations.{role}', None)
        if isinstance(node, dict) and node.get('kind'):
            note = node['kind'] + (f' (MCP {node["mcp"]})' if node.get('mcp') else '')
            r.add(OK, f'роль {role}', note)
        else:
            r.add(BAD, f'роль {role}', 'не заполнена — docs/integrations.md')


def main():
    r = Report()
    check_tools(r)
    check_venv(r)
    root = check_data(r)
    check_hooks(r, root)
    check_integrations(r, root)
    print()
    if r.bad:
        print(f'не хватает обязательного: {r.bad} — начать с docs/setup.md')
        return 1
    print('всё обязательное на месте')
    return 0


if __name__ == '__main__':
    sys.exit(main())
