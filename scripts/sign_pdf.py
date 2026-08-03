#!/usr/bin/env python3
"""Вставка подписи в acceptance-PDF инкрементальным обновлением.

Использование:
    python3 scripts/sign_pdf.py input.pdf output.pdf [signatures_dir]

Что делает:
- находит на последней странице строки `_______ <имя обвезника>` (pdftotext -bbox),
  имя берётся из `profile.json`;
- выбирает случайные PNG-подписи из signatures/ (разные для колонок EN и ES);
- дописывает в конец PDF объекты картинок (RGB + SMask), контент-стрим с их
  отрисовкой, обновлённые объекты контента и ресурсов страницы и новую xref-секцию
  с /Prev — исходные байты файла не меняются вовсе.

Структура документа не зашита: страницы, /Contents и /Resources находятся разбором
(scripts/pdfobj.py), поддержаны варианты «ссылка на поток», «массив в объекте» и
«инлайн-массив», существующий /XObject в ресурсах сливается, остаточная матрица
преобразования страницы инвертируется. Проверено на генераторах macOS Quartz
(акты 18–22) и Samsung (акты 1–17).

Проверка результата — scripts/verify_signed.sh. Если скрипт упал, сначала посмотреть,
чем документ отличается: python3 scripts/pdf_probe.py файл.pdf
Устройство и геометрия подписи — .claude/skills/sign-docs/internals.md
"""
import re, zlib, random, sys, struct, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdfobj import (load, page_list, page_field, media_box, page_streams,
                    residual_ctm, inv, obj_dict, obj_raw, write_update, words, _value, ensure_new,
                    hline_in_box)
import reqs
import signatures

# имя под линией подписи — якорь поиска: в акте это `______ <имя обвезника>`
SIGNER_WORDS = reqs.get('entrepreneur.name').split()



def read_rgba(src):
    """PNG -> (ширина, высота, RGBA). Принимает байты (образцы приходят из
    зашифрованного контейнера в память) или путь к файлу."""
    d = src if isinstance(src, (bytes, bytearray)) else open(src, 'rb').read()
    i = 8; idat = b''
    while i < len(d):
        ln = struct.unpack('>I', d[i:i+4])[0]
        typ = d[i+4:i+8]; data = d[i+8:i+8+ln]; i += 12 + ln
        if typ == b'IHDR':
            w, h = struct.unpack('>II', data[:8])
        elif typ == b'IDAT':
            idat += data
    raw = zlib.decompress(idat); stride = w * 4
    out = bytearray(w * h * 4); pos = 0
    for y in range(h):
        f = raw[pos]; pos += 1
        assert f == 0, 'PNG with filters not supported (our signature PNGs use filter 0)'
        out[y*stride:(y+1)*stride] = raw[pos:pos+stride]; pos += stride
    return w, h, out


def ink_bbox(w, h, rgba, thr=10):
    minx, miny, maxx, maxy = w, h, -1, -1
    for i in range(w * h):
        if rgba[i*4+3] > thr:
            x, y = i % w, i // w
            minx = min(minx, x); maxx = max(maxx, x)
            miny = min(miny, y); maxy = max(maxy, y)
    return minx, miny, maxx, maxy


# Эталон размера подписи в документе: чернила шириной REF_W pt при типичной для
# набора образцов пропорции REF_ASPECT (медиана по signatures/). Масштаб считается
# по площади чернил, а не по одной ширине: у образцов пропорции разнятся на ±25%,
# и при фиксированной ширине «плоские» подписи выглядели заметно мельче остальных.
REF_W = 84.0
REF_ASPECT = 0.203


def norm_scale(ink_w, ink_h, ref_w=REF_W):
    """Масштаб pt/px, при котором площадь чернил равна эталонной."""
    return ((ref_w * ref_w * REF_ASPECT) / (ink_w * ink_h)) ** 0.5


def baseline_row(w, h, rgba, thr=10, dens=0.20):
    """Строка картинки, на которой стоит основная масса штрихов.

    Привязываться к самому нижнему пикселю нельзя: у части образцов длинный
    нижний вынос, и такие подписи вставали бы выше остальных. См. signatures/METRICS.md.
    """
    rows = []
    for y in range(h):
        base = y * w * 4
        rows.append(sum(1 for x in range(w) if rgba[base + x*4 + 3] > thr))
    mx = max(rows) or 1
    for y in range(h - 1, -1, -1):
        if rows[y] >= dens * mx:
            return y
    return h - 1


