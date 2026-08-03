#!/usr/bin/env python3
"""Выписки банка: приёмка PDF в finance/bank/raw/ и разбор операций в CSV.

    python3 scripts/parse_izvod.py --ingest DIR   # разложить свежие PDF по raw/
    python3 scripts/parse_izvod.py                # пересобрать transactions_*.csv

Номера счетов — в `profile.json` (`accounts.rsd`, `accounts.eur`), в коде их нет.

## Приёмка (--ingest)

Берёт PDF из DIR под любыми именами, по содержимому определяет счёт и дату и кладёт:

  finance/bank/raw/rsd/<год>/<динарский счёт>_YYYYMMDD.pdf   izvod за день
  finance/bank/raw/eur/<год>/<девизный счёт>_YYYYMMDD.pdf    izvod за день

Раскладка по годам: иначе в одной папке через пару лет под тысячу файлов. Одна дата
в имени — izvod за день (так банк шлёт почтой); выписка за период (выгрузка из eBank)
получает две даты `_YYYYMMDD_YYYYMMDD`. Файл, который уже лежит в raw (побайтово тот же),
молча пропускается; одинаковое имя с другим содержимым получает суффикс `_b`, `_c`, … —
обе версии остаются на диске. PDF не по счетам ИП (личные счета, счета поставщиков,
прочее) отбрасываются с пометкой.

## Разбор

Каждая операция — строка CSV: account,date,direction,amount,currency,counterparty,
counterparty_account,code,purpose,category,source_file. Дедупликация по совокупности
полей, поэтому пересечение периодов в raw безвредно.

Динарский izvod: суммы ловятся по колонкам, позиции которых берутся из строки шапки
«broj računa … zaduženje … odobrenje»; числа вне зоны колонок (даты, «Obr. naknada»,
курсы, poziv na broj) отбрасываются. Каждый файл сверяется с дневными итогами
«Ukupno dinara» — при расхождении скрипт падает, а не пишет мусор в CSV.

Девизный izvod: запись — блок от «N.» до следующего номера; первая строка блока несёт
динарскую пару (teret/korist), вторая — валютную; направление по валютной паре.

## Категории (их использует build_report.py)

  income     приход выручки: EUR — naplata от клиента; RSD — динарская
             противостоимость проданной валюты (šifra 286/393, OTKUP DEVIZA)
  fx         списание EUR с девизного счёта при продаже валюты (зеркало income RSD)
  tax        уплата налогов и взносов — счета публичных приходов 840-…
  supplier   оплата счетов поставщиков — по их счетам из profile.json
  bank_fee   провизии и наknade банка, наплата за карточку/ebank
  personal   перевод на личный счёт владельца
  cash       снятие наличных бизнес-картой: банкоматы банка (ATM) и партнёрская
             сеть MULTICARD
  card       покупки бизнес-картой (IKEA, Decathlon, …)
  founding   собственный взнос при открытии счёта — не выручка, в отчёт не идёт
  other      всё прочее (смотреть глазами и при необходимости учить категоризатор)
"""
import re, os, sys, csv, glob, shutil, hashlib, argparse, subprocess
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reqs  # noqa: E402

DATA = reqs.data_root()         # корень данных: finance/, months/, signatures/
RAW = os.path.join(DATA, 'finance', 'bank', 'raw')
RSD_ACC = reqs.get('accounts.rsd')
EUR_ACC = reqs.get('accounts.eur')
PERSONAL_ACC_DIGITS = reqs.get('accounts.personal_digits')
# Выгрузка «Promet po računu» печатает счёт 18 цифрами. Динарский в profile.json лежит
# ровно в этом виде, девизный — в коротком (`00-000-0000000.0`), поэтому его 18-значную
# форму берём из IBAN: два знака после кода страны — контрольные, дальше сам счёт.
ACC18 = {''.join(c for c in RSD_ACC if c.isdigit()): 'rsd',
         ''.join(c for c in reqs.get('accounts.eur_iban') if c.isdigit())[2:]: 'eur'}
# только цифры: в выписке номер счёта поставщика приходит без разделителей
SUPPLIER_ACCS = {''.join(c for c in s['account'] if c.isdigit()): s
                 for s in reqs.suppliers()}
