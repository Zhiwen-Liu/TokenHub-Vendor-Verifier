"""Selected-test readiness and preparation, isolated from model test jobs."""
from __future__ import annotations

import copy
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

from . import engine
from .diagnostics import failure_details
from thvv.prepare_linux import inspect_component


DATASET_MIN_EVALSCOPE = {
    'tvbench': '1.9.1',
}


def _sanitize_key(key):
    return re.sub(r'[^a-zA-Z0-9_-]', '_', key)


class WorkerContext:
    """Encapsulates a single check/prepare item's subprocess execution."""

    def __init__(self, env, item_key):
        self.env = env
        self.item_key = item_key
        self.process = None
        self.cancelled = threading.Event()
        self.log_path = env.directory / f'worker-{_sanitize_key(item_key)}.log'

    def command(self, args, timeout=120, cmd_env=None, idle_timeout=None):
        if self.cancelled.is_set() or self.env.stopping.is_set():
            return False
        effective_idle = idle_timeout or min(timeout, 300)
        def _lower_priority():
            try:
                os.nice(10)
            except OSError:
                pass
        with self.log_path.open('w+') as output:
            try:
                self.process = subprocess.Popen(
                    args, cwd=engine.ROOT,
                    env=cmd_env or engine.engine_env(self.env.directory.parent),
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=True, preexec_fn=_lower_priority)
            except OSError as exc:
                self.env.log(f'[{self.item_key}] {exc}\n')
                return False
            last_activity = time.monotonic()
            offset = 0
            while True:
                with self.log_path.open() as reader:
                    reader.seek(offset)
                    chunk = reader.read()
                    if chunk:
                        offset = reader.tell()
                        last_activity = time.monotonic()
                if chunk:
                    self.env.log(chunk)
                code = self.process.poll()
                if code is not None:
                    with self.log_path.open() as reader:
                        reader.seek(offset)
                        self.env.log(reader.read())
                    self.process = None
                    return code == 0
                idle = time.monotonic() - last_activity
                if self.cancelled.is_set() or self.env.stopping.is_set() or idle > effective_idle:
                    terminated = self.cancelled.is_set() or self.env.stopping.is_set()
                    try:
                        os.killpg(self.process.pid, signal.SIGTERM)
                        self.process.wait(timeout=3)
                    except (subprocess.TimeoutExpired, OSError, ProcessLookupError):
                        try:
                            os.killpg(self.process.pid, signal.SIGKILL)
                            self.process.wait()
                        except (OSError, ProcessLookupError):
                            pass
                    self.process = None
                    reason = '操作已被终止' if terminated else f'操作无响应超时（{int(effective_idle)} 秒无新输出）；已下载缓存会保留，下次准备可复用'
                    self.env.log(f'\n[{self.item_key}] {reason}\n')
                    return False
                self.cancelled.wait(.2)

    def stop(self):
        self.cancelled.set()
        proc = self.process
        if proc:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass

    def probe(self, kind, value, options, prepare=False):
        path = self.env.directory / f'probe-{_sanitize_key(self.item_key)}.json'
        path.unlink(missing_ok=True)
        command = ('prepare-' if prepare else 'check-') + kind
        self.command([str(engine.engine_python()), str(engine.ROOT / 'web/runtime_probe.py'),
                      command, value, str(path), json.dumps(options)],
                     timeout=600 if prepare else 300)
        if path.exists():
            return json.loads(path.read_text())
        return failure_details('加载进程未完成，或达到检查时间上限', kind=kind, preparing=prepare, code='timeout')


def runtime_spec(spec):
    # No endpoint or credential is needed for downloading dependencies and data.
    return {'mode': spec['mode'], 'model': spec.get('model', ''),
            'datasets': spec.get('datasets', []) if spec['mode'] == 'eval' else [],
            'options': {k: v for k, v in spec.get('options', {}).items()
                        if k in ('bucket', 'buckets', 'perf-command', 'tokenizer', 'limit')}}


def fingerprint(spec):
    return hashlib.sha256(json.dumps(runtime_spec(spec), sort_keys=True).encode()).hexdigest()


def dependency_requirements(spec):
    registry, _ = engine.registry()
    datasets = spec.get('datasets', []) if spec['mode'] == 'eval' else []
    chosen = [registry[d] for d in datasets]
    required = {'core'}
    for flag, name in [('requires_docker', 'docker'), ('requires_sandbox', 'docker'),
                       ('requires_swebench', 'swebench'), ('requires_ms_enclave', 'ms_enclave')]:
        if any(d.get(flag) for d in chosen):
            required.add(name)
    if 'tau2_bench' in datasets:
        required.add('tau2')
    if 'msr_vtt' in datasets:
        required.update(('java', 'video', 'git_lfs'))
    if spec['mode'] == 'perf':
        required.add('git_lfs')
    return required


def dependency_stamp():
    """Cheap filesystem signature; never import engine packages or run pip."""
    python = engine.engine_python()
    paths = [python, python.parent.parent / 'pyvenv.cfg',
             engine.ROOT / 'thvv/eval/requirements.txt', engine.ROOT / 'thvv/perf/requirements.txt',
             engine.ROOT / 'web/engine-requirements.txt', engine.ROOT / 'web/dependency_probe.py']
    paths.extend([engine.ROOT / 'thvv/prepare_linux.py', engine.ROOT / 'thvv/prepare-docker.sh'])
    paths.extend(python.parent.parent.glob('lib/python*/site-packages/*.dist-info/METADATA'))
    values = []
    for path in sorted(paths):
        try:
            stat = path.stat()
            values.append((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ino))
        except OSError:
            values.append((str(path), None))
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def selected_buckets(spec):
    opts = spec['options']
    return ([opts['bucket']] if opts.get('bucket') else []) if opts.get('perf-command', 'bench') == 'bench' else opts.get('buckets', ' '.join(engine.BUCKETS[:-1])).split()


