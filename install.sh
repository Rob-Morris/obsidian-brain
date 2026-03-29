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
# Requirements: git, python3 (3.10+)
# Safe to re-run with a new path.

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()  { printf '  %s\n' "$*" >&2; }
step()  { printf '\n\033[1m▸ %s\033[0m\n' "$*" >&2; }
err()   { printf '\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }

# Run a command with a spinner animation
spin() {
    local msg="$1"; shift
    local frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    local pid

    "$@" &
    pid=$!

    while kill -0 "$pid" 2>/dev/null; do
        for (( i=0; i<${#frames}; i++ )); do
            printf '\r  \033[36m%s\033[0m %s' "${frames:$i:1}" "$msg" >&2
            sleep 0.08
            kill -0 "$pid" 2>/dev/null || break
        done
    done

    wait "$pid"
    local exit_code=$?
    if [ $exit_code -eq 0 ]; then
        printf '\r  \033[32m✓\033[0m %s\n' "$msg" >&2
    else
        printf '\r  \033[31m✗\033[0m %s\n' "$msg" >&2
        return $exit_code
    fi
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

if [ "${1:-}" = "--uninstall" ]; then
    # Check for --force flag (skips interactive prompts, for agent/scripted use)
    FORCE=false
    VAULT_PATH=""
    shift
    for arg in "$@"; do
        if [ "$arg" = "--force" ] || [ "$arg" = "-f" ]; then
            FORCE=true
        else
            VAULT_PATH="$arg"
        fi
    done

    if [ -z "$VAULT_PATH" ]; then
        printf 'Which vault? Path: '
        read -r VAULT_PATH
        [ -z "$VAULT_PATH" ] && err "No path provided."
    fi

    VAULT_PATH="${VAULT_PATH/#\~/$HOME}"
    if [ -d "$(dirname "$VAULT_PATH")" ]; then
        VAULT_PATH="$(cd "$(dirname "$VAULT_PATH")" && pwd)/$(basename "$VAULT_PATH")"
    fi

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
            printf '\n\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n\n' >&2
            exit 0
        fi
    fi

    printf '\n' >&2
    spin "Removing brain system files" bash -c "
        rm -rf \"$VAULT_PATH/.brain-core\"
        rm -rf \"$VAULT_PATH/.brain\"
        rm -rf \"$VAULT_PATH/.venv\"
        rm -f  \"$VAULT_PATH/.mcp.json\"
        rm -f  \"$VAULT_PATH/CLAUDE.md\"
    "

    if [ "$FORCE" = false ]; then
        printf '\n'
        printf '  Also delete the Obsidian vault and all data at \033[1m%s\033[0m? [y/N]: ' "$VAULT_PATH"
        read -r delete_all
        if [ "$delete_all" = "y" ] || [ "$delete_all" = "Y" ]; then
            printf '\n'
            printf '\033[1;31m  PERMANENTLY DELETE everything in %s, not just the brain.\033[0m\n' "$VAULT_PATH"
            info "This includes all notes, projects, documents, and Obsidian settings."
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

# Parse install flags
FORCE=false
VAULT_PATH=""
for arg in "$@"; do
    if [ "$arg" = "--force" ] || [ "$arg" = "-f" ]; then
        FORCE=true
    else
        VAULT_PATH="$arg"
    fi
done

step "Checking prerequisites"

command -v git >/dev/null 2>&1 || err "git is required. Install it and try again."

# Find the best available Python 3.10+ for the MCP server venv
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

if [ -z "$PYTHON" ]; then
    err "Python 3.10+ is required but not found.\nInstall it with: brew install python@3.12\nThen re-run this script."
fi
printf '  \033[1mgit\033[0m ✓  \033[1mpython %s\033[0m ✓  \033[1mfrontal lobe\033[0m (recommended but not required) ✓\n' "$py_version" >&2

# ---------------------------------------------------------------------------
# Vault path
# ---------------------------------------------------------------------------

if [ -z "$VAULT_PATH" ]; then
    DEFAULT_PATH="$(pwd)"
    printf '\nWhere should your brain live? [%s]: ' "$DEFAULT_PATH"
    read -r VAULT_PATH
    [ -z "$VAULT_PATH" ] && VAULT_PATH="$DEFAULT_PATH"
fi

# Expand ~ and resolve to absolute path
VAULT_PATH="${VAULT_PATH/#\~/$HOME}"
if [ -d "$(dirname "$VAULT_PATH")" ]; then
    VAULT_PATH="$(cd "$(dirname "$VAULT_PATH")" && pwd)/$(basename "$VAULT_PATH")"
fi

UPGRADE_MODE=false
if [ -d "$VAULT_PATH/.brain-core" ]; then
    EXISTING_VERSION=$(cat "$VAULT_PATH/.brain-core/VERSION" 2>/dev/null || echo "unknown")
    NEEDS_UPGRADE=true
else
    NEEDS_UPGRADE=false
fi

# ---------------------------------------------------------------------------
# Clone repo (to temp dir if running via curl or outside a clone)
# ---------------------------------------------------------------------------

REPO_URL="https://github.com/robmorris/obsidian-brain.git"
REPO_DIR=""
CLEANUP_REPO=false

# Check if we're running from inside an existing clone
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ "${BASH_SOURCE[0]}" != "bash" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || true
fi

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/src/brain-core/VERSION" ]; then
    REPO_DIR="$SCRIPT_DIR"
    LOCAL_VERSION=$(cat "$REPO_DIR/src/brain-core/VERSION" 2>/dev/null || echo "unknown")
    printf '\n  \033[1mInstalling from local copy\033[0m at %s (v%s)\n' "$REPO_DIR" "$LOCAL_VERSION" >&2
else
    REPO_DIR="$(mktemp -d)"
    CLEANUP_REPO=true
    printf '\n' >&2
    spin "Downloading obsidian-brain" git clone --depth 1 --quiet "$REPO_URL" "$REPO_DIR"
    DL_VERSION=$(cat "$REPO_DIR/src/brain-core/VERSION" 2>/dev/null || echo "unknown")
    printf '    \033[1mVersion:\033[0m v%s\n' "$DL_VERSION" >&2
fi

cleanup() {
    if [ "$CLEANUP_REPO" = true ] && [ -n "$REPO_DIR" ]; then
        rm -rf "$REPO_DIR"
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Copy template vault
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Upgrade prompt (now that we know the source version)
# ---------------------------------------------------------------------------

if [ "$NEEDS_UPGRADE" = true ]; then
    SOURCE_VERSION=$(cat "$REPO_DIR/src/brain-core/VERSION" 2>/dev/null || echo "unknown")
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

printf '\n' >&2
if [ "$UPGRADE_MODE" = true ]; then
    spin "Upgrading brain-core" python3 "$REPO_DIR/src/brain-core/scripts/upgrade.py" --source "$REPO_DIR/src/brain-core" --vault "$VAULT_PATH" 2>/dev/null
    NEW_VERSION=$(cat "$VAULT_PATH/.brain-core/VERSION" 2>/dev/null || echo "unknown")
    printf '    \033[1mUpgraded to:\033[0m v%s\n' "$NEW_VERSION" >&2
else
    spin "Creating vault at $VAULT_PATH" bash -c "
        mkdir -p \"$VAULT_PATH\"
        cp -R \"$REPO_DIR/template-vault/.\" \"$VAULT_PATH/\"
        rm -f \"$VAULT_PATH/.brain-core\"
        cp -R \"$REPO_DIR/src/brain-core/.\" \"$VAULT_PATH/.brain-core/\"
    "
fi

# ---------------------------------------------------------------------------
# Install Python dependencies
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# MCP server (optional)
# ---------------------------------------------------------------------------

printf '\n' >&2
if [ "$FORCE" = true ]; then
    REGISTER_MCP=y
else
    printf '  \033[1mRegister the Brain MCP server? (recommended)\033[0m [Y/n]: ' >&2
    read -r REGISTER_MCP
fi
if [ -z "$REGISTER_MCP" ] || [ "$REGISTER_MCP" = "y" ] || [ "$REGISTER_MCP" = "Y" ]; then
    printf '\n' >&2
    spin "Setting up Python virtual environment" bash -c "
        \"$PYTHON\" -m venv \"$VAULT_PATH/.venv\"
        \"$VAULT_PATH/.venv/bin/pip\" install --quiet --upgrade pip
        \"$VAULT_PATH/.venv/bin/pip\" install --quiet \"mcp>=1.0.0\"
    "
    printf '\n' >&2
    spin "Registering Brain MCP server" python3 "$VAULT_PATH/.brain-core/scripts/init.py" --vault "$VAULT_PATH" 2>/dev/null
    printf '    \033[1mScope:\033[0m local (this vault only)\n' >&2
    printf '    \033[1mVerify:\033[0m claude mcp list\n' >&2
else
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
printf '     → Settings > Appearance > CSS Snippets > folder-colours\n'
printf '\n'
printf '  \033[1m3. Open your agent in the vault folder\033[0m\n'
printf '     → cd %s\n' "$VAULT_PATH"
printf '\n'
printf '  \033[1m4. Start a conversation\033[0m\n'
printf '     → try "I just had an idea about..."\n'
printf '\n\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n\n'
