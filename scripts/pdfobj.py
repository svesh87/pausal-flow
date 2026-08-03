#!/usr/bin/env python3
"""Минимальный разбор PDF для инкрементального добавления подписи.

Общая база для sign_pdf.py (акты) и pdf_probe.py (диагностика). Исходный файл не
меняется: все правки дописываются в конец новой ревизией и уходят в отдельный файл-результат.
Готовый результат тоже не перезаписывается — см. `ensure_new()`.

Поддерживается только классическая xref-таблица с `trailer` (все документы клиента
и банка такие). Если попадётся xref-поток (PDF 1.5+) — падаем с понятным
сообщением: это повод остановиться и разобрать файл руками, а не подписывать наугад.
"""
import re, os, zlib, subprocess


# --- матрицы -----------------------------------------------------------------

def mul(m, n):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a*A+b*C, a*B+b*D, c*A+d*C, c*B+d*D, e*A+f*C+E, e*B+f*D+F)


def inv(m):
    a, b, c, d, e, f = m
    det = a*d - b*c
    assert abs(det) > 1e-9, 'вырожденная CTM'
    ia, ib, ic, idd = d/det, -b/det, -c/det, a/det
    return (ia, ib, ic, idd, -(e*ia + f*ic), -(e*ib + f*idd))


def residual_ctm(raw):
    """CTM, остающаяся действующей после потока (cm на нулевой глубине q/Q)."""
    ctm = (1, 0, 0, 1, 0, 0)
    depth = 0
    pat = (rb'(?<![A-Za-z])(q|Q)(?![A-Za-z])|'
           rb'([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+cm(?![A-Za-z])')
    for m in re.finditer(pat, raw):
        if m.group(1) == b'q':
            depth += 1
        elif m.group(1) == b'Q':
            depth = max(0, depth - 1)
        elif depth == 0:
            ctm = mul(tuple(float(m.group(i)) for i in range(2, 8)), ctm)
    return ctm


# --- разбор файла ------------------------------------------------------------

def _dict_span(b, start=0):
    """Границы первого сбалансированного словаря << >> начиная с start."""
    i = b.find(b'<<', start)
    assert i != -1, 'словарь не найден'
    depth, j = 0, i
    while j < len(b) - 1:
        if b[j:j+2] == b'<<':
            depth += 1; j += 2; continue
        if b[j:j+2] == b'>>':
            depth -= 1; j += 2
            if depth == 0:
                return i, j
            continue
        j += 1
    raise AssertionError('несбалансированный словарь')


def _parse_xref(d, off):
    table, trailers, seen = {}, [], set()
    while off is not None and off not in seen:
        seen.add(off)
        while off < len(d) and d[off:off+1] in (b' ', b'\r', b'\n'):
            off += 1
        if d[off:off+4] != b'xref':
            return None, None
        i = off + 4
        while True:
            m = re.match(rb'\s*(\d+)\s+(\d+)\s*[\r\n]+', d[i:i+64])
            if not m:
                break
            first, cnt = int(m.group(1)), int(m.group(2))
            i += m.end()
            for k in range(cnt):
                em = re.match(rb'\s*(\d{10})\s+(\d{5})\s+([nf])', d[i:i+30])
                if not em:
                    break
                i += em.end()
                if d[i:i+1] in (b' ', b'\r'):
                    i += 1
                if d[i:i+1] in (b'\r', b'\n'):
                    i += 1
                if em.group(3) == b'n' and (first + k) not in table:
                    table[first + k] = int(em.group(1))
        tm = d.find(b'trailer', i)
        if tm == -1:
            break
        te = d.find(b'startxref', tm)
        tr = d[tm:te if te != -1 else len(d)]
        trailers.append(tr)
        pm = re.search(rb'/Prev\s+(\d+)', tr)
        off = int(pm.group(1)) if pm else None
    return table, trailers


def load(path):
    d = open(path, 'rb').read()
    sx = d.rfind(b'startxref')
    assert sx != -1, f'{path}: нет startxref — файл повреждён'
    start = int(re.search(rb'startxref\s+(\d+)', d[sx:sx+64]).group(1))
    table, trailers = _parse_xref(d, start)
    assert table, (f'{path}: xref-поток вместо таблицы (PDF 1.5+). '
                   'Этот случай не разобран — остановиться и решить руками.')
    tr = trailers[0]
    root = int(re.search(rb'/Root\s+(\d+)\s+0\s+R', tr).group(1))
    im = re.search(rb'/Info\s+(\d+)\s+0\s+R', tr)
    idm = re.search(rb'/ID\s*\[[^\]]*\]', tr)
    return {
        'path': path, 'data': d, 'xref': table, 'xref_kind': 'таблица',
        'trailer': tr, 'root': root,
        'info': int(im.group(1)) if im else None,
        'id': idm.group(0) if idm else b'',
        'size': int(re.search(rb'/Size\s+(\d+)', tr).group(1)),
        'startxref': start,
    }