# Служебные счета самого банка: у всех его клиентов одинаковые, реквизитами владельца
# не являются и потому живут в коде. Они же перечислены в `scripts/check_clean.allow` —
# иначе проверка по форме считала бы их утечкой. Появился новый — вписать и туда.
BANK_CARD_ACC = '190000000000007747'    # снятия и покупки бизнес-картой
BANK_FEE_ACC = '190000000000000666'     # провизии и накнаде
CSV_FIELDS = ['account', 'date', 'direction', 'amount', 'currency', 'counterparty',
              'counterparty_account', 'code', 'purpose', 'category', 'source_file']
# сумма: 1,234.56 — но не кусок даты 14.10.2024 и не 2024.10.15
NUM = re.compile(r'(?<![\d.,])\d{1,3}(?:,\d{3})*\.\d\d(?![.\d])')


def pdftext(path):
    r = subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True)
    assert r.returncode == 0, f'{path}: pdftotext не смог прочитать файл'
    return r.stdout.decode('utf-8', 'replace')


def amount(s):
    return Decimal(s.replace(',', ''))


def iso(d):
    dd, mm, yy = d.split('.')
    return f'{yy}-{mm}-{dd}'


# ---------------------------------------------------------------- приёмка

def classify_pdf(txt):
    """-> ('rsd'|'eur', [даты ISO]) либо (None, причина)."""
    if 'Promet po ra' in txt:
        digits = re.search(r'Broj ra\S*una:\s*(\d{15,18})', txt)
        per = re.search(r'Za period:\s*(\d{2}\.\d{2}\.\d{4})-(\d{2}\.\d{2}\.\d{4})', txt)
        assert digits and per, ('выгрузка «Promet po računu» без счёта или периода — '
                                'форма изменилась')
        kind = ACC18.get(digits.group(1))
        if kind is None:
            return None, f'«Promet po računu» по чужому счёту {digits.group(1)}'
        return kind, [iso(per.group(1)), iso(per.group(2))]
    if RSD_ACC in txt and 'IZVOD BR' in txt:
        m = re.search(r'NA RA\S*UNU DANA\s+(\d{2}\.\d{2}\.\d{4})', txt)
        assert m, 'динарский izvod без строки «ZA PROMENU … DANA DD.MM.YYYY» — форма изменилась'
        return 'rsd', [iso(m.group(1))]
    if EUR_ACC in txt and 'Izvod racuna' in txt:
        dates = sorted(iso(d) for d in
                       re.findall(r'^\s*\d{1,3}\.\s+\S.*?(\d{2}\.\d{2}\.\d{4})', txt, re.M))
        if not dates:
            m = re.search(r'Datum izrade izvoda:\s*(\d{2}\.\d{2}\.\d{4})', txt)
            assert m, 'девизный izvod без дат операций и без даты изготовления'
            dates = [iso(m.group(1))]
        return 'eur', dates
    return None, 'не похоже на izvod по счетам ИП из profile.json (динарский / девизный)'


def ingest(src_dir):
    taken = skipped = rejected = 0
    for p in sorted(glob.glob(os.path.join(src_dir, '**', '*.pdf'), recursive=True)):
        kind, dates = classify_pdf(pdftext(p))
        if kind is None:
            print(f'мимо : {p} — {dates}')
            rejected += 1
            continue
        acc = RSD_ACC if kind == 'rsd' else EUR_ACC
        d0, d1 = dates[0].replace('-', ''), dates[-1].replace('-', '')
        stem = f'{acc}_{d0}' + (f'_{d1}' if d1 != d0 else '')
        # раскладка по годам: иначе в одной папке через пару лет под тысячу файлов
        year_dir = os.path.join(RAW, kind, dates[0][:4])
        os.makedirs(year_dir, exist_ok=True)
        digest = hashlib.sha256(open(p, 'rb').read()).hexdigest()
        dst = None
        for suffix in ['', '_b', '_c', '_d', '_e']:
            cand = os.path.join(year_dir, f'{stem}{suffix}.pdf')
            if not os.path.exists(cand):
                dst = cand
                break
            if hashlib.sha256(open(cand, 'rb').read()).hexdigest() == digest:
                dst = None
                break
        else:
            raise AssertionError(f'{stem}: больше пяти разных файлов на одну дату — '
                                 'разобраться руками')
        if dst is None:
            skipped += 1
            continue
        shutil.copyfile(p, dst)
        print(f'взял : {os.path.relpath(dst, DATA)}')
        taken += 1
    print(f'итого: {taken} новых, {skipped} уже были, {rejected} не по счетам ИП')


