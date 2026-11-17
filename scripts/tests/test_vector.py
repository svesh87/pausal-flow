"""Векторная подпись: искажение шаблона и вставка путями вместо картинки.

Шаблон здесь выдуманный — завитушка из двух кривых, а не подпись владельца: настоящие
шаблоны живут только в зашифрованном контейнере данных. Свойства, которые проверяются,
от формы не зависят: искажение воспроизводимо по seed, не выходит за объявленный предел,
каждое подписание даёт свой вариант, и в документ не попадает ни одной картинки.
"""
import io
import json
import math
import os
import random
import re
import stat
import tarfile

import pdfobj
import pytest
import sig_vector
import sign_pdf
import signatures


def _template():
    """Синтетический шаблон в формате `sig_trace.py`: рамка чернил, базовая линия, контуры."""
    return {
        'source': 'synthetic', 'width': 400, 'height': 120,
        'ink': [20, 30, 379, 99], 'baseline': 90, 'color': [0.35, 0.35, 0.5],
        'contours': [
            {'start': [20, 90], 'segs': [['C', 80, 30, 140, 30, 200, 80],
                                         ['C', 260, 99, 320, 40, 379, 30],
                                         ['L', 379, 40],
                                         ['C', 320, 50, 260, 99, 200, 90],
                                         ['C', 140, 40, 80, 40, 20, 99]]},
            {'start': [100, 60], 'segs': [['L', 120, 60], ['L', 120, 70], ['L', 100, 70]]},
        ],
    }


def _points(contours):
    out = []
    for c in contours:
        out.append(tuple(c['start']))
        for s in c['segs']:
            vals = s[1:]
            out += [(vals[i], vals[i + 1]) for i in range(0, len(vals), 2)]
    return out


def test_same_seed_gives_same_variant():
    """По seed из вывода подписания вариант восстанавливается — документ можно разобрать."""
    tpl = _template()
    a = sig_vector.deform(tpl, random.Random(7))
    b = sig_vector.deform(tpl, random.Random(7))
    assert a == b


def test_two_signings_differ():
    tpl = _template()
    rng = random.Random(1)
    ops1, seed1 = sig_vector.draw(tpl, rng, 100, 30, 50, 50)
    ops2, seed2 = sig_vector.draw(tpl, rng, 100, 30, 50, 50)
    assert seed1 != seed2
    assert ops1 != ops2


def test_field_displacement_within_limit(monkeypatch):
    """Плавное поле сдвигает любую точку не дальше `WARP_AMP` высоты чернил.

    Аффинная часть выключена, чтобы мерить только поле: её вклад зависит от положения точки
    и проверяется отдельно.
    """
    monkeypatch.setattr(sig_vector, 'SLANT_DEG', 0.0)
    monkeypatch.setattr(sig_vector, 'STRETCH_X', 0.0)
    monkeypatch.setattr(sig_vector, 'STRETCH_Y', 0.0)
    tpl = _template()
    ih = tpl['ink'][3] - tpl['ink'][1] + 1
    limit = sig_vector.WARP_AMP * ih + 1e-9
    src = _points(tpl['contours'])
    for seed in range(50):
        moved = _points(sig_vector.deform(tpl, random.Random(seed)))
        for (x0, y0), (x1, y1) in zip(src, moved, strict=True):
            assert abs(x1 - x0) <= limit and abs(y1 - y0) <= limit


def test_total_displacement_within_declared_bound():
    """Поле, наклон и растяжение вместе укладываются в предел, выводимый из констант."""
    tpl = _template()
    ix0, iy0, ix1, iy1 = tpl['ink']
    iw, ih = ix1 - ix0 + 1, iy1 - iy0 + 1
    amp = sig_vector.WARP_AMP * ih
    shear = math.tan(math.radians(sig_vector.SLANT_DEG))
    # x: поле, растяжение от центра рамки (с учётом сдвига поля), наклон от базовой линии
    bx = amp * (1 + sig_vector.STRETCH_X) + sig_vector.STRETCH_X * iw / 2 + shear * (ih + amp)
    by = amp * (1 + sig_vector.STRETCH_Y) + sig_vector.STRETCH_Y * ih
    src = _points(tpl['contours'])
    for seed in range(50):
        moved = _points(sig_vector.deform(tpl, random.Random(seed)))
        for (x0, y0), (x1, y1) in zip(src, moved, strict=True):
            assert abs(x1 - x0) <= bx + 1e-9 and abs(y1 - y0) <= by + 1e-9


def _act(make_pdf):
    """Синтетический акт с двумя линиями подписи под именем обвезника из фикстуры."""
    return make_pdf('act.pdf', (('______________ Test Owner', 72, 200),
                                ('______________ Test Owner', 320, 200)))