def png_obj(path, n_img, n_smask):
    w, h, rgba = read_rgba(path)
    rgb = bytes(bytearray(b for i in range(w*h) for b in rgba[i*4:i*4+3]))
    alpha = bytes(rgba[3::4])
    zrgb = zlib.compress(rgb, 9); za = zlib.compress(alpha, 9)
    o1 = (f'{n_img} 0 obj\n<</Type/XObject/Subtype/Image/Width {w}/Height {h}'
          f'/ColorSpace/DeviceRGB/BitsPerComponent 8/Filter/FlateDecode'
          f'/SMask {n_smask} 0 R/Length {len(zrgb)}>>\nstream\n').encode() \
        + zrgb + b'\nendstream\nendobj\n'
    o2 = (f'{n_smask} 0 obj\n<</Type/XObject/Subtype/Image/Width {w}/Height {h}'
          f'/ColorSpace/DeviceGray/BitsPerComponent 8/Filter/FlateDecode'
          f'/Length {len(za)}>>\nstream\n').encode() + za + b'\nendstream\nendobj\n'
    return (w, h, rgba), o1, o2


def find_lines(pdf, page, page_h):
    """Линии подписи: (x0, x1, y_снизу) для каждой `______ <имя обвезника>`.

    На странице есть ещё и линии второй стороны — отбираем только те, за которыми идёт
    именем обвезника. Y уточняем по растру: рамка слова-подчёркивания ниже
    самой линии на глубину выносного элемента шрифта.
    """
    ws = words(pdf, page)[0][3]
    lines = []
    for i, (x0, y0, x1, y1, t) in enumerate(ws):
        if re.fullmatch(r'_{10,}', t):
            nxt = ' '.join(w[4] for w in ws[i+1:i+3])
            if all(w in nxt for w in SIGNER_WORDS):
                y = hline_in_box(pdf, page, x0, x1, y0, y1, page_h)
                lines.append((x0, x1, y if y is not None else page_h - y1))
    return lines


def _patch_contents(doc, pnum, cf, n_content, objects):
    """Подключить наш контент-стрим к странице, не ломая существующий."""
    if cf['kind'] == 'массив в объекте':
        num = cf['obj']
        body = obj_raw(doc, num)
        inner = body[body.find(b'[')+1:body.rfind(b']')].strip()
        objects[num] = (f'{num} 0 obj\n[ '.encode() + inner
                        + f' {n_content} 0 R ]'.encode() + b'\nendobj\n')
    elif cf['kind'] in ('ссылка на поток', 'инлайн-массив'):
        pd = obj_dict(doc, pnum)
        val = _value(pd, b'Contents')
        if cf['kind'] == 'инлайн-массив':
            new = val[:val.rfind(b']')] + f' {n_content} 0 R ]'.encode()
        else:
            new = b'[ ' + val + f' {n_content} 0 R ]'.encode()
        m = re.search(rb'/Contents(?![A-Za-z0-9])\s*', pd)
        pd_new = pd[:m.end()] + new + pd[m.end()+len(val):]
        objects[pnum] = f'{pnum} 0 obj\n<<'.encode() + pd_new + b'>>\nendobj\n'
    else:
        raise AssertionError(f'страница {pnum}: непонятная форма /Contents ({cf["kind"]}) '
                             '— остановиться и разобрать руками (scripts/pdf_probe.py)')


def _patch_resources(doc, pnum, rf, entries, objects):
    """Добавить наши XObject в ресурсы страницы, сохранив уже имеющиеся."""
    add = b'/XObject<<' + entries + b'>>'
    if rf['kind'] == 'ссылка на словарь':
        num = rf['obj']
        rd = obj_dict(doc, num)
        xv = _value(rd, b'XObject')
        if xv is None:
            rd_new = rd + add
        else:
            assert xv[:2] == b'<<', (f'ресурсы {num}: /XObject задан ссылкой — '
                                     'случай не разобран, остановиться и решить руками')
            merged = xv[:xv.rfind(b'>>')] + entries + b'>>'
            i = rd.find(xv)
            rd_new = rd[:i] + merged + rd[i+len(xv):]
        objects[num] = f'{num} 0 obj\n<<'.encode() + rd_new + b'>>\nendobj\n'
    elif rf['kind'] == 'инлайн-словарь':
        pd = obj_dict(doc, pnum)
        val = _value(pd, b'Resources')
        xv = _value(val, b'XObject')
        if xv is None:
            new = val[:val.rfind(b'>>')] + add + b'>>'
        else:
            assert xv[:2] == b'<<', 'инлайн /XObject задан ссылкой — разобрать руками'
            merged = xv[:xv.rfind(b'>>')] + entries + b'>>'
            i = val.find(xv)
            new = val[:i] + merged + val[i+len(xv):]
        i = pd.find(val)
        pd_new = pd[:i] + new + pd[i+len(val):]
        objects[pnum] = f'{pnum} 0 obj\n<<'.encode() + pd_new + b'>>\nendobj\n'
    else:
        raise AssertionError(f'страница {pnum}: непонятная форма /Resources ({rf["kind"]}) '
                             '— остановиться и разобрать руками (scripts/pdf_probe.py)')