# ---------------------------------------------------------------- разбор RSD

def parse_rsd(path):
    txt = pdftext(path)
    date = iso(re.search(r'NA RA\S*UNU DANA\s+(\d{2}\.\d{2}\.\d{4})', txt).group(1))
    lines = txt.splitlines()

    hdr = next((ln for ln in lines if 'broj ra' in ln and 'zadu' in ln and 'odobrenje' in ln), None)
    assert hdr, f'{path}: нет шапки колонок «broj računa … zaduženje … odobrenje»'
    z_pos, o_pos = hdr.index('zadu'), hdr.index('odobrenje')
    zone_lo, zone_hi = z_pos - 12, o_pos + len('odobrenje') + 14

    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == 'PROMENE')
        end = next(i for i, ln in enumerate(lines) if ln.strip().startswith('Ukupno za ra'))
    except StopIteration as e:
        raise AssertionError(
            f'{path}: не найдены границы секции PROMENE — форма изменилась') from e
    body = lines[start:end]

    # строки с номером налога — якоря записей
    anchors = [i for i, ln in enumerate(body) if re.match(r'^\s{1,8}\d{1,3}\s{2,}\S', ln)
               and not NUM.match(ln.strip())]
    if not anchors:
        # izvod без исполненных операций (бывают дни с одними Neizvršeni nalozi)
        tot0 = re.search(r'Ukupno dinara\s+([\d,]+\.\d\d)\s+([\d,]+\.\d\d)', txt)
        assert tot0 and amount(tot0.group(1)) == 0 and amount(tot0.group(2)) == 0, (
            f'{path}: в PROMENE не найдено ни одной записи, а итоги дня ненулевые')
        return []

    def entry_meta(ai):
        lo, hi = max(ai - 2, 0), min(ai + 5, len(body))
        window = body[lo:hi]
        name = body[ai - 1].strip() if ai >= 1 else ''
        name = re.split(r'\s{2,}', name)[0].strip(' ,')
        acc = ''
        for ln in window:
            m = re.search(r'\b\d{3}-\d{4,16}-\d{2}\b', ln)
            if m:
                acc = m.group(0)
                break
        code, svrha = '', ''
        for ln in window:
            m = re.search(r'-\s*:\s*-\s*(\d{3})\s*(.*)$', ln)
            if m:
                code = m.group(1)
                # хвост строки может быть колонкой «Podaci za reklamaciju» — длинное число
                svrha = re.sub(r'\s*\d{12,}\s*$', '', m.group(2)).strip()
                break
        return name, acc, code, svrha, re.sub(r'\s+', ' ', ' '.join(ln.strip() for ln in window))

    ops = []
    for i, ln in enumerate(body):
        if 'Ukupno' in ln:
            continue
        for m in NUM.finditer(ln):
            if not (zone_lo <= m.end() <= zone_hi):
                continue
            # число, принадлежащее «Obr. naknada: 0.00» или «Kurs: 115.00» на той же строке
            tail = ln[max(0, m.start() - 24):m.start()]
            if 'naknada:' in tail or 'Kurs' in tail:
                continue
            # значение разорванной «Obr. naknada:» на отдельной строке
            if ln.strip() == m.group(0):
                back = [b for b in body[max(i - 4, 0):i] if b.strip()]
                if any('naknada:' in b and not NUM.search(b.split('naknada:')[1]) for b in back):
                    continue
            amt = amount(m.group(0))
            if amt == 0:
                continue
            center = (m.start() + m.end()) / 2
            direction = 'debit' if center < (z_pos + len('zaduženje') + o_pos) / 2 else 'credit'
            ai = min(anchors, key=lambda a: abs(a - i))
            name, acc, code, svrha, blob = entry_meta(ai)
            ops.append(dict(account=RSD_ACC, date=date, direction=direction, amount=str(amt),
                            currency='RSD', counterparty=name, counterparty_account=acc,
                            code=code, purpose=svrha or blob[:200],
                            source_file=os.path.relpath(path, DATA), _blob=blob))

    tot = re.search(r'Ukupno dinara\s+([\d,]+\.\d\d)\s+([\d,]+\.\d\d)', txt)
    assert tot, f'{path}: не найдена строка итогов «Ukupno dinara»'
    want = (amount(tot.group(1)), amount(tot.group(2)))
    got = (sum((Decimal(o['amount']) for o in ops if o['direction'] == 'debit'), Decimal(0)),
           sum((Decimal(o['amount']) for o in ops if o['direction'] == 'credit'), Decimal(0)))
    assert got == want, (f'{path}: операции не сходятся с итогами дня: '
                         f'разобрано deb/cre {got[0]}/{got[1]}, в izvod-е {want[0]}/{want[1]}')
    return ops