def tokenizers(spec):
    """Read the pinned shell's keyword table instead of maintaining a second default."""
    script = (engine.ROOT / 'thvv/perf/run.sh').read_text()
    model = spec.get('model', '').lower()
    selected = 'zai-org/GLM-4.6'
    for patterns, name in re.findall(r'^\s+(\*[^\n]+?)\)\s+detected="([^"]+)"', script, re.M):
        if any(p.strip('*') in model for p in patterns.split('|')):
            selected = name
            break
    explicit = spec['options'].get('tokenizer')
    return list(dict.fromkeys(explicit or ('zai-org/GLM-4.6' if b in ('512k', '1m') else selected) for b in selected_buckets(spec)))


class Environment:
    def __init__(self, directory, gate, tasks_active):
        self.directory = Path(directory) / 'environment'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.gate, self.tasks_active = gate, tasks_active
        self.lock = threading.RLock()
        self.process = None
        self.thread = None
        self.stopping = threading.Event()
        self.workers = {}
        self.workers_lock = threading.Lock()
        self.current = None
        self.groups = {'environment': None, 'data': None}
        self.core_report = {}
        self.cleanup_plan = None
        path = self.directory / 'status.json'
        if path.exists():
            saved = json.loads(path.read_text())
            if saved.get('version') == 2:
                self.current = saved.get('current')
                self.groups.update(saved.get('groups', {}))
            # Older combined checks have no dependency signature; check once again.
            if self.current and self.current['status'] == 'running':
                self.current.update(status='interrupted', ready=False, message='服务关闭，环境检查或准备已中断，请重新执行')
                self.groups[self.current['scope']] = self.current
                self.persist()

    def persist(self):
        target = self.directory / 'status.json'
        tmp = target.with_suffix('.tmp')
        tmp.write_text(json.dumps({'version': 2, 'current': self.current, 'groups': self.groups}, ensure_ascii=False))
        tmp.replace(target)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy({**(self.current or {}), 'groups': self.groups})

    def busy(self):
        with self.lock:
            return bool(self.current and self.current['status'] == 'running')

    def stop(self):
        with self.lock:
            if not self.busy():
                raise ValueError('当前没有正在进行的操作')
            self.stopping.set()
            proc = self.process
        if proc:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        with self.workers_lock:
            for worker in list(self.workers.values()):
                worker.stop()
        if self.thread:
            self.thread.join(timeout=10)
        with self.lock:
            self.stopping.clear()
            if self.current and self.current['status'] == 'running':
                self.current.update(status='interrupted', ready=False, finished=time.time(),
                                    message='操作已被用户终止；已下载缓存会保留，下次准备可复用')
                self.groups[self.current['scope']] = self.current
                self.persist()
            return self.snapshot()

    def stop_item(self, item_id):
        with self.lock:
            if not self.busy():
                raise ValueError('当前没有正在进行的操作')
        with self.workers_lock:
            worker = self.workers.get(item_id)
        if worker:
            worker.stop()
            with self.workers_lock:
                self.workers.pop(item_id, None)
        self.item(item_id, item_id, 'skipped', '已被用户跳过', True)
        return self.snapshot()

    def readiness(self, spec):
        with self.lock:
            env, data = self.groups['environment'], self.groups['data']
            stamp = dependency_stamp()
            environment_ready = bool(env and env['status'] == 'completed' and env['ready']
                                     and env.get('stamp') == stamp
                                     and dependency_requirements(spec) <= set(env.get('requirements', [])))
            data_ready = bool(data and data['status'] == 'completed' and data['ready']
                              and data.get('stamp') == stamp and data['fingerprint'] == fingerprint(spec)
                              and time.time() - data.get('finished', 0) < 300)
            return {'environment': environment_ready, 'data': data_ready}

    def validated(self, spec):
        return all(self.readiness(spec).values()) and self.system_ready(spec)

    def system_ready(self, spec):
        required = dependency_requirements(spec) & {'java', 'docker'}
        env = engine.engine_env(self.directory.parent)
        return all(inspect_component(component, env)['ready'] for component in sorted(required))

    def start(self, spec, prepare=False, scope='environment'):
        with self.gate, self.lock:
            if scope not in self.groups:
                raise ValueError('未知准备范围')
            if self.busy():
                raise ValueError('环境检查或准备正在进行，请等待结束')
            if prepare and self.tasks_active():
                raise ValueError('请等当前测试任务结束后再准备环境，避免安装过程影响正在运行的测试')
            if prepare and scope == 'environment' and self.readiness(spec)['environment'] and self.system_ready(spec):
                # A valid dependency check authorizes reuse, not another installation.
                self.current = copy.deepcopy(self.groups[scope])
                now = time.time()
                self.current.update(id=uuid.uuid4().hex, action='prepare', started=now, finished=now,
                                    reused_check=True, log='',
                                    message='已就绪：直接复用已有环境，无需重新安装（沿用有效检查结果）')
                for item in self.current['items']:
                    item['disposition'] = 'reused'
                self.groups[scope] = self.current
                self.persist()
                return self.snapshot()
            job_id = uuid.uuid4().hex
            prior_items = {}
            if prepare:
                prior = self.groups.get(scope)
                if prior and prior.get('status') == 'completed':
                    prior_items = {i['id']: i for i in prior.get('items', []) if i.get('state') == 'ready'}
            self.current = {'id': job_id, 'status': 'running', 'action': 'prepare' if prepare else 'check',
                            'scope': scope, 'requirements': sorted(dependency_requirements(spec)),
                            'fingerprint': fingerprint(spec), 'ready': False, 'items': [], 'log': '',
                            'prior_ready': prior_items,
                            'message': '正在检查运行环境' if scope == 'environment' else '正在检查数据集与 Tokenizer', 'started': time.time()}
            self.groups[scope] = self.current
            self.persist()
            self.thread = threading.Thread(target=self.work, args=(runtime_spec(spec), prepare), daemon=True)
            self.thread.start()
            return self.snapshot()

    def preview_cleanup(self, scope, spec):
        from .cleanup import inventory
        with self.gate, self.lock:
            if self.busy() or self.tasks_active():
                raise ValueError('请等待测试和环境操作结束后再清理')
            names = tokenizers(spec) if scope == 'data' and spec['mode'] == 'perf' else []
            plan = inventory(scope, spec, names, data_dir=self.directory.parent)
            plan.update(id=uuid.uuid4().hex, scope=scope, created=time.time())
            self.cleanup_plan = plan
            return {'id': plan['id'], 'scope': scope, 'bytes': plan['bytes'], 'notes': plan['notes'],
                    'items': [{k: e[k] for k in ('label', 'path', 'bytes')} for e in plan['entries']]}

    def clean(self, plan_id):
        with self.gate, self.lock:
            if self.busy() or self.tasks_active():
                raise ValueError('请等待测试和环境操作结束后再清理')
            plan = self.cleanup_plan
            if not plan or plan['id'] != plan_id or time.time() - plan['created'] > 300:
                raise ValueError('清理预览已失效，请重新预览')
            self.cleanup_plan = None
            scope = plan['scope']
            if scope == 'environment':
                self.groups['data'] = None
            self.current = {'id': uuid.uuid4().hex, 'scope': scope, 'action': 'clean', 'status': 'running',
                            'ready': False, 'items': [], 'log': '', 'started': time.time(), 'message': '正在清理已确认的内容'}
            self.groups[scope] = self.current
            self.persist()
            self.thread = threading.Thread(target=self.clean_work, args=(plan,), daemon=True)
            self.thread.start()
            return self.snapshot()

    def clean_work(self, plan):
        from .cleanup import execute
        try:
            execute(plan['entries'])
            with self.lock:
                self.current.update(status='completed', finished=time.time(),
                                    message=f"清理完成：{len(plan['entries'])} 项。需要使用时请重新准备并检查。")
                self.persist()
        except Exception as exc:
            with self.lock:
                self.current.update(status='failed', finished=time.time(), message=f'清理未完成：{exc}')
                self.persist()

    def log(self, value):
        value = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', value)
        value = re.sub(r'\x1b\].*?\x07', '', value)
        value = re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', value)
        for key, secret in os.environ.items():
            if secret and len(secret) > 5 and re.search(r'TOKEN|PASSWORD|SECRET|API_KEY', key, re.I):
                value = value.replace(secret, '[REDACTED]')
        with self.lock:
            self.current['log'] = (self.current['log'] + value)[-60000:]
            self.persist()

    def item(self, key, label, state, detail, automatic=False, **extra):
        value = {'id': key, 'label': label, 'state': state, 'detail': detail, 'automatic': automatic, **extra}
        with self.lock:
            previous = next((i for i in self.current['items'] if i['id'] == key), None)
            value['started'] = previous.get('started', time.time()) if previous else time.time()
            value['elapsed_seconds'] = round(time.time() - value['started'], 1)
            self.current['items'] = [i for i in self.current['items'] if i['id'] != key] + [value]
            self.current['message'] = label + ' · ' + detail
            self.persist()
        return value

    def reuse_prior(self, key, label):
        """If preparing and this item was ready in the prior check, reuse it directly."""
        with self.lock:
            prior = self.current.get('prior_ready', {}).get(key)
        if not prior:
            return False
        prior_copy = {**prior, 'disposition': 'reused', 'started': time.time(), 'elapsed_seconds': 0}
        with self.lock:
            self.current['items'] = [i for i in self.current['items'] if i['id'] != key] + [prior_copy]
            self.current['message'] = label + ' · 复用已有检查结果'
            self.persist()
        return True

    def command(self, args, timeout=120, env=None, idle_timeout=None):
        if self.stopping.is_set():
            raise RuntimeError('服务已关闭')
        effective_idle = idle_timeout or min(timeout, 300)
        def _lower_priority():
            try:
                os.nice(10)
            except OSError:
                pass
        path = self.directory / 'command.log'
        with path.open('w+') as output:
            try:
                self.process = subprocess.Popen(args, cwd=engine.ROOT, env=env or engine.engine_env(self.directory.parent), stdin=subprocess.DEVNULL,
                                                stdout=output, stderr=subprocess.STDOUT, start_new_session=True, preexec_fn=_lower_priority)
            except OSError as exc:
                self.log(str(exc) + '\n')
                return False
            last_activity = time.monotonic()
            offset = 0
            while True:
                with path.open() as reader:
                    reader.seek(offset)
                    chunk = reader.read()
                    if chunk:
                        offset = reader.tell()
                        last_activity = time.monotonic()
                if chunk:
                    self.log(chunk)
                code = self.process.poll()
                if code is not None:
                    with path.open() as reader:
                        reader.seek(offset)
                        self.log(reader.read())
                    self.process = None
                    return code == 0
                idle = time.monotonic() - last_activity
                if self.stopping.is_set() or idle > effective_idle:
                    terminated = self.stopping.is_set()
                    os.killpg(self.process.pid, signal.SIGTERM)
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(self.process.pid, signal.SIGKILL)
                        self.process.wait()
                    self.process = None
                    self.log('\n操作已被终止；已下载缓存会保留，下次准备可复用。\n' if terminated
                             else f'\n操作无响应超时（{int(effective_idle)} 秒无新输出）；已下载缓存会保留，下次准备可复用。\n')
                    return False
                self.stopping.wait(.2)

    def core_ready(self, evalscope_min=None):
        if not engine.readiness()['ready']:
            self.core_report = {}
            return False
        args = ['core']
        if evalscope_min:
            args.append(evalscope_min)
        self.core_report = self.dependency_report(*args)
        return self.core_report['ready'] and self.command([str(engine.engine_python()), '-m', 'pip', 'check'], timeout=30)

    def dependency_report(self, mode, *args):
        path = self.directory / 'dependency-report.json'
        path.unlink(missing_ok=True)
        self.command([str(engine.engine_python()), str(engine.ROOT / 'web/dependency_probe.py'), mode, str(path), *args], timeout=60)
        return json.loads(path.read_text()) if path.exists() else {'ready': False, 'versions': [], 'error_details': '组件检查未完成或超时'}

    def probe(self, kind, value, options, prepare=False):
        path = self.directory / 'probe.json'
        path.unlink(missing_ok=True)
        command = ('prepare-' if prepare else 'check-') + kind
        self.command([str(engine.engine_python()), str(engine.ROOT / 'web/runtime_probe.py'), command, value, str(path), json.dumps(options)], timeout=600 if prepare else 300)
        if path.exists():
            return json.loads(path.read_text())
        return failure_details('加载进程未完成，或达到检查时间上限', kind=kind, preparing=prepare, code='timeout')

    def package(self, name, module, requirement, prepare):
        key = 'package:' + module
        report = self.dependency_report('optional', requirement, module)
        ok = report['ready']
        reused = ok
        if not ok and prepare:
            self.item(key, name, 'preparing', '正在安装框架要求的可选依赖', True)
            # The same pinned source tag, without cloning the current default branch first.
            source = 'https://github.com/sierra-research/tau2-bench/archive/refs/tags/v0.2.0.zip' if module == 'tau2' else requirement
            installed = self.command([str(engine.engine_python()), '-m', 'pip', 'install', '-r', 'web/engine-requirements.txt', source], timeout=600)
            report = self.dependency_report('optional', requirement, module)
            ok = installed and report['ready']
        self.item(key, name, 'ready' if ok else 'missing', '版本与组件加载检查通过' if ok else '组件缺失、版本不符或加载失败，在运行环境中点击“准备缺失项”修复。', True,
                  versions=report.get('versions', []), error_details=report.get('error_details', ''),
                  disposition=('reused' if reused else 'prepared') if ok else None)

    def system_component(self, component, label, prepare):
        self.item(component, label, 'checking', '检查实际版本与可用性')
        result = inspect_component(component, engine.engine_env(self.directory.parent))
        reused = result['ready']
        if not reused and prepare and result['automatic']:
            message = '下载并校验 Java 8，安装到项目数据目录' if component == 'java' else '按 Linux 发行版准备并启动 Docker，需要相应管理员权限'
            self.item(component, label, 'preparing', message, True)
            path = self.directory / f'{component}-report.json'
            path.unlink(missing_ok=True)
            self.command([sys.executable, str(engine.ROOT / 'thvv/prepare_linux.py'), 'prepare', component,
                          '--data-dir', str(self.directory.parent), '--output', str(path)], timeout=600)
            if path.is_file():
                result = json.loads(path.read_text())
            else:
                result = {**result, 'ready': False, 'detail': '自动准备未完成或超时，请展开日志查看原因，修复后重试'}
        state = 'ready' if result['ready'] else 'missing' if result['automatic'] else 'manual'
        required = 'Java 8（评分组件要求；框架未锁定补丁版本）' if component == 'java' else 'Docker 服务可访问（框架未锁定版本）'
        self.item(component, label, state, result['detail'], result['automatic'],
                  versions=[{'name': 'Java' if component == 'java' else 'Docker Engine', 'required': required,
                             'installed': result.get('version') or '未检测到', 'matches': result['ready']}],
                  disposition=('reused' if reused else 'prepared') if result['ready'] else None)

    def check_git_lfs(self, prepare):
        key = 'git_lfs'
        label = 'Git LFS'
        self.item(key, label, 'checking', '检查 Git LFS 是否可用', True)
        try:
            result = subprocess.run(['git', 'lfs', 'version'], capture_output=True, text=True, timeout=10)
            installed = result.returncode == 0
            version = result.stdout.strip().split()[0] if installed else ''
        except (subprocess.TimeoutExpired, OSError):
            installed = False
            version = ''
        if installed:
            self.item(key, label, 'ready', f'Git LFS 可用（{version}）', True,
                      disposition='reused',
                      versions=[{'name': 'Git LFS', 'required': '已安装', 'installed': version or '已安装', 'matches': True}])
            return
        if not prepare:
            self.item(key, label, 'missing', 'Git LFS 未安装；MSR-VTT 视频和压测数据依赖 Git LFS 拉取', True,
                      versions=[{'name': 'Git LFS', 'required': '已安装', 'installed': '未检测到', 'matches': False}])
            return
        self.item(key, label, 'preparing', '正在安装 Git LFS', True)
        ok = self.command(['sudo', '-n', 'apt-get', 'install', '-y', 'git-lfs'], timeout=120)
        if ok:
            subprocess.run(['git', 'lfs', 'install'], capture_output=True, timeout=10)
            self.item(key, label, 'ready', 'Git LFS 已安装', True, disposition='prepared',
                      versions=[{'name': 'Git LFS', 'required': '已安装', 'installed': '已安装', 'matches': True}])
        else:
            self.item(key, label, 'manual', 'Git LFS 自动安装失败，请手动安装：sudo apt-get install git-lfs', True,
                      versions=[{'name': 'Git LFS', 'required': '已安装', 'installed': '未检测到', 'matches': False}])

    def check_environment(self, spec, prepare):
        from packaging.version import Version
        datasets = spec.get('datasets', []) if spec['mode'] == 'eval' else []
        evalscope_min = None
        for ds in datasets:
            min_ver = DATASET_MIN_EVALSCOPE.get(ds)
            if min_ver:
                if evalscope_min is None or Version(min_ver) > Version(evalscope_min):
                    evalscope_min = min_ver
        if not (prepare and self.reuse_prior('core', '评测框架核心依赖')):
            self.item('core', '评测框架核心依赖', 'checking', '检查版本与实际导入', True)
            core = self.core_ready(evalscope_min=evalscope_min)
            reused = core
            if not core and prepare:
                self.item('core', '评测框架核心依赖', 'preparing', '正在安装核心依赖', True)
                env = engine.engine_env()
                env['THVV_PYTHON'] = sys.executable
                if os.environ.get('THVV_ENGINE_PYTHON'):
                    core = self.command([str(engine.engine_python()), '-m', 'pip', 'install', '-r', 'web/engine-requirements.txt'], timeout=600, env=env)
                else:
                    core = self.command(['bash', 'web/install-engine.sh'], timeout=600, env=env)
                core = core and self.core_ready(evalscope_min=evalscope_min)
            self.item('core', '评测框架核心依赖', 'ready' if core else 'missing',
                      '版本、导入及依赖冲突检查通过' if core else '依赖缺失、版本不符或加载失败，在运行环境中点击"准备缺失项"修复。', True,
                      versions=self.core_report.get('versions', []), error_details=self.core_report.get('error_details', ''),
                      disposition=('reused' if reused else 'prepared') if core else None)
        else:
            core = self.core_ready(evalscope_min=evalscope_min)
        if core:
            self.check_evalscope_upgrade(spec, prepare)
        registry, _ = engine.registry()
        chosen = [registry[d] for d in spec['datasets']]
        if any(d.get('requires_docker') or d.get('requires_sandbox') for d in chosen):
            if not (prepare and self.reuse_prior('docker', 'Docker 服务')):
                self.system_component('docker', 'Docker 服务', prepare)
        if 'msr_vtt' in spec['datasets']:
            if not (prepare and self.reuse_prior('java', 'Java（MSR-VTT METEOR 评分）')):
                self.system_component('java', 'Java（MSR-VTT METEOR 评分）', prepare)
        needs_lfs = 'msr_vtt' in spec.get('datasets', []) or spec['mode'] == 'perf'
        if needs_lfs:
            if not (prepare and self.reuse_prior('git_lfs', 'Git LFS')):
                self.check_git_lfs(prepare)
        if core:
            if 'tau2_bench' in spec['datasets']:
                if not (prepare and self.reuse_prior('package:tau2', 'tau2-bench')):
                    self.package('tau2-bench', 'tau2', 'git+https://github.com/sierra-research/tau2-bench@v0.2.0', prepare)
            if any(d.get('requires_swebench') for d in chosen):
                if not (prepare and self.reuse_prior('package:swebench', 'swebench')):
                    self.package('swebench', 'swebench', 'swebench==4.1.0', prepare)
            if any(d.get('requires_ms_enclave') for d in chosen):
                if not (prepare and self.reuse_prior('package:ms_enclave', 'ms_enclave')):
                    self.package('ms_enclave', 'ms_enclave', 'ms-enclave>=0.0.5', prepare)
            if 'msr_vtt' in spec['datasets']:
                if not (prepare and self.reuse_prior('package:pycocoevalcap.meteor.meteor', '视频评分组件')):
                    self.package('视频评分组件', 'pycocoevalcap.meteor.meteor', 'pycocoevalcap==1.2', prepare)
                if not (prepare and self.reuse_prior('package:pycocotools.mask', 'COCO 数据组件')):
                    self.package('COCO 数据组件', 'pycocotools.mask', 'pycocotools==2.0.11', prepare)
        if prepare and core and not self.command([str(engine.engine_python()), '-m', 'pip', 'check'], timeout=30):
            self.item('core', '评测框架核心依赖', 'missing', '安装后存在依赖冲突，请查看日志', True)

    def _get_evalscope_installed(self):
        try:
            result = subprocess.run([str(engine.engine_python()), '-c',
                                     'from importlib.metadata import version; print(version("evalscope"))'],
                                    capture_output=True, text=True, timeout=10)
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            return '0.0.0'

    def _get_evalscope_pinned(self):
        for line in (engine.ROOT / 'web/engine-requirements.txt').read_text().splitlines():
            line = line.strip()
            if line.startswith('evalscope'):
                import re as _re
                match = _re.search(r'==(\d+\.\d+\.\d+)', line)
                if match:
                    return match.group(1)
        return '1.9.0'

    def check_evalscope_upgrade(self, spec, prepare):
        from packaging.version import Version
        datasets = spec.get('datasets', []) if spec['mode'] == 'eval' else []
        needed = {}
        for ds in datasets:
            min_ver = DATASET_MIN_EVALSCOPE.get(ds)
            if min_ver:
                needed[ds] = min_ver
        installed = self._get_evalscope_installed()
        pinned = self._get_evalscope_pinned()
        if needed:
            max_required = max(needed.values(), key=Version)
            if Version(installed) >= Version(max_required):
                ds_names = '、'.join(needed.keys())
                self.item('evalscope_version', 'evalscope 版本兼容性',
                          'ready', f'当前 evalscope {installed} 满足 {ds_names} 的要求（>= {max_required}）', True,
                          disposition='reused',
                          versions=[{'name': 'evalscope', 'required': f'>= {max_required}', 'installed': installed, 'matches': True}])
                return
            ds_names = '、'.join(needed.keys())
            detail = f'{ds_names} 需要 evalscope >= {max_required}，当前版本 {installed}'
            if not prepare:
                self.item('evalscope_version', 'evalscope 版本兼容性',
                          'missing', detail + '。请在"准备缺失项"中升级，或手动执行：pip install evalscope[perf]==' + max_required, True,
                          versions=[{'name': 'evalscope', 'required': f'>= {max_required}', 'installed': installed, 'matches': False}])
                return
            self.item('evalscope_version', 'evalscope 版本兼容性',
                      'preparing', f'正在升级 evalscope 到 {max_required}', True)
            ok = self.command([str(engine.engine_python()), '-m', 'pip', 'install',
                               f'evalscope[perf]=={max_required}'], timeout=600)
            new_ver = self._get_evalscope_installed() if ok else installed
            if ok and Version(new_ver) >= Version(max_required):
                self.item('evalscope_version', 'evalscope 版本兼容性',
                          'ready', f'已升级 evalscope {installed} → {new_ver}', True,
                          disposition='prepared',
                          versions=[{'name': 'evalscope', 'required': f'>= {max_required}', 'installed': new_ver, 'matches': True}])
            else:
                self.item('evalscope_version', 'evalscope 版本兼容性',
                          'missing', '升级失败，请查看日志', True,
                          error_details=f'尝试升级 evalscope 到 {max_required} 失败',
                          versions=[{'name': 'evalscope', 'required': f'>= {max_required}', 'installed': installed, 'matches': False}])
        else:
            if installed == pinned:
                return
            detail = f'当前 evalscope {installed} 与锁定版本 {pinned} 不一致（可能因其他数据集升级过）'
            if not prepare:
                self.item('evalscope_version', 'evalscope 版本回退',
                          'missing', detail + '。请在"准备缺失项"中回退，或手动执行：pip install evalscope[perf]==' + pinned, True,
                          versions=[{'name': 'evalscope', 'required': f'== {pinned}', 'installed': installed, 'matches': False}])
                return
            self.item('evalscope_version', 'evalscope 版本回退',
                      'preparing', f'正在回退 evalscope 到锁定版本 {pinned}', True)
            ok = self.command([str(engine.engine_python()), '-m', 'pip', 'install',
                               f'evalscope[perf]=={pinned}'], timeout=600)
            new_ver = self._get_evalscope_installed() if ok else installed
            if ok and new_ver == pinned:
                self.item('evalscope_version', 'evalscope 版本回退',
                          'ready', f'已回退 evalscope {installed} → {pinned}', True,
                          disposition='prepared',
                          versions=[{'name': 'evalscope', 'required': f'== {pinned}', 'installed': new_ver, 'matches': True}])
            else:
                self.item('evalscope_version', 'evalscope 版本回退',
                          'missing', '回退失败，请查看日志', True,
                          error_details=f'尝试回退 evalscope 到 {pinned} 失败',
                          versions=[{'name': 'evalscope', 'required': f'== {pinned}', 'installed': new_ver, 'matches': False}])

    def check_data(self, spec, prepare):
        core = engine.readiness()['ready']
        if spec['mode'] == 'perf':
            self.perf_data(spec, prepare)
            resources = [('tokenizer', name, 'Tokenizer · ' + name) for name in tokenizers(spec)]
        else:
            resources = [('data', name, '数据集 · ' + name) for name in spec['datasets']]
            if 'msr_vtt' in spec['datasets'] and core:
                self.check_msr_vtt_videos(prepare)
        max_workers = int(os.environ.get('THVV_DATA_WORKERS', '0')) or max(1, min(os.cpu_count() or 2, 4))
        tasks = []
        for kind, name, label in resources:
            key = kind + ':' + name
            if not core:
                self.item(key, label, 'blocked', '先准备核心依赖，再验证本地数据', True)
                continue
            if prepare and self.reuse_prior(key, label):
                continue
            tasks.append((kind, name, label, key))
        if tasks:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(self._check_data_item, kind, name, label, key, spec, prepare): key
                           for kind, name, label, key in tasks}
                concurrent.futures.wait(futures)
        if spec['mode'] == 'eval' and core and not self.stopping.is_set():
            self.check_docker_images(spec, prepare)

    def check_msr_vtt_videos(self, prepare):
        key = 'msr_vtt_videos'
        label = 'MSR-VTT 本地视频'
        parent = 'data:msr_vtt'
        if prepare and self.reuse_prior(key, label):
            return
        self.item(key, label, 'checking', '检查本地视频文件（Git LFS）', True, parent=parent)
        video_dirs = {
            'test_1k': (engine.ROOT / 'thvv/eval/datasets/msr_vtt/test_1k', 1000),
            'smoke': (engine.ROOT / 'thvv/eval/datasets/msr_vtt/smoke', 40),
        }
        total_expected = sum(count for _, count in video_dirs.values())
        total_valid = 0
        total_pointer = 0
        total_missing = 0
        for subset, (directory, expected) in video_dirs.items():
            if not directory.is_dir():
                total_missing += expected
                continue
            for mp4 in directory.glob('*.mp4'):
                with mp4.open('rb') as fh:
                    header = fh.read(80)
                if header.startswith(b'version https://git-lfs.github.com/spec'):
                    total_pointer += 1
                elif len(header) > 0:
                    total_valid += 1
                else:
                    total_missing += 1
        if total_valid == total_expected and total_pointer == 0 and total_missing == 0:
            self.item(key, label, 'ready',
                      f'全部 {total_expected} 个视频文件已就绪（test_1k: {video_dirs["test_1k"][1]}，smoke: {video_dirs["smoke"][1]}）', True,
                      disposition='reused', parent=parent)
            return
        detail_parts = []
        if total_valid:
            detail_parts.append(f'{total_valid} 个已就绪')
        if total_pointer:
            detail_parts.append(f'{total_pointer} 个仍为 Git LFS 指针')
        if total_missing:
            detail_parts.append(f'{total_missing} 个缺失')
        detail = '、'.join(detail_parts) + f'（共需 {total_expected} 个）'
        if not prepare:
            self.item(key, label, 'missing', detail + '；点击"准备缺失项"拉取 Git LFS 视频', True, parent=parent)
            return
        self.item(key, label, 'preparing', f'正在拉取 MSR-VTT 视频（Git LFS），需要 {total_pointer + total_missing} 个文件', True, parent=parent)
        has_lfs = self.command(['git', 'lfs', 'version'], timeout=10)
        if not has_lfs:
            self.item(key, label, 'missing', '请先安装 Git LFS', True, parent=parent)
            return
        ok = self.command(['git', 'lfs', 'pull', '--include=thvv/eval/datasets/msr_vtt/'], timeout=600)
        if not ok:
            self.item(key, label, 'missing', 'Git LFS 拉取失败或超时，请查看日志', True, parent=parent)
            return
        total_valid_after = 0
        total_pointer_after = 0
        for subset, (directory, expected) in video_dirs.items():
            if not directory.is_dir():
                continue
            for mp4 in directory.glob('*.mp4'):
                with mp4.open('rb') as fh:
                    header = fh.read(80)
                if header.startswith(b'version https://git-lfs.github.com/spec'):
                    total_pointer_after += 1
                elif len(header) > 0:
                    total_valid_after += 1
        if total_valid_after == total_expected and total_pointer_after == 0:
            self.item(key, label, 'ready',
                      f'全部 {total_expected} 个视频文件已就绪（本次拉取 {total_valid_after - total_valid} 个）', True,
                      disposition='prepared', parent=parent)
        else:
            self.item(key, label, 'missing',
                      f'拉取后仍有 {total_pointer_after} 个 LFS 指针，{total_valid_after}/{total_expected} 个可用', True, parent=parent)

    def _check_data_item(self, kind, name, label, key, spec, prepare):
        worker = WorkerContext(self, key)
        with self.workers_lock:
            self.workers[key] = worker
        try:
            if worker.cancelled.is_set() or self.stopping.is_set():
                return
            self.item(key, label, 'checking', '正在尝试从本地加载', True)
            result = worker.probe(kind, name, spec['options'])
            reused = result['ready']
            missing_dependency = result.get('error_code') == 'missing_dependency'
            if not result['ready'] and prepare and not missing_dependency and not worker.cancelled.is_set():
                self.item(key, label, 'preparing', '按框架规则下载并加载；大型数据可能耗时较长', True)
                result = worker.probe(kind, name, spec['options'], True)
                missing_dependency = result.get('error_code') == 'missing_dependency'
            if worker.cancelled.is_set() or self.stopping.is_set():
                return
            online = bool(result.get('online_media'))
            detail = result['detail']
            if online and name == 'msr_vtt':
                with self.lock:
                    video_item = next((i for i in self.current['items'] if i['id'] == 'msr_vtt_videos'), None)
                if video_item and video_item.get('state') == 'ready':
                    online = False
                    detail += '；本地视频已就绪，运行时使用本地文件，无需联网'
            if online:
                detail += f"；{result['online_media']} 个媒体引用为在线地址，运行时仍需联网，未验证远端媒体可用性"
            self.item(key, label, ('online' if online else 'ready') if result['ready'] else 'blocked' if missing_dependency else 'missing', detail, True,
                      error_details=result.get('error_details', ''), error_code=result.get('error_code'),
                      disposition=('reused' if reused else 'prepared') if result['ready'] else None)
        finally:
            with self.workers_lock:
                self.workers.pop(key, None)

    def collect_docker_images(self, dataset_name, options, worker=None):
        key = 'docker_images_collect:' + dataset_name
        path = self.directory / f'docker-images-{_sanitize_key(dataset_name)}.json'
        path.unlink(missing_ok=True)
        cmd = [str(engine.engine_python()), str(engine.ROOT / 'web/docker_images_probe.py'),
               dataset_name, str(path), json.dumps(options)]
        if worker:
            worker.command(cmd, timeout=300)
        else:
            self.command(cmd, timeout=300)
        if path.exists():
            return json.loads(path.read_text())
        return {'ready': False, 'images': [], 'error': '镜像列表收集未完成或超时'}

    def check_docker_images(self, spec, prepare):
        registry, _ = engine.registry()
        docker_datasets = [d for d in spec.get('datasets', [])
                           if registry.get(d, {}).get('requires_docker')
                           or registry.get(d, {}).get('requires_sandbox')]
        if not docker_datasets:
            return
        max_workers = int(os.environ.get('THVV_DATA_WORKERS', '0')) or max(1, min(os.cpu_count() or 2, 4))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self._check_docker_images_item, ds, spec, prepare): ds
                       for ds in docker_datasets}
            concurrent.futures.wait(futures)

    def _check_docker_images_item(self, dataset_name, spec, prepare):
        key = 'docker_images:' + dataset_name
        label = 'Docker 镜像'
        parent = 'data:' + dataset_name
        worker = WorkerContext(self, key)
        with self.workers_lock:
            self.workers[key] = worker
        try:
            if worker.cancelled.is_set() or self.stopping.is_set():
                return
            self.item(key, label, 'checking', '正在收集所需镜像列表', True, parent=parent)
            result = self.collect_docker_images(dataset_name, spec.get('options', {}), worker)
            if worker.cancelled.is_set() or self.stopping.is_set():
                return
            if not result['ready']:
                self.item(key, label, 'missing', f"镜像列表收集失败：{result.get('error', '未知错误')}", True,
                          error_details=result.get('error', ''), parent=parent)
                return
            images = result['images']
            if not images:
                self.item(key, label, 'ready', '该数据集无需预拉取 Docker 镜像', True, disposition='reused', parent=parent)
                return
            local, missing = self._check_local_images(images)
            if not missing:
                self.item(key, label, 'ready', f'全部 {len(images)} 个镜像已在本地', True, disposition='reused', parent=parent)
                return
            if not prepare:
                self.item(key, label, 'missing',
                          f'已有 {len(local)}/{len(images)} 个镜像，缺少 {len(missing)} 个', True, parent=parent)
                return
            self.item(key, label, 'preparing',
                      f'正在拉取 {len(missing)} 个缺失镜像（已有 {len(local)}/{len(images)} 个）', True, parent=parent)
            def _on_pull_progress(p, f, done, total):
                self.item(key, label, 'preparing',
                          f'正在拉取镜像 {done}/{total}（成功 {p}，失败 {f}，已有 {len(local)+p}/{len(images)} 个）', True, parent=parent)
            pulled, failed = self._pull_images(missing, worker, on_progress=_on_pull_progress)
            total_local = len(local) + pulled
            if worker.cancelled.is_set() or self.stopping.is_set():
                if total_local > len(local):
                    self.item(key, label, 'missing',
                              f'拉取已中断，本次新增 {pulled} 个（已有 {total_local}/{len(images)} 个）', True, parent=parent)
                return
            if failed:
                self.item(key, label, 'missing',
                          f'{total_local}/{len(images)} 个镜像已就绪，{failed} 个拉取失败', True,
                          error_details=f'{failed} 个镜像拉取失败，请检查网络和 Docker Hub 访问', parent=parent)
            else:
                self.item(key, label, 'ready',
                          f'全部 {len(images)} 个镜像已就绪（本次拉取 {pulled} 个）', True,
                          disposition='prepared', parent=parent)
        finally:
            with self.workers_lock:
                self.workers.pop(key, None)

    def _check_local_images(self, images):
        try:
            result = subprocess.run(['docker', 'images', '--format', '{{.Repository}}:{{.Tag}}'],
                                    capture_output=True, text=True, timeout=30,
                                    env=engine.engine_env(self.directory.parent))
            local_set = set(result.stdout.strip().split('\n')) if result.stdout.strip() else set()
        except (subprocess.TimeoutExpired, OSError):
            local_set = set()
        local = [img for img in images if img in local_set]
        missing = [img for img in images if img not in local_set]
        return local, missing

    def _pull_images(self, images, worker=None, on_progress=None):
        pulled = 0
        failed = 0
        for idx, image in enumerate(images, 1):
            stopped = (worker.cancelled.is_set() if worker else False) or self.stopping.is_set()
            if stopped:
                self.log(f'\n镜像拉取已中断，已完成 {pulled}/{len(images)}\n')
                failed += len(images) - idx + 1
                break
            self.log(f'\n[{idx}/{len(images)}] docker pull {image}\n')
            if worker:
                ok = worker.command(['docker', 'pull', image], timeout=600)
            else:
                ok = self.command(['docker', 'pull', image], timeout=600)
            if ok:
                pulled += 1
            else:
                failed += 1
                self.log(f'拉取失败：{image}\n')
            if on_progress:
                on_progress(pulled, failed, idx, len(images))
        return pulled, failed

    def work(self, spec, prepare):
        try:
            if self.current['scope'] == 'environment':
                self.check_environment(spec, prepare)
            else:
                self.check_data(spec, prepare)
            with self.lock:
                if self.stopping.is_set():
                    for i in self.current['items']:
                        if i['state'] in ('checking', 'preparing'):
                            i.update(state='skipped', detail='操作已终止，本项未完成')
                self.current['ready'] = all(i['state'] in ('ready', 'online') for i in self.current['items'])
                self.current['offline_ready'] = all(i['state'] == 'ready' for i in self.current['items'])
                skipped = sum(i['state'] == 'skipped' for i in self.current['items'])
                missing = sum(i['state'] not in ('ready', 'online', 'skipped') for i in self.current['items'])
                reused = sum(i.get('disposition') == 'reused' for i in self.current['items'])
                prepared = sum(i.get('disposition') == 'prepared' for i in self.current['items'])
                summary = f'复用已有 {reused} 项，本次准备 {prepared} 项'
                if skipped:
                    summary += f'，跳过 {skipped} 项'
                message = ('已就绪：' + summary) if self.current['ready'] else f'{summary}；{missing} 项尚未就绪'
                if not prepare:
                    message = '检查完成：' + ('全部已就绪，可直接复用' if self.current['ready'] else f'{missing} 项尚未就绪') + '；未执行安装或下载'
                self.current.update(status='completed', finished=time.time(), stamp=dependency_stamp(), message=message)
                if self.stopping.is_set():
                    self.current.update(status='interrupted', ready=False, message='操作已被终止；未完成的项标记为已跳过，已下载缓存会保留，下次准备可复用')
                self.persist()
        except Exception as exc:
            self.log(f'{type(exc).__name__}: {exc}\n')
            with self.lock:
                self.current.update(status='interrupted' if self.stopping.is_set() else 'failed', ready=False, finished=time.time(), message='操作已中断，请重新检查' if self.stopping.is_set() else '环境检查未完成，请展开日志查看原因')
                self.persist()

    def perf_data(self, spec, prepare):
        for bucket in selected_buckets(spec):
            key, label = 'perf:' + bucket, '压测数据 · ' + bucket
            single = {'mode': 'perf', 'options': {'bucket': bucket}}
            ok = not engine.missing_perf_data(single)
            reused = ok
            if not ok and prepare:
                self.item(key, label, 'preparing', '正在拉取本档 Git LFS 数据', True)
                if self.command(['git', 'lfs', 'version'], timeout=10):
                    self.command(['git', 'lfs', 'pull', '--include=thvv/perf/datasets_clean/perf_zh_' + bucket + '.jsonl', '--exclude='], timeout=600)
                else:
                    self.log('请先安装 Git LFS，随后重试准备。\n')
                ok = not engine.missing_perf_data(single)
            self.item(key, label, 'ready' if ok else 'missing', '数据文件存在且首条记录格式有效' if ok else '文件缺失、格式无效或仍为 Git LFS 指针；可拉取准备（需要 Git LFS）', True,
                      disposition=('reused' if reused else 'prepared') if ok else None)

    def close(self):
        self.stopping.set()
        with self.workers_lock:
            for worker in list(self.workers.values()):
                worker.stop()
        if self.thread:
            self.thread.join(timeout=6)
        with self.lock:
            if self.current and self.current['status'] == 'running':
                self.current.update(status='interrupted', ready=False, message='服务已关闭，请重新检查或准备')
                self.persist()
