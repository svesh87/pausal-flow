#!/usr/bin/env bash
# Скелет приватного репозитория данных: каталоги, конфиг, симлинки на движок, хук.
#
#   scripts/init_private.sh <каталог данных>
#
# Движок (этот репозиторий) — публичный: навыки, скрипты, документация. Данные — приватные:
# `profile.json`, папки месяцев, финансы, договоры, образцы подписи. Они живут в отдельном
# репозитории git, и связывает их симлинк `engine` внутри данных: физическое расположение
# движка знает только он, ни один коммитящийся файл о нём не знает.
#
# Скрипт идемпотентен: существующие каталоги, конфиг и симлинки не переписываются.
# Существующий репозиторий git он не трогает вообще — ни `git init`, ни `git config`
# в нём не делает, только докладывает, что осталось сделать руками.
set -euo pipefail

ENGINE=$(cd "$(dirname "$0")/.." && pwd)
TARGET=${1:?каталог данных: scripts/init_private.sh ~/docs/ip-data}
mkdir -p "$TARGET"
TARGET=$(cd "$TARGET" && pwd)

if [[ "$TARGET" == "$ENGINE" ]]; then
    echo 'каталог данных совпадает с каталогом движка — так нельзя: движок публичный' >&2
    exit 1
fi

echo "движок: $ENGINE"
echo "данные: $TARGET"

# --- каталоги -----------------------------------------------------------------
# Пустые каталоги git не хранит, но человеку нужно видеть, куда что кладётся.
for d in months contracts signatures docs tmp/inbox \
         finance/bank/raw/rsd finance/bank/raw/eur finance/payments \
         finance/taxes/statements finance/kpo; do
    mkdir -p "$TARGET/$d"
done

# --- конфиг -------------------------------------------------------------------
if [[ -e "$TARGET/profile.json" ]]; then
    echo 'profile.json: уже есть, не трогаю'
else
    cp "$ENGINE/profile.example.json" "$TARGET/profile.json"
    echo 'profile.json: скопирован из образца — заполнить своими реквизитами'
fi

# --- симлинки на движок -------------------------------------------------------
# Внутри данных движок всегда зовётся `engine/…`, поэтому переезд движка на другой путь
# сводится к перенаведению симлинка. Оба симлинка — в .gitignore.
if [[ -L "$TARGET/engine" || -e "$TARGET/engine" ]]; then
    echo "engine: уже есть -> $(readlink -f "$TARGET/engine")"
else
    # Относительный путь удобнее (данные и движок переезжают вместе), но
    # `realpath --relative-to` есть не везде — тогда кладём абсолютный.
    rel=$(realpath --relative-to="$TARGET" "$ENGINE" 2>/dev/null || echo "$ENGINE")
    ln -s "$rel" "$TARGET/engine"
    echo "engine -> $(readlink "$TARGET/engine")"
fi
mkdir -p "$TARGET/.claude"
if [[ -L "$TARGET/.claude/skills" || -e "$TARGET/.claude/skills" ]]; then
    echo '.claude/skills: уже есть, не трогаю'
else
    ln -s ../engine/.claude/skills "$TARGET/.claude/skills"
    echo '.claude/skills -> ../engine/.claude/skills'
fi

# --- .gitignore ---------------------------------------------------------------
if [[ -e "$TARGET/.gitignore" ]]; then
    echo '.gitignore: уже есть, не трогаю'
else
    cat > "$TARGET/.gitignore" <<'IGNORE'
# Движок — отдельный публичный репозиторий, подключён симлинком; в данных его не коммитим.
engine
.claude/

# Планы, черновики, приёмка документов и прогоны на копиях.
tmp/

# Образцы подписи хранятся только зашифрованными: в git идёт samples.tar.gz.gpg.
signatures/*.png
signatures/**/*.png

# Стоп-лист имён для проверки чистоты движка: сам файл перечисляет то, что ищет.
.check_clean.local

# Пересобираемое и служебное.
.cache/
*.ppm
*.pgm
__pycache__/
*.pyc
IGNORE
    echo '.gitignore: создан'
fi

# --- CLAUDE.md ----------------------------------------------------------------
if [[ -e "$TARGET/CLAUDE.md" ]]; then
    echo 'CLAUDE.md: уже есть, не трогаю'
else
    cat > "$TARGET/CLAUDE.md" <<'CLAUDE'
# Данные ИП (приватный репозиторий)

Здесь только данные: `profile.json`, `months/`, `finance/`, `contracts/`, `signatures/`,
`docs/history.md`. Навыки, скрипты и документация — в движке, он подключён симлинком
`engine` и живёт своим публичным репозиторием.

@engine/CLAUDE.md

## Местное

- скрипты запускаются по пути движка из корня данных: `python3 engine/scripts/<имя>.py`,
  пересборка — `engine/scripts/rebuild_all.sh`;
- гейты движка гоняются из каталога движка (`cd engine`), там же лежит `.venv`;
- правки движка коммитятся в его репозиторий, правки данных — в этот; два коммита,
  два репозитория;
- личная история форм документов — `docs/history.md`, журнал разобранных тревог —
  `finance/known_issues.md`, стоп-лист имён для проверки чистоты — `.check_clean.local`.
CLAUDE
    echo 'CLAUDE.md: создан'
fi

# --- хук ----------------------------------------------------------------------
mkdir -p "$TARGET/.githooks"
cat > "$TARGET/.githooks/pre-commit" <<'HOOK'
#!/usr/bin/env bash
# Пре-коммит репозитория данных: открытые образцы подписи в коммит не идут.
# Чистоту движка проверяет его собственный хук — здесь движка нет.
set -euo pipefail
staged=$(git diff --cached --name-only --diff-filter=AM | grep -E '^signatures/.*\.png$' || true)
if [[ -n "$staged" ]]; then
    echo 'ОТКРЫТЫЙ ОБРАЗЕЦ ПОДПИСИ В ИНДЕКСЕ: в git идёт только samples.tar.gz.gpg' >&2
    echo "$staged" | sed 's/^/  /' >&2
    exit 1
fi
HOOK
chmod +x "$TARGET/.githooks/pre-commit"
echo '.githooks/pre-commit: записан'

# --- git ----------------------------------------------------------------------
if [[ -d "$TARGET/.git" ]]; then
    echo 'git: репозиторий уже есть — не трогаю. Хук включается так:'
    echo "  git -C $TARGET config core.hooksPath .githooks"
else
    git -C "$TARGET" init -q
    git -C "$TARGET" config core.hooksPath .githooks
    echo 'git: репозиторий создан, хук включён'
fi

echo
echo 'дальше:'
echo "  1) заполнить $TARGET/profile.json (роли интеграций — docs/integrations.md)"
echo "  2) собрать контейнер образцов подписи — docs/signatures.md"
echo "  3) python3 $ENGINE/scripts/doctor.py  (запускать из каталога данных)"
