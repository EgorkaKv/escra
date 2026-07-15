#!/usr/bin/env bash
# Pulls the latest deploy branch, syncs dependencies, restarts the service.
# Invoked by the /github-push webhook (see app/webapp/api.py) — never edit
# this file mid-flight from a running deploy; the `main` function wraps the
# whole body so bash parses it into memory before `git pull` can rewrite the
# file out from under the interpreter.
set -euo pipefail

main() {
  local repo_dir
  repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  cd "$repo_dir"

  local branch="${DEPLOY_BRANCH:-main}"

  echo "[deploy] fetching origin/$branch"
  git fetch --quiet origin "$branch"

  echo "[deploy] fast-forwarding to origin/$branch"
  git merge --ff-only "origin/$branch"

  echo "[deploy] syncing dependencies"
  uv sync

  echo "[deploy] restarting escra.service"
  systemctl restart escra
}

main "$@"
