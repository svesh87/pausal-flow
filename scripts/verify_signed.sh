#!/bin/bash
# Проверка подписанного документа. Обязательный шаг перед отправкой контрагенту или в банк.
#
#   scripts/verify_signed.sh <исходный.pdf> <подписанный.pdf> [--look]
#
# Проверяет: исходные байты не тронуты (дописано только в конец), документ рендерится без
# ошибок, страниц столько же, текст не потерян, /CreationDate не изменилась, /ModDate обновлён.
# С --look дополнительно раскладывает PNG-рендеры страниц во временный каталог и печатает путь,
# чтобы посмотреть область подписи глазами (каталог удалить самому).
#
# Признаки провала, которые это ловит: «Weird page contents» и пустая страница (неверно
# подключён контент-стрим), уехавший или помельчавший текст (не инвертирована остаточная
# матрица преобразования), потерянные элементы бланка (перезаписан /XObject вместо слияния).
set -u
in=${1:?исходный PDF}; out=${2:?подписанный PDF}; look=${3:-}
T=$(mktemp -d); rc=0

if cmp -n "$(stat -c%s "$in")" "$in" "$out" >/dev/null 2>&1; then
    echo "  байты исходника    : целы"
else
    echo "  байты исходника    : ИЗМЕНЕНЫ — это не инкрементальное обновление"; rc=1
fi

pdftoppm -r 60 "$in"  "$T/a" 2>"$T/ea"; pa=$(ls "$T"/a-*.ppm 2>/dev/null | wc -l)
pdftoppm -r 60 "$out" "$T/b" 2>"$T/eb"; pb=$(ls "$T"/b-*.ppm 2>/dev/null | wc -l)
errs=$(grep -c . "$T/eb")
if [ "$errs" = 0 ] && [ "$pb" = "$pa" ] && [ "$pb" != 0 ]; then
    echo "  рендер             : без ошибок, страниц $pb"
else
    echo "  рендер             : ОШИБКИ ($errs), страниц $pa -> $pb"; head -3 "$T/eb"; rc=1
fi

pdftotext "$in" "$T/a.txt" 2>/dev/null; pdftotext "$out" "$T/b.txt" 2>/dev/null
wa=$(wc -w <"$T/a.txt"); wb=$(wc -w <"$T/b.txt")
if [ "$wb" -ge "$wa" ]; then
    echo "  текст              : слов $wa -> $wb"
else
    echo "  текст              : ПОТЕРЯН ($wa -> $wb)"; rc=1
fi

ca=$(pdfinfo "$in"  2>/dev/null | sed -n 's/^CreationDate: *//p')
cb=$(pdfinfo "$out" 2>/dev/null | sed -n 's/^CreationDate: *//p')
mb=$(pdfinfo "$out" 2>/dev/null | sed -n 's/^ModDate: *//p')
ma=$(pdfinfo "$in"  2>/dev/null | sed -n 's/^ModDate: *//p')
[ "$ca" = "$cb" ] && echo "  CreationDate       : не изменилась ($cb)" \
                  || { echo "  CreationDate       : ИЗМЕНЕНА ($ca -> $cb)"; rc=1; }
[ "$mb" != "$ma" ] && echo "  ModDate            : обновлён ($mb)" \
                   || { echo "  ModDate            : НЕ ОБНОВЛЁН ($mb)"; rc=1; }

if [ "$look" = "--look" ]; then
    pdftoppm -png -r 150 "$out" "$T/look" 2>/dev/null
    rm -f "$T"/a-*.ppm "$T"/b-*.ppm
    echo "  рендеры для глаз   : $T/look-*.png  (после просмотра: rm -rf $T)"
else
    rm -rf "$T"
fi
[ $rc = 0 ] && echo "  ИТОГ               : ок" || echo "  ИТОГ               : ЕСТЬ ПРОБЛЕМЫ"
exit $rc