# ---------------------------------------------------------------- разбор EUR

def parse_eur(path):
    txt = pdftext(path)
    ops = []
    lines = txt.splitlines()
    starts = [i for i, ln in enumerate(lines) if re.match(r'^\s{0,4}\d{1,3}\.\s+\S', ln)]
    stops = [i for i, ln in enumerate(lines) if 'Ukupni Promet' in ln or 'Novi saldo' in ln]
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else (min(stops) if stops else len(lines))
        block = lines[s:e]
        dm = re.search(r'(\d{2}\.\d{2}\.\d{4})\s+(\d{2}\.\d{2}\.\d{4})', block[0])
        assert dm, f'{path}: запись без пары дат в первой строке:\n{block[0]}'
        din = NUM.findall(block[0])
        val = NUM.findall(block[1]) if len(block) > 1 else []
        assert len(din) == 2 and len(val) == 2, (
            f'{path}: у записи не по две суммы в строках (дин {din}, вал {val}) — форма изменилась')
        teret, korist = amount(val[0]), amount(val[1])
        opis = re.sub(r'\s+', ' ', ' '.join(ln.strip() for ln in block))[:220]
        direction = 'credit' if korist > 0 else 'debit'
        ops.append(dict(account=EUR_ACC, date=iso(dm.group(1)), direction=direction,
                        amount=str(korist if korist > 0 else teret), currency='EUR',
                        counterparty='', counterparty_account='', code='',
                        purpose=opis, source_file=os.path.relpath(path, DATA)))
    m = re.search(r'Ukupni Promet:\s+([\d,]+\.\d\d)(?:\s+([\d,]+\.\d\d))?', txt)
    if m:
        want = sum(amount(g) for g in m.groups() if g)
        got = sum((Decimal(o['amount']) for o in ops), Decimal(0))
        assert got == want, f'{path}: сумма операций {got} не сходится с Ukupni Promet {want}'
    return ops


# ---------------------------------------------------------------- Promet po računu

PROMET_NUM = re.compile(r'(?<![\d.,])(?:\d{1,3}(?:,\d{3})*\.\d\d|0,00)(?![.\d])')


