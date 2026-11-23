#!/usr/bin/env bash
# Shared bootstrap for a fresh machine. An explicit selection is never ignored.
choose_python() {
    local candidate
    local -a candidates
    if [[ -n "${THVV_PYTHON:-}" ]]; then
        candidates=("$THVV_PYTHON")
    else
        candidates=(python3.13 python3.12 python3.11 python3 "$HOME/miniconda3/bin/python3")
    fi
    for candidate in "${candidates[@]}"; do
        if "$candidate" -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null; then
            runtime="$candidate"
            return 0
        fi
    done
    echo '未找到 Python 3.11 或更新版本。请先安装 Python，或用 THVV_PYTHON 指定可用的 Python 路径。' >&2
    return 1
}
