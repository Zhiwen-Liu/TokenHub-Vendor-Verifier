#!/usr/bin/env python3
"""Resolve a verified local tokenizer: cache, ModelScope, then HF mirror."""

import argparse
from contextlib import redirect_stdout
import os
from pathlib import Path
import sys

DEFAULT_HF_ENDPOINT = 'https://hf-mirror.com'
# Only tokenizer/configuration files, never model weights.
TOKENIZER_FILES = ['*.json', '*.py', '*.txt', '*.model', '*.tiktoken', '*.jinja']


def configure_downloads(env):
    # Hub settings must be set before importing transformers/huggingface_hub.
    if not env.get('HF_ENDPOINT'):
        env['HF_ENDPOINT'] = DEFAULT_HF_ENDPOINT
    if env['HF_ENDPOINT'].rstrip('/') == DEFAULT_HF_ENDPOINT:
        env.setdefault('HF_HUB_DISABLE_XET', '1')
        env.setdefault('HF_HUB_DISABLE_IMPLICIT_TOKEN', '1')
    return env


def _snapshot(source, name, local_only):
    if source == 'modelscope':
        from modelscope import snapshot_download
        return snapshot_download(name, allow_patterns=TOKENIZER_FILES, local_files_only=local_only)
    from huggingface_hub import snapshot_download
    return snapshot_download(name, allow_patterns=TOKENIZER_FILES, local_files_only=local_only,
                             endpoint=os.environ['HF_ENDPOINT'])


def _verify(path):
    from transformers import AutoTokenizer
    try:
        AutoTokenizer.from_pretrained(path, trust_remote_code=True, local_files_only=True)
    except AttributeError as exc:
        # Same compatibility fallback as EvalScope 1.9.0's tokenizer loader.
        if 'max_position_embeddings' not in str(exc):
            raise
        AutoTokenizer.from_pretrained(path, trust_remote_code=False, local_files_only=True)


def resolve_tokenizer(name, *, download=False):
    """Return the local path used by both readiness checks and EvalScope."""
    configure_downloads(os.environ)
    local = Path(name).expanduser()
    if local.is_dir():
        path = str(local.resolve())
        _verify(path)
        return path
    if local.is_absolute() or name.startswith(('./', '../', '~')):
        raise FileNotFoundError(f'Tokenizer 本地目录不存在：{name}')

    sources = [('modelscope', '魔搭社区'), ('huggingface', 'Hugging Face 下载源 ' + os.environ['HF_ENDPOINT'])]
    errors = []
    for local_only in ([True, False] if download else [True]):
        for source, label in sources:
            try:
                if not local_only:
                    print(f'[tokenizer] 正在从{label}准备 {name}', file=sys.stderr, flush=True)
                path = str(Path(_snapshot(source, name, local_only)).resolve())
                _verify(path)
                print(f'[tokenizer] 本地加载通过：{name}（{label}缓存）', file=sys.stderr, flush=True)
                return path
            except Exception as exc:
                detail = f'{label}: {type(exc).__name__}: {exc}'
                errors.append(detail)
                if not local_only:
                    print('[tokenizer] ' + detail, file=sys.stderr, flush=True)
    if not download:
        raise FileNotFoundError(f'Tokenizer 本地缓存不完整：{name}\n' + '\n'.join(errors))
    raise RuntimeError('Tokenizer 下载或加载失败，魔搭社区和 Hugging Face 下载源均未就绪。\n' + '\n'.join(errors[-2:]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resolve', action='store_true', help='仅在标准输出返回已验证的本地目录')
    args = parser.parse_args()
    try:
        # SDK progress messages must not contaminate the path consumed by Bash.
        with redirect_stdout(sys.stderr):
            path = resolve_tokenizer(os.environ.get('TOKENIZER', 'zai-org/GLM-4.6'), download=True)
        print(path if args.resolve else f'[OK] Tokenizer 已准备：{path}')
        return 0
    except Exception as exc:
        print(f'[fail] {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
