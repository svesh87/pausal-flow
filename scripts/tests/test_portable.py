"""Переносимость: реквизиты только в конфиге, в текстах — роли.

Смысл всей затеи с `profile.json` в том, что движок — навыки, скрипты, документацию —
можно отдать другому человеку как есть. Проверка не на слово: тот же скрипт, что стоит
в пре-коммите, гоняется тестом.
"""
import os
import subprocess

import pytest
import reqs

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_portable_part_is_clean():
    """В движке нет ни реквизитов, ни имён, ни абсолютных путей.

    Подмена конфига на фикстуру здесь снимается намеренно: проверять надо против
    **настоящих** реквизитов владельца, как это делает пре-коммит. С фикстурой тест
    ловил бы совпадения с её заглушками — а они по построению те же, что в образце
    конфига и в синтетических примерах.
    """
    env = {k: v for k, v in os.environ.items() if k not in ('TASKS_PROFILE', 'TASKS_DATA')}
    r = subprocess.run([os.path.join(ROOT, 'scripts', 'check_clean.sh')],
                       capture_output=True, cwd=ROOT, env=env)
    assert r.returncode == 0, (r.stdout + r.stderr).decode('utf-8', 'replace')


def test_missing_field_names_the_key(monkeypatch, tmp_path):
    """Незаполненное поле должно называть себя и образец, а не падать в KeyError."""
    p = tmp_path / 'profile.json'
    p.write_text('{"entrepreneur": {}}')
    monkeypatch.setenv('TASKS_PROFILE', str(p))
    reqs._cache.clear()
    with pytest.raises(AssertionError) as e:
        reqs.get('accounts.rsd')
    assert 'accounts.rsd' in str(e.value)
    assert 'profile.example.json' in str(e.value)
    reqs._cache.clear()


def test_profile_path_overridden_by_env(monkeypatch, tmp_path):
    """Подмена конфига через окружение — то, на чём стоят эти тесты."""
    p = tmp_path / 'profile.json'
    p.write_text('{"client": {"name": "Другой Клиент"}}')
    monkeypatch.setenv('TASKS_PROFILE', str(p))
    reqs._cache.clear()
    assert reqs.get('client.name') == 'Другой Клиент'
    reqs._cache.clear()
