#!/usr/bin/env python3
"""Векторные шаблоны подписи из образцов-сканов: трассировка контуров и упаковка.

    engine/.venv/bin/python engine/scripts/sig_trace.py <образец.png> [...]          # замер
    engine/.venv/bin/python engine/scripts/sig_trace.py <образец.png> [...] --pack   # контейнер

Разовый инструмент, а не часть подписания: ему нужны `numpy` и `potracer` (порт potrace
на чистом Python) из `.venv` движка, `requirements-dev.txt`. Подписание шаблоны только
читает (`sig_vector.py`) и обходится стандартной библиотекой.

Образцы берутся из зашифрованного контейнера в память (`signatures.load()`), шаблоны уходят
в `signatures/vectors.tar.gz.gpg` тоже из памяти — открытые на диск не пишутся ни те, ни другие.
Контейнер образцов PNG не трогается.

## Что в шаблоне

JSON в координатах пикселей скана, отсчёт сверху — тех же, что у PNG-образца, поэтому
посадка в документ считается одинаково в обоих режимах:

- `width`, `height` — размер скана; `ink` — рамка чернил; `baseline` — базовая линия письма
  (`ink_bbox` и `baseline_row` из `sign_pdf.py`, как в PNG-режиме);
- `color` — цвет чернил, медиана RGB по непрозрачным пикселям, доли единицы;
- `contours` — замкнутые контуры: `start` и сегменты `["C", c1x, c1y, c2x, c2y, x, y]`
  или `["L", x, y]`. Внешние контуры и дырки в петлях вперемешку: заливка even-odd.

## Качество

Мерой служит IoU — пересечение площадей маски скана и растра контуров, отношение
к объединению. Порог `IOU_MIN`; ниже — шаблон не упаковывается.
"""
import json
import os
import sys

import numpy as np
import potrace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signatures  # noqa: E402
from sign_pdf import read_rgba, ink_bbox, baseline_row  # noqa: E402

ALPHA_EDGE = 127      # порог альфы, по которому проходит край штриха (середина сглаживания)
OPAQUE = 200          # пиксели для цвета чернил: только плотная середина штриха
IOU_MIN = 0.95        # ниже — контур заметно расходится со сканом
TRACE = {'turdsize': 4, 'alphamax': 1.0, 'opttolerance': 0.2}
FLAT_STEPS = 12       # отрезков на кривую при растеризации для замера


def ink_mask(w, h, rgba):
    a = np.frombuffer(bytes(rgba), dtype=np.uint8).reshape(h, w, 4)
    return a[:, :, 3] > ALPHA_EDGE, a


def ink_color(a):
    px = a[a[:, :, 3] > OPAQUE][:, :3]
    return [round(float(v) / 255, 4) for v in np.median(px, axis=0)]


def _pt(p):
    return [round(float(p.x), 3), round(float(p.y), 3)]


def trace(mask):
    """Контуры potrace. Bitmap ждёт «светлое = True» и сам инвертирует — даём ~mask."""
    path = potrace.Bitmap(~mask).trace(**TRACE)
    contours = []
    for curve in path:
        segs = []
        for s in curve.segments:
            if s.is_corner:
                segs.append(['L', *_pt(s.c)])
                segs.append(['L', *_pt(s.end_point)])
            else:
                segs.append(['C', *_pt(s.c1), *_pt(s.c2), *_pt(s.end_point)])
        contours.append({'start': _pt(curve.start_point), 'segs': segs})
    return contours


def polylines(contours, steps=FLAT_STEPS):
    """Контуры -> ломаные: кривые Безье режутся на `steps` отрезков."""
    out = []
    for c in contours:
        pts = [tuple(c['start'])]
        for s in c['segs']:
            if s[0] == 'L':
                pts.append((s[1], s[2]))
                continue
            x0, y0 = pts[-1]
            c1x, c1y, c2x, c2y, x, y = s[1:]
            for i in range(1, steps + 1):
                t = i / steps
                u = 1 - t
                pts.append((u**3*x0 + 3*u*u*t*c1x + 3*u*t*t*c2x + t**3*x,
                            u**3*y0 + 3*u*u*t*c1y + 3*u*t*t*c2y + t**3*y))
        out.append(np.array(pts))
    return out


def rasterize(contours, w, h):
    """Заливка even-odd по центрам пикселей: строка за строкой, пересечения рёбер."""
    edges = []
    for p in polylines(contours):
        q = np.roll(p, -1, axis=0)
        edges.append(np.hstack([p, q]))
    e = np.vstack(edges)
    x0, y0, x1, y1 = e[:, 0], e[:, 1], e[:, 2], e[:, 3]
    out = np.zeros((h, w), dtype=bool)
    xs = np.arange(w) + 0.5
    for row in range(h):
        yc = row + 0.5
        hit = (y0 <= yc) != (y1 <= yc)
        if not hit.any():
            continue
        t = (yc - y0[hit]) / (y1[hit] - y0[hit])
        cx = np.sort(x0[hit] + t * (x1[hit] - x0[hit]))
        cnt = np.searchsorted(cx, xs)            # пересечений левее центра пикселя
        out[row] = cnt % 2 == 1
    return out


def template(name, png):
    w, h, rgba = read_rgba(png)
    mask, a = ink_mask(w, h, rgba)
    contours = trace(mask)
    got = rasterize(contours, w, h)
    iou = float((got & mask).sum() / (got | mask).sum())
    tpl = {
        'source': name, 'width': w, 'height': h,
        'ink': list(ink_bbox(w, h, rgba)), 'baseline': baseline_row(w, h, rgba),
        'color': ink_color(a), 'contours': contours,
    }
    return tpl, iou


def main(argv):
    picks = [a for a in argv if not a.startswith('--')]
    assert picks, 'назвать образцы: sig_trace.py sig_05_….png [...] [--pack]'
    samples = signatures.load()
    missing = [p for p in picks if p not in samples]
    assert not missing, f'нет в контейнере: {missing}; есть: {sorted(samples)}'
    members, bad = {}, []
    for p in picks:
        tpl, iou = template(p, samples[p])
        nseg = sum(len(c['segs']) for c in tpl['contours'])
        print(f'{p}: контуров {len(tpl["contours"])}, сегментов {nseg}, IoU {iou:.4f}')
        if iou < IOU_MIN:
            bad.append(p)
        members[os.path.splitext(p)[0] + '.json'] = json.dumps(
            tpl, ensure_ascii=False, separators=(',', ':')).encode()
    assert not bad, f'IoU ниже {IOU_MIN}: {bad} — подобрать TRACE или взять другой образец'
    if '--pack' in argv:
        signatures.pack_members(members, signatures.VECTORS)


if __name__ == '__main__':
    main(sys.argv[1:])