def test_vector_signature_adds_paths_not_images(make_pdf, tmp_path):
    """Подпись ложится путями: в дописанной части нет ни одной картинки и /XObject."""
    src = _act(make_pdf)
    out = str(tmp_path / 'signed.pdf')
    tpls = {'a.json': _template(), 'b.json': _template()}
    picks, page = sign_pdf.sign(src, out, samples=tpls, rng=random.Random(3), mode='vector')
    original = open(src, 'rb').read()
    signed = open(out, 'rb').read()
    assert signed.startswith(original)                  # исходные байты не тронуты
    tail = signed[len(original):]
    assert b'/Image' not in tail and b'/XObject' not in tail
    assert b'f*' in tail and tail.count(b' rg\n') == 2
    assert len(picks) == 2 and all('#' in p for p in picks)   # имя шаблона и seed
    assert picks[0].split('#')[0] != picks[1].split('#')[0]   # в колонках разные шаблоны
    doc = pdfobj.load(out)                              # результат разбирается заново
    assert len(pdfobj.page_list(doc)) == 1


def test_png_signature_still_embeds_images(make_pdf, tmp_path):
    """PNG-режим остаётся прежним: картинки и ссылки на них в ресурсах страницы."""
    import struct
    import zlib

    def png(w=40, h=12):
        row = b'\x00' + bytes((60, 60, 120, 255)) * w
        def chunk(t, d):
            return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d))
        return (b'\x89PNG\r\n\x1a\n'
                + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(row * h)) + chunk(b'IEND', b''))

    src = _act(make_pdf)
    out = str(tmp_path / 'signed.pdf')
    sign_pdf.sign(src, out, samples={'a.png': png(), 'b.png': png()}, rng=random.Random(3))
    tail = open(out, 'rb').read()[len(open(src, 'rb').read()):]
    assert tail.count(b'/Subtype/Image') == 4            # по картинке и маске на линию
    assert re.search(rb'/XObject<</SvSig0 \d+ 0 R /SvSig1', tail)


def test_vector_signing_refuses_existing_result(make_pdf, tmp_path):
    src = _act(make_pdf)
    out = str(tmp_path / 'signed.pdf')
    sign_pdf.sign(src, out, samples={'a.json': _template()}, rng=random.Random(1), mode='vector')
    with pytest.raises(AssertionError, match='не перезаписывается'):
        sign_pdf.sign(src, out, samples={'a.json': _template()}, mode='vector')


def test_mode_flag_overrides_profile(monkeypatch):
    """Флаг запуска важнее профиля; без ключа в профиле — PNG, как до векторного режима."""
    monkeypatch.setattr(signatures.reqs, 'get', lambda key, default=None: default)
    assert signatures.mode([]) == 'png'
    assert signatures.mode(['--vector']) == 'vector'
    monkeypatch.setattr(signatures.reqs, 'get', lambda key, default=None: 'vector')
    assert signatures.mode([]) == 'vector'
    assert signatures.mode(['--png']) == 'png'
    monkeypatch.setattr(signatures.reqs, 'get', lambda key, default=None: 'svg')
    with pytest.raises(AssertionError, match='signatures.mode'):
        signatures.mode([])


def test_vector_container_read_from_memory(tmp_path, monkeypatch):
    """Шаблоны читаются из контейнера одним вызовом gpg, JSON разбирается в память."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        data = json.dumps(_template()).encode()
        info = tarfile.TarInfo('sig_x.json')
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    payload = tmp_path / 'vectors.tar.gz'
    payload.write_bytes(buf.getvalue())
    counter = tmp_path / 'calls'
    counter.write_text('')
    gpg = tmp_path / 'gpg'
    gpg.write_text(f'#!/usr/bin/env bash\necho x >> {counter}\ncat {payload}\n')
    gpg.chmod(gpg.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    monkeypatch.setattr(signatures, 'VECTORS', str(payload))
    signatures._cache.clear()
    try:
        first = signatures.load_vectors()
        assert signatures.load_vectors() is first
        assert list(first) == ['sig_x.json'] and first['sig_x.json']['baseline'] == 90
        assert counter.read_text().count('x') == 1
    finally:
        signatures._cache.clear()


def test_missing_vector_container_names_the_way_out(tmp_path, monkeypatch):
    monkeypatch.setattr(signatures, 'VECTORS', str(tmp_path / 'нет.gpg'))
    signatures._cache.clear()
    with pytest.raises(AssertionError) as e:
        signatures.load_vectors()
    assert 'sig_trace.py' in str(e.value) and '--png' in str(e.value)
    signatures._cache.clear()
