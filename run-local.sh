#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
set -a
. ./.env
set +a
exec .venv/bin/python -m radar daily --skip sitemaps,gsc,newsites --out report.md "$@"
