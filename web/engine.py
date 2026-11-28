"""Translate the approved web controls into native THVV invocations."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urlsplit, urlunsplit

from thvv.perf.scripts.setup_tokenizer import configure_downloads
from thvv.prepare_linux import runtime_env

ROOT = Path(__file__).resolve().parents[1]
BUCKETS = ['1k', '9k', '16k', '32k', '64k', '128k', '200k', '512k', '1m']
PERF_ENV = {'buckets': 'BUCKETS', 'ladder': 'CONCURRENCY_LADDER', 'cooldown': 'BUCKET_COOLDOWN', 'success': 'SUCCESS_RATE_MIN', 'warmup': 'WARMUP_NUM', 'tokenizer': 'TOKENIZER'}
EVAL_FLAGS = {'limit': '--limit', 'repeats': '--repeats', 'batch': '--eval_batch_size', 'judge-model': '--judge_model', 'judge-url': '--judge_base_url', 'sandbox': '--sandbox'}


_registry_cache = {'stamp': None, 'value': None}


def registry():
    script = ROOT / 'thvv/eval/scripts/run_eval.py'
    try:
        stat = script.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None
    if _registry_cache['value'] is not None and _registry_cache['stamp'] == stamp:
        return _registry_cache['value']
    tree = ast.parse(script.read_text())
    names = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ('SUPPORTED_DATASETS', 'ALL_EXCLUDED_DATASETS'):
                    names[target.id] = ast.literal_eval(node.value)
    value = (names['SUPPORTED_DATASETS'], names['ALL_EXCLUDED_DATASETS'])
    _registry_cache['stamp'] = stamp
    _registry_cache['value'] = value
    return value


def judge_defaults():
    """Read the native CLI fallbacks without importing its runtime dependencies."""
    names = {'--judge_model': 'model', '--judge_base_url': 'url'}
    result = {}
    tree = ast.parse((ROOT / 'thvv/eval/scripts/run_eval.py').read_text())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'add_argument' and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value in names):
            continue
        default = next(k.value for k in node.keywords if k.arg == 'default')
        # Web runs isolate these environment variables and use the native fallback.
        result[names[node.args[0].value]] = ast.literal_eval(default.args[1])
    if result.keys() != {'model', 'url'}:
        raise ValueError('无法读取框架裁判模型默认配置')
    return result


def requires_judge(spec):
    datasets, _ = registry()
    return spec['mode'] == 'eval' and any(datasets[d].get('requires_judge_model') for d in spec['datasets'])


def judge_config(options):
    defaults = judge_defaults()
    model = options.get('judge-model', '').strip() or defaults['model']
    url = options.get('judge-url', '').strip() or defaults['url']
    if any(ord(c) < 32 for c in model):
        raise ValueError('请填写有效裁判模型名称')
    return {'model': model, 'url': base_url(url)}


def base_url(value):
    value = value.strip()
    if any(ord(c) < 32 for c in value):
        raise ValueError('接口地址包含无效字符')
    u = urlsplit(value)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.fragment:
        raise ValueError('请填写不含账号密码的 HTTP(S) 基础地址')
    # Credentials belong in the encrypted key field, not in an endpoint URL.
    if re.search(r'(^|&)(api[-_]?key|key|token|access[-_]?token|authorization|secret)=', u.query, re.I):
        raise ValueError('请将密钥放到 API 密钥字段')
    if u.query:
        raise ValueError('当前框架入口不支持带查询参数的基础地址，请使用服务商提供的基础 URL')
    try:
        _ = u.port
    except ValueError:
        raise ValueError('接口地址端口无效') from None
    path = re.sub(r'/(chat/completions|messages)$', '', u.path.rstrip('/'), flags=re.I)
    return urlunsplit((u.scheme, u.netloc, path, '', ''))


def endpoint(url, protocol):
    return base_url(url) + ('/messages' if protocol == 'anthropic' else '/chat/completions')


def engine_python():
    return Path(os.environ.get('THVV_ENGINE_PYTHON', ROOT / '.venv-engine/bin/python')).absolute()


def engine_env(data_dir=None):
    env = dict(os.environ)
    env.pop('DATASET_DIR', None)
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    for key in ['API_URL', 'API_KEY', 'MODEL_NAME', 'TOKENHUB_URL', 'TOKENHUB_KEY', 'TOKENHUB_MODEL', 'PROVIDER', 'PROTOCOL', 'TOKENIZER', 'TEMPERATURE', 'DATASET_TOKENIZER', 'TPM_BUDGET', 'EVAL_DATASET_DIR', 'JUDGE_API_KEY', 'JUDGE_MODEL', 'JUDGE_BASE_URL', 'PROVIDER_OVERRIDE', 'datasets', *PERF_ENV.values(), *('N_' + b for b in BUCKETS)]:
        env.pop(key, None)
    env['PATH'] = str(engine_python().parent) + os.pathsep + env.get('PATH', '')
    env['PYTHONUNBUFFERED'] = '1'
    env['THVV_ALLOW_EMPTY_API_KEY'] = '1'
    env = runtime_env(env, data_dir or os.environ.get('THVV_WEB_DATA', ROOT / '.thvv-web'))
    return configure_downloads(env)


def readiness():
    return {'ready': engine_python().exists() and (engine_python().parent / 'evalscope').is_file(), 'python': str(engine_python())}


def normalize_spec(data):
    result = {key: data[key] for key in ('object_id', 'model', 'mode', 'protocol')}
    model = result['model'].strip()
    if not model or model.startswith(('/', '-')) or '..' in model.split('/') or any(ord(c) < 32 for c in model):
        raise ValueError('请填写有效模型名称')
    result['model'] = model
    options = {k: str(v).strip() for k, v in data.get('options', {}).items() if v is not None and str(v).strip()}
    allowed = {'perf-command', 'bucket', 'number', 'parallel', 'provider-name', *PERF_ENV, *EVAL_FLAGS, *('n-' + b for b in BUCKETS)}
    if options.keys() - allowed:
        raise ValueError('包含未支持的测试参数')
    for key in ['number', 'parallel', 'limit', 'repeats', 'batch', 'warmup', 'cooldown', *('n-' + b for b in BUCKETS)]:
        if key in options:
            if not options[key].isdigit() or int(options[key]) < (0 if key in ('warmup', 'cooldown') else 1):
                raise ValueError(f'{key} 的数值无效')
    if 'success' in options and not 0 <= float(options['success']) <= 100:
        raise ValueError('成功率阈值须在 0–100 之间')
    if 'provider-name' in options and not re.fullmatch(r'[\w.-]+', options['provider-name']):
        raise ValueError('供应商名称仅支持文字、数字、点、连字符和下划线')
    if 'judge-url' in options:
        options['judge-url'] = base_url(options['judge-url'])
    datasets, excluded = registry()
    chosen = list(dict.fromkeys(data.get('datasets', [])))
    if data.get('all'):
        chosen = [k for k in datasets if k not in excluded]
    if result['mode'] == 'eval':
        if result['protocol'] != 'openai':
            raise ValueError('当前框架效果评测仅支持 OpenAI 兼容协议')
        if not chosen or set(chosen) - datasets.keys():
            raise ValueError('请选择框架支持的数据集')
        if 'swe_bench_pro' in chosen and 'sandbox' not in options:
            raise ValueError('SWE-Bench Pro 需要 Sandbox 配置')
    else:
        command = options.get('perf-command', 'bench')
        if command not in ('bench', 'bench-all'):
            raise ValueError('无效压测方式')
        if command == 'bench' and options.get('bucket') not in BUCKETS:
            raise ValueError('请选择压测输入长度')
        if 'buckets' in options and any(b not in BUCKETS for b in options['buckets'].split()):
            raise ValueError('输入长度列表含无效档位')
        if 'ladder' in options and any(not v.isdigit() or int(v) < 1 for v in options['ladder'].split()):
            raise ValueError('并发梯度应为空格分隔的正整数')
    result.update(options=options, datasets=chosen, all=bool(data.get('all')))
    return result


def prepare_run(task, workspace, secrets):
    """Real source directories keep all native outputs inside this task's workspace."""
    for mode in ('perf', 'eval'):
        dest = workspace / mode
        dest.mkdir(parents=True)
        shutil.copytree(ROOT / 'thvv' / mode / 'scripts', dest / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(ROOT / 'thvv' / mode / 'run.sh', dest / 'run.sh')
        shutil.copy2(ROOT / 'thvv' / mode / 'requirements.txt', dest / 'requirements.txt')
        for name in ('datasets', 'datasets_clean', 'references'):
            source = ROOT / 'thvv' / mode / name
            if source.exists():
                (dest / name).symlink_to(source, target_is_directory=True)
    (workspace / 'configs').mkdir()
    # No .env is copied: the selected endpoint must not be overridden by a global .env.
    env = engine_env()
    spec, obj = task['spec'], task['object']
    env.update(API_URL=endpoint(obj['url'], spec['protocol']), MODEL_NAME=spec['model'], API_KEY=secrets.get('api_key', ''), PROTOCOL=spec['protocol'])
    opts = spec['options']
    if spec['mode'] == 'eval':
        env['API_URL'] = obj['url']
        env['JUDGE_API_KEY'] = secrets.get('judge_api_key', '')
        env['PROVIDER'] = opts.get('provider-name', 'unknown')
        cmd = [str(engine_python()), str(workspace / 'eval/scripts/run_eval.py'), '--datasets', ','.join(spec['datasets'])]
        for key, flag in EVAL_FLAGS.items():
            if key in opts:
                cmd.extend([flag, opts[key]])
        if 'msr_vtt' in spec['datasets']:
            video_dir = ROOT / 'thvv/eval/datasets/msr_vtt/test_1k'
            if video_dir.is_dir():
                cmd.extend(['--dataset_hub', 'huggingface', '--msr_vtt_video_dir', str(video_dir)])
    else:
        for key, name in PERF_ENV.items():
            if key in opts:
                env[name] = opts[key]
        for b in BUCKETS:
            if 'n-' + b in opts:
                env['N_' + b] = opts['n-' + b]
        command = opts.get('perf-command', 'bench')
        cmd = ['bash', '-o', 'pipefail', str(workspace / 'perf/run.sh'), command]
        if command == 'bench':
            cmd += [opts['bucket'], opts.get('number', '500'), opts.get('parallel', '10')]
    return cmd, env


def missing_perf_data(spec):
    if spec['mode'] != 'perf':
        return []
    opts = spec['options']
    buckets = [opts.get('bucket')] if opts.get('perf-command', 'bench') == 'bench' else opts.get('buckets', ' '.join(BUCKETS[:-1])).split()
    missing = []
    for bucket in buckets:
        p = ROOT / 'thvv/perf/datasets_clean' / f'perf_zh_{bucket}.jsonl'
        if not p.is_file():
            missing.append(bucket)
        else:
            with p.open('rb') as f:
                beginning = f.read(80)
                if not beginning or beginning.startswith(b'version https://git-lfs.github.com/spec'):
                    missing.append(bucket)
                    continue
                f.seek(0)
                try:
                    first = json.loads(f.readline())
                    if not isinstance(first, list) or not first or not all(isinstance(message, dict) and 'role' in message and 'content' in message for message in first):
                        missing.append(bucket)
                except (ValueError, UnicodeError):
                    missing.append(bucket)
    return missing
