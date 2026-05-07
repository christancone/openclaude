#!/usr/bin/env bash
# Pre-commit fast-tier wrapper. Runs unit + lint tests inside the container.
#
# Fails loud if docker isn't up — that's a deliberate friction point: tests
# that depend on the container should never be silently skipped because the
# container happened to be down.

set -euo pipefail

if ! docker compose ps --status running --quiet | grep -q .; then
  cat <<EOF >&2
sparengine pre-commit: docker compose not running.
  bring it up first:  docker compose up -d
  or skip this hook:  git commit --no-verify
EOF
  exit 1
fi

if ! docker compose ps sparengine --status running --quiet | grep -q .; then
  echo "sparengine pre-commit: 'sparengine' service not running" >&2
  exit 1
fi

exec docker compose exec -T sparengine bash -c \
  "cd /app/sparengine-export && python -m pytest tests/ -m 'unit or lint' -q"
