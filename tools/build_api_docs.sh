#!/bin/sh
# Build the code reference (HTML) from the docstrings with pdoc.
#
#   python3 -m venv /tmp/pdoc-venv && /tmp/pdoc-venv/bin/pip install pdoc pillow
#   PDOC=/tmp/pdoc-venv/bin/pdoc tools/build_api_docs.sh
#
# Output: docs/api/index.html (not tracked by git). Docstrings follow the
# Google style (Args:, Returns:, Raises:).
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PDOC=${PDOC:-pdoc}
OUT=${OUT:-"$ROOT/docs/api"}
cd "$ROOT/src"
"$PDOC" --docformat google --output-directory "$OUT" sonoscdplayer
echo "Code reference written to $OUT/index.html"
