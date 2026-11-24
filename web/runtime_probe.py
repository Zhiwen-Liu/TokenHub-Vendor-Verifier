"""Runs in the engine environment. Loads data/tokenizers, never performs inference."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from web.diagnostics import failure_details
from thvv.perf.scripts.setup_tokenizer import configure_downloads, resolve_tokenizer


class OfflineCacheMiss(BaseException):
    """Abort this check before SDK Exception/OSError retry loops can sleep.

    This is confined to the disposable offline probe process and caught by main.
    Online preparation and the original framework keep their normal retry policy.
    """


def offline():
    os.environ.update(HF_HUB_OFFLINE='1', HF_DATASETS_OFFLINE='1', MODELSCOPE_OFFLINE='1',
                      LITELLM_LOCAL_MODEL_COST_MAP='True')
    def deny(*args, **kwargs):
        raise OfflineCacheMiss('本地缓存不完整，加载器需要访问远端数据源')
    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    socket.create_connection = deny
    socket.getaddrinfo = deny


def load_native():
    location = ROOT / 'thvv/eval/scripts/run_eval.py'
    sys.path.insert(0, str(location.parent))
    spec = importlib.util.spec_from_file_location('thvv_native_eval', location)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    return native


def load_data(name, options, prepare=False):
    # Build the actual native command to preserve its subset, hub and local mirror rules.
    native = load_native()
    sys.argv = ['run_eval.py', '--model_name', 'environment-check', '--base_url', 'http://127.0.0.1:1/v1', '--api_key', 'unused', '--datasets', name]
    if options.get('limit'):
        sys.argv += ['--limit', options['limit']]
    args = native.parse_args()
    # Explicit preparation may repair an incomplete snapshot; checks remain offline.
    command, _ = native.build_eval_command(args, name, use_cached_tau2=not prepare)
    from evalscope.arguments import add_argument
    from evalscope.config import TaskConfig
    from evalscope.api.registry import get_benchmark
    parser = argparse.ArgumentParser()
    add_argument(parser)
    config = TaskConfig.from_args(parser.parse_args(command[command.index('eval') + 1:]))
    # Repetitions duplicate samples in RAM and do not affect which data is downloaded.
    config.repeats = 1
    adapter = get_benchmark(name, config)
    loaded = adapter.load_dataset()
    # Native adapters return a DatasetDict or a (test, few-shot) pair.
    if loaded is None:
        loaded = adapter.test_dataset
    if isinstance(loaded, tuple):
        loaded = loaded[0]
    total, online = 0, 0
    for subset in loaded.values():
        total += len(subset)
        for sample in subset:
            if isinstance(sample.input, list):
                for message in sample.input:
                    if not isinstance(message.content, list):
                        continue
                    for content in message.content:
                        for field in ('video', 'image', 'audio'):
                            value = getattr(content, field, None)
                            if not isinstance(value, str) or value.startswith('data:'):
                                continue
                            if value.startswith(('http://', 'https://')):
                                online += 1
                            elif value and not Path(value).is_file():
                                raise ValueError('媒体文件缺失：' + value)
                            elif value:
                                with Path(value).open('rb') as file:
                                    if file.read(80).startswith(b'version https://git-lfs.github.com/spec'):
                                        raise ValueError('媒体文件仍为 Git LFS 指针：' + value)
    if not total:
        raise ValueError('数据集为空，不能开始评测')
    return {'detail': f'按框架规则成功加载 {total} 条样本（未发出模型请求）', 'online_media': online}


def main():
    command, value, output = sys.argv[1:4]
    options = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
    configure_downloads(os.environ)
    if command.startswith('check-'):
        offline()
    try:
        if command.endswith('data'):
            result = load_data(value, options, prepare=command.startswith('prepare-'))
        elif command.endswith('tokenizer'):
            resolve_tokenizer(value, download=command.startswith('prepare-'))
            result = {'detail': 'Tokenizer 本地加载通过'}
        else:
            raise ValueError('未知检查项')
        result['ready'] = True
    except (Exception, OfflineCacheMiss) as exc:
        result = failure_details(f'{type(exc).__name__}: {exc}', kind='tokenizer' if command.endswith('tokenizer') else 'data',
                                 preparing=command.startswith('prepare-'), code='missing_cache' if isinstance(exc, OfflineCacheMiss) else None)
    Path(output).write_text(json.dumps(result, ensure_ascii=False))
    print(result['detail'], flush=True)
    if result.get('error_details'):
        print(result['error_details'], flush=True)
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
