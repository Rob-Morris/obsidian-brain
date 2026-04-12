#!/usr/bin/env bash
#
# install.sh — Create a new Obsidian Brain vault.
#
# One-liner:
#   bash <(curl -fsSL https://raw.githubusercontent.com/robmorris/obsidian-brain/main/install.sh)
#
# From a clone:
#   bash install.sh ~/my-brain
#   bash install.sh              # prompts for path
#
# Uninstall:
#   bash install.sh --uninstall ~/my-brain
#
# What it does:
#   1. Clones the repo to a temp directory (or uses existing clone)
#   2. Copies template-vault to your chosen location
#   3. Copies brain-core into the vault as .brain-core
#   4. Installs Python dependencies into a vault-local .venv
#   5. Registers the Brain MCP server for Claude Code
#
# Requirements: git, python3 (3.10+ for MCP server, 3.9+ otherwise)
# Safe to re-run with a new path.

set -euo pipefail

# ---------------------------------------------------------------------------
# Stdin check — detect curl | bash (stdin is not a terminal)
# ---------------------------------------------------------------------------

if [ ! -t 0 ] && [ "${1:-}" != "--force" ] && [ "${1:-}" != "-f" ]; then
    printf '\033[31mError: stdin is not a terminal.\033[0m\n' >&2
    printf '\n' >&2
    printf '  It looks like you piped this script (curl ... | bash).\n' >&2
    printf '  Use this instead:\n' >&2
    printf '\n' >&2
    printf '    bash <(curl -fsSL https://raw.githubusercontent.com/robmorris/obsidian-brain/main/install.sh)\n' >&2
    printf '\n' >&2
    printf '  Or pass --force to skip all prompts:\n' >&2
    printf '\n' >&2
    printf '    bash <(curl -fsSL https://raw.githubusercontent.com/robmorris/obsidian-brain/main/install.sh) --force ~/brain\n' >&2
    printf '\n' >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()  { printf '  %s\n' "$*" >&2; }
