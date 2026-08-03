#!/usr/bin/env python3
"""Разметка образцов подписи: геометрия чернил + базовая линия письма.

    python3 scripts/sig_metrics.py [signatures_dir]

Пишет рядом с образцами `metrics.json` (машинно) и `METRICS.md` (глазами).
Подписыватели берут оттуда `baseline` — строку, на которой «стоит» основная
масса штрихов (без учёта хвостов-выносов). Привязка по базовой линии, а не по
самому нижнему пикселю, даёт одинаковое положение относительно линии подписи
для всех образцов — у части из них длинный нижний вынос, и по крайнему пикселю
такие подписи вставали заметно выше остальных.

Как считается: по каждой строке картинки берётся количество чернильных пикселей;
базовая линия — самая нижняя строка, где чернил хотя бы 20% от максимума по
строкам. Хвосты тоньше этого порога и в базовую линию не попадают.
"""
import sys, os, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sign_pdf import read_rgba, ink_bbox, baseline_row, norm_scale, REF_W, REF_ASPECT
import reqs
import signatures

THR = 10          # порог альфы: что считаем чернилами
DENS = 0.20       # доля от максимальной плотности строки


def metrics(name, png):
    """Геометрия образца по его байтам: рамка чернил, базовая линия, масштаб."""
    w, h, rgba = read_rgba(png)
    ix0, iy0, ix1, iy1 = ink_bbox(w, h, rgba, THR)
    iw, ih = ix1 - ix0 + 1, iy1 - iy0 + 1
    baseline = baseline_row(w, h, rgba, THR, DENS)
    sc = norm_scale(iw, ih)
    return {
        'file': name, 'width': w, 'height': h,
        'ink': [ix0, iy0, ix1, iy1],
        'ink_w': iw, 'ink_h': ih,
        'baseline': baseline,
        'descender': iy1 - baseline,          # насколько хвост уходит ниже базовой линии
        'aspect': round(ih / iw, 4),
        'scale_pt_per_px': round(sc, 6),      # масштаб, дающий эталонную площадь чернил
        'w_pt': round(iw * sc, 1),            # ширина чернил в документе при этом масштабе
        'h_pt': round(ih * sc, 1),            # высота чернил в документе
    }


def main(sig_dir):
    samples = signatures.load()
    out = [metrics(name, samples[name]) for name in sorted(samples)]
    with open(os.path.join(sig_dir, 'metrics.json'), 'w') as f:
        json.dump({m['file']: m for m in out}, f, indent=1, ensure_ascii=False)
    lines = [
        '# Разметка образцов подписи',
        '',
        'Сгенерировано `scripts/sig_metrics.py`; машинная версия — `metrics.json`.',
        'Координаты в пикселях картинки, отсчёт сверху.',
        '',
        '- `ink` — рамка чернил (x0, y0, x1, y1);',
        '- `baseline` — строка, на которой стоит основная масса штрихов;',
        '- `descender` — насколько ниже базовой линии уходит хвост;',
        '- `aspect` — высота чернил / ширина чернил;',
        '- `scale` — масштаб pt/px, при котором площадь чернил равна эталонной;',
        '- `ш×в в pt` — во что образец превращается в документе при этом масштабе.',
        '',
        'Подписыватели ставят **базовую линию** чуть ниже линии подписи в документе,',
        'поэтому хвосты естественно пересекают линию, а положение не зависит от того,',
        'есть у конкретного образца длинный вынос или нет.',
        '',
        f'Масштаб нормируется **по площади чернил** к эталону {REF_W:.0f} pt по ширине',
        f'при пропорции {REF_ASPECT} (медиана набора). Нормировать по одной ширине нельзя:',
        'пропорции образцов разнятся на ±25%, и «плоские» подписи выглядели бы мельче.',
        'Один и тот же эталон применяется и к акту, и к бланку банка — чтобы',
        'подпись в разных документах выглядела одного размера.',
        '',
        '| образец | размер | чернила (x0,y0,x1,y1) | ш×в чернил | baseline | '
        'descender | aspect | scale | ш×в в pt |',
        '|---|---|---|---|---|---|---|---|---|',
    ]
    for m in out:
        lines.append(f'| `{m["file"]}` | {m["width"]}×{m["height"]} | '
                     f'{",".join(str(v) for v in m["ink"])} | {m["ink_w"]}×{m["ink_h"]} | '
                     f'{m["baseline"]} | {m["descender"]} | {m["aspect"]} | '
                     f'{m["scale_pt_per_px"]} | {m["w_pt"]}×{m["h_pt"]} |')
    with open(os.path.join(sig_dir, 'METRICS.md'), 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print(f'разметлено образцов: {len(out)}')
    for m in out:
        print(f'  {m["file"]}: ink {m["ink_w"]}x{m["ink_h"]}, baseline {m["baseline"]}, '
              f'descender {m["descender"]}')


if __name__ == '__main__':
    d = sys.argv[1] if len(sys.argv) > 1 else reqs.data_path('signatures')
    main(d)
