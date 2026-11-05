#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "Run bash setup_macos_linux.sh first."
  exit 1
fi
exec .venv/bin/python -m streamlit run app.py
