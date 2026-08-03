#!/usr/bin/env python3
"""Диагностика структуры PDF — что именно надо патчить при вставке подписи.

    python3 scripts/pdf_probe.py file.pdf

Печатает: тип xref (таблица/поток), Root/Info, список страниц с номерами объектов,
формой /Contents (ref | массив-объект | инлайн-массив) и /Resources (инлайн | ref),
MediaBox и остаточную матрицу преобразования контента страницы.

Нужен, когда контрагент или банк меняет генератор документа и подписыватель падает:
сначала посмотреть, чем новый файл отличается, потом править якоря.
"""
import sys

from pdfobj import load, page_list, page_field, media_box, residual_ctm, page_streams


def main(path):
    doc = load(path)
    print(f'file        : {path}')
    print(f'xref        : {doc["xref_kind"]}, объектов в карте: {len(doc["xref"])}')
    print(f'trailer     : Root={doc["root"]} Info={doc["info"]} '
          f'Size={doc["size"]} Prev={doc["startxref"]}')
    pages = page_list(doc)
    print(f'страниц     : {len(pages)}')
    for i, pnum in enumerate(pages, 1):
        c = page_field(doc, pnum, b'Contents')
        r = page_field(doc, pnum, b'Resources')
        mb = media_box(doc, pnum)
        raw = b''.join(page_streams(doc, pnum))
        ctm = residual_ctm(raw)
        print(f'  стр.{i}: объект {pnum}')
        print(f'    Contents  : {c["kind"]}' + (f' -> объект {c["obj"]}' if c.get('obj') else '')
              + f'  потоков: {len(c["streams"])}')
        print(f'    Resources : {r["kind"]}' + (f' -> объект {r["obj"]}' if r.get('obj') else '')
              + f'  XObject: {"есть" if r["has_xobject"] else "нет"}')
        print(f'    MediaBox  : {mb}')
        print(f'    ост. CTM  : {[f"{v:.4f}" for v in ctm]}'
              + ('  (единичная)' if ctm == (1, 0, 0, 1, 0, 0) else '  <-- нужна инверсия'))


if __name__ == '__main__':
    main(sys.argv[1])
