"""Runs in the engine environment. Collects Docker image names required by datasets."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_native():
    location = ROOT / 'thvv/eval/scripts/run_eval.py'
    sys.path.insert(0, str(location.parent))
    spec = importlib.util.spec_from_file_location('thvv_native_eval', location)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    return native


def collect_images(dataset_name, options):
    """Return a list of Docker image names required by the given dataset."""
    native = load_native()
    registry = native.SUPPORTED_DATASETS
    info = registry.get(dataset_name, {})

    requires_docker = info.get('requires_docker', False)
    requires_sandbox = info.get('requires_sandbox', False)
    if not requires_docker and not requires_sandbox:
        return []

    if dataset_name == 'live_code_bench':
        return ['python:3.11-slim']

    if dataset_name == 'swe_bench_verified_mini_agentic':
        return _collect_swebench_images(dataset_name, options)

    if dataset_name == 'swe_bench_pro':
        return _collect_swebench_pro_images(dataset_name, options)

    for prefix in ('swe_bench',):
        if dataset_name.startswith(prefix):
            if info.get('requires_sandbox'):
                return _collect_swebench_pro_images(dataset_name, options)
            return _collect_swebench_images(dataset_name, options)

    if dataset_name.startswith('live_code_bench'):
        return ['python:3.11-slim']

    return []


def _collect_swebench_images(dataset_name, options):
    """Collect image names for swe_bench_verified_mini_agentic style datasets."""
    from evalscope.benchmarks.swe_bench.utils import get_remote_docker_image_from_id
    samples = _load_dataset_samples(dataset_name, options)
    images = []
    seen = set()
    for sample in samples:
        instance_id = sample.metadata.get('instance_id', '')
        if not instance_id:
            continue
        image = get_remote_docker_image_from_id(instance_id)
        if image not in seen:
            seen.add(image)
            images.append(image)
    return images


def _collect_swebench_pro_images(dataset_name, options):
    """Collect image names for swe_bench_pro style datasets."""
    from evalscope.benchmarks.swe_bench_pro.utils import get_dockerhub_image_uri
    samples = _load_dataset_samples(dataset_name, options)
    images = []
    seen = set()
    for sample in samples:
        md = sample.metadata
        instance_id = md.get('instance_id', '')
        repo = md.get('repo', '')
        tag = md.get('dockerhub_tag', '')
        if tag:
            image = f'jefzda/sweap-images:{tag}'
        elif instance_id and repo:
            image = get_dockerhub_image_uri(instance_id, repo)
        else:
            continue
        if image not in seen:
            seen.add(image)
            images.append(image)
    return images


def _load_dataset_samples(dataset_name, options):
    """Load dataset samples via the framework without running inference."""
    native = load_native()
    sys.argv = ['run_eval.py', '--model_name', 'environment-check',
                '--base_url', 'http://127.0.0.1:1/v1', '--api_key', 'unused',
                '--datasets', dataset_name]
    if options.get('limit'):
        sys.argv += ['--limit', options['limit']]
    args = native.parse_args()
    command, _ = native.build_eval_command(args, dataset_name, use_cached_tau2=True)

    from evalscope.arguments import add_argument
    from evalscope.config import TaskConfig
    from evalscope.api.registry import get_benchmark

    parser = argparse.ArgumentParser()
    add_argument(parser)
    config = TaskConfig.from_args(parser.parse_args(command[command.index('eval') + 1:]))
    config.repeats = 1

    adapter = get_benchmark(dataset_name, config)
    if hasattr(adapter, 'build_docker_images'):
        adapter.build_docker_images = False
    if hasattr(adapter, 'pull_remote_images_if_available'):
        adapter.pull_remote_images_if_available = False
    adapter.load_dataset()
    loaded = adapter.test_dataset
    samples = []
    for subset in loaded.values():
        samples.extend(subset)
    return samples


def main():
    dataset_name = sys.argv[1]
    output_path = sys.argv[2]
    options = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}

    from thvv.perf.scripts.setup_tokenizer import configure_downloads
    configure_downloads(os.environ)

    try:
        images = collect_images(dataset_name, options)
        result = {'ready': True, 'images': images}
    except Exception as exc:
        result = {'ready': False, 'images': [], 'error': f'{type(exc).__name__}: {exc}'}

    Path(output_path).write_text(json.dumps(result, ensure_ascii=False))
    print(f'Collected {len(result["images"])} Docker images for {dataset_name}', flush=True)
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
