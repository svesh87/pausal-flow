"""Два корня: движок и данные лежат в разных репозиториях.

Движок (`scripts/`, навыки, документация) публичный и не знает, где данные; данные
(`profile.json`, `months/`, `finance/`, `signatures/`) приватные и не знают, где движок.
Значит, корень данных обязан находиться сам — и обязан честно отсутствовать в чистом
клоне движка, а не приводить к записи мимо цели.
"""
import os
import subprocess

import reqs

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_data_root_from_env(monkeypatch, tmp_path):
    """`TASKS_DATA` перебивает всё: так гоняются прогоны на копиях данных."""
    monkeypatch.setenv('TASKS_DATA', str(tmp_path))
    assert reqs.data_root() == str(tmp_path)


def test_data_root_found_upwards(monkeypatch, tmp_path):
    """Обычный режим: скрипт запущен из подкаталога данных, корень найден поиском вверх."""
    (tmp_path / 'profile.json').write_text('{}')
    deep = tmp_path / 'months' / '2026_06'
    deep.mkdir(parents=True)
    monkeypatch.delenv('TASKS_DATA', raising=False)
    monkeypatch.delenv('TASKS_PROFILE', raising=False)
    monkeypatch.chdir(deep)
    assert reqs.data_root() == str(tmp_path)


def test_data_root_falls_back_to_code_root(monkeypatch, tmp_path):
    """Данных нет вообще — корнем становится сам движок, и это не падение.

    На этом стоят тесты, синтетические примеры и первое знакомство нового владельца:
    клон без данных должен запускаться и внятно сообщать, чего не хватает. Локальная
    подсказка `.tasks_data` здесь отводится в никуда: у владельца она обычно есть,
    а проверяется именно поведение чужого клона.
    """
    monkeypatch.delenv('TASKS_DATA', raising=False)
    monkeypatch.delenv('TASKS_PROFILE', raising=False)
    monkeypatch.setattr(reqs, 'DATA_HINT', str(tmp_path / 'нет-подсказки'))
    monkeypatch.chdir(tmp_path)
    assert reqs.data_root() == reqs.CODE_ROOT
    assert not reqs.has_data() or os.path.exists(os.path.join(reqs.CODE_ROOT, 'profile.json'))


def test_data_root_from_local_hint(monkeypatch, tmp_path):
    """Подсказка в корне движка нужна гейтам: они гоняются из каталога движка.

    Без неё пре-коммит проверял бы движок только по форме — настоящие реквизиты владельца
    ему были бы неизвестны, и утечка значения прошла бы незамеченной.
    """
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'profile.json').write_text('{}')
    hint = tmp_path / 'hint'
    hint.write_text(str(data) + '\n')
    monkeypatch.delenv('TASKS_DATA', raising=False)
    monkeypatch.delenv('TASKS_PROFILE', raising=False)
    monkeypatch.setattr(reqs, 'DATA_HINT', str(hint))
    monkeypatch.chdir(tmp_path / '..')
    assert reqs.data_root() == str(data)


def test_missing_profile_message_names_the_way_out(monkeypatch, tmp_path):
    """Сообщение про отсутствующий конфиг называет и образец, и переменную окружения."""
    monkeypatch.setenv('TASKS_PROFILE', str(tmp_path / 'profile.json'))
    reqs._cache.clear()
    try:
        reqs.load()
        raise AssertionError('ожидалось падение: файла реквизитов нет')
    except AssertionError as e:
        assert 'profile.example.json' in str(e)
        assert 'TASKS_DATA' in str(e)
    reqs._cache.clear()


def test_rebuild_without_data_is_quiet(tmp_path):
    """Пересборка в клоне без данных — сообщение и ноль, а не трассировка."""
    env = dict(os.environ, TASKS_DATA=str(tmp_path))
    env.pop('TASKS_PROFILE', None)
    r = subprocess.run([os.path.join(CODE_ROOT, 'scripts', 'rebuild_all.sh')],
                       capture_output=True, env=env, cwd=str(tmp_path))
    out = (r.stdout + r.stderr).decode('utf-8', 'replace')
    assert r.returncode == 0, out
    assert 'данных нет' in out, out
