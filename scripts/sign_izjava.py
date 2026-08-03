#!/usr/bin/env python3
"""Заполнение и подписание второго бланка банка — «Izjava / Statement».

    python3 scripts/sign_izjava.py "<бланк>_for_sign.pdf" "<оригинальное имя>.pdf"

Бланк приходит вторым вложением в письме о поступлении и уже содержит номер счёта,
дату и сумму — дозаполнить надо только реквизиты заявителя, по два поля в сербской
и английской половинах:

| Поле | Значение |
|---|---|
| `Davalac izjave` / `Declerant` | имя из `profile.json` + подпись справа от имени |
| `Tel/ fax /e-mail` / `Phone/e-mail` | телефон и почта из `profile.json` |

Раскладка выведена из уже отправленных экземпляров бланка: текст печатается на самой линии
от её начала, подпись ставится сразу справа от имени, низом на линии. Якоря ищутся по тексту,
координаты линий — по растру, так что смена вёрстки бланка не ломает заполнение молча:
скрипт падает с понятным сообщением.

Бланк приходит не всегда: состав вложений в письме банка меняется, и в каких случаях
он появляется, снаружи не видно. Пришёл — заполняется этим скриптом.
См. .claude/skills/sign-docs/SKILL.md.
"""
import re, zlib, random, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdfobj import (load, page_list, page_field, media_box, page_streams, residual_ctm,
                    inv, obj_dict, obj_raw, write_update, words, _value, ensure_new, hline_in_box)
from sign_pdf import png_obj, ink_bbox, baseline_row, norm_scale
from sign_bank import ttf_metrics, enc_text, TTF_PATH, CACUTE_CODE
import reqs
import signatures

NAME = reqs.get('entrepreneur.izjava_name')
CONTACT = (reqs.get('entrepreneur.phone') + ' / '
           + reqs.get('entrepreneur.email'))
FONT_SIZE = 9.0


def _find(ws, *texts):
    for w in ws:
        if w[4] in texts:
            return w
    raise AssertionError(f'якорь {texts} не найден — бланк Izjava изменился, '
                         'разобрать руками (scripts/pdf_probe.py, pdftotext -bbox)')


def _line_after(ws, anchor, page_h, pdf, page):
    """Линия подчёркивания правее якоря на той же строке: (x0, x1, y снизу)."""
    ay0 = anchor[1]
    best = None
    for x0, y0, x1, y1, t in ws:
        if re.fullmatch(r'_{5,}', t) and x0 > anchor[0] and abs(y0 - ay0) < 3:
            if best is None or x0 < best[0]:
                best = (x0, x1, y0, y1)
    assert best, f'справа от «{anchor[4]}» нет линии для заполнения — бланк изменился'
    x0, x1, y0, y1 = best
    y = hline_in_box(pdf, page, x0, x1, y0, y1, page_h)
    return x0, x1, y if y is not None else page_h - y1


