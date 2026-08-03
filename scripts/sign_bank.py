#!/usr/bin/env python3
"""Заполнение и подпись банковского «Obaveštenje o prilivu» (Alta Banka).

Использование:
    python3 scripts/sign_bank.py blank.pdf out.pdf invoice.pdf [--date DD.MM.YYYY]
                                [--place <место>] [--force]

Место издавања и прочие реквизиты — из `profile.json`; `--place` только переопределяет.
Готовый результат не перезаписывается без `--force`: подписанный документ уже
у контрагента.

Что делает:
- кросс-проверяет № фактуры и сумму: поля бланка (Po osnovu, Iznos) против
  инвойса клиента (INVOICE #, TOTAL) — при расхождении падает;
- находит поля по якорным надписям формы (pdftotext -bbox, на любой странице) —
  форма банка со временем меняется, фиксированных координат нет;
- заполняет таблицу PODACI ZA STATISTIKU (redni broj=1, šifra osnova=302,
  br./godina fakture, «Plaćanje po fakturi br. N», EUR-сумма), Mesto i datum;
- ставит случайную подпись из signatures/ над «Pečat i potpis korisnika naplate»;
- всё — инкрементальным обновлением: исходные байты бланка не меняются;
- сербское «ć» — внедрённый Liberation Sans (/Differences cacute).

Проверка результата — scripts/verify_signed.sh (см. .claude/skills/sign-docs/SKILL.md).
"""
import re, zlib, random, subprocess, sys, struct, os, glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sign_pdf import ink_bbox, png_obj, baseline_row, norm_scale
from pdfobj import pdf_date, ensure_new
import reqs
import signatures

TTF_PATH='/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'
CACUTE_CODE=230  # байт для ć в нашей кодировке

