#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv-web/bin/python ]]; then
    source web/python-runtime.sh
    choose_python
    "$runtime" -m venv .venv-web
fi
if ! .venv-web/bin/python -c 'import fastapi, uvicorn, cryptography, httpx' 2>/dev/null; then
    .venv-web/bin/python -m pip install -r web/requirements.txt
fi
if [[ ! -f "${THVV_WEB_DATA:-.thvv-web}/auth.json" ]]; then
    echo '首次启动，请设置 admin 登录密码。'
    .venv-web/bin/python -m web.auth
fi
echo 'THVV 页面：http://127.0.0.1:8765'
exec .venv-web/bin/python -m uvicorn web.app:create_app --factory --host 127.0.0.1 --port "${THVV_WEB_PORT:-8765}"
