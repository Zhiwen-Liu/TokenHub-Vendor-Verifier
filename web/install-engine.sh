#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv-engine/bin/python ]]; then
    source web/python-runtime.sh
    choose_python
    "$runtime" -m venv .venv-engine
fi
.venv-engine/bin/python -m pip install -r web/engine-requirements.txt