def parse_promet(path):
    """Выгрузка eBank «Promet po računu» — используется только как заполнитель дней,
    не покрытых почтовыми izvod-ами (rebuild отбрасывает пересечения)."""
    txt = pdftext(path)
    kind, _ = classify_pdf(txt)
    account, currency = (RSD_ACC, 'RSD') if kind == 'rsd' else (EUR_ACC, 'EUR')
    lines = txt.splitlines()
    starts = [i for i, ln in enumerate(lines)
              if re.match(r'^\s{0,6}\d{1,4}\s+\d{2}\.\d{2}\.\d{4}\s', ln)]
    ops = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(lines)
        block = [ln for ln in lines[s:e] if ln.strip() and 'Value Date' not in ln]
        first = block[0]
        date = iso(re.search(r'(\d{2}\.\d{2}\.\d{4})', first).group(1))
        nums = PROMET_NUM.findall(first)
        assert len(nums) >= 2, f'{path}: у записи не видно пары сумм:\n{first}'
        deb, cre = (Decimal('0') if v == '0,00' else amount(v) for v in nums[-2:])
        blob = re.sub(r'\s+', ' ', ' '.join(ln.strip() for ln in block))
        acc = ''
        for m in re.finditer(r'\b((?:190|200|840)\d{11,15})\b', blob):
            acc = m.group(1)
            break
        for direction, amt in (('debit', deb), ('credit', cre)):
            if amt == 0:
                continue
            ops.append(dict(account=account, date=date, direction=direction, amount=str(amt),
                            currency=currency, counterparty='', counterparty_account=acc,
                            code='', purpose=blob[:220],
                            source_file=os.path.relpath(path, DATA), _blob=blob))
    return ops


# ---------------------------------------------------------------- категории

def categorize(op):
    p = (op.get('_blob', '') + ' ' + op['purpose'] + ' ' + op['counterparty']).upper()
    acc = re.sub(r'\D', '', op['counterparty_account'])  # счета сравниваем без дефисов
    if op['currency'] == 'EUR':
        if op['direction'] == 'credit':
            return 'income' if ('NALOG ZA NAPLATU' in p or 'CODIFICACION' in p
                                or 'INFORMACIONE USLUGE' in p) else 'other'
        return 'fx' if 'OTKUP' in p or 'PRODAJ' in p or 'DEVIZNI NALOG' in p else 'other'
    # сначала правила по счёту получателя — окно контекста может цеплять соседнюю
    # запись izvod-а, и ключевые слова из неё не должны перебивать точный счёт
    if acc.startswith('840'):
        return 'tax'
    if acc in SUPPLIER_ACCS:
        return 'supplier'
    if acc == PERSONAL_ACC_DIGITS:
        return 'personal'
    # оплата картой на портале eUprave (CHIP CARD_EUPRAVA) — налог через LPA-портал,
    # в сумме сидит комиссия портала 50 RSD
    if 'EUPRAVA' in p:
        return 'tax'
    # ATM — банкоматы банка, MULTICARD — партнёрская сеть банкоматов: и то и то наличные
    if acc == BANK_CARD_ACC and op['direction'] == 'debit':
        return 'cash' if 'ATM' in p or 'MULTICARD' in p else 'card'
    if op['direction'] == 'credit' and ('PROTIVVREDNOST' in p or 'OTKUP' in p):
        return 'income'
    # собственный взнос при открытии счёта: приход со счёта самого банка по ссылке
    # на договор об открытии. Не выручка и не прочее — отдельная категория, чтобы
    # `other: 0` оставался честным признаком «ничего неизвестного не появилось»
    if op['direction'] == 'credit' and 'PRILIV PO REF' in p:
        return 'founding'
    if acc == BANK_FEE_ACC:
        return 'bank_fee'
    # ключевые слова — только когда счёт ничего не решил: у поставщика мог поменяться
    # счёт, а имя в назначении остаётся (см. former_name в profile.json)
    if any(w and w.upper() in p
           for sup in reqs.suppliers()
           for w in (sup['slug'], *sup['name'].split(), *sup.get('former_name', '').split())
           if len(w) > 4):
        return 'supplier'
    if op['direction'] == 'debit' and 'KARTICA' in p:
        return 'cash' if 'ATM' in p or 'MULTICARD' in p else 'card'
    if 'PROVIZ' in p or 'NAKNADA' in p or 'NAPLATA KARTICE' in p or 'EBANK' in p:
        return 'bank_fee'
    return 'other'


def ops_between(kind, after, before):
    """Операции из собранного CSV за дни строго между двумя izvod-ами.

    Возвращает (сальдо, дни). Сальдо со знаком: credit плюсом, debit минусом."""
    p = os.path.join(DATA, 'finance', 'bank', f'transactions_{kind}.csv')
    if not os.path.exists(p):
        return None, []
    total, days = Decimal('0'), set()
    with open(p, newline='') as f:
        for r in csv.DictReader(f):
            if after < r['date'] < before:
                total += amount(r['amount']) * (1 if r['direction'] == 'credit' else -1)
                days.add(r['date'])
    return total, sorted(days)