def ttf_metrics(path, chars):
    d=open(path,'rb').read()
    numTables=struct.unpack('>H',d[4:6])[0]
    tabs={}
    for i in range(numTables):
        off=12+i*16
        tag=d[off:off+4].decode('latin1')
        tabs[tag]=struct.unpack('>II',d[off+8:off+16])
    def tab(t): o,ln=tabs[t]; return d[o:o+ln]
    head=tab('head'); upm=struct.unpack('>H',head[18:20])[0]
    xMin,yMin,xMax,yMax=struct.unpack('>hhhh',head[36:44])
    hhea=tab('hhea'); asc,desc=struct.unpack('>hh',hhea[4:8])
    numH=struct.unpack('>H',hhea[34:36])[0]
    hmtx=tab('hmtx')
    cm=tab('cmap')
    n=struct.unpack('>H',cm[2:4])[0]
    sub=None
    for i in range(n):
        pid,eid,off=struct.unpack('>HHI',cm[4+i*8:12+i*8])
        if (pid,eid) in ((3,1),(0,3)):
            sub=off; break
    fmt=struct.unpack('>H',cm[sub:sub+2])[0]
    assert fmt==4
    segX2=struct.unpack('>H',cm[sub+6:sub+8])[0]
    ends=struct.unpack(f'>{segX2//2}H',cm[sub+14:sub+14+segX2])
    starts=struct.unpack(f'>{segX2//2}H',cm[sub+16+segX2:sub+16+segX2*2])
    deltas=struct.unpack(f'>{segX2//2}h',cm[sub+16+segX2*2:sub+16+segX2*3])
    rngOff_base=sub+16+segX2*3
    rngs=struct.unpack(f'>{segX2//2}H',cm[rngOff_base:rngOff_base+segX2])
    def gid(u):
        """Глиф по код-пойнту: cmap формата 4, ветки delta и idRangeOffset."""
        for i in range(len(ends)):
            if u<=ends[i]:
                if u<starts[i]: return 0
                if rngs[i]==0: return (u+deltas[i])&0xFFFF
                # idRangeOffset path
                addr = rngOff_base + i*2 + rngs[i] + (u-starts[i])*2
                g=struct.unpack('>H',cm[addr:addr+2])[0]
                return (g+deltas[i])&0xFFFF if g else 0
        return 0
    def adv(g):
        if g<numH: a=struct.unpack('>H',hmtx[g*4:g*4+2])[0]
        else: a=struct.unpack('>H',hmtx[(numH-1)*4:(numH-1)*4+2])[0]
        return a*1000//upm
    widths={c:adv(gid(ord(c))) for c in chars}
    return dict(upm=upm, bbox=[xMin*1000//upm,yMin*1000//upm,xMax*1000//upm,yMax*1000//upm],
                ascent=asc*1000//upm, descent=desc*1000//upm, widths=widths, data=d)

def enc_text(s):
    out=bytearray()
    for ch in s:
        if ch=='ć': out.append(CACUTE_CODE)
        else:
            b=ch.encode('latin1'); out+=b
    # escape PDF string
    res=bytearray()
    for b in out:
        if b in (0x28,0x29,0x5c): res+=b'\\'
        res.append(b)
    return bytes(res)

def _decode_stream(d, num):
    j=re.search(rb'\n'+str(num).encode()+rb'\s+0\s+obj',d).start()
    head=d[j:j+400]
    ln=int(re.search(rb'/Length\s+(\d+)',head).group(1))
    st=d.find(b'stream',j)+6
    while d[st] in (13,10): st+=1
    raw=d[st:st+ln]
    if b'FlateDecode' in head:
        raw=zlib.decompress(raw)
    return raw

def _mul(m,n):
    a,b,c,dd,e,f=m; A,B,C,D,E,F=n
    return (a*A+b*C, a*B+b*D, c*A+dd*C, c*B+dd*D, e*A+f*C+E, e*B+f*D+F)

def _inv(m):
    a,b,c,d,e,f=m
    det=a*d-b*c
    assert abs(det)>1e-9, 'вырожденная CTM'
    ia, ib, ic, idd = d/det, -b/det, -c/det, a/det
    ie = -(e*ia + f*ic)
    iff = -(e*ib + f*idd)
    return (ia,ib,ic,idd,ie,iff)

def residual_ctm(raw):
    """CTM, оставшаяся действующей после потока (cm на нулевой глубине q/Q)."""
    ctm=(1,0,0,1,0,0); depth=0
    cm_pat=(rb'(?<![A-Za-z])(q|Q)(?![A-Za-z])|'
            rb'([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)'
            rb'\s+cm(?![A-Za-z])')
    for m in re.finditer(cm_pat,raw):
        if m.group(1)==b'q': depth+=1
        elif m.group(1)==b'Q': depth=max(0,depth-1)
        elif depth==0:
            vals=tuple(float(m.group(i)) for i in range(2,8))
            ctm=_mul(vals,ctm)
    return ctm

def _read_ppm(path):
    data=open(path,'rb').read()
    assert data[:2]==b'P6'
    parts=[]; i=2
    while len(parts)<3:
        while data[i] in b' \t\r\n': i+=1
        if data[i:i+1]==b'#':
            while data[i] not in b'\r\n': i+=1
            continue
        j=i
        while data[j] not in b' \t\r\n': j+=1
        parts.append(int(data[i:j])); i=j
    i+=1
    w,h,_=parts
    return w,h,data[i:i+w*h*3]

def _raster(pdf,page,dpi=150):
    import tempfile
    tmp=tempfile.mkdtemp()
    base=os.path.join(tmp,'p')
    subprocess.run(['pdftoppm','-r',str(dpi),'-f',str(page),'-l',str(page),pdf,base],check=True)
    f=[x for x in sorted(glob.glob(base+'*.ppm'))][0]
    w,h,px=_read_ppm(f)
    return w,h,px,dpi

def _dark(px,w,x,y,thr=140):
    o=(y*w+x)*3
    return (px[o]+px[o+1]+px[o+2])//3 < thr

def table_geometry(pdf, page, page_h, header_bottom_pt, x_hint0, x_hint1):
    """Границы первой строки данных и вертикальные разделители — по линиям в растре.
    Возвращает (row_top_pt, row_bottom_pt, [border_x_pt...]) в координатах PDF (top-origin по Y)."""
    w,h,px,dpi=_raster(pdf,page)
    k=dpi/72.0
    y0=int((header_bottom_pt+1)*k); y1=int((header_bottom_pt+70)*k)
    x0=max(0,int((x_hint0-12)*k)); x1=min(w-1,int((x_hint1+12)*k))
    span=x1-x0+1
    hlines=[]
    for y in range(y0,min(y1,h)):
        cnt=sum(1 for x in range(x0,x1+1,2) if _dark(px,w,x,y))
        if cnt*2 >= span*0.75: hlines.append(y)
    groups=[]
    for y in hlines:
        if groups and y-groups[-1][-1]<=6: groups[-1].append(y)   # рамки двойные
        else: groups.append([y])
    assert len(groups)>=2, 'горизонтальные линии таблицы не найдены'
    rt=max(groups[0]); rb=min(groups[1])
    band0=int(rt)+2; band1=int(rb)-2
    bh=band1-band0+1
    assert bh>4, 'строка таблицы слишком узкая'
    vx=[]
    for x in range(x0,x1+1):
        cnt=sum(1 for y in range(band0,band1+1) if _dark(px,w,x,y))
        if cnt >= bh*0.8: vx.append(x)
    vgroups=[]
    for x in vx:
        if vgroups and x-vgroups[-1][-1]<=2: vgroups[-1].append(x)
        else: vgroups.append([x])
    borders=[sum(g)/len(g)/k for g in vgroups]
    return rt/k, rb/k, borders

def find_hline_above(pdf, page, label_word, dpi=150, thr=200):
    """Горизонтальная линия над подписью/местом-датой: (x0_pt, x1_pt, y_pt).
    Линии бланка бывают светло-серыми, поэтому порог мягче, чем для рамок таблицы."""
    w,h,px,dpi=_raster(pdf,page,dpi)
    k=dpi/72.0
    lx0,ly0,lx1,ly1,_=label_word
    cx=int((lx0+lx1)/2*k)
    ytop=int((ly0-30)*k); ybot=int(ly0*k)
    best=None
    for y in range(ybot,ytop,-1):
        if not _dark(px,w,cx,y,thr): continue
        xa=cx
        while xa>0 and _dark(px,w,xa-1,y,thr): xa-=1
        xb=cx
        while xb<w-1 and _dark(px,w,xb+1,y,thr): xb+=1
        if (xb-xa) > 40*k:
            best=(xa/k, xb/k, y/k); break
    assert best, 'линия над подписью не найдена'
    return best

def page_objects(d):
    """Объекты страниц в порядке документа: [(objnum, dict_bytes), ...]"""
    kids=None
    for m in re.finditer(rb'(\d+) 0 obj\s*<<(.*?)>>\s*endobj', d, re.S):
        b=m.group(2)
        if re.search(rb'/Type\s*/Pages',b):
            km=re.search(rb'/Kids\s*\[(.*?)\]',b,re.S)
            if km:
                kids=[int(x) for x in re.findall(rb'(\d+) 0 R',km.group(1))]
                break
    objs={}
    for m in re.finditer(rb'(\d+) 0 obj\s*<<(.*?)>>\s*endobj', d, re.S):
        if re.search(rb'/Type\s*/Page[^s]',m.group(2)+b' '):
            objs[int(m.group(1))]=m.group(2)
    if kids:
        return [(k,objs[k]) for k in kids if k in objs]
    return sorted(objs.items())

def get_words(pdf):
    out=subprocess.run(['pdftotext','-bbox',pdf,'-'],capture_output=True).stdout.decode()
    pages=re.split(r'<page width="([\d.]+)" height="([\d.]+)">',out)
    result=[]  # (page_idx, W, H, [(x0,y0,x1,y1,text)])
    idx=0
    for i in range(1,len(pages),3):
        W,H=float(pages[i]),float(pages[i+1])
        ws=[(float(a),float(b),float(c),float(e),t) for a,b,c,e,t in
            re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" '
                       r'yMax="([\d.]+)">([^<]+)</word>',pages[i+2])]
        result.append((idx,W,H,ws)); idx+=1
    return result

def find_word(ws, text, after=None):
    for w in ws:
        if w[4]==text and (after is None or w[1]>after):
            return w
    raise AssertionError(f'якорь "{text}" не найден — форма изменилась, нужен разбор руками')

def pdftext(path):
    return subprocess.run(['pdftotext',path,'-'],
                          capture_output=True).stdout.decode('utf-8','replace')

def sign_bank(blank, out_pdf, invoice_pdf, date_str=None, place=None, samples=None, rng=None,
              opis='Pla\u0107anje po fakturi br. {inv}', name=None, force=False):
    ensure_new(out_pdf, force)
    place = place or reqs.get('entrepreneur.issue_place')
    rng=rng or random.SystemRandom()
    # --- кросс-проверки ---
    bt=pdftext(blank)
    m=re.search(r'Po osnovu\s*\n?\s*.*?Invoice\s+([\w-]+)',bt,re.I)
    assert m, 'в бланке не найден номер инвойса (Po osnovu)'
    inv_no_bank=m.group(1)
    m=re.search(r'Iznos\s*\n?\s*EUR\s*([\d.,]+)',bt)
    assert m, 'в бланке не найдена сумма (Iznos)'
    amount_bank=m.group(1)
    it=pdftext(invoice_pdf)
    inv_no=re.search(r'INVOICE\s*#\s*(\S+)',it).group(1)
    inv_date=re.search(r'DATE\s+(\d{2}\.\d{2}\.(\d{4}))',it)
    inv_year=inv_date.group(2)
    m=re.search(r'TOTAL[\s\S]{0,40}?([\d][\d.,]*)',it)
    assert m, 'в инвойсе не найдена сумма TOTAL'
    total_inv=m.group(1)
    a1=amount_bank.replace(',','').replace('.00','')
    a2=total_inv.replace(',','')
    assert inv_no_bank==inv_no, \
        f'номер фактуры расходится: банк "{inv_no_bank}" vs инвойс "{inv_no}"'
    assert a1==a2, f'сумма расходится: банк "{amount_bank}" vs инвойс "{total_inv}"'
    if date_str is None:
        import datetime
        date_str=datetime.date.today().strftime('%d.%m.%Y')
    # --- якоря ---
    pages=get_words(blank)
    target=None
    for p in pages:
        if any('STATISTIKU' in w[4] for w in p[3]):
            target=p; break
    assert target, 'страница с таблицей PODACI ZA STATISTIKU не найдена'
    pi,W,H,ws=target
    # место/дата и подпись могут быть на другой странице (в старых бланках так и есть)
    sigpage=None
    for p in pages:
        if (any(w[4]=='Mesto' for w in p[3])
                and any(w[4].startswith('Pe') and 'at' in w[4] for w in p[3])):
            sigpage=p
    assert sigpage, 'страница с "Mesto i datum" / "Pečat i potpis" не найдена'
    spi,SW,SH,sws=sigpage
    redni=find_word(ws,'Redni')
    hdr_broj=find_word(ws,'broj',after=redni[1]-1)
    hdr_osn=find_word(ws,'osnova',after=redni[1]-1)
    hdr_tr=find_word(ws,'transakcije')
    hdr_iznos=[w for w in ws if w[4]=='Iznos' and abs(w[1]-hdr_tr[1])<6][0]
    lbl_mesto=find_word(sws,'Mesto')
    lbl_pecat=[w for w in sws if w[4].startswith('Pe') and 'at' in w[4]
               and abs(w[1]-lbl_mesto[1])<3][0]
    hdr_br=[w for w in ws if w[4]=='Br.' and abs(w[1]-hdr_tr[1])<12]
    hdr_god=[w for w in ws if w[4]=='Godina' and abs(w[1]-hdr_tr[1])<12]
    assert hdr_br and hdr_god, 'заголовки столбцов Br./Godina не найдены — форма изменилась'
    header_bottom=max(w[3] for w in (hdr_broj,hdr_osn,hdr_tr,hdr_iznos))
    row_top,row_bottom,borders=table_geometry(blank,pi+1,H,header_bottom,max(8,hdr_broj[0]-20),min(W-8,hdr_iznos[2]+140))
    assert len(borders)>=7, (f'ожидалось >=7 вертикальных разделителей, '
                             f'найдено {len(borders)}: форма изменилась')
    # клетку для каждого столбца определяем по центру его заголовка — устойчиво к
    # двойным рамкам и к перестановке столбцов в новых версиях бланка
    col_anchors=[hdr_broj,hdr_osn,hdr_br[0],hdr_god[0],hdr_tr,hdr_iznos]
    cells=[]
    for a in col_anchors:
        cx=(a[0]+a[2])/2
        cell=None
        for i in range(len(borders)-1):
            if borders[i]-0.5 <= cx <= borders[i+1]+0.5:
                cell=(borders[i],borders[i+1]); break
        assert cell, f'не найдена клетка для столбца "{a[4]}" (x={cx:.1f})'
        cells.append(cell)
    values=['1','302',inv_no,inv_year,opis.format(inv=inv_no),f'EUR {amount_bank}']
    sizes=[7.5,7.5,7.5,7.5,7.5,7.5]
    used=set(''.join(values))
    tm=ttf_metrics(TTF_PATH,used)
    def text_w(s,size):
        return sum(tm['widths'].get(ch,500) for ch in s)/1000.0*size
    row_mid=(row_top+row_bottom)/2
    table_fields=[]
    for (cx0,cx1),val,sz in zip(cells,values,sizes,strict=True):
        tw=text_w(val,sz)
        if tw > (cx1-cx0)-4:                      # не влезает — уменьшить кегль
            sz=max(5.5, sz*((cx1-cx0)-4)/tw); tw=text_w(val,sz)
        x=(cx0+cx1)/2 - tw/2
        y=H-(row_mid + sz*0.36)                   # baseline по центру строки
        table_fields.append((x,y,sz,val))
    # Mesto i datum — по центру линии над подписью (на своей странице)
    lx0,lx1,ly=find_hline_above(blank,spi+1,lbl_mesto)
    pd=f'{place}, {date_str}'
    sig_fields=[]
    used|=set(pd)
    if name: used|=set(name)
    tm=ttf_metrics(TTF_PATH,used)
    pw=text_w(pd,10.0)
    sig_fields.append(((lx0+lx1)/2-pw/2, SH-(ly-3.0), 10.0, pd))

    # --- PDF структура бланка ---
    d=open(blank,'rb').read()
    prev=int(d[d.rfind(b'startxref')+9:d.rfind(b'%%EOF')].strip())
    trailer=d[d.rfind(b'trailer'):d.rfind(b'startxref')]
    size=int(re.search(rb'/Size\s+(\d+)',trailer).group(1))
    root=re.search(rb'/Root\s+(\d+) 0 R',trailer).group(1).decode()
    info_m=re.search(rb'/Info\s+(\d+) 0 R',trailer)
    idm=re.search(rb'/ID\s*\[[^\]]*\]',trailer)
    pobjs=page_objects(d)
    assert len(pobjs)>=max(pi,spi)+1, 'страниц в PDF меньше, чем нашёл pdftotext'

    def page_info(idx):
        num,pdict=pobjs[idx]
        mres=re.search(rb'/Resources\s+(\d+) 0 R',pdict)
        assert mres, f'страница {idx+1}: inline /Resources — остановиться и разобрать руками'
        res_num=int(mres.group(1))
        jr=re.search(rb'\n?'+str(res_num).encode()+rb'\s+0\s+obj',d).start()
        rd=d[jr:d.find(b'endobj',jr)]
        res_inner=rd[rd.find(b'<<')+2:rd.rfind(b'>>')]
        mcont=re.search(rb'/Contents\s*\[?\s*((\d+) 0 R)',pdict)
        assert mcont, f'страница {idx+1}: /Contents не найден'
        ctm=residual_ctm(_decode_stream(d,int(mcont.group(2))))
        return dict(num=num, pdict=pdict, res_num=res_num, res_inner=res_inner,
                    cont_ref=mcont.group(1), cont_num=int(mcont.group(2)),
                    is_array=bool(re.search(rb'/Contents\s*\[',pdict)), ctm=ctm)

    # --- новые объекты ---
    n0=size
    n_font,n_fd,n_ff,n_img,n_sm=n0,n0+1,n0+2,n0+3,n0+4
    zff=zlib.compress(tm['data'],9)
    o_ff=(f'{n_ff} 0 obj\n<</Filter/FlateDecode/Length {len(zff)}'
          f'/Length1 {len(tm["data"])}>>\nstream\n').encode()+zff+b'\nendstream\nendobj\n'
    o_fd=(f'{n_fd} 0 obj\n<</Type/FontDescriptor/FontName/LiberationSans/Flags 32'
          f'/FontBBox[{tm["bbox"][0]} {tm["bbox"][1]} {tm["bbox"][2]} {tm["bbox"][3]}]'
          f'/ItalicAngle 0/Ascent {tm["ascent"]}/Descent {tm["descent"]}/CapHeight {tm["ascent"]}'
          f'/StemV 80/FontFile2 {n_ff} 0 R>>\nendobj\n').encode()
    widths=[]
    for code in range(32,256):
        ch='ć' if code==CACUTE_CODE else chr(code)
        widths.append(str(tm['widths'].get(ch,500)))
    o_font=(f'{n_font} 0 obj\n<</Type/Font/Subtype/TrueType/BaseFont/LiberationSans'
            f'/FirstChar 32/LastChar 255/Widths[{" ".join(widths)}]'
            f'/FontDescriptor {n_fd} 0 R'
            f'/Encoding<</Type/Encoding/BaseEncoding/WinAnsiEncoding'
            f'/Differences[{CACUTE_CODE} /cacute]>>'
            f'>>\nendobj\n').encode()
    # подпись
    samples=samples or signatures.load()
    sp=rng.choice(sorted(samples))
    (iw,ih,rgba),o_img,o_sm=png_obj(samples[sp],n_img,n_sm)
    ix0,iy0,ix1,iy1=ink_bbox(iw,ih,rgba)
    sx0,sx1,sy=find_hline_above(blank,spi+1,lbl_pecat)
    bl=baseline_row(iw,ih,rgba)
    # масштаб — тот же эталон, что и в акте клиента (см. signatures/METRICS.md)
    sc=norm_scale(ix1-ix0+1,iy1-iy0+1)*rng.uniform(0.97,1.03)
    ink_w=(ix1-ix0+1)*sc
    # если печатаем ФИО (так делали в старых бланках) — оно слева на линии,
    # подпись тогда центруется в оставшейся правой части линии
    zone0,zone1=sx0,sx1
    if name:
        nw=text_w(name,10.0)
        nx=sx0+4.0
        sig_fields.append((nx, SH-(sy-3.0), 10.0, name))
        zone0=nx+nw+8.0
    sig_x=(zone0+zone1)/2-ink_w/2-ix0*sc
    # базовая линия письма чуть ниже линии бланка — штрихи её пересекают
    sig_y=SH-(sy+rng.uniform(2.0,3.0))-(ih-1-bl)*sc

    # что рисуем на какой странице
    work={pi: dict(fields=list(table_fields), sig=None)}
    work.setdefault(spi, dict(fields=[], sig=None))
    work[spi]['fields']+=sig_fields
    work[spi]['sig']=(iw,ih,sc,sig_x,sig_y)

    objs=[(n_font,o_font),(n_fd,o_fd),(n_ff,o_ff),(n_img,o_img),(n_sm,o_sm)]
    next_num=n0+5
    patched_pages={}; patched_res={}
    for idx,item in sorted(work.items()):
        info=page_info(idx)
        cs=b'q\n'
        if any(abs(a-b)>1e-6 for a,b in zip(info['ctm'],(1,0,0,1,0,0),strict=True)):
            cs+=('{:.6f} {:.6f} {:.6f} {:.6f} {:.6f} {:.6f} cm\n'
                 .format(*_inv(info['ctm']))).encode()
        for x,y,size_pt,text in item['fields']:
            cs+=f'BT /FSIG {size_pt} Tf {x:.2f} {y:.2f} Td ('.encode()+enc_text(text)+b') Tj ET\n'
        if item['sig']:
            w_,h_,sc_,x_,y_=item['sig']
            cs+=f'q {w_*sc_:.2f} 0 0 {h_*sc_:.2f} {x_:.2f} {y_:.2f} cm /SigB Do Q\n'.encode()
        cs+=b'Q\n'
        n_cs=next_num; next_num+=1
        objs.append((n_cs,f'{n_cs} 0 obj\n<</Length {len(cs)}>>\nstream\n'.encode()
                     +cs+b'endstream\nendobj\n'))
        # /Contents: дописать ссылку (внутрь массива, если он массив)
        if info['is_array']:
            # n_cs привязывается аргументом по умолчанию: без этого лямбда взяла бы
            # значение переменной цикла на момент вызова, а не на момент создания
            newpd=re.sub(rb'(/Contents\s*\[[^\]]*)\]',
                         lambda mm, n=n_cs: mm.group(1)+f' {n} 0 R]'.encode(),
                         info['pdict'], count=1)
        else:
            newpd=info['pdict'].replace(
                b'/Contents '+info['cont_ref'],
                b'/Contents[ '+info['cont_ref']+f' {n_cs} 0 R]'.encode(),1)
        patched_pages[info['num']]=f'{info["num"]} 0 obj\n<<'.encode()+newpd+b'>>\nendobj\n'
        # ресурсы: шрифт и XObject (объект ресурсов может быть общим у страниц)
        ri=patched_res.get(info['res_num'])
        if ri is None:
            ri=info['res_inner']
            mfont=re.search(rb'/Font\s*<<',ri)
            if mfont: ri=ri[:mfont.end()]+f'/FSIG {n_font} 0 R '.encode()+ri[mfont.end():]
            else: ri+=f'/Font<</FSIG {n_font} 0 R>>'.encode()
            mx=re.search(rb'/XObject\s*<<',ri)
            if mx: ri=ri[:mx.end()]+f'/SigB {n_img} 0 R '.encode()+ri[mx.end():]
            else: ri+=f'/XObject<</SigB {n_img} 0 R>>'.encode()
            patched_res[info['res_num']]=ri
    for num,ri in patched_res.items():
        objs.append((num,f'{num} 0 obj\n<<'.encode()+ri+b'>>\nendobj\n'))
    for num,ob in patched_pages.items():
        objs.append((num,ob))

    # подписание — реальное изменение документа, поэтому обновляем /ModDate.
    # /CreationDate не трогаем: это когда банк создал бланк.
    if info_m:
        inum=int(info_m.group(1))
        ji=re.search(rb'\n?'+str(inum).encode()+rb'\s+0\s+obj',d).start()
        ib=d[ji:d.find(b'endobj',ji)]
        ib=ib[ib.find(b'<<')+2:ib.rfind(b'>>')]
        stamp=f'/ModDate ({pdf_date()})'.encode()
        if re.search(rb'/ModDate\s*\(',ib):
            ib=re.sub(rb'/ModDate\s*\((?:[^()\\]|\\.)*\)',stamp,ib,count=1)
        else:
            ib=ib+stamp
        objs.append((inum,f'{inum} 0 obj\n<<'.encode()+ib+b'>>\nendobj\n'))

    # --- сборка (инкрементальное обновление) ---
    out=bytearray(d)
    if out[-1:]!=b'\n': out+=b'\n'
    offs={}
    for num,ob in objs:
        offs[num]=len(out); out+=ob
    xref_off=len(out)
    x=b'xref\n'
    for num in sorted(offs):
        x+=f'{num} 1\n{offs[num]:010d} 00000 n \n'.encode()
    tr=f'trailer\n<</Size {next_num} /Root {root} 0 R '
    if info_m: tr+=f'/Info {info_m.group(1).decode()} 0 R '
    tr+=f'/Prev {prev} '
    x+=tr.encode()
    if idm: x+=idm.group(0)
    x+=f'>>\nstartxref\n{xref_off}\n%%EOF\n'.encode()
    out+=x
    open(out_pdf,'wb').write(bytes(out))
    return dict(invoice=inv_no, amount=amount_bank, year=inv_year, signature=os.path.basename(sp),
                place_date=f'{place}, {date_str}', opis=opis.format(inv=inv_no),
                pages=dict(table=pi+1, sign=spi+1), name=name or '')

if __name__=='__main__':
    args=[a for a in sys.argv[1:] if not a.startswith('--')]
    kw={}
    for i,a in enumerate(sys.argv):
        if a=='--date': kw['date_str']=sys.argv[i+1]
        if a=='--place': kw['place']=sys.argv[i+1]
        if a=='--opis': kw['opis']=sys.argv[i+1]
        if a=='--name': kw['name']=sys.argv[i+1]
    kw['force']='--force' in sys.argv
    print(sign_bank(args[0],args[1],args[2],**kw))
