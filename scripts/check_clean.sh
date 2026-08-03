#!/usr/bin/env bash
# Движок не содержит ничего личного — тонкая обёртка над scripts/check_clean.py.
#
#   scripts/check_clean.sh
#
# Отдельный shell-вход остаётся потому, что его зовут пре-коммит и гейты: команда короткая
# и одинаковая во всех текстах. Вся логика — в питоне, там же и описание трёх проверок.
set -euo pipefail
exec python3 "$(dirname "$0")/check_clean.py" "$@"