step()  { printf '\n\033[1m▸ %s\033[0m\n' "$*" >&2; }
err()   { printf '\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }

# Parse --force/-f flag and positional path from arguments
parse_flags() {
    FORCE=false
    VAULT_PATH=""
    for arg in "$@"; do
        if [ "$arg" = "--force" ] || [ "$arg" = "-f" ]; then
            FORCE=true
        else
            VAULT_PATH="$arg"
        fi
    done
}

# Expand ~ and resolve to absolute path
resolve_path() {
    local p="${1/#\~/$HOME}"
    if [ -d "$(dirname "$p")" ]; then
        p="$(cd "$(dirname "$p")" && pwd)/$(basename "$p")"
    fi
    printf '%s' "$p"
}

spin() {
    local msg="$1"; shift
    local frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    local pid time_str
    local start_time=$SECONDS

    _elapsed() {
        local e=$(( SECONDS - start_time ))
        if [ $e -ge 5 ]; then time_str=" (${e}s)"; else time_str=""; fi
    }

    local errlog
    errlog=$(mktemp)
    "$@" 2>"$errlog" &
    pid=$!

    while kill -0 "$pid" 2>/dev/null; do
        _elapsed
        for (( i=0; i<${#frames}; i++ )); do
            printf '\r  \033[36m%s\033[0m %s\033[2m%s\033[0m' "${frames:$i:1}" "$msg" "$time_str" >&2
            sleep 0.08
            kill -0 "$pid" 2>/dev/null || break
        done
    done

    wait "$pid"
    local exit_code=$?
    _elapsed
    if [ $exit_code -eq 0 ]; then
        printf '\r  \033[32m✓\033[0m %s\033[2m%s\033[0m\n' "$msg" "$time_str" >&2
    else
        printf '\r  \033[31m✗\033[0m %s\033[2m%s\033[0m\n' "$msg" "$time_str" >&2
        if [ -s "$errlog" ]; then
            printf '    \033[31m%s\033[0m\n' "$(head -5 "$errlog")" >&2
        fi
    fi
    rm -f "$errlog"
    return $exit_code
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

if [ "${1:-}" = "--uninstall" ]; then
    shift
    parse_flags "$@"

    if [ -z "$VAULT_PATH" ]; then
        if [ "$FORCE" = true ]; then
            err "Path is required with --force. Usage: bash install.sh --uninstall --force /path/to/vault"
        fi
        printf 'Which vault? Path: '
        read -r VAULT_PATH
        [ -z "$VAULT_PATH" ] && err "No path provided."
    fi

    VAULT_PATH="$(resolve_path "$VAULT_PATH")"

    [ -d "$VAULT_PATH" ] || err "Directory not found: $VAULT_PATH"
    [ -d "$VAULT_PATH/.brain-core" ] || err "Not a brain vault (no .brain-core/ found): $VAULT_PATH"

    step "Uninstalling the brain from $VAULT_PATH"
    printf '\n'
    printf '  \033[1mThis will uninstall the brain from your Obsidian vault.\033[0m\n' >&2
    info "Your notes and other files will not be affected."
    printf '\n'
    info "The uninstall will remove the following brain files only:"
    printf '\n'
    info "  - .brain-core/  (brain engine)"
    info "  - .brain/       (compiled data, caches)"
    info "  - .venv/        (Python virtual environment)"
    info "  - .mcp.json     (MCP server registration)"
    info "  - CLAUDE.md     (agent bootstrap file)"

    if [ "$FORCE" = false ]; then
        printf '\n'
        printf '  Remove brain system files from \033[1m%s\033[0m? [Y/n]: ' "$VAULT_PATH"
        read -r confirm
        if [ -n "$confirm" ] && [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
            printf '\n\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n\n' >&2
            printf '  \033[1;32m✓ Uninstall cancelled. No changes made.\033[0m\n' >&2
            printf '  You clearly still have a brain.\n' >&2
            printf '\n\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n\n' >&2
            exit 0
        fi
    fi

    printf '\n' >&2
    spin "Removing brain system files" bash -c '
        rm -rf "$1/.brain-core"
        rm -rf "$1/.brain"
        rm -rf "$1/.venv"
        rm -f  "$1/.mcp.json"
        rm -f  "$1/CLAUDE.md"
    ' _ "$VAULT_PATH"

    if [ "$FORCE" = false ]; then
        printf '\n'
        printf '  Also delete the Obsidian vault and all data at \033[1m%s\033[0m? [y/N]: ' "$VAULT_PATH"
        read -r delete_all
        if [ "$delete_all" = "y" ] || [ "$delete_all" = "Y" ]; then
            # Count user artefacts (markdown files outside system directories)
            artefact_count=$(find "$VAULT_PATH" \
                -path "$VAULT_PATH/.brain-core" -prune -o \
                -path "$VAULT_PATH/.brain" -prune -o \
                -path "$VAULT_PATH/.venv" -prune -o \
                -path "$VAULT_PATH/.obsidian" -prune -o \
                -path "$VAULT_PATH/_Config" -prune -o \
                -path "$VAULT_PATH/_Assets" -prune -o \
                -path "$VAULT_PATH/.pytest_cache" -prune -o \
                -name '*.md' -type f -print 2>/dev/null | wc -l | tr -d ' ')
            printf '\n'
            printf '\033[1;31m  PERMANENTLY DELETE everything in %s, not just the brain.\033[0m\n' "$VAULT_PATH"
            if [ "$artefact_count" -gt 0 ] 2>/dev/null; then
                info "That's ~$artefact_count artefacts — notes, projects, documents, and Obsidian settings."
            else
                info "This includes all notes, projects, documents, and Obsidian settings."
            fi
            printf '\033[1;31m  This cannot be undone.\033[0m Not even by your agent.\n'
            printf '\n'
            printf '  Type "farewell, cruel world" to confirm: '
            read -r confirm_all
            if [ "$confirm_all" = "farewell, cruel world" ]; then
                rm -rf "$VAULT_PATH"
                info "Deleted $VAULT_PATH"
            else
                info "Good call. Your data lives on, brain-free and care-free."
            fi
        fi
    fi

    printf '\n\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n\n'
    printf '  \033[1;32m✓ Your brain has been removed. Hopefully just this one.\033[0m\n'
    printf '\n'
    info "If you registered the brain globally (--user), also run:"
    info "  claude mcp remove brain --scope user"
    printf '\n\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n\n'
    exit 0
fi

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

parse_flags "$@"

step "Checking prerequisites"

command -v git >/dev/null 2>&1 || err "git is required. Install it and try again."
command -v python3 >/dev/null 2>&1 || err "python3 is required. Install it and try again."

# Find Python 3.10+ for the MCP server venv (optional — vault works without it)
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    path=$(command -v "$candidate" 2>/dev/null) || continue
    ver=$("$path" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null) || continue
    major=$(echo "$ver" | cut -d. -f1)
    minor=$(echo "$ver" | cut -d. -f2)
    if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
        PYTHON="$path"
        py_version="$ver"
        break
    fi
done

if [ -n "$PYTHON" ]; then
    printf '  \033[1mgit\033[0m ✓  \033[1mpython %s\033[0m ✓  \033[1mfrontal lobe\033[0m (recommended but not required) ✓\n' "$py_version" >&2
else
    py_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "?")
    printf '  \033[1mgit\033[0m ✓  \033[1mpython %s\033[0m ✓  \033[1mfrontal lobe\033[0m (recommended but not required) ✓\n' "$py_version" >&2
    printf '  \033[33mNote: Python 3.10+ not found. Vault will be created but MCP server setup will be skipped.\033[0m\n' >&2
    info "Install later with: brew install python@3.12"
fi

# ---------------------------------------------------------------------------
# Vault path
# ---------------------------------------------------------------------------

if [ -z "$VAULT_PATH" ]; then
    if [ "$FORCE" = true ]; then
        VAULT_PATH="$(pwd)"
    else
        DEFAULT_PATH="$(pwd)"
        printf '\nWhere should your brain live? [%s]: ' "$DEFAULT_PATH"
        read -r VAULT_PATH
        [ -z "$VAULT_PATH" ] && VAULT_PATH="$DEFAULT_PATH"
    fi
fi

VAULT_PATH="$(resolve_path "$VAULT_PATH")"

# Detect if we're running from inside an existing clone (used for safety check + clone step)
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ "${BASH_SOURCE[0]}" != "bash" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || true
fi

# Guard against dangerous target paths
case "$VAULT_PATH" in
    /|/usr|/usr/*|/bin|/sbin|/etc|/var|/tmp|/System|/Library)
        err "Refusing to install into system directory: $VAULT_PATH" ;;
esac
if [ "$VAULT_PATH" = "$HOME" ]; then
    err "Refusing to install into your home directory. Choose a subdirectory."
fi
# Prevent installing into the source repo itself
if [ -n "${SCRIPT_DIR:-}" ] && [ "$VAULT_PATH" = "$SCRIPT_DIR" ]; then
    err "Cannot install into the source repo. Choose a different path."
fi
# Ensure parent directory exists (vault dir itself will be created)
VAULT_PARENT="$(dirname "$VAULT_PATH")"
if [ ! -d "$VAULT_PARENT" ]; then
    err "Parent directory does not exist: $VAULT_PARENT"
fi

UPGRADE_MODE=false
EXISTING_VAULT=false
EXISTING_VERSION=""
if [ -d "$VAULT_PATH/.brain-core" ]; then
    EXISTING_VERSION=$(cat "$VAULT_PATH/.brain-core/VERSION" 2>/dev/null || echo "unknown")
elif [ -d "$VAULT_PATH" ] && [ -n "$(ls -A "$VAULT_PATH" 2>/dev/null)" ]; then
    # Non-empty directory without brain-core — install brain-core only, not the template
    EXISTING_VAULT=true
    if [ -d "$VAULT_PATH/.obsidian" ]; then
        printf '\n  Existing Obsidian vault detected at \033[1m%s\033[0m\n' "$VAULT_PATH" >&2
    else
        printf '\n  Existing directory detected at \033[1m%s\033[0m\n' "$VAULT_PATH" >&2
    fi
    info "Will install brain-core and config only (your existing files won't be touched)."
fi

# ---------------------------------------------------------------------------
# Clone repo (to temp dir if running via curl or outside a clone)
# ---------------------------------------------------------------------------

REPO_URL="https://github.com/robmorris/obsidian-brain.git"
REPO_DIR=""
CLEANUP_REPO=false

cleanup() {
    if [ "$CLEANUP_REPO" = true ] && [ -n "$REPO_DIR" ]; then
        rm -rf "$REPO_DIR"
    fi
}
trap cleanup EXIT

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/src/brain-core/VERSION" ]; then
    REPO_DIR="$SCRIPT_DIR"
    SOURCE_VERSION=$(cat "$REPO_DIR/src/brain-core/VERSION" 2>/dev/null || echo "unknown")
    printf '\n  \033[1mInstalling from local copy\033[0m at %s (v%s)\n' "$REPO_DIR" "$SOURCE_VERSION" >&2
else
    REPO_DIR="$(mktemp -d)"
    CLEANUP_REPO=true
    printf '\n' >&2
    spin "Downloading obsidian-brain" git clone --depth 1 --quiet "$REPO_URL" "$REPO_DIR"
    SOURCE_VERSION=$(cat "$REPO_DIR/src/brain-core/VERSION" 2>/dev/null || echo "unknown")
    printf '    \033[1mVersion:\033[0m v%s\n' "$SOURCE_VERSION" >&2
fi

# ---------------------------------------------------------------------------
# Upgrade prompt or fresh install
# ---------------------------------------------------------------------------

if [ -n "$EXISTING_VERSION" ]; then
    if [ "$FORCE" = true ]; then
        UPGRADE_MODE=true
        printf '\n  Upgrading v%s → v%s (--force)\n' "$EXISTING_VERSION" "$SOURCE_VERSION" >&2
    else
        printf '\n' >&2
        printf '  \033[1mThe brain is already installed (v%s).\033[0m\n' "$EXISTING_VERSION" >&2
        printf '  Would you like to upgrade it to \033[1mv%s\033[0m? [Y/n]: ' "$SOURCE_VERSION" >&2
        read -r UPGRADE
        if [ -z "$UPGRADE" ] || [ "$UPGRADE" = "y" ] || [ "$UPGRADE" = "Y" ]; then
            UPGRADE_MODE=true
        else
            printf '\n\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n\n' >&2
            printf '  \033[1;32m✓ Upgrade skipped. No changes made.\033[0m\n' >&2
            printf '\n\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n\n' >&2
            exit 0
        fi
    fi
fi

printf '\n' >&2
if [ "$UPGRADE_MODE" = true ]; then
    spin "Upgrading brain-core" python3 "$REPO_DIR/src/brain-core/scripts/upgrade.py" --source "$REPO_DIR/src/brain-core" --vault "$VAULT_PATH"
    NEW_VERSION=$(cat "$VAULT_PATH/.brain-core/VERSION" 2>/dev/null || echo "unknown")
    printf '    \033[1mUpgraded to:\033[0m v%s\n' "$NEW_VERSION" >&2
    if [ -f "$VAULT_PATH/.venv/bin/pip" ]; then
        spin "Syncing Python dependencies" "$VAULT_PATH/.venv/bin/pip" install --quiet -r "$VAULT_PATH/.brain-core/brain_mcp/requirements.txt"
    fi
elif [ "$EXISTING_VAULT" = true ]; then
    spin "Installing brain into existing vault" bash -c '
        vault="$1"; repo="$2"; tmpl="$2/template-vault"

        # Brain core
        rm -f "$vault/.brain-core"
        cp -R "$repo/src/brain-core/." "$vault/.brain-core/"

        # Config and system scaffolding (skip dirs that already exist)
        for dir in _Config _Assets _Temporal _Plugins _Workspaces .backups; do
            if [ -d "$tmpl/$dir" ] && [ ! -d "$vault/$dir" ]; then
                cp -R "$tmpl/$dir" "$vault/$dir"
            fi
        done

        # Agent bootstrap files (skip if present)
        [ -f "$vault/Agents.md" ] || cp "$tmpl/Agents.md" "$vault/Agents.md"
        [ -f "$vault/CLAUDE.md" ] || cp "$tmpl/CLAUDE.md" "$vault/CLAUDE.md"

        # CSS snippet only — namespaced, safe to overwrite (it is ours)
        mkdir -p "$vault/.obsidian/snippets"
        cp "$tmpl/.obsidian/snippets/brain-folder-colours.css" "$vault/.obsidian/snippets/" 2>/dev/null || true
    ' _ "$VAULT_PATH" "$REPO_DIR"
else
    spin "Creating vault at $VAULT_PATH" bash -c '
        mkdir -p "$1"
        if command -v rsync >/dev/null 2>&1; then
            rsync -a --exclude ".pytest_cache" "$2/template-vault/" "$1/"
        else
            cp -R "$2/template-vault/." "$1/"
            rm -rf "$1/.pytest_cache"
        fi
        rm -f "$1/.brain-core"
        cp -R "$2/src/brain-core/." "$1/.brain-core/"
    ' _ "$VAULT_PATH" "$REPO_DIR"
fi

# ---------------------------------------------------------------------------
# MCP server + Python venv (optional)
# ---------------------------------------------------------------------------

printf '\n' >&2
if [ -z "$PYTHON" ]; then
    info "MCP server setup skipped (requires Python 3.10+)."
    info "Install Python 3.10+, then run: python3 .brain-core/scripts/init.py"
elif [ "$FORCE" = true ]; then
    REGISTER_MCP=y
else
    printf '  \033[1mRegister the Brain MCP server? (recommended)\033[0m [Y/n]: ' >&2
    read -r REGISTER_MCP
fi
if [ -n "$PYTHON" ] && { [ -z "${REGISTER_MCP:-}" ] || [ "${REGISTER_MCP:-}" = "y" ] || [ "${REGISTER_MCP:-}" = "Y" ]; }; then
    printf '\n' >&2
    spin "Setting up Python virtual environment" bash -c '
        "$1" -m venv "$2/.venv"
        "$2/.venv/bin/pip" install --quiet --upgrade pip -r "$2/.brain-core/brain_mcp/requirements.txt"
    ' _ "$PYTHON" "$VAULT_PATH"
    printf '\n' >&2
    if spin "Registering Brain MCP server" python3 "$VAULT_PATH/.brain-core/scripts/init.py" --vault "$VAULT_PATH"; then
        printf '    \033[1mScope:\033[0m local (this vault only)\n' >&2
        printf '    \033[1mVerify:\033[0m claude mcp list\n' >&2
    else
        printf '\n' >&2
        info "MCP registration failed. You can retry with:"
        info "  python3 $VAULT_PATH/.brain-core/scripts/init.py --vault $VAULT_PATH"
    fi
elif [ -n "$PYTHON" ]; then
    printf '\n' >&2
    info "MCP skipped. You can register later with: python3 .brain-core/scripts/init.py"
fi

# ---------------------------------------------------------------------------
# Optional: Obsidian CLI (uncomment when ready)
# ---------------------------------------------------------------------------

# if [ "$FORCE" = false ]; then
#     printf '\n' >&2
#     printf '  \033[1mInstall the Obsidian CLI?\033[0m Optional — improves search and rename. [y/N]: ' >&2
#     read -r INSTALL_CLI
#     if [ "$INSTALL_CLI" = "y" ] || [ "$INSTALL_CLI" = "Y" ]; then
#         printf '\n' >&2
#         if command -v npm >/dev/null 2>&1; then
#             spin "Installing Obsidian CLI" npm install -g obsidian-cli-rest 2>/dev/null
#         else
#             info "npm not found. Install Node.js, then run: npm install -g obsidian-cli-rest"
#         fi
#     fi
# fi

# TODO: Add prompt for brain CLI when implemented

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

printf '\n\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n\n'
printf '  \033[1;32m✓ Your brain is ready.\033[0m\n'
printf '\n'
info "Next steps:"
printf '\n'
printf '  \033[1m1. Open as a vault in Obsidian\033[0m\n'
printf '     → %s\n' "$VAULT_PATH"
printf '\n'
printf '  \033[1m2. Enable the CSS snippet\033[0m\n'
printf '     → Settings > Appearance > CSS Snippets > brain-folder-colours\n'
printf '\n'
printf '  \033[1m3. Open your agent in the vault folder\033[0m\n'
printf '     → cd %s\n' "$VAULT_PATH"
printf '\n'
printf '  \033[1m4. Start a conversation\033[0m\n'
printf '     → try "I just had an idea about..."\n'
printf '\n\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n\n'
