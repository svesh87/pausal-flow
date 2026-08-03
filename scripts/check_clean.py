#!/usr/bin/env python3
"""Движок не содержит ничего личного. Три независимые проверки.

    python3 scripts/check_clean.py

Движок — публичный репозиторий: навыки, скрипты, документация. Данные — приватный.
Утечка выглядит одинаково безобидно в обе стороны: номер счёта в примере, имя клиента
в комментарии, ключ проекта в правилах формулировок. Поэтому проверок три, и они
перекрывают друг друга:

**По форме.** Работает всегда, даже в чистом клоне без `profile.json`: жиро-счёт,
IBAN, длинный числовой прогон, `imap://`, телефон, домашний путь, почтовый адрес вне
`example.com`. Это единственная проверка, которая ловит утечку у человека, который
только что склонировал движок и ещё ничего не настроил.

**По значениям.** Включается, когда `profile.json` доступен: каждое его значение длиннее
пяти символов ищется в движке. Не устаревает — сменился счёт, ищется новый. Плюс ключи
проектов трекера ищутся как префиксы номеров задач (`KEY-1234`), потому что сами по себе
они короткие и проверку по длине не прошли бы.

**По стоп-листу.** Имена собственные — клиент, поставщики, коллеги — лежат в приватном
файле `.check_clean.local` в корне данных, по строке на слово. Файлу, который перечисляет
искомые имена, в публичном репозитории места нет, поэтому его отсутствие — не ошибка,
а обычное состояние чужого клона.

Осознанные исключения — `scripts/check_clean.allow`: счета публичных приходов, служебные
счета банка, выдуманные ключи задач из примеров. Каждая строка с объяснением, почему
она безопасна.
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reqs  # noqa: E402

# Обычно проверяется тот движок, внутри которого лежит сам скрипт. Переменная `TASKS_ENGINE`
# позволяет указать другое дерево — на этом стоят тесты самой проверки: им нужен движок
# с намеренной утечкой, а портить настоящий ради теста нельзя.
ROOT = os.environ.get('TASKS_ENGINE') or reqs.CODE_ROOT
ALLOW_FILE = os.path.join(ROOT, 'scripts', 'check_clean.allow')
STOPLIST_NAME = '.check_clean.local'

# Что проверяем: весь движок. Список именно перечислением, а не «всё дерево»: рядом
# в одном каталоге может лежать приватный репозиторий данных (когда движок подключён
# симлинком), и обход всего подряд принял бы данные за движок.
TARGETS = ['.claude', 'CLAUDE.md', 'README.md', 'docs', 'scripts', 'examples',
           'profile.example.json', 'LICENSE']
# Исключения по путям: сам чекер называет искомые шаблоны в тексте; фикстуры тестов
# набиты заглушками той же формы; `docs/history.md` — личная история владельца, она
# живёт в приватном репозитории и в публичный не копируется.
EXCLUDE = ('scripts/check_clean.py', 'scripts/check_clean.sh', 'scripts/check_clean.allow',
           'scripts/tests/fixtures/', 'docs/history.md')
# Проверка по форме здесь бессмысленна: и образец конфига, и тесты состоят из заглушек
# ровно этой формы — счёт из нулей, IBAN из нулей, выдуманные жиро-счёта поставщиков.
# Проверка по значениям и стоп-листу на них при этом остаётся: настоящий счёт, попавший
# в тест, — такая же утечка, как в навыке.
FORMS_SKIP = ('profile.example.json', 'scripts/tests/')
# Образец конфига — заглушки по построению: совпадение его строки с настоящим значением
# означает, что владелец поля ещё не заполнил, а не что реквизит утёк. Стоп-лист имён
# его всё равно проверяет.
# `LICENSE` — единственное место, где имя автора в публичном репозитории уместно: это
# строка авторства, а не утёкший реквизит. Захочет владелец её подписать — гейт не упадёт.
VALUES_SKIP = ('profile.example.json', 'LICENSE')
SKIP_EXT = ('.pdf', '.xlsx', '.png', '.gpg', '.ppm', '.pgm', '.pyc', '.tar', '.gz')
# Поля конфига, которые обязаны быть видны в движке: это названия продуктов и сервисов,
# а не реквизиты владельца. Без них инструкции ломаются — «выгрузи в …» непонятно куда.
SKIP_VALUES = (('mail', 'supplier_invoices_from'),
               ('integrations', 'tasks', 'kind'), ('integrations', 'tasks', 'mcp'),
               ('integrations', 'chat', 'kind'), ('integrations', 'chat', 'mcp'),
               ('integrations', 'mail', 'kind'), ('integrations', 'mail', 'mcp'),
               ('integrations', 'accounting', 'kind'))

FORMS = [
    ('жиро-счёт', re.compile(r'\b\d{3}-\d{13}-\d{2}\b')),
    ('IBAN', re.compile(r'\bRS\d{18,20}\b')),
    ('длинный номер (счёт, ПИБ, идентификатор)', re.compile(r'\b\d{13,}\b')),
    ('IMAP-путь к ящику', re.compile(r'imap://\S+')),
    ('телефон', re.compile(r'\+\d{9,}')),
    ('домашний путь', re.compile(r'/home/\S*')),
    ('почтовый адрес', re.compile(r'\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b')),
]
# Домены, на которых почтовые адреса безопасны по определению: RFC 2606 оставляет
# example.* специально под примеры.
SAFE_MAIL = ('@example.com', '@example.org', '@example.net')


def allowed():
    """Осознанные исключения из проверки по форме."""
    if not os.path.exists(ALLOW_FILE):
        return set()
    out = set()
    for line in open(ALLOW_FILE):
        line = line.split('#')[0].strip()
        if line:
            out.add(line)
    return out


def stoplist():
    """Имена собственные из приватного файла в корне данных; пусто — это нормально."""
    p = os.path.join(reqs.data_root(), STOPLIST_NAME)
    if not os.path.exists(p):
        return []
    return [ln.split('#')[0].strip() for ln in open(p) if ln.split('#')[0].strip()]


def profile_values():
    """Значения реквизитов длиннее пяти символов плюс префиксы номеров задач.

    Короткие значения («DO», «6201») искать бессмысленно: они дают ложные срабатывания
    на любом тексте. Но ключ проекта важен именно как префикс — поэтому он ищется
    в виде `KEY-1234`, а не сам по себе.
    """
    if not reqs.has_data():
        return [], []
    def walk(node, path=()):
        if isinstance(node, dict):
            for k, v in node.items():
                if path + (k,) not in SKIP_VALUES:
                    yield from walk(v, path + (k,))
        elif isinstance(node, list):
            for v in node:
                yield from walk(v, path)
        elif isinstance(node, str):
            yield path, node

    values, patterns = set(), []
    for path, v in walk(reqs.load()):
        if path[:2] == ('integrations', 'tasks') and path[-1] == 'projects':
            continue                        # ключи проектов обрабатываются ниже
        if len(v) > 5:
            values.add(v)
    for key in reqs.get('integrations.tasks.projects', []) or []:
        if key:
            patterns.append((f'номер задачи проекта {key}',
                             re.compile(rf'\b{re.escape(key)}-\d+\b')))
    return sorted(values, key=len, reverse=True), patterns


def files():
    """Текстовые файлы движка — по одному разу, без бинарного и исключённого."""
    for target in TARGETS:
        p = os.path.join(ROOT, target)
        if os.path.isfile(p):
            yield target
            continue
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames if d not in ('__pycache__', '.git')]
            for name in sorted(filenames):
                rel = os.path.relpath(os.path.join(dirpath, name), ROOT)
                if rel.startswith(EXCLUDE) or rel.endswith(SKIP_EXT):
                    continue
                yield rel


def scan():
    """[(файл, номер строки, что нашли, строка)] — все находки всех проверок."""
    values, key_patterns = profile_values()
    allow = allowed()
    stops = stoplist()
    hits = []
    for rel in files():
        try:
            text = open(os.path.join(ROOT, rel), encoding='utf-8').read()
        except (UnicodeDecodeError, OSError):
            continue
        check_forms = not rel.startswith(FORMS_SKIP)
        check_values = not rel.startswith(VALUES_SKIP)
        for n, line in enumerate(text.splitlines(), 1):
            if check_forms:
                for label, rx in FORMS:
                    for m in rx.finditer(line):
                        found = m.group(0)
                        # Разрешённым считается и кусок разрешённого: одна и та же цифровая
                        # последовательность попадает сразу под несколько шаблонов —
                        # `840-0000711122843-32` целиком и её 13 цифр внутри.
                        if any(found in a for a in allow):
                            continue
                        if label == 'почтовый адрес' and found.endswith(SAFE_MAIL):
                            continue
                        hits.append((rel, n, f'{label}: {found}', line.strip()))
                for label, rx in key_patterns:
                    m = rx.search(line)
                    if m and m.group(0) not in allow:
                        hits.append((rel, n, f'{label}: {m.group(0)}', line.strip()))
            if check_values:
                for v in values:
                    if v in line:
                        hits.append((rel, n, f'реквизит из profile.json: {v}', line.strip()))
            low = line.lower()
            for s in stops:
                if s.lower() in low:
                    hits.append((rel, n, f'имя из стоп-листа: {s}', line.strip()))
    return hits


def staged_signature_samples():
    """Открытые образцы подписи в индексе git — в репозиторий идёт только контейнер."""
    root = reqs.data_root()
    if not os.path.isdir(root):
        return []                           # корня данных нет вообще — нечего и смотреть
    r = subprocess.run(['git', 'diff', '--cached', '--name-only', '--diff-filter=AM'],
                       capture_output=True, cwd=root)
    if r.returncode != 0:
        return []                           # не репозиторий git — проверка неприменима
    return [n for n in r.stdout.decode('utf-8', 'replace').split()
            if n.startswith('signatures/') and n.endswith('.png')]


def main():
    hits = scan()
    staged = staged_signature_samples()
    for rel, n, what, line in hits:
        print(f'{rel}:{n}: {what}', file=sys.stderr)
        print(f'    {line[:160]}', file=sys.stderr)
    if staged:
        print('ОТКРЫТЫЙ ОБРАЗЕЦ ПОДПИСИ В ИНДЕКСЕ: в git идёт только samples.tar.gz.gpg',
              file=sys.stderr)
        for n in staged:
            print(f'    {n}', file=sys.stderr)
    if hits or staged:
        print('\nЛичное — только в приватном репозитории данных; в движке — роли '
              'и ссылка на конфиг. Осознанные исключения — scripts/check_clean.allow.',
              file=sys.stderr)
        return 1
    modes = ['по форме']
    if reqs.has_data():
        modes.append('по значениям profile.json')
    if stoplist():
        modes.append('по стоп-листу')
    print(f'движок чист: проверки — {", ".join(modes)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
