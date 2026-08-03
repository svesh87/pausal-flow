"""Работа с документами: отказ перезаписать подписанное и образцы подписи из контейнера.

Обе проверки про необратимое. Перезапись подписанного документа рвёт его тождество с тем,
что лежит у контрагента; лишний вызов gpg — лишнее касание токена, которое легко пропустить,
и тогда подписание падает на середине.
"""
import os
import stat

import pdfobj
import pytest
import signatures


def test_ensure_new_allows_missing_file(tmp_path):
    pdfobj.ensure_new(str(tmp_path / 'нет-такого.pdf'))


def test_ensure_new_refuses_existing(make_pdf):
    """Существующий результат не перезаписывается — сообщение должно объяснять, почему."""
    path = make_pdf('signed.pdf')
    with pytest.raises(AssertionError) as e:
        pdfobj.ensure_new(path)
    assert 'не перезаписывается' in str(e.value)
    assert '--force' in str(e.value)


def test_ensure_new_allows_with_force(make_pdf):
    pdfobj.ensure_new(make_pdf('signed.pdf'), force=True)


def _fake_gpg(tmp_path, payload, counter):
    """Подставной gpg в PATH: считает вызовы и отдаёт готовый контейнер."""
    script = tmp_path / 'gpg'
    script.write_text('#!/usr/bin/env bash\n'
                      f'echo x >> {counter}\n'
                      f'cat {payload}\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(tmp_path)


def test_container_decrypted_once_per_process(tmp_path, monkeypatch):
    """Один вызов gpg на процесс: у шифровального ключа политика касания.

    Акту нужны два разных образца, бланку — один; если бы каждый брался отдельным вызовом,
    владельцу пришлось бы касаться токена несколько раз за один шаг.
    """
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for name in ('sig_01.png', 'sig_02.png'):
            info = tarfile.TarInfo(name)
            data = 'PNG-заглушка'.encode()
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    payload = tmp_path / 'container.tar.gz'
    payload.write_bytes(buf.getvalue())
    counter = tmp_path / 'calls'
    counter.write_text('')

    monkeypatch.setenv('PATH', _fake_gpg(tmp_path, payload, counter)
                       + os.pathsep + os.environ['PATH'])
    monkeypatch.setattr(signatures, 'CONTAINER', str(payload))
    signatures._cache.clear()

    first = signatures.load()
    second = signatures.load()
    assert sorted(first) == ['sig_01.png', 'sig_02.png']
    assert second is first                              # второй раз — из кэша процесса
    assert counter.read_text().count('x') == 1
    signatures._cache.clear()


def test_missing_container_says_how_to_build(tmp_path, monkeypatch):
    monkeypatch.setattr(signatures, 'CONTAINER', str(tmp_path / 'нет.gpg'))
    signatures._cache.clear()
    with pytest.raises(AssertionError) as e:
        signatures.load()
    assert '--pack' in str(e.value)
    signatures._cache.clear()


def test_synthetic_pdf_carries_info_dictionary(make_pdf):
    """У синтетического PDF есть /Info: без него подписанное нечем проверить.

    `verify_signed.sh` сравнивает `/CreationDate` и требует обновлённый `/ModDate`.
    Документ без `/Info` проходит подпись, но проверку — нет, и демо-прогон в чужом клоне
    выглядел бы поломкой подписи, а не особенностью заглушки.
    """
    doc = pdfobj.load(make_pdf('with-info.pdf'))
    assert doc['info'] is not None
