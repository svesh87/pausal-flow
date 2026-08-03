"""Настройка с нуля: скелет данных, доктор, синтетический пример.

Эти три вещи существуют ради человека, который только что склонировал движок и у которого
нет ни одного документа. Проверяются они здесь именно в таком состоянии: пустой каталог
вместо данных, конфиг из образца, ни одного PDF. Если что-то из этого падает, новый
владелец упирается в трассировку на первом же шаге.
"""
import os
import subprocess
import sys

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run(args, data=None, cwd=None):
    env = dict(os.environ)
    if data is not None:
        env['TASKS_DATA'] = data
        env.pop('TASKS_PROFILE', None)
    r = subprocess.run(args, capture_output=True, env=env, cwd=cwd or CODE_ROOT)
    return r.returncode, (r.stdout + r.stderr).decode('utf-8', 'replace')


def test_init_private_builds_skeleton(tmp_path):
    """Скелет данных: каталоги, конфиг из образца, симлинки на движок, хук."""
    target = tmp_path / 'data'
    code, out = run([os.path.join(CODE_ROOT, 'scripts', 'init_private.sh'), str(target)])
    assert code == 0, out
    assert (target / 'profile.json').exists()
    assert (target / 'months').is_dir() and (target / 'finance' / 'bank' / 'raw').is_dir()
    assert os.path.islink(target / 'engine')
    assert os.path.islink(target / '.claude' / 'skills')
    assert (target / '.githooks' / 'pre-commit').exists()
    assert 'engine' in (target / '.gitignore').read_text()


def test_init_private_is_idempotent(tmp_path):
    """Повторный запуск ничего не переписывает: конфиг с реквизитами дороже скелета."""
    target = tmp_path / 'data'
    run([os.path.join(CODE_ROOT, 'scripts', 'init_private.sh'), str(target)])
    (target / 'profile.json').write_text('{"свой": "конфиг"}')
    code, out = run([os.path.join(CODE_ROOT, 'scripts', 'init_private.sh'), str(target)])
    assert code == 0, out
    assert (target / 'profile.json').read_text() == '{"свой": "конфиг"}'


def test_init_private_refuses_to_use_engine_dir():
    """Данные внутри движка — прямой путь к утечке: движок публичный."""
    code, out = run([os.path.join(CODE_ROOT, 'scripts', 'init_private.sh'), CODE_ROOT])
    assert code != 0
    assert 'публичный' in out


def test_doctor_names_what_is_missing(tmp_path):
    """Без данных доктор не падает, а перечисляет недостающее и куда смотреть."""
    code, out = run([sys.executable, os.path.join(CODE_ROOT, 'scripts', 'doctor.py')],
                    data=str(tmp_path / 'nodata'))
    assert code == 1, out
    assert 'корень данных' in out
    assert 'docs/setup.md' in out


def test_examples_build_and_reports_assemble(tmp_path):
    """Синтетический пример собирается и на нём собираются реестры, отчёт и книга КПО.

    Это дымовой тест всей цепочки производных в клоне без данных: если он зелёный, чужой
    клон работоспособен, а не просто читается.
    """
    example = tmp_path / 'example-data'
    code, out = run([sys.executable, os.path.join(CODE_ROOT, 'scripts', 'make_examples.py'),
                     str(example)], data=str(tmp_path / 'nodata'))
    assert code == 0, out
    assert (example / 'profile.json').exists()
    assert (example / 'months' / '2026_06' / 'tasks.md').exists()
    assert (example / 'months' / '2026_06' / 'acceptance_1.pdf').exists()

    for args in (['scripts/tax_registry.py'], ['scripts/payments_registry.py'],
                 ['scripts/build_report.py'], ['scripts/kpo_book.py', '--offline']):
        code, out = run([sys.executable, os.path.join(CODE_ROOT, *args[0].split('/')),
                         *args[1:]], data=str(example))
        assert code == 0, f'{args}: {out}'
    report = (example / 'finance' / 'report.md').read_text()
    assert 'Приход EUR' in report
    assert (example / 'finance' / 'kpo' / 'entries.md').exists()


def test_example_and_fixture_cover_every_required_field():
    """Образец конфига и фикстура тестов знают все поля, которых требует доктор.

    Иначе доктор ругается на пустое поле у человека, который аккуратно заполнил образец,
    — а тесты при этом зелёные, потому что фикстура живёт своей жизнью.
    """
    import json

    import doctor

    def flat(d, prefix=()):
        out = {}
        for k, v in d.items():
            path = prefix + (k,)
            if isinstance(v, dict):
                out.update(flat(v, path))
            else:
                out['.'.join(path)] = v
        return out

    example = flat(json.load(open(os.path.join(CODE_ROOT, 'profile.example.json'))))
    fixture = flat(json.load(open(os.path.join(CODE_ROOT, 'scripts', 'tests', 'fixtures',
                                               'profile.json'))))
    for field in doctor.REQUIRED_FIELDS:
        assert field in example, f'{field} требует доктор, а в profile.example.json его нет'
        assert field in fixture, f'{field} требует доктор, а в фикстуре тестов его нет'


def test_example_month_has_a_section_per_act_language(tmp_path):
    """Секций «Формулировки в акте» столько, сколько языков в конфиге.

    Языки — свойство клиента, а не движка: в навыке и в примере их набор не зашит,
    иначе локаль клиента читалась бы прямо из публичного репозитория.
    """
    import json

    example = json.load(open(os.path.join(CODE_ROOT, 'profile.example.json')))
    langs = example['client']['act_languages']
    target = tmp_path / 'example-data'
    code, out = run([sys.executable, os.path.join(CODE_ROOT, 'scripts', 'make_examples.py'),
                     str(target)], data=str(tmp_path / 'nodata'))
    assert code == 0, out
    text = (target / 'months' / '2026_06' / 'tasks.md').read_text()
    assert text.count('## Формулировки в акте') == len(langs)
    for lang in langs:
        assert f'## Формулировки в акте ({lang.upper()})' in text
