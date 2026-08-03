#!/usr/bin/env bash
# Включить пре-коммит движка: он лежит в scripts/githooks и переживает клонирование,
# в отличие от .git/hooks. Достаточно выполнить один раз после клона.
#
#   scripts/install_hooks.sh
#
# Хук движка проверяет, что в движке нет ничего личного (scripts/check_clean.sh).
# У репозитория данных хук свой и другой — он не пускает в коммит открытые образцы
# подписи; его ставит scripts/init_private.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
git config core.hooksPath scripts/githooks
chmod +x scripts/githooks/*
echo "core.hooksPath = $(git config core.hooksPath)"
echo 'хук pre-commit включён: проверяет, что в движке нет реквизитов, имён и путей'
