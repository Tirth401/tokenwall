#!/usr/bin/env bash
# Build Ramulator 2.1 (git submodule) with the Apple Clang fixes and install
# its Python package into this repo's .venv.
#
# Usage, from the repo root:
#   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
#   scripts/setup_ramulator2.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
R2="$ROOT/external/ramulator2"
PY="$ROOT/.venv/bin/python"
PATCH="$ROOT/patches/ramulator2/0001-apple-clang-build-fixes.patch"

if [ ! -x "$PY" ]; then
  echo "No venv found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

git -C "$ROOT" submodule update --init external/ramulator2

# Apply the build patch once (idempotent).
if git -C "$R2" apply --check "$PATCH" 2>/dev/null; then
  git -C "$R2" apply "$PATCH"
  echo "Applied $PATCH"
elif git -C "$R2" apply --check --reverse "$PATCH" 2>/dev/null; then
  echo "Patch already applied"
else
  echo "Patch does not apply cleanly to $(git -C "$R2" rev-parse --short HEAD); inspect manually" >&2
  exit 1
fi

# The patch bumps fmt to 11.2.0. Drop a stale fetched copy so CMake re-fetches.
if [ -d "$R2/ext/fmt" ] && ! grep -q "FMT_VERSION 110200" "$R2/ext/fmt/include/fmt/base.h" 2>/dev/null; then
  rm -rf "$R2/ext/fmt"
fi

BUILD_DIR="$R2/build" JOBS="${JOBS:-8}" "$R2/build.sh" -DPython_EXECUTABLE="$PY"

"$PY" -m pip install --quiet -e "$R2"
"$PY" -c "import ramulator; print('ramulator import OK from', ramulator.__file__)"
