#!/usr/bin/env bash
# Moonshot x Doppelganger 0.9.4 compatibility patch - Steam Deck / Linux launcher.
# Run from Konsole (Desktop Mode). Extra arguments go to doppel_compat.py, e.g. --status.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

PYTHON=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    PYTHON="$(command -v "$cand")"
    break
  fi
done
if [[ -z "$PYTHON" ]]; then
  echo "python3 was not found. It ships with SteamOS; in Desktop Mode open Konsole and try again."
  exit 1
fi

status=0
"$PYTHON" "$DIR/doppel_compat.py" "$@" || status=$?
if [[ -t 0 && $# -eq 0 ]]; then
  read -r -p "Press Enter to close..." _
fi
exit $status
