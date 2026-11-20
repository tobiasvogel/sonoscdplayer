#!/bin/sh
# Build the code reference (HTML) from the docstrings with pdoc.
#
#   tools/build_api_docs.sh
#
# Run the script itself, not "pdoc tools/build_api_docs.sh". pdoc comes
# from the Debian package python3-pdoc (installed by scripts/setup.sh from
# scripts/packages.lst); set PDOC=/path/to/pdoc to use another one.
#
# Output: docs/api/index.html (not tracked by git). Without the script,
# "cd src && pdoc --docformat google sonoscdplayer" serves the same pages on
# http://localhost:8080. Docstrings follow the Google style (Args:,
# Returns:, Raises:).
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PDOC=${PDOC:-pdoc}
OUT=${OUT:-"$ROOT/docs/api"}
cd "$ROOT/src"
"$PDOC" --docformat google --output-directory "$OUT" sonoscdplayer
echo "Code reference written to $OUT/index.html"
