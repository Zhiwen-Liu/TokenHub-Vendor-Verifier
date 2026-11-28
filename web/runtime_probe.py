"""Runs in the engine environment. Loads data/tokenizers, never performs inference."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
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


def _cache_dirs(adapter):
    """Yield the evalscope Arrow cache dirs for every subset of this adapter."""
    from evalscope.utils.io_utils import gen_hash, safe_filename
    from evalscope.constants import DEFAULT_EVALSCOPE_CACHE_DIR

    if adapter.dataset_dir:
        base = os.path.join(adapter.dataset_dir, 'datasets')
    else:
        base = os.path.join(DEFAULT_EVALSCOPE_CACHE_DIR, 'datasets')
    for subset in adapter.subset_list:
        split = subset if adapter.split_as_subset else adapter.eval_split
        subset_name = adapter.default_subset if adapter.split_as_subset else subset
        dataset_hash = gen_hash(f'{adapter.dataset_id}{split}{subset_name}{None}{dict()}')
        yield os.path.join(base, f'{safe_filename(adapter.dataset_id)}-{dataset_hash}')


def _count_arrow_cache(adapter):
    """Count total samples from on-disk Arrow cache (memory-mapped, ~0 extra RAM).

    Returns the total row count across all subsets, or *None* when any
    subset's cache directory is missing (meaning data hasn't been downloaded).
    """
    import datasets as hf_datasets

    total = 0
    for cache_dir in _cache_dirs(adapter):
        if not os.path.isdir(cache_dir):
            return None
        ds = hf_datasets.load_from_disk(cache_dir)
        total += len(ds)
        del ds
    return total


def _clear_invalid_caches(adapter, hint_paths=()):
    """Remove empty/incomplete Arrow cache dirs so a prepare can re-download.

    evalscope only checks ``os.path.exists`` before ``load_from_disk``; a leftover
    empty directory therefore blocks re-download forever. We only delete dirs that
    lack ``dataset_info.json`` (never a valid cache), and only for this adapter's
    own subsets plus any path quoted in the failure, so concurrent prepares for
    other datasets are never touched.
    """
    import shutil

    removed = []
    for path in dict.fromkeys([*_cache_dirs(adapter), *hint_paths]):
        if os.path.isdir(path) and not os.path.exists(os.path.join(path, 'dataset_info.json')):
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path)
    return removed


def load_data(name, options, prepare=False):
    # Build the actual native command to preserve its subset, hub and local mirror rules.
    native = load_native()
    sys.argv = ['run_eval.py', '--model_name', 'environment-check', '--base_url', 'http://127.0.0.1:1/v1', '--api_key', 'unused', '--datasets', name]
    if options.get('limit'):
        sys.argv += ['--limit', options['limit']]
    # Mirror engine.py: msr_vtt must use huggingface hub + local video dir
    if name == 'msr_vtt':
        video_dir = ROOT / 'thvv/eval/datasets/msr_vtt/test_1k'
        if video_dir.is_dir():
            sys.argv += ['--dataset_hub', 'huggingface', '--msr_vtt_video_dir', str(video_dir)]
    args = native.parse_args()
    # Explicit preparation may repair an incomplete snapshot; checks remain offline.
    command, _ = native.build_eval_command(args, name, use_cached_tau2=not prepare)
    from evalscope.arguments import add_argument
    from evalscope.config import TaskConfig
    from evalscope.api.registry import get_benchmark
    parser = argparse.ArgumentParser()
    add_argument(parser)
    config = TaskConfig.from_args(parser.parse_args(command[command.index('eval') + 1:]))
    config.repeats = 1
    adapter = get_benchmark(name, config)
    if hasattr(adapter, 'build_docker_images'):
        adapter.build_docker_images = False
    if hasattr(adapter, 'pull_remote_images_if_available'):
        adapter.pull_remote_images_if_available = False
    # 判断是否为多模态数据集（需要遍历样本验证媒体文件）
    ds_config = native.SUPPORTED_DATASETS.get(name, {})
    is_multimodal = bool(ds_config.get('multimodal'))

    if not is_multimodal:
        config.limit = 3

        def build_adapter():
            fresh = get_benchmark(name, config)
            if hasattr(fresh, 'build_docker_images'):
                fresh.build_docker_images = False
            if hasattr(fresh, 'pull_remote_images_if_available'):
                fresh.pull_remote_images_if_available = False
            return fresh

        adapter = build_adapter()
        try:
            test_ds, _ = adapter.load()
        except Exception as exc:
            # A leftover empty Arrow dir blocks evalscope from re-downloading.
            # Only preparation may repair it; offline checks must stay read-only.
            if not prepare:
                raise
            hints = re.findall(r'(/[^\s"\']+?)\s+is neither', str(exc))
            if not _clear_invalid_caches(adapter, hints):
                raise
            adapter = build_adapter()
            test_ds, _ = adapter.load()
        if test_ds is None:
            raise ValueError('数据集为空，下载可能失败')
        sampled = sum(len(subset) for subset in test_ds.values())
        del test_ds
        if not sampled:
            raise ValueError('数据集为空，不能开始评测')
        total = _count_arrow_cache(adapter) or sampled
        del adapter
        verb = '下载' if prepare else '加载'
        return {'detail': f'按框架规则成功{verb}验证（共 {total} 条样本，未发出模型请求）', 'online_media': 0}
    # 多模态检查模式：需要遍历样本验证媒体文件
    loaded = adapter.load_dataset()
    if loaded is None:
        loaded = adapter.test_dataset
    if isinstance(loaded, tuple):
        loaded = loaded[0]
    total = sum(len(subset) for subset in loaded.values())
    if not total:
        raise ValueError('数据集为空，不能开始评测')
    # 检查模式：遍历样本验证媒体文件
    online = 0
    for subset in loaded.values():
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
    return {'detail': f'按框架规则成功加载 {total} 条样本（未发出模型请求）', 'online_media': online}


def main():
    command, value, output = sys.argv[1:4]
    options = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
    configure_downloads(os.environ)
    if command.startswith('check-'):
        if command == 'check-data':
            native = load_native()
            ds_config = native.SUPPORTED_DATASETS.get(value, {})
            if not ds_config.get('multimodal'):
                offline()
        else:
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