def obj_raw(doc, num):
    """Байты объекта от `N 0 obj` до `endobj`."""
    off = doc['xref'].get(num)
    assert off is not None, f'объект {num} отсутствует в xref'
    d = doc['data']
    m = re.compile(rb'\s*' + str(num).encode() + rb'\s+\d+\s+obj').match(d, off)
    assert m, f'по смещению {off} нет объекта {num}'
    end = d.find(b'endobj', m.end())
    assert end != -1, f'объект {num}: нет endobj'
    return d[m.end():end]


def obj_dict(doc, num):
    """Внутренности словаря объекта (без << >>). Для потоков — только словарь."""
    b = obj_raw(doc, num)
    i, j = _dict_span(b)
    return b[i+2:j-2]


def is_dict_obj(doc, num):
    return b'<<' in obj_raw(doc, num).split(b'stream')[0]


def stream_data(doc, num):
    b = obj_raw(doc, num)
    st = b.find(b'stream')
    assert st != -1, f'объект {num} — не поток'
    head = b[:st]
    k = st + 6
    while b[k] in (13, 10):
        k += 1
    lm = re.search(rb'/Length\s+(\d+)', head)
    if lm:
        raw = b[k:k+int(lm.group(1))]
    else:
        raw = b[k:b.rfind(b'endstream')]
    if b'FlateDecode' in head:
        raw = zlib.decompress(raw)
    return raw


def _refs(b):
    return [int(x) for x in re.findall(rb'(\d+)\s+0\s+R', b)]


def page_list(doc):
    """Номера объектов страниц в порядке документа."""
    rootd = obj_dict(doc, doc['root'])
    pages = int(re.search(rb'/Pages\s+(\d+)\s+0\s+R', rootd).group(1))
    out = []

    def walk(num, depth=0):
        assert depth < 32, 'слишком глубокое дерево страниц'
        b = obj_dict(doc, num)
        if re.search(rb'/Type\s*/Pages', b):
            km = re.search(rb'/Kids\s*\[(.*?)\]', b, re.S)
            assert km, f'узел {num}: нет /Kids'
            for k in _refs(km.group(1)):
                walk(k, depth + 1)
        else:
            out.append(num)

    walk(pages)
    return out


def _value(b, key):
    """Сырое значение ключа: до следующего ключа того же уровня."""
    m = re.search(rb'/' + key + rb'(?![A-Za-z0-9])', b)
    if not m:
        return None
    rest = b[m.end():].lstrip()
    if rest[:2] == b'<<':
        i, j = _dict_span(rest)
        return rest[i:j]
    if rest[:1] == b'[':
        depth, k = 0, 0
        while k < len(rest):
            if rest[k:k+1] == b'[':
                depth += 1
            elif rest[k:k+1] == b']':
                depth -= 1
                if depth == 0:
                    return rest[:k+1]
            k += 1
        raise AssertionError(f'/{key.decode()}: несбалансированный массив')
    m2 = re.match(rb'(\d+\s+0\s+R|/[^\s/\[\]<>]+|[-\d.]+)', rest)
    return m2.group(1) if m2 else None


def page_field(doc, pnum, key):
    """Описание /Contents или /Resources страницы: где лежит и в какой форме."""
    pd = obj_dict(doc, pnum)
    val = _value(pd, key)
    res = {'kind': 'нет', 'obj': None, 'streams': [], 'has_xobject': False, 'value': val}
    if val is None:
        return res
    rm = re.fullmatch(rb'(\d+)\s+0\s+R', val)
    if rm:
        num = int(rm.group(1))
        res['obj'] = num
        body = obj_raw(doc, num).lstrip()
        if body[:1] == b'[':
            res['kind'] = 'массив в объекте'
            res['streams'] = _refs(body[:body.find(b']') + 1])
        elif b'stream' in body:
            res['kind'] = 'ссылка на поток'
            res['streams'] = [num]
        else:
            res['kind'] = 'ссылка на словарь'
            res['has_xobject'] = b'/XObject' in body
    elif val[:1] == b'[':
        res['kind'] = 'инлайн-массив'
        res['streams'] = _refs(val)
    elif val[:2] == b'<<':
        res['kind'] = 'инлайн-словарь'
        res['has_xobject'] = b'/XObject' in val
    return res


