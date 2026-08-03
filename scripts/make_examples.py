#!/usr/bin/env python3
"""Синтетический пример: дерево данных из выдуманных чисел плюс демо-акт.

    python3 scripts/make_examples.py [каталог]      # по умолчанию tmp/example-data

Зачем: в чистом клоне движка нет ни одного документа, и понять, как выглядят данные
и что вообще делают скрипты, не на чем. Этот скрипт собирает маленькое дерево данных —
конфиг из образца, файл месяца с реестром, операции в CSV, кэш курса — на котором
реестры, сводный отчёт и книга КПО собираются целиком и без сети.

Что в примере **не** воспроизводится:

- разбор выписок банка (`parse_izvod.py`): его вход — PDF конкретного банка с конкретной
  вёрсткой; поддельная выписка проверяла бы не парсер, а саму подделку. Поэтому CSV операций
  здесь записан напрямую, а `parse_izvod.py` на примере не запускается — он бы затёр CSV
  нулём операций;
- подпись (`sign_pdf.py`): нужны образцы подписи владельца, а их в движке нет и быть
  не должно. Демо-акт с двумя строками подписи скрипт всё же кладёт: если контейнер
  образцов уже собран, на нём видно, как подпись встаёт в документ;
- выгрузка в бухгалтерский сервис (`pausal_import.py`): она сверяет номер, дату и сумму
  по трём источникам — реестру месяца, инвойсу и бланку банка, — и без настоящих
  PDF сверять ей нечего.

Каталог примера перезаписывается целиком при каждом запуске — это черновик, а не данные.
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdfobj  # noqa: E402
import reqs  # noqa: E402

MONTH = '2026_06'
CSV_HEADER = ('account,date,direction,amount,currency,counterparty,counterparty_account,'
              'code,purpose,category,source_file')
# Курс в кэше нужен на дату счёта: книга КПО пересчитывает доход в динары по среднему курсу
# НБС на эту дату и без курса честно падает, а не берёт соседний день.
RATE_DATE = '2026-07-01'
RATE = 117.2

TASKS_MD_HEAD = f"""# Задачи за {MONTH}

Пример: выдуманный месяц, выдуманные задачи. Показывает форму файла месяца — из него
берут данные `pausal_import.py` (выгрузка в бухгалтерский сервис) и `kpo_book.py` (книга
оборота), поэтому важны не тексты, а структура секций и формат строк реестра.

Окно сборки: 2026-06-01 — 2026-06-30

## Как отправлено

- OPS-1042 - Ограничить SSH подключения между дата-центрами
- OPS-1187 - Зачистить освободившиеся сервера и отказаться от них
- SUP-2381 - Отбить DDOS на сервис DNS
- SUP-2404 - Починить инструмент для проверки бэкапов баз PostgreSQL
"""
# Формулировки в акте — по секции на каждый язык из конфига. Языки здесь не перечислены
# по той же причине, что и в навыке: их набор — свойство клиента, а не системы. Пример
# честно показывает форму секции и то, что содержимое надо перевести самому.
ACT_SECTION = """
## Формулировки в акте ({lang})

- OPS-1042 - <перевод: ограничить SSH подключения между дата-центрами>
- OPS-1187 - <перевод: зачистить освободившиеся сервера>
- SUP-2381 - <перевод: отбить DDOS на сервис DNS>
- SUP-2404 - <перевод: починить инструмент проверки бэкапов>
"""
REGISTRY = """
## Реестр

