#!/bin/sh
# Local daily run. With no AISA_API_KEY the free signals still work (HF, GitHub
# breakout, sitemaps); paid AIsa steps auto-skip with a one-line reason.
# Optional: RADAR_X_POSTS_FILE=/path/to/posts.json  (or NDJSON) for file-backed X.
# Optional: .env with AISA_API_KEY / RADAR_X_POSTS_FILE / TRUSTMRR_API_KEY / …
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ -f ./.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
PY="${RADAR_PYTHON:-.venv/bin/python}"
if [ ! -x "$PY" ]; then
  PY=python3
fi
# Default skip list for a lightweight free/local pass. Pass more --skip / flags after --.
# Paid signals also auto-skip when AISA_API_KEY is missing; this just keeps the run fast.
FREE_SKIP="${RADAR_FREE_SKIP:-sitemaps,gsc,newsites,asks,sellers,review,usecases,tutorials,research}"
exec "$PY" -m radar daily --skip "$FREE_SKIP" --out report.md "$@"
