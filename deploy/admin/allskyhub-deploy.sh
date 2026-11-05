#!/bin/bash
# Deploy from the hub's admin page (server/src/allskyhub_server/admin/deploy.py), run as
# root by allskyhub-deploy.service when allskyhub-deploy.path sees a request.
#
# The hub (unprivileged) leaves /var/lib/allskyhub-server/deploy/request: line 1 the sha
# it showed, line 2 who asked. Everything in that folder is untrusted: only a 40-hex sha
# is taken from it, and only if it is exactly origin/main. Nothing else is read or run.
# Answers: state (running, ok, failed), target (the sha) and log.
#
# Everything is in main(), which bash reads completely before running it: git pull
# replaces this very file during the run.
set -euo pipefail

main() {
    # Overridable only for trying the script out in a sandbox.
    local SPOOL=${ALLSKYHUB_DEPLOY_SPOOL:-/var/lib/allskyhub-server/deploy}
    local REPO=${ALLSKYHUB_DEPLOY_REPO:-/opt/allskyhub-server}
    local ENV_FILE=${ALLSKYHUB_ENV_FILE:-/etc/allskyhub-server/allskyhub-server.env}
    local HUB_USER=${ALLSKYHUB_HUB_USER:-allskyhub-server}
    local HUB_URL=${ALLSKYHUB_HUB_URL:-https://allskyhub.org}

    local LOCK=${ALLSKYHUB_DEPLOY_LOCK:-/run/allskyhub-deploy.lock}

    [[ -f $SPOOL/request ]] || exit 0
    # The lock outside the spool, and everything in the spool read and written as the hub,
    # never by root: the folder is the hub's, and a link planted there must not make root
    # read or write elsewhere (security review 2026-11-04).
    exec 9>"$LOCK"
    flock -n 9 || exit 0
    as_hub() { runuser -u "$HUB_USER" -- "$@"; }

    local sha
    sha=$(as_hub head -n 1 -- "$SPOOL/request" 2>/dev/null | tr -d '[:space:]') || true
    # Removed as the hub, like everything in its folder: never by root through a link.
    as_hub rm -f -- "$SPOOL/request" || exit 0
    answer() {  # answer <file> <text>
        as_hub rm -f -- "$SPOOL/$1"
        # shellcheck disable=SC2016  # $1 is the inner shell's
        printf '%s\n' "$2" | as_hub sh -c 'umask 027; cat > "$1"' _ "$SPOOL/$1"
    }
    as_hub rm -f -- "$SPOOL/log"
    # shellcheck disable=SC2016  # $1 is the inner shell's
    as_hub sh -c 'umask 027; : > "$1"' _ "$SPOOL/log"
    say() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*" | as_hub tee -a -- "$SPOOL/log" >/dev/null; }
    run() { "$@" 2>&1 | as_hub tee -a -- "$SPOOL/log" >/dev/null; }

    if [[ ! $sha =~ ^[0-9a-f]{40}$ ]]; then
        answer target ""
        answer state failed
        say "Ungültige Anfrage."
        exit 0
    fi
    answer target "$sha"
    answer state running
    say "Deploy von ${sha:0:7} angefordert."

    cd "$REPO"
    local before
    before=$(git rev-parse HEAD)
    fail() {
        say "FEHLER: $*"
        if [[ $(git rev-parse HEAD) != "$before" ]]; then
            say "Zurück auf ${before:0:7}."
            run git reset --hard "$before" || true
            run uv sync --frozen --no-dev --package allskyhub-server || true
        fi
        answer state failed
        exit 0
    }

    if [[ -n $(git status --porcelain --untracked-files=no) ]]; then
        fail "Der Checkout hat lokale Änderungen, bitte von Hand prüfen."
    fi
    say "git fetch"
    run git fetch --quiet origin main || fail "git fetch"
    local main
    main=$(git rev-parse origin/main)
    [[ $main == "$sha" ]] || fail "main ist inzwischen ${main:0:7}, angefordert war ${sha:0:7}."
    say "git merge --ff-only ${sha:0:7} (vorher ${before:0:7})"
    run git merge --ff-only "$sha" || fail "kein Fast-Forward möglich"
    say "uv sync"
    run uv sync --frozen --no-dev --package allskyhub-server || fail "uv sync"
    say "Migrationen"
    run sudo -u "$HUB_USER" bash -c \
        'set -a; . "$1"; exec "$2/.venv/bin/allskyhub-server" migrate' _ "$ENV_FILE" "$REPO" \
        || fail "Migration (der Hub läuft weiter mit der alten Version)"
    say "Neustart"
    run systemctl restart allskyhub-server || fail "Neustart"
    local version=""
    for _ in $(seq 1 30); do
        version=$(curl -fsS --max-time 3 "$HUB_URL/healthz" 2>/dev/null \
            | sed -n 's/.*"version": *"\([0-9a-f]*\)".*/\1/p') || true
        [[ $version == "${sha:0:7}" ]] && break
        sleep 1
    done
    if [[ $version != "${sha:0:7}" ]]; then
        say "FEHLER: Der Hub meldet nach dem Neustart '${version:-nichts}'."
        answer state failed
        exit 0
    fi
    say "Fertig: ${sha:0:7} läuft."
    answer state ok
}

main "$@"
exit 0