- Инвойс: № 26-7-DEMO от 01.07.2026 (`invoice1.pdf`)
- Оплата: 03.07.2026, EUR 1,000.00 (`Obavestenje o prilivu DEMO.pdf`)
- Акт: `acceptance_1.pdf` → `acceptance_1_signed.pdf`
"""

def rows(example):
    """Синтетические операции: имена и счета — из `profile.example.json`.

    Ровно те же заглушки, что скрипт кладёт в `profile.json` примера: иначе разбивка
    по поставщикам в отчёте не сойдётся с конфигом, а в движке появилась бы вторая копия
    имён, которая при правке образца молча разойдётся с первой.
    """
    supplier = (example.get('suppliers') or [{}])[0]
    sup_name = supplier.get('name', '')
    sup_acc = supplier.get('account', '')
    client = example['client']['name']
    rsd = [
        'RSD,2026-07-03,credit,117200.00,RSD,,,286,PROTIVVREDNOST DEMO,income,demo.pdf',
        f'RSD,2026-07-05,debit,3000.00,RSD,{sup_name},{sup_acc},,zakup,supplier,demo.pdf',
        'RSD,2026-07-10,debit,1500.00,RSD,,840000071112284332,,porez demo,tax,demo.pdf',
        'RSD,2026-07-10,debit,250.00,RSD,,190000000000000666,,provizija,bank_fee,demo.pdf',
    ]
    eur = [f'EUR,2026-07-03,credit,1000.00,EUR,{client},,,naplata demo,income,demo.pdf']
    return rsd, eur


def example_profile():
    """Образец конфига — источник всех заглушек примера."""
    with open(os.path.join(reqs.CODE_ROOT, 'profile.example.json')) as f:
        return json.load(f)


def signer_name(example):
    """Имя, которое печатается у строк подписи демо-акта.

    Если данные уже есть — имя владельца из его конфига: тогда `sign_pdf.py` найдёт
    строки подписи и демо-акт можно подписать по-настоящему. Если данных нет —
    заглушка из образца, и демо-акт остаётся просто иллюстрацией формы.
    """
    return reqs.get('entrepreneur.name') if reqs.has_data() \
        else example['entrepreneur']['name']


def acceptance_pdf(path, name):
    """Демо-акт: две строки подписи, как в настоящем акте — исполнитель и заказчик."""
    pdfobj.build_simple(path, [
        ('ACCEPTANCE OF SERVICES No 1 (demo)', 72, 780),
        ('Period: June 2026', 72, 760),
        ('1. Restrict SSH connections between data centers', 72, 720),
        ('2. Decommission released servers', 72, 700),
        ('3. Mitigate DDOS on the DNS service', 72, 680),
        ('4. Fix the database backup verification tool', 72, 660),
        ('_' * 20, 72, 560),
        (name, 72, 545),
        ('_' * 20, 330, 560),
        (name, 330, 545),
    ])


def build(target):
    if os.path.exists(target):
        shutil.rmtree(target)
    os.makedirs(os.path.join(target, 'months', MONTH))
    os.makedirs(os.path.join(target, 'finance', 'bank'))
    os.makedirs(os.path.join(target, '.cache'))

    example = example_profile()
    shutil.copyfile(os.path.join(reqs.CODE_ROOT, 'profile.example.json'),
                    os.path.join(target, 'profile.json'))
    langs = example['client'].get('act_languages') or ['en']
    tasks_md = TASKS_MD_HEAD + ''.join(ACT_SECTION.format(lang=lang.upper())
                                       for lang in langs) + REGISTRY
    with open(os.path.join(target, 'months', MONTH, 'tasks.md'), 'w') as f:
        f.write(tasks_md)
    rsd, eur = rows(example)
    for csv_name, csv_rows in (('transactions_rsd.csv', rsd), ('transactions_eur.csv', eur)):
        with open(os.path.join(target, 'finance', 'bank', csv_name), 'w') as f:
            f.write('\n'.join([CSV_HEADER, *csv_rows]) + '\n')
    with open(os.path.join(target, '.cache', 'nbs_rates.json'), 'w') as f:
        json.dump({RATE_DATE: RATE}, f)

    name = signer_name(example)
    acceptance_pdf(os.path.join(target, 'months', MONTH, 'acceptance_1.pdf'), name)
    return name


def main():
    target = os.path.abspath(sys.argv[1] if len(sys.argv) > 1
                             else os.path.join(os.getcwd(), 'tmp', 'example-data'))
    name = build(target)
    rel = os.path.relpath(target, os.getcwd())
    print(f'пример собран: {rel}')
    print('  профиль из profile.example.json, месяц с реестром, операции в CSV, курс в кэше')
    print()
    print('что на нём запускается (parse_izvod.py — нет, ему нужны настоящие выписки):')
    for cmd in ('scripts/tax_registry.py', 'scripts/payments_registry.py',
                'scripts/build_report.py', 'scripts/kpo_book.py --offline'):
        print(f'  TASKS_DATA={rel} python3 {cmd}')
    print()
    print('чего в примере нет: выгрузка в бухгалтерский сервис (pausal_import.py) сверяет')
    print('  номер, дату и сумму по трём документам — инвойсу, бланку банка')
    print('  и реестру месяца, — так что без настоящих PDF ей нечего сверять')
    print()
    print(f'демо-акт с двумя строками подписи на имя «{name}»: '
          f'{rel}/months/{MONTH}/acceptance_1.pdf')
    if reqs.has_data():
        print('  контейнер образцов есть — можно подписать по-настоящему:')
        print(f'  python3 scripts/sign_pdf.py {rel}/months/{MONTH}/acceptance_1.pdf '
              f'{rel}/months/{MONTH}/acceptance_1_signed.pdf')
    else:
        print('  подписать пока нечем: образцов подписи в движке нет — см. docs/signatures.md')


if __name__ == '__main__':
    main()
