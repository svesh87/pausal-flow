#!/usr/bin/env python3
"""Векторная подпись: шаблон из контейнера, плавное искажение, пути в контент-стрим PDF.

Шаблоны собирает `sig_trace.py` (разово, в `.venv`); здесь только чтение и отрисовка,
на стандартной библиотеке — подписание по-прежнему запускается системным `python3`.

Режим выбирает `signatures.mode()`. Подписыватели (`sign_pdf.py`, `sign_bank.py`,
`sign_izjava.py`) считают посадку одинаково в обоих режимах — по `geom()` шаблона те же
`w`, `h`, рамка чернил и базовая линия, что дала бы картинка, — и меняется только то, что
рисуется внутри той же матрицы `w·sc 0 0 h·sc X Y cm`: вместо `/Имя Do` — залитые пути.

## Искажение

Каждое подписание — своё. Поле смещения плавное и общее для всех точек контура: сумма
`WARP_TERMS` синусоид низкой частоты со случайными фазами, частотами и весами. Точки
двигаются согласованно, поэтому штрих не рвётся и край не «дрожит» — в отличие от шума
по отдельным точкам. Сверху — лёгкая аффинная часть: наклон вокруг базовой линии
и растяжение по осям.

Пределы — константы ниже; смещение от поля не больше `WARP_AMP` высоты чернил
в любой точке (веса нормированы, |sin| ≤ 1).
"""
import math
import random

WARP_AMP = 0.03            # предел смещения поля, доля высоты чернил
WARP_TERMS = 3             # синусоид в поле, на каждую ось
WARP_FREQ_U = (0.5, 2.0)   # периодов на ширину чернил — низкая частота, без ряби
WARP_FREQ_V = (0.0, 0.7)   # периодов на высоту чернил
SLANT_DEG = 3.0            # наклон вокруг базовой линии, ± градусов
STRETCH_X = 0.04           # растяжение по ширине, ± доля
STRETCH_Y = 0.05           # растяжение по высоте, ± доля
DIGITS = 5                 # знаков в координатах единичного квадрата: 1e-5 ширины < 0.02 px скана


def names(templates):
    """Отсортированные имена шаблонов — порядок стабилен между запусками."""
    return sorted(templates)


def geom(tpl):
    """(w, h, (ix0, iy0, ix1, iy1), baseline) — то же, что PNG-путь берёт из картинки."""
    return tpl['width'], tpl['height'], tuple(tpl['ink']), tpl['baseline']


def _field(rng):
    terms = [(rng.random(), rng.uniform(*WARP_FREQ_U), rng.uniform(*WARP_FREQ_V),
              rng.uniform(0, 2 * math.pi)) for _ in range(WARP_TERMS)]
    total = sum(t[0] for t in terms) or 1.0
    return [(a / total, fu, fv, ph) for a, fu, fv, ph in terms]


def warp(tpl, rng):
    """Функция (x, y) -> (x', y') в пикселях шаблона: поле смещения плюс аффинная часть."""
    ix0, iy0, ix1, iy1 = tpl['ink']
    iw, ih = ix1 - ix0 + 1, iy1 - iy0 + 1
    bl = tpl['baseline']
    cx = (ix0 + ix1) / 2
    amp = WARP_AMP * ih
    fx, fy = _field(rng), _field(rng)
    shear = math.tan(math.radians(rng.uniform(-SLANT_DEG, SLANT_DEG)))
    sx = 1 + rng.uniform(-STRETCH_X, STRETCH_X)
    sy = 1 + rng.uniform(-STRETCH_Y, STRETCH_Y)

    def f(x, y):
        u, v = (x - ix0) / iw, (y - iy0) / ih
        dx = amp * sum(a * math.sin(2 * math.pi * (fu * u + fv * v) + ph) for a, fu, fv, ph in fx)
        dy = amp * sum(a * math.sin(2 * math.pi * (fu * u + fv * v) + ph) for a, fu, fv, ph in fy)
        x, y = x + dx, y + dy
        x = cx + (x - cx) * sx + shear * (bl - y)
        y = bl + (y - bl) * sy
        return x, y
    return f


def deform(tpl, rng):
    """Контуры шаблона после искажения: тот же формат, новые координаты."""
    f = warp(tpl, rng)
    out = []
    for c in tpl['contours']:
        segs = []
        for s in c['segs']:
            if s[0] == 'L':
                segs.append(['L', *f(s[1], s[2])])
            else:
                segs.append(['C', *f(s[1], s[2]), *f(s[3], s[4]), *f(s[5], s[6])])
        out.append({'start': list(f(*c['start'])), 'segs': segs})
    return out


def path_ops(contours, w, h):
    """Контуры в пикселях (y вниз) -> операторы пути в единичном квадрате картинки (y вверх)."""
    def p(x, y):
        return f'{round(x / w, DIGITS)} {round(1 - y / h, DIGITS)}'
    parts = []
    for c in contours:
        parts.append(f'{p(*c["start"])} m')
        for s in c['segs']:
            if s[0] == 'L':
                parts.append(f'{p(s[1], s[2])} l')
            else:
                parts.append(f'{p(s[1], s[2])} {p(s[3], s[4])} {p(s[5], s[6])} c')
        parts.append('h')
    return '\n'.join(parts)


def draw(tpl, rng, W, H, X, Y):
    """Подпись в контент-стрим: (байты, seed). Матрица та же, что у картинки в PNG-режиме.

    seed искажения берётся из `rng` и возвращается: по нему вариант воспроизводится,
    если подписанный документ понадобится разобрать.
    """
    seed = rng.getrandbits(32)
    contours = deform(tpl, random.Random(seed))
    r, g, b = tpl['color']
    body = path_ops(contours, tpl['width'], tpl['height'])
    ops = (f'q {W:.2f} 0 0 {H:.2f} {X:.2f} {Y:.2f} cm {r:.4f} {g:.4f} {b:.4f} rg\n'
           f'{body}\nf* Q\n')
    return ops.encode(), seed