def media_box(doc, pnum):
    num, depth = pnum, 0
    while num is not None and depth < 32:
        b = obj_dict(doc, num)
        mb = _value(b, b'MediaBox')
        if mb:
            return [float(x) for x in re.findall(rb'[-\d.]+', mb)]
        pm = re.search(rb'/Parent\s+(\d+)\s+0\s+R', b)
        num = int(pm.group(1)) if pm else None
        depth += 1
    return [0.0, 0.0, 595.276, 841.89]


def page_streams(doc, pnum):
    return [stream_data(doc, n) for n in page_field(doc, pnum, b'Contents')['streams']]


# --- запись новой ревизии ----------------------------------------------------

def pdf_date(t=None):
    """Дата в формате PDF: D:YYYYMMDDHHmmSS+HH'mm'."""
    import time
    t = t or time.localtime()
    off = -(time.altzone if t.tm_isdst else time.timezone)
    sign = '+' if off >= 0 else '-'
    off = abs(off)
    return (time.strftime('D:%Y%m%d%H%M%S', t)
            + f"{sign}{off//3600:02d}'{off%3600//60:02d}'")


def _touch_moddate(doc, objects, moddate):
    """Обновить /ModDate в /Info: подписание — реальное изменение документа."""
    num = doc['info']
    if num is None or num in objects:
        return
    body = obj_dict(doc, num)
    stamp = f'/ModDate ({moddate})'.encode()
    if re.search(rb'/ModDate\s*\(', body):
        body = re.sub(rb'/ModDate\s*\((?:[^()\\]|\\.)*\)', stamp, body, count=1)
    else:
        body = body + stamp
    objects[num] = f'{num} 0 obj\n<<'.encode() + body + b'>>\nendobj\n'


def ensure_new(out_path, force=False):
    """Отказ перезаписать уже существующий результат.

    Подписанный документ отправлен контрагенту, и повторная подпись — не то же самое:
    меняются `/ModDate` и сами подписи внутри (образец выбирается случайно, геометрия
    пересчитывается заново), то есть файл перестаёт совпадать с тем, что у контрагента.
    Поэтому по умолчанию скрипты подписи не пишут поверх; осознанная перезапись — `--force`.
    """
    assert force or not os.path.exists(out_path), (
        f'{out_path} уже существует — подписанный документ не перезаписывается: '
        'у контрагента лежит именно этот файл, а повторная подпись меняет и дату '
        'изменения, и подписи внутри. Если перезапись нужна осознанно — флаг --force, '
        'иначе подписывать в другой файл (прогоны — в tmp/test/<месяц>/)')


def write_update(doc, out_path, objects, moddate=None):
    """objects: {номер: полные байты объекта `N 0 obj ... endobj\\n`}."""
    _touch_moddate(doc, objects, moddate or pdf_date())
    out = bytearray(doc['data'])
    if out[-1:] != b'\n':
        out += b'\n'
    offs = {}
    for num in sorted(objects):
        offs[num] = len(out)
        out += objects[num]
    xref_off = len(out)
    nums = sorted(offs)
    x = b'xref\n'
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j+1] == nums[j] + 1:
            j += 1
        x += f'{nums[i]} {j-i+1}\n'.encode()
        for n in nums[i:j+1]:
            x += f'{offs[n]:010d} 00000 n \n'.encode()
        i = j + 1
    size = max(doc['size'], max(nums) + 1)
    tr = f'trailer\n<</Size {size} /Root {doc["root"]} 0 R '.encode()
    if doc['info'] is not None:
        tr += f'/Info {doc["info"]} 0 R '.encode()
    tr += f'/Prev {doc["startxref"]} '.encode() + doc['id']
    x += tr + f'>>\nstartxref\n{xref_off}\n%%EOF\n'.encode()
    open(out_path, 'wb').write(bytes(out + x))