def classify_gap(kind, a, b, filled, days):
    """Описание разрыва цепочки: закрыт он добором или нет.

    `a`, `b` — соседние izvod-ы (дата, номер, prethodno, novo, файл). Закрыт, если
    `novo` предыдущего плюс сальдо операций между ними даёт `prethodno` следующего:
    значит не хватает только PDF, а операции в CSV есть. `filled=None` — CSV ещё нет,
    судить не о чем, считаем открытым.
    """
    return dict(kind=kind, after=a[0], before=b[0], izvod_a=a[1], izvod_b=b[1],
                novo=str(a[3]), prethodno=str(b[2]), delta=str(b[2] - a[3]),
                closed=filled is not None and a[3] + filled == b[2],
                filled=None if filled is None else str(filled), days=days)


def balance_chain():
    """Сверка непрерывности остатков: novo stanje каждого izvod-а должно совпадать
    с prethodno stanje следующего.

    Дырка в нумерации сама по себе не беда: izvod за пропущенный день может быть
    восполнен выгрузкой «Promet po računu», и тогда операции в CSV есть, а PDF нет.
    Поэтому каждый разрыв проверяется арифметикой: `novo` + сальдо операций
    за дни между izvod-ами = `prethodno` следующего. Сошлось — разрыв **закрыт**,
    данные полные, звать пользователя не за чем. Не сошлось — открытый разрыв,
    в CSV действительно не хватает операций, лечится выгрузкой периода из eBank."""
    chains = {'rsd': [], 'eur': []}
    period = re.compile(r'_\d{8}_\d{8}(_[a-z])?\.pdf$')  # выгрузки за период — не izvod-ы
    for p in sorted(glob.glob(os.path.join(RAW, 'rsd', '*', '*.pdf'))):
        if period.search(p):
            continue
        txt = pdftext(p)
        no = re.search(r'IZVOD BR\.\s*(\d+)', txt)
        date = iso(re.search(r'NA RA\S*UNU DANA\s+(\d{2}\.\d{2}\.\d{4})', txt).group(1))
        stanje = re.search(r'^\s+([\d,]+\.\d\d)\s+([\d,]+\.\d\d)\s+([\d,]+\.\d\d)'
                           r'\s+([\d,]+\.\d\d)\s+\d+',
                           txt, re.M)
        assert stanje, f'{p}: не найдена строка STANJE — форма изменилась'
        chains['rsd'].append((date, int(no.group(1)), amount(stanje.group(1)),
                              amount(stanje.group(4)), p))
    for p in sorted(glob.glob(os.path.join(RAW, 'eur', '*', '*.pdf'))):
        if period.search(p):
            continue
        txt = pdftext(p)
        no = re.search(r'Broj izvoda:\s*(\d+)', txt)
        prev = re.search(r'Prethodni saldo u valuti:?\s+([\d,]+\.\d\d)', txt)
        new = re.search(r'Novi saldo u valuti:\s+([\d,]+\.\d\d)', txt)
        dm = re.search(r'Datum izrade izvoda:\s*(\d{2}\.\d{2}\.\d{4})', txt)
        if not (prev and new):
            continue
        chains['eur'].append((iso(dm.group(1)), int(no.group(1)), amount(prev.group(1)),
                              amount(new.group(1)), p))
    gaps, spans = [], {}
    for kind, rows in chains.items():
        rows.sort()
        for a, b in zip(rows, rows[1:], strict=False):
            if a[3] != b[2]:
                filled, days = ops_between(kind, a[0], b[0])
                gaps.append(classify_gap(kind, a, b, filled, days))
        if rows:
            spans[kind] = dict(first=rows[0][0], opening=str(rows[0][2]),
                               last=rows[-1][0], closing=str(rows[-1][3]))
    return gaps, spans


