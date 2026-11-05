#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "First-time setup: creating a private Python environment..."
  python3 -m venv .venv
fi

REQ_HASH="$(.venv/bin/python -c "import hashlib; print(hashlib.sha256(open('requirements.txt','rb').read()).hexdigest())")"
OLD_HASH=""
if [[ -f ".venv/.requirements-hash" ]]; then
  OLD_HASH="$(cat .venv/.requirements-hash)"
fi
if ! .venv/bin/python -c 'import streamlit, pandas, pykakasi, platformdirs' >/dev/null 2>&1; then
  OLD_HASH=""
fi

if [[ "$REQ_HASH" != "$OLD_HASH" ]]; then
  echo "Installing or updating required packages..."
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
  printf '%s\n' "$REQ_HASH" > .venv/.requirements-hash
fi

echo "Starting the confectionery costing app..."
exec .venv/bin/python -m streamlit run app.py
