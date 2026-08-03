#!/usr/bin/env python3
"""Образцы подписи: зашифрованный контейнер и расшифровка в память.

    python3 scripts/signatures.py --list                       # что внутри контейнера
    python3 scripts/signatures.py --pack <папка с PNG> [имя]   # пересобрать контейнер

В репозитории данных лежит только `signatures/samples.tar.gz.gpg`, зашифрованный на ключ из
`profile.json` (`gpg.recipient`). Открытые PNG на диск не пишутся вообще: скрипты подписи
получают их байтами через `load()`, поэтому нечего забыть удалить и нечего случайно
закоммитить.

## Почему один архив, а не файл на образец

Шифровальный подключ живёт на токене, и каждый вызов `gpg` требует подтверждения — касания
или PIN. Один контейнер = один вызов за запуск, даже когда акту нужны два разных образца:
`load()` кэширует расшифрованное на время процесса.

## Касание токена

У шифровального подключа политика касания `Cached`: **первая расшифровка требует физического
касания**, дальше около 15 секунд касание не запрашивается. Поэтому `gpg` здесь может ждать
оператора — команда, которая выглядит подвисшей, скорее всего просит коснуться ключа,
и через ~15 секунд без касания падает с «Время исчерпано». Это не поломка скрипта.

Практический вывод: подписание документа = одно касание. Если подписываются несколько
документов подряд, они успевают уложиться в окно кэша.
"""
import io
import os
import subprocess
import sys
import tarfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reqs  # noqa: E402

DATA = reqs.data_root()         # образцы — данные, а не движок: в клоне без данных их нет
SIG_DIR = os.path.join(DATA, 'signatures')
CONTAINER = os.path.join(SIG_DIR, 'samples.tar.gz.gpg')
_cache = {}


def _decrypt(attempts=3):
    """Байты расшифрованного контейнера. Пропущенное касание — не ошибка, а повтор.

    Окно касания короткое (~15 с), и его легко не заметить, поэтому перед каждым вызовом
    печатается предупреждение, а на таймаут делается ещё попытка. Прочие ошибки gpg
    (нет ключа, битый файл) повторять смысла нет — падаем сразу.
    """
    for attempt in range(1, attempts + 1):
        print(f'>>> КОСНИСЬ ТОКЕНА: расшифровка образцов подписи ждёт подтверждения '
              f'(попытка {attempt} из {attempts}, окно ~15 с)', file=sys.stderr, flush=True)
        r = subprocess.run(['gpg', '--decrypt', '--quiet', '--batch', CONTAINER],
                           capture_output=True)
        if r.returncode == 0:
            return r.stdout
        err = r.stderr.decode('utf-8', 'replace').strip()
        timed_out = any(w in err for w in ('Время исчерпано', 'Timeout', 'timed out'))
        assert timed_out and attempt < attempts, (
            f'gpg не расшифровал {os.path.relpath(CONTAINER, DATA)}: {err[:300]}. '
            + ('Касание так и не поймано — запустить ещё раз и коснуться токена сразу.'
               if timed_out else
               'Проверить, что ключ доступен (токен вставлен, агент отвечает): '
               'gpg --list-secret-keys'))
        print('    касание не поймано, повторяю', file=sys.stderr, flush=True)
    raise AssertionError('недостижимо')                     # цикл всегда выходит по return


def load():
    """{имя файла: байты PNG} — расшифровка контейнера, один вызов gpg за процесс."""
    if CONTAINER in _cache:
        return _cache[CONTAINER]
    assert os.path.exists(CONTAINER), (
        f'нет контейнера образцов {os.path.relpath(CONTAINER, DATA)} — собрать его: '
        'python3 scripts/signatures.py --pack <папка с PNG>')
    out = {}
    with tarfile.open(fileobj=io.BytesIO(_decrypt()), mode='r:gz') as tar:
        for m in tar.getmembers():
            if m.isfile() and m.name.endswith('.png'):
                out[os.path.basename(m.name)] = tar.extractfile(m).read()
    assert out, f'{os.path.relpath(CONTAINER, DATA)}: внутри нет ни одного PNG'
    _cache[CONTAINER] = out
    return out


def names():
    """Отсортированные имена образцов — порядок стабилен между запусками."""
    return sorted(load())


def pack(src_dir, name='samples.tar.gz.gpg'):
    """Собрать контейнер из папки с PNG. Существующий заменяется — он лишь производное.

    Имя контейнера — параметр, потому что образцы могут понадобиться в нескольких наборах.
    Кладутся они плоско: `load()` различает их по имени файла, и вложенные папки дали бы
    совпадающие имена.
    """
    out_path = os.path.join(SIG_DIR, name)
    files = sorted(n for n in os.listdir(src_dir) if n.endswith('.png'))
    assert files, f'в {src_dir} нет PNG'
    recipient = reqs.get('gpg.recipient')
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for n in files:
            tar.add(os.path.join(src_dir, n), arcname=n)
    r = subprocess.run(['gpg', '--encrypt', '--yes', '--batch', '--trust-model', 'always',
                        '--recipient', recipient, '--output', out_path],
                       input=buf.getvalue(), capture_output=True)
    assert r.returncode == 0, (
        f'gpg не зашифровал контейнер: {r.stderr.decode("utf-8", "replace").strip()[:300]}. '
        f'Проверить, что открытый ключ {recipient} есть в связке: gpg --list-keys {recipient}')
    print(f'{os.path.relpath(out_path, DATA)}: образцов {len(files)}, получатель {recipient}')


if __name__ == '__main__':
    if '--pack' in sys.argv:
        rest = sys.argv[sys.argv.index('--pack') + 1:]
        pack(rest[0], *rest[1:2])
    else:
        for n in names():
            print(n)
