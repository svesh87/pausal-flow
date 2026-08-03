"""Сама проверка чистоты движка: что она ловит и чего не поднимает зря.

Проверка чистоты — единственный механизм, который стоит между приватными реквизитами
и публичным репозиторием, поэтому она обязана быть проверена как обычный код. Тесты
гоняют её на синтетическом «движке» с намеренной утечкой: портить настоящий ради теста
нельзя, а верить на слово нечему.
"""
import os
import subprocess

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHECKER = os.path.join(CODE_ROOT, 'scripts', 'check_clean.py')


def engine(tmp_path, files, allow=None):
    """Собрать поддельный движок: CLAUDE.md с содержимым и, при желании, allow-файл."""
    (tmp_path / 'scripts').mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
    if allow is not None:
        (tmp_path / 'scripts' / 'check_clean.allow').write_text(allow, encoding='utf-8')
    return str(tmp_path)


def run(engine_dir, data_dir=None):
    env = dict(os.environ, TASKS_ENGINE=engine_dir)
    if data_dir:
        env['TASKS_DATA'] = data_dir
        env.pop('TASKS_PROFILE', None)
    r = subprocess.run(['python3', CHECKER], capture_output=True, env=env)
    return r.returncode, (r.stdout + r.stderr).decode('utf-8', 'replace')


def test_form_check_catches_account_without_config(tmp_path):
    """Жиро-счёт находится в клоне без profile.json — только по форме."""
    d = engine(tmp_path, {'CLAUDE.md': 'платить на 265-1234567890123-11 до пятницы\n'})
    code, out = run(d, data_dir=str(tmp_path / 'nodata'))
    assert code == 1
    assert 'жиро-счёт' in out


def test_form_check_catches_home_path_and_imap(tmp_path):
    """Абсолютный домашний путь и ящик в IMAP-URI — тоже форма, конфиг не нужен."""
    d = engine(tmp_path, {'docs/setup.md': 'лежит в /home/vlasnik/docs\n',
                          'CLAUDE.md': 'imap://kdo%40posta.rs@imap.posta.rs/Bank\n'})
    code, out = run(d, data_dir=str(tmp_path / 'nodata'))
    assert code == 1
    assert 'домашний путь' in out and 'IMAP-путь' in out


def test_ticket_key_prefix_is_caught(tmp_path):
    """Ключ проекта короткий, но номер задачи с ним — утечка: акт уходит наружу."""
    d = engine(tmp_path, {'CLAUDE.md': 'пример: PROJ-4711 «починить фигню»\n'})
    code, out = run(d)                      # конфиг — фикстура с projects: ["PROJ"]
    assert code == 1
    assert 'номер задачи проекта PROJ' in out


def test_allowlist_lets_public_accounts_through(tmp_path):
    """Счета публичных приходов и служебные счета банка — не утечка, они в allow-файле."""
    d = engine(tmp_path, {'CLAUDE.md': 'налог идёт на 840-0000711122843-32\n'},
               allow='840-0000711122843-32   # уплатный счёт, публичный\n')
    code, out = run(d, data_dir=str(tmp_path / 'nodata'))
    assert code == 0, out


def test_stoplist_from_data_root_catches_names(tmp_path):
    """Имена собственные лежат приватно: в публичном репозитории такому списку не место."""
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'profile.json').write_text('{"integrations": {"tasks": {"projects": []}}}')
    (data / '.check_clean.local').write_text('Пера Перић\n# комментарий\n')
    d = engine(tmp_path / 'engine', {'CLAUDE.md': 'акт от пера перић приходит в июле\n'})
    code, out = run(d, data_dir=str(data))
    assert code == 1
    assert 'стоп-лист' in out


def test_missing_stoplist_is_not_an_error(tmp_path):
    """Чужой клон без стоп-листа проходит проверку: отсутствие файла — норма."""
    d = engine(tmp_path / 'engine', {'CLAUDE.md': 'ничего личного\n'})
    code, out = run(d, data_dir=str(tmp_path / 'nodata'))
    assert code == 0, out
    assert 'по стоп-листу' not in out