def build_simple(path, lines, page=(595, 842)):
    """Собрать минимальный PDF с заданными строками текста: [(текст, x, y), …].

    Одна страница, шрифт Helvetica, классическая xref-таблица с trailer — ровно та форма,
    которую понимает `load()`. Нужен там, где требуется настоящий файл, а настоящих
    документов быть не должно: тесты разбора и подписи, синтетические примеры для чужого
    клона движка. Координаты — в точках PDF, отсчёт снизу.

    `/Info` с `/CreationDate` кладётся не для красоты: без него подписанный результат нельзя
    проверить `verify_signed.sh` — проверять было бы нечего, а настоящие документы банка
    и клиента этот словарь всегда несут.
    """
    def obj(num, body):
        return f'{num} 0 obj\n'.encode() + body + b'\nendobj\n'

    content = b'BT /F1 12 Tf\n'
    for text, x, y in lines:
        content += f'1 0 0 1 {x} {y} Tm ({text}) Tj\n'.encode()
    content += b'ET\n'
    stream = zlib.compress(content)
    objs = [
        obj(1, b'<</Type/Catalog/Pages 2 0 R>>'),
        obj(2, b'<</Type/Pages/Kids[3 0 R]/Count 1>>'),
        obj(3, f'<</Type/Page/Parent 2 0 R/MediaBox[0 0 {page[0]} {page[1]}]'.encode()
               + b'/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>'),
        obj(4, f'<</Length {len(stream)}/Filter/FlateDecode>>\nstream\n'.encode()
               + stream + b'\nendstream'),
        obj(5, b'<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>'),
        obj(6, f'<</CreationDate ({pdf_date()})/Producer (pausal-flow build_simple)>>'.encode()),
    ]
    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for o in objs:
        offsets.append(len(out))
        out += o
    xref = len(out)
    out += f'xref\n0 {len(objs) + 1}\n'.encode() + b'0000000000 65535 f \n'
    for off in offsets:
        out += f'{off:010d} 00000 n \n'.encode()
    out += (f'trailer\n<</Size {len(objs) + 1}/Root 1 0 R/Info 6 0 R>>\n'
            f'startxref\n{xref}\n%%EOF\n').encode()
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        open(path, 'wb').write(bytes(out))
    return bytes(out)


def read_ppm(path):
    d = open(path, 'rb').read()
    assert d[:2] == b'P6', f'{path}: не PPM'
    vals, i = [], 2
    while len(vals) < 3:
        while d[i:i+1].isspace():
            i += 1
        if d[i:i+1] == b'#':
            while d[i:i+1] not in (b'\n', b'\r'):
                i += 1
            continue
        j = i
        while not d[j:j+1].isspace():
            j += 1
        vals.append(int(d[i:j])); i = j
    i += 1
    w, h, _ = vals
    return w, h, d[i:i+w*h*3]


def hline_in_box(pdf, page, x0, x1, ytop, ybot, page_h, dpi=150, thr=200):
    """Y (снизу, pt) фактически нарисованной горизонтальной линии в окне.

    pdftotext отдаёт рамку слова-подчёркивания, а сама линия рисуется выше её низа
    на глубину нижнего выносного элемента шрифта (3–4 pt). Промах на эти пункты
    визуально заметен, поэтому линию находим по растру.
    """
    import tempfile, subprocess as sp, os as _os
    t = tempfile.mkdtemp()
    try:
        sp.run(['pdftoppm', '-r', str(dpi), '-f', str(page), '-l', str(page), pdf, t + '/p'],
               check=True, capture_output=True)
        f = [x for x in sorted(_os.listdir(t)) if x.endswith('.ppm')]
        assert f, 'pdftoppm не дал растр'
        w, h, px = read_ppm(_os.path.join(t, f[0]))
    finally:
        for x in _os.listdir(t):
            _os.remove(_os.path.join(t, x))
        _os.rmdir(t)
    k = dpi / 72.0
    rx0, rx1 = max(0, int(x0 * k)), min(w - 1, int(x1 * k))
    ry0, ry1 = max(0, int(ytop * k)), min(h - 1, int((ybot + 3) * k))
    best, best_cnt = None, 0
    for ry in range(ry0, ry1 + 1):
        row = ry * w * 3
        cnt = sum(1 for rx in range(rx0, rx1 + 1) if px[row + rx*3] < thr)
        if cnt > best_cnt:
            best, best_cnt = ry, cnt
    if best is None or best_cnt < (rx1 - rx0) * 0.5:
        return None
    return page_h - (best + 1) / k


def words(pdf, page=None):
    """Слова с координатами из pdftotext -bbox: [(page_idx, W, H, [(x0,y0,x1,y1,txt)])]."""
    cmd = ['pdftotext', '-bbox']
    if page:
        cmd += ['-f', str(page), '-l', str(page)]
    out = subprocess.run(cmd + [pdf, '-'], capture_output=True).stdout.decode('utf-8', 'replace')
    parts = re.split(r'<page width="([\d.]+)" height="([\d.]+)">', out)
    result = []
    for i in range(1, len(parts), 3):
        W, H = float(parts[i]), float(parts[i+1])
        ws = [(float(a), float(b), float(c), float(e), t) for a, b, c, e, t in
              re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" '
                         r'yMax="([\d.]+)">([^<]+)</word>',
                         parts[i+2])]
        result.append((len(result), W, H, ws))
    return result