def sign(pdf_in, pdf_out, samples=None, rng=None, force=False):
    """Подписать акт: по одной подписи на каждую линию `______ <имя>` последней страницы.

    `samples` — {имя: байты PNG}; по умолчанию расшифровывается контейнер
    `signatures/samples.tar.gz.gpg` (один вызов gpg за запуск)."""
    ensure_new(pdf_out, force)
    rng = rng or random.SystemRandom()
    doc = load(pdf_in)
    pages = page_list(doc)
    mb = None
    for idx in range(len(pages) - 1, -1, -1):     # линии подписи ищем с конца документа
        mb = media_box(doc, pages[idx])
        lines = find_lines(pdf_in, idx + 1, mb[3] - mb[1])
        if lines:
            pidx = idx
            break
    else:
        raise AssertionError(f'{pdf_in}: строки подписи не найдены ни на одной странице '
                             '— документ изменился, разобрать руками')
    assert len(lines) == 2, f'{pdf_in}: ожидались 2 строки подписи, найдено {len(lines)}'

    pnum = pages[pidx]
    cf = page_field(doc, pnum, b'Contents')
    rf = page_field(doc, pnum, b'Resources')
    ctm = residual_ctm(b''.join(page_streams(doc, pnum)))

    n0 = doc['size']
    samples = samples or signatures.load()
    sigs = sorted(samples)
    objects = {}
    picks, chosen = [], []
    content = b'q\n'
    if ctm != (1, 0, 0, 1, 0, 0):
        content += ('{:.6f} {:.6f} {:.6f} {:.6f} {:.6f} {:.6f} cm\n'
                    .format(*inv(ctm))).encode()
    names = []
    for k, (lx0, lx1, ly) in enumerate(lines):
        pool = [s for s in sigs if s not in chosen] or sigs
        p = rng.choice(pool); chosen.append(p); picks.append(p)
        n_img, n_sm = n0 + k*2, n0 + k*2 + 1
        (w, h, rgba), o1, o2 = png_obj(samples[p], n_img, n_sm)
        objects[n_img], objects[n_sm] = o1, o2
        names.append((f'SvSig{k}', n_img))
        ix0, iy0, ix1, iy1 = ink_bbox(w, h, rgba)
        bl = baseline_row(w, h, rgba)
        sc = norm_scale(ix1 - ix0 + 1, iy1 - iy0 + 1) * rng.uniform(0.97, 1.03)
        maxw = (lx1 - lx0) * 1.05          # шире линии подписи не растягиваем
        if (ix1 - ix0 + 1) * sc > maxw:
            sc = maxw / (ix1 - ix0 + 1)
        # по горизонтали — по центру линии подписи, с небольшим случайным сдвигом
        X = (lx0 + lx1) / 2 - (ix0 + ix1) / 2 * sc + rng.uniform(-3, 3)
        # по вертикали — базовая линия письма чуть ниже линии подписи, чтобы штрихи
        # её заметно пересекали (одинаково для образцов с выносом и без)
        Y = ly - rng.uniform(2.0, 3.0) - (h - 1 - bl) * sc
        content += f'q {w*sc:.2f} 0 0 {h*sc:.2f} {X:.2f} {Y:.2f} cm /SvSig{k} Do Q\n'.encode()
    content += b'Q\n'

    n_content = n0 + 2 * len(lines)
    objects[n_content] = (f'{n_content} 0 obj\n<</Length {len(content)}>>\nstream\n'.encode()
                          + content + b'endstream\nendobj\n')
    entries = b''.join(f'/{nm} {num} 0 R '.encode() for nm, num in names)
    _patch_contents(doc, pnum, cf, n_content, objects)
    _patch_resources(doc, pnum, rf, entries, objects)
    write_update(doc, pdf_out, objects)
    return picks, pidx + 1


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--force']
    picks, page = sign(args[0], args[1], force='--force' in sys.argv)
    print(f'picked: {picks} (страница {page})')
