#!/usr/bin/env bash
# Condor installer — bootstrap, then hand off to `condor init`.
#
#   curl -fsSL https://condor.hummingbot.org/install.sh | bash
#   curl -fsSL ... | bash -s -- --harness claude-code,hermes
#
# Architecture (docs/installing.md, docs/changes-from-main.md §5):
# Stage A (this script) does bootstrap only — deps, clone-or-update, venv,
# .env template. Every product question (harness and account onboarding)
# lives in `condor init`, which is re-runnable forever without
# this script. Prompts work under `curl | bash` because init runs with
# stdin rebound to /dev/tty.
#
# Flags:
#   --dir <path>        install directory      (default: ~/condor)
#   --branch <name>     git branch to install  (default: main)
#   --harness <list>    preseed init's harness selection (comma-separated)
#   --no-init           bootstrap only; print the init command and exit
#   --non-interactive   never prompt; use detected/default harnesses
#   --stage-json        emit one {"ok":...,"stage":...} JSON line per stage,
#                       so an agent harness can drive this installer
set -euo pipefail

REPO_URL="https://github.com/hummingbot/condor.git"
INSTALL_DIR="${CONDOR_DIR:-$HOME/condor}"
BRANCH="main"
HARNESS=""
RUN_INIT=true
NON_INTERACTIVE=false
STAGE_JSON=false

while [ $# -gt 0 ]; do
    case "$1" in
        --dir) INSTALL_DIR="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --harness) HARNESS="$2"; shift 2 ;;
        --no-init) RUN_INIT=false; shift ;;
        --non-interactive) NON_INTERACTIVE=true; shift ;;
        --stage-json) STAGE_JSON=true; shift ;;
        *) echo "error: unknown flag $1" >&2; exit 1 ;;
    esac
done

log() { echo "==> $*"; }

stage() { # stage <name> <ok true|false> [reason]
    if [ "$STAGE_JSON" = true ]; then
        if [ -n "${3:-}" ]; then
            printf '{"ok":%s,"stage":"%s","reason":"%s"}\n' "$2" "$1" "$3"
        else
            printf '{"ok":%s,"stage":"%s"}\n' "$2" "$1"
        fi
    fi
}

die() { # die <stage> <message>
    stage "$1" false "$2"
    echo "error: $2" >&2
    exit 1
}

# Probe by actually opening /dev/tty: a bare -e test passes inside Docker
# builds where the open then fails, and init would crash on its prompts.
have_tty() { (: </dev/tty) 2>/dev/null; }

# ── Stage: deps ──────────────────────────────────────────────────────
# git and uv are Condor's own build dependencies — installing uv here is
# bootstrap, not harness installation (init never installs harnesses).

command -v git >/dev/null 2>&1 || die deps "git is required — install it and re-run"

if ! command -v uv >/dev/null 2>&1; then
    log "Installing uv (Python package manager)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null 2>&1 || die deps "uv install finished but 'uv' is not on PATH — open a new shell and re-run"
fi
stage deps true

# ── Stage: clone ─────────────────────────────────────────────────────
# Running from inside a checkout uses it; otherwise clone or update
# $INSTALL_DIR. A dirty checkout is never touched.

if [ -f pyproject.toml ] && grep -q '^name = "condor"' pyproject.toml 2>/dev/null; then
    INSTALL_DIR="$(pwd)"
    log "Using current checkout: $INSTALL_DIR"
elif [ -d "$INSTALL_DIR/.git" ]; then
    log "Updating existing checkout: $INSTALL_DIR"
    if [ -n "$(git -C "$INSTALL_DIR" status --porcelain)" ]; then
        log "Checkout has local changes — leaving code as-is"
    else
        git -C "$INSTALL_DIR" fetch origin "$BRANCH"
        git -C "$INSTALL_DIR" checkout "$BRANCH"
        git -C "$INSTALL_DIR" merge --ff-only "origin/$BRANCH" \
            || die clone "cannot fast-forward $BRANCH — resolve manually in $INSTALL_DIR"
    fi
else
    log "Cloning condor ($BRANCH) into $INSTALL_DIR"
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"
stage clone true

# ── Stage: python ────────────────────────────────────────────────────

log "Installing Python dependencies (uv sync --dev)"
uv sync --dev
stage python true

# ── Stage: env ───────────────────────────────────────────────────────
# Template only — values are init's job.

if [ ! -f .env ]; then
    cp .env.example .env
    log "Created .env from template"
fi
stage env true

# ── Stage: init (handoff) ────────────────────────────────────────────
# Everything from here is a product question and belongs to `condor init`.

INIT_CMD=(uv run python -m condor.cli init)
[ -n "$HARNESS" ] && INIT_CMD+=(--harness "$HARNESS")

if [ "$RUN_INIT" = false ]; then
    stage init true "skipped (--no-init)"
    log "Bootstrap done. Finish setup with:  cd $INSTALL_DIR && ${INIT_CMD[*]}"
    exit 0
fi

if [ "$NON_INTERACTIVE" = true ]; then
    stage init true "running non-interactive"
    "${INIT_CMD[@]}" </dev/null
elif have_tty; then
    stage init true "handing off to condor init"
    exec </dev/tty
    exec "${INIT_CMD[@]}"
else
    stage init false "no tty"
    log "No terminal available. Finish setup with:  cd $INSTALL_DIR && ${INIT_CMD[*]}"
    exit 0
fi
