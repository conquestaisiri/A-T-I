#!/usr/bin/env bash
# Scoped verify for ATI — runs per-file/per-subsystem without the 1696 full-suite gate.
# Wave A 007: ruff --fix + mypy --warn-unused-configs + pytest -q -k <file> scoped.
# Usage:
#   ./scripts/verify.sh                # ruff + mypy + full pytest (1696) — Wave boundary only
#   ./scripts/verify.sh test_risk_gate # ruff + mypy + pytest -q -k test_risk_gate (scoped per-task)
#   ./scripts/verify.sh --quick        # ruff + mypy only (no pytest)

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SCOPE="${1:-}"

echo "==> ruff check backend tests"
py -3 -m ruff check backend tests

echo "==> ruff format --check backend tests"
py -3 -m ruff format --check backend tests

echo "==> mypy backend --warn-unused-configs"
py -3 -m mypy backend --warn-unused-configs

if [[ "$SCOPE" == "--quick" ]]; then
  echo "==> quick verify done (no pytest)"
  exit 0
fi

if [[ -n "$SCOPE" && "$SCOPE" != "--full" ]]; then
  echo "==> pytest scoped: -q -k $SCOPE"
  py -3 -m pytest -q -k "$SCOPE"
else
  echo "==> pytest full: -q"
  py -3 -m pytest -q
fi

echo "==> verify OK"
