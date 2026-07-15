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

  # Restart in a SEPARATE transient unit, not inline. This script runs as a
  # child of the escra process, i.e. inside escra.service's own cgroup. A plain
  # `systemctl restart escra` here asks systemd to stop that cgroup, which
  # SIGTERMs *this script and the systemctl client too* (KillMode=control-group)
  # before the restart reliably lands — so the pull succeeds but the process
  # never comes back on the new code. systemd-run hands the restart to PID 1 in
  # its own cgroup, so it survives escra being torn down. --no-block returns
  # immediately; --collect cleans up the transient unit afterwards.
  echo "[deploy] scheduling restart of escra.service (detached)"
  systemd-run --collect --no-block --unit="escra-redeploy-$$" systemctl restart escra
}

main "$@"