def print_balance_chain():
    gaps, spans = balance_chain()
    for g in gaps:
        head = (f'{g["after"]} (izvod {g["izvod_a"]}, novo {g["novo"]}) -> '
                f'{g["before"]} (izvod {g["izvod_b"]}, prethodno {g["prethodno"]}), '
                f'дельта {g["delta"]}')
        if g['closed']:
            print(f'пропуск {g["kind"]} ЗАКРЫТ: {head} — izvod-а нет, но операции за '
                  f'{", ".join(g["days"])} добраны из выгрузки, сальдо сходится')
        else:
            print(f'РАЗРЫВ {g["kind"]}: {head} — не хватает izvod-а, в CSV нет операций '
                  f'на {g["delta"]}' + (f' (добрано {g["filled"]})' if g['filled'] else ''))
    for kind, s in spans.items():
        open_gaps = [g for g in gaps if g['kind'] == kind and not g['closed']]
        closed = sum(1 for g in gaps if g['kind'] == kind and g['closed'])
        state = 'ЕСТЬ РАЗРЫВЫ' if open_gaps else 'непрерывен'
        if closed and not open_gaps:
            state += f' (пропусков izvod-ов: {closed}, все закрыты добором)'
        print(f'баланс {kind}: {state}, '
              f'{s["first"]} (входящий {s["opening"]}) .. {s["last"]} (исходящий {s["closing"]})')


def rebuild():
    rows, seen = [], set()
    covered = {'rsd': set(), 'eur': set()}  # даты, покрытые дневными izvod-ами
    promet_files = {'rsd': [], 'eur': []}

    def add(op):
        key = (op['account'], op['date'], op['direction'], op['amount'],
               op['purpose'], op['counterparty_account'])
        if key in seen:
            return
        seen.add(key)
        op['category'] = categorize(op)
        op.pop('_blob', None)
        rows.append(op)

    for kind, parser in (('rsd', parse_rsd), ('eur', parse_eur)):
        for p in sorted(glob.glob(os.path.join(RAW, kind, '*', '*.pdf'))):
            # файл с двумя датами в имени — выгрузка за период, разберём вторым проходом
            if re.search(r'_\d{8}_\d{8}(_[a-z])?\.pdf$', p):
                promet_files[kind].append(p)
                continue
            for op in parser(p):
                covered[kind].add(op['date'])
                add(op)

    # выгрузки за период — только заполнитель дней, не покрытых дневными izvod-ами.
    # даты в выгрузке — валютирования, в izvod-е — книжения (могут расходиться на
    # 1–3 дня), поэтому вторая сетка от двойного счёта — та же операция рядом по дате
    import datetime
    near = {}
    for op in rows:
        near.setdefault((op['account'], op['direction'], op['amount']), []).append(op['date'])

    def is_dup(op):
        if op['date'] in covered[{'RSD': 'rsd', 'EUR': 'eur'}[op['currency']]]:
            return True
        d0 = datetime.date.fromisoformat(op['date'])
        for d in near.get((op['account'], op['direction'], op['amount']), []):
            if abs((datetime.date.fromisoformat(d) - d0).days) <= 5:
                return True
        return False

    for files in promet_files.values():
        for p in files:
            fresh = [op for op in parse_promet(p) if not is_dup(op)]
            for op in fresh:
                add(op)
            if fresh:
                days = sorted({op['date'] for op in fresh})
                print(f'{os.path.relpath(p, DATA)}: добор {len(fresh)} операций '
                      f'за непокрытые дни {", ".join(days)}')
    for cur, name in (('EUR', 'transactions_eur.csv'), ('RSD', 'transactions_rsd.csv')):
        out = os.path.join(DATA, 'finance', 'bank', name)
        sel = sorted((r for r in rows if r['currency'] == cur),
                     key=lambda r: (r['date'], r['source_file']))
        with open(out, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            w.writerows(sel)
        cats = {}
        for r in sel:
            cats[r['category']] = cats.get(r['category'], 0) + 1
        print(f'{os.path.relpath(out, DATA)}: {len(sel)} операций, категории {cats}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ingest', metavar='DIR', help='разложить свежие PDF из DIR по bank/raw/')
    a = ap.parse_args()
    if a.ingest:
        ingest(a.ingest)
    rebuild()
    print_balance_chain()


if __name__ == '__main__':
    main()