def sign_izjava(blank, out_pdf, samples=None, rng=None, name=NAME, contact=CONTACT,
                force=False):
    ensure_new(out_pdf, force)
    rng = rng or random.SystemRandom()
    doc = load(blank)
    pages = page_list(doc)
    assert len(pages) == 1, f'ожидалась одна страница, найдено {len(pages)} — разобрать руками'
    pnum = pages[0]
    mb = media_box(doc, pnum)
    H = mb[3] - mb[1]
    ws = words(blank, 1)[0][3]

    # две половины бланка: сербская и английская
    halves = []
    for who, tel in (('izjave', 'Tel/'), ('Declerant', 'Phone/e-mail')):
        a_name = _find(ws, who)
        a_tel = _find(ws, tel)
        halves.append((_line_after(ws, a_name, H, blank, 1),
                       _line_after(ws, a_tel, H, blank, 1)))

    tm = ttf_metrics(TTF_PATH, set(name + contact))

    def text_w(s, size):
        return sum(tm['widths'].get(c, 500) for c in s) * size / 1000.0

    n0 = doc['size']
    n_font, n_fd, n_ff = n0, n0 + 1, n0 + 2
    zff = zlib.compress(tm['data'], 9)
    objects = {
        n_ff: (f'{n_ff} 0 obj\n<</Filter/FlateDecode/Length {len(zff)}'
               f'/Length1 {len(tm["data"])}>>\nstream\n').encode() + zff + b'\nendstream\nendobj\n',
        n_fd: (f'{n_fd} 0 obj\n<</Type/FontDescriptor/FontName/LiberationSans/Flags 32'
               f'/FontBBox[{tm["bbox"][0]} {tm["bbox"][1]} {tm["bbox"][2]} {tm["bbox"][3]}]'
               f'/ItalicAngle 0/Ascent {tm["ascent"]}/Descent {tm["descent"]}'
               f'/CapHeight {tm["ascent"]}/StemV 80/FontFile2 {n_ff} 0 R>>\nendobj\n').encode(),
    }
    widths = [str(tm['widths'].get('ć' if c == CACUTE_CODE else chr(c), 500))
              for c in range(32, 256)]
    objects[n_font] = (f'{n_font} 0 obj\n<</Type/Font/Subtype/TrueType/BaseFont/LiberationSans'
                       f'/FirstChar 32/LastChar 255/Widths[{" ".join(widths)}]'
                       f'/FontDescriptor {n_fd} 0 R'
                       f'/Encoding<</Type/Encoding/BaseEncoding/WinAnsiEncoding'
                       f'/Differences[{CACUTE_CODE} /cacute]>>>>\nendobj\n').encode()

    ctm = residual_ctm(b''.join(page_streams(doc, pnum)))
    content = b'q\n'
    if ctm != (1, 0, 0, 1, 0, 0):
        content += ('{:.6f} {:.6f} {:.6f} {:.6f} {:.6f} {:.6f} cm\n'
                    .format(*inv(ctm))).encode()

    # Зазор от линии: базовая линия текста выше линии на глубину нижних выносных
    # элементов шрифта, иначе хвосты «g», «/», «@» наезжают на черту.
    gap = abs(tm['descent']) / 1000.0 * FONT_SIZE + 0.8

    # текст. Цвет задаём явно чёрным: без этого наследуется цвет заливки, оставшийся
    # от контента бланка (в Izjava он синий).
    content += b'0 g\n0 G\nBT\n'
    for (nx0, _nx1, ny), (tx0, _tx1, ty) in halves:
        for x, y, s in ((nx0 + 2.0, ny + gap, name), (tx0 + 2.0, ty + gap, contact)):
            content += (f'/FIZ {FONT_SIZE} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm (').encode() \
                       + enc_text(s) + b') Tj\n'
    content += b'ET\n'

    # подписи — по одной в каждой половине, сразу справа от напечатанного имени
    samples = samples or signatures.load()
    sigs = sorted(samples)
    picks, chosen, names = [], [], []
    n_img = n_font + 3
    for k, ((nx0, _nx1, ny), _tel) in enumerate(halves):
        pool = [s for s in sigs if s not in chosen] or sigs
        p = rng.choice(pool); chosen.append(p); picks.append(p)
        ni, ns = n_img + k * 2, n_img + k * 2 + 1
        (w, h, rgba), o1, o2 = png_obj(samples[p], ni, ns)
        objects[ni], objects[ns] = o1, o2
        names.append((f'SigIz{k}', ni))
        ix0, iy0, ix1, iy1 = ink_bbox(w, h, rgba)
        bl = baseline_row(w, h, rgba)
        sc = norm_scale(ix1 - ix0 + 1, iy1 - iy0 + 1) * rng.uniform(0.97, 1.03)
        x = nx0 + 2.0 + text_w(name, FONT_SIZE) + rng.uniform(8, 14) - ix0 * sc
        y = ny - rng.uniform(2.0, 3.0) - (h - 1 - bl) * sc
        content += f'q {w*sc:.2f} 0 0 {h*sc:.2f} {x:.2f} {y:.2f} cm /SigIz{k} Do Q\n'.encode()
    content += b'Q\n'

    n_content = n_img + 2 * len(halves)
    objects[n_content] = (f'{n_content} 0 obj\n<</Length {len(content)}>>\nstream\n'.encode()
                          + content + b'endstream\nendobj\n')

    # подключение к странице
    cf = page_field(doc, pnum, b'Contents')
    pd = obj_dict(doc, pnum)
    if cf['kind'] == 'ссылка на поток':
        val = _value(pd, b'Contents')
        new = b'[ ' + val + f' {n_content} 0 R ]'.encode()
    elif cf['kind'] == 'инлайн-массив':
        val = _value(pd, b'Contents')
        new = val[:val.rfind(b']')] + f' {n_content} 0 R ]'.encode()
    elif cf['kind'] == 'массив в объекте':
        body = obj_raw(doc, cf['obj'])
        inner = body[body.find(b'[')+1:body.rfind(b']')].strip()
        objects[cf['obj']] = (f'{cf["obj"]} 0 obj\n[ '.encode() + inner
                              + f' {n_content} 0 R ]'.encode() + b'\nendobj\n')
        new = None
    else:
        raise AssertionError(f'непонятная форма /Contents ({cf["kind"]}) — разобрать руками')

    rf = page_field(doc, pnum, b'Resources')
    assert rf['kind'] == 'инлайн-словарь', (f'/Resources в форме «{rf["kind"]}» — '
                                            'случай не разобран, остановиться и решить руками')
    res = _value(pd, b'Resources')
    add_f = f'/Font<</FIZ {n_font} 0 R>>'.encode()
    add_x = b'/XObject<<' + b''.join(f'/{nm} {num} 0 R '.encode() for nm, num in names) + b'>>'
    res_new = res
    for key, add, entries in ((b'Font', add_f, f'/FIZ {n_font} 0 R '.encode()),
                              (b'XObject', add_x,
                               b''.join(f'/{nm} {num} 0 R '.encode() for nm, num in names))):
        cur = _value(res_new, key)
        if cur is None:
            res_new = res_new[:res_new.rfind(b'>>')] + add + b'>>'
        else:
            assert cur[:2] == b'<<', f'/{key.decode()} задан ссылкой — разобрать руками'
            merged = cur[:cur.rfind(b'>>')] + entries + b'>>'
            i = res_new.find(cur)
            res_new = res_new[:i] + merged + res_new[i+len(cur):]
    pd_new = pd.replace(res, res_new, 1)
    if new is not None:
        val = _value(pd, b'Contents')
        m = re.search(rb'/Contents(?![A-Za-z0-9])\s*', pd_new)
        pd_new = pd_new[:m.end()] + new + pd_new[m.end()+len(val):]
    objects[pnum] = f'{pnum} 0 obj\n<<'.encode() + pd_new + b'>>\nendobj\n'

    write_update(doc, out_pdf, objects)
    return {'name': name, 'contact': contact, 'signatures': picks, 'halves': len(halves)}


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    print(sign_izjava(args[0], args[1], force='--force' in sys.argv))
