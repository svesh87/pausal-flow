#!/usr/bin/env bash
# Пересборка всего производного в правильном порядке.
#
#   scripts/rebuild_all.sh [--offline]
#
# Порядок обязателен: CSV операций — основа для реестра налогов, реестра платежей
# и отчёта; книги КПО читают файлы месяцев и курсы НБС. `--offline` передаётся в kpo_book.py
# и запрещает ему ходить в сеть за курсами.
#
# Он же — регрессионный тест репозитория: после правок в скриптах прогнать и убедиться,
# что `git diff` по сгенерированному пуст. Скрипты сами не переписывают то, что не изменилось,
# поэтому «пусто» — это именно «данные те же», а не «повезло».
#
# Движок и данные — разные каталоги (см. докблок `scripts/reqs.py`), поэтому скрипты
# запускаются по пути движка, а работают в корне данных. Корень данных берётся у `reqs.py`:
# правило поиска живёт в одном месте. Без данных — не ошибка: сообщаем и выходим нулём,
# чтобы гейты в чистом клоне движка были зелёными.
#
# Что здесь НЕ пересобирается: выгрузки в паушал (`pausal_import.py <месяц>` — по месяцу,
# и только после того, как пришла оплата) и метрики образцов подписи (`sig_metrics.py` —
# только при смене набора образцов).
set -euo pipefail
ENGINE=$(cd "$(dirname "$0")/.." && pwd)

if ! python3 "$ENGINE/scripts/reqs.py" --has-data; then
    echo "данных нет: не нашёл profile.json ни в TASKS_DATA, ни поиском вверх от $PWD."
    echo 'это чистый клон движка — пересобирать нечего. Как завести данные: docs/setup.md'
    exit 0
fi
DATA=$(python3 "$ENGINE/scripts/reqs.py" --data-root)
export TASKS_DATA="$DATA"
cd "$DATA"
echo "движок: $ENGINE"
echo "данные: $DATA"

echo '== операции из выписок =='
python3 "$ENGINE/scripts/parse_izvod.py"
echo '== реестр налогов =='
python3 "$ENGINE/scripts/tax_registry.py"
echo '== реестр платежей поставщикам =='
python3 "$ENGINE/scripts/payments_registry.py"
echo '== сводный отчёт =='
python3 "$ENGINE/scripts/build_report.py"
echo '== книги КПО =='
python3 "$ENGINE/scripts/kpo_book.py" "$@"

rm -rf "$ENGINE/scripts/__pycache__"
