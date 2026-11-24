"""Selected-test readiness and preparation, isolated from model test jobs."""
from __future__ import annotations

import copy
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
        required.update(('java', 'video'))
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
            self.current = {'id': job_id, 'status': 'running', 'action': 'prepare' if prepare else 'check',
                            'scope': scope, 'requirements': sorted(dependency_requirements(spec)),
                            'fingerprint': fingerprint(spec), 'ready': False, 'items': [], 'log': '',
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
        # Download tools can include credential-bearing registry URLs in their output.
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

    def command(self, args, timeout=120, env=None):
        if self.stopping.is_set():
            raise RuntimeError('服务已关闭')
        # Stream to disk so a large pip/download output cannot block the child process.
        path = self.directory / 'command.log'
        with path.open('w+') as output:
            try:
                self.process = subprocess.Popen(args, cwd=engine.ROOT, env=env or engine.engine_env(self.directory.parent), stdin=subprocess.DEVNULL,
                                                stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as exc:
                self.log(str(exc) + '\n')
                return False
            started, offset = time.monotonic(), 0
            while True:
                with path.open() as reader:
                    reader.seek(offset)
                    chunk = reader.read()
                    offset = reader.tell()
                if chunk:
                    self.log(chunk)
                code = self.process.poll()
                if code is not None:
                    # Drain after exit as the last write may race with the read above.
                    with path.open() as reader:
                        reader.seek(offset)
                        self.log(reader.read())
                    self.process = None
                    return code == 0
                if self.stopping.is_set() or time.monotonic() - started > timeout:
                    os.killpg(self.process.pid, signal.SIGTERM)
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(self.process.pid, signal.SIGKILL)
                        self.process.wait()
                    self.process = None
                    self.log('\n操作已中断或超时；已下载缓存会保留，下次准备可复用。\n')
                    return False
                self.stopping.wait(.2)

    def core_ready(self):
        if not engine.readiness()['ready']:
            self.core_report = {}
            return False
        self.core_report = self.dependency_report('core')
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
        self.command([str(engine.engine_python()), str(engine.ROOT / 'web/runtime_probe.py'), command, value, str(path), json.dumps(options)], timeout=7200 if prepare else 120)
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
            installed = self.command([str(engine.engine_python()), '-m', 'pip', 'install', '-r', 'web/engine-requirements.txt', source], timeout=1800)
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
                          '--data-dir', str(self.directory.parent), '--output', str(path)], timeout=1800)
            if path.is_file():
                result = json.loads(path.read_text())
            else:
                result = {**result, 'ready': False, 'detail': '自动准备未完成或超时，请展开日志查看原因，修复后重试'}
        state = ('online' if component == 'docker' else 'ready') if result['ready'] else 'missing' if result['automatic'] else 'manual'
        required = 'Java 8（评分组件要求；框架未锁定补丁版本）' if component == 'java' else 'Docker 服务可访问（框架未锁定版本）'
        self.item(component, label, state, result['detail'], result['automatic'],
                  versions=[{'name': 'Java' if component == 'java' else 'Docker Engine', 'required': required,
                             'installed': result.get('version') or '未检测到', 'matches': result['ready']}],
                  disposition=('reused' if reused else 'prepared') if result['ready'] else None)

    def check_environment(self, spec, prepare):
        self.item('core', '评测框架核心依赖', 'checking', '检查版本与实际导入', True)
        core = self.core_ready()
        reused = core
        if not core and prepare:
            self.item('core', '评测框架核心依赖', 'preparing', '正在安装核心依赖', True)
            env = engine.engine_env()
            env['THVV_PYTHON'] = sys.executable
            if os.environ.get('THVV_ENGINE_PYTHON'):
                core = self.command([str(engine.engine_python()), '-m', 'pip', 'install', '-r', 'web/engine-requirements.txt'], timeout=1800, env=env)
            else:
                core = self.command(['bash', 'web/install-engine.sh'], timeout=1800, env=env)
            core = core and self.core_ready()
        self.item('core', '评测框架核心依赖', 'ready' if core else 'missing',
                  '版本、导入及依赖冲突检查通过' if core else '依赖缺失、版本不符或加载失败，在运行环境中点击“准备缺失项”修复。', True,
                  versions=self.core_report.get('versions', []), error_details=self.core_report.get('error_details', ''),
                  disposition=('reused' if reused else 'prepared') if core else None)
        registry, _ = engine.registry()
        chosen = [registry[d] for d in spec['datasets']]
        if any(d.get('requires_docker') or d.get('requires_sandbox') for d in chosen):
            self.system_component('docker', 'Docker 服务', prepare)
        if 'msr_vtt' in spec['datasets']:
            self.system_component('java', 'Java（MSR-VTT METEOR 评分）', prepare)
        if core:
            if 'tau2_bench' in spec['datasets']:
                self.package('tau2-bench', 'tau2', 'git+https://github.com/sierra-research/tau2-bench@v0.2.0', prepare)
            if any(d.get('requires_swebench') for d in chosen):
                self.package('swebench', 'swebench', 'swebench==4.1.0', prepare)
            if any(d.get('requires_ms_enclave') for d in chosen):
                self.package('ms_enclave', 'ms_enclave', 'ms-enclave>=0.0.5', prepare)
            if 'msr_vtt' in spec['datasets']:
                self.package('视频评分组件', 'pycocoevalcap.meteor.meteor', 'pycocoevalcap==1.2', prepare)
                self.package('COCO 数据组件', 'pycocotools.mask', 'pycocotools==2.0.11', prepare)
        # Optional package installation may have changed transitive dependencies.
        if prepare and core and not self.command([str(engine.engine_python()), '-m', 'pip', 'check'], timeout=30):
            self.item('core', '评测框架核心依赖', 'missing', '安装后存在依赖冲突，请查看日志', True)

    def check_data(self, spec, prepare):
        core = engine.readiness()['ready']
        if spec['mode'] == 'perf':
            self.perf_data(spec, prepare)
            resources = [('tokenizer', name, 'Tokenizer · ' + name) for name in tokenizers(spec)]
        else:
            resources = [('data', name, '数据集 · ' + name) for name in spec['datasets']]
        for kind, name, label in resources:
            key = kind + ':' + name
            if not core:
                self.item(key, label, 'blocked', '先准备核心依赖，再验证本地数据', True)
                continue
            self.item(key, label, 'checking', '正在尝试从本地加载', True)
            result = self.probe(kind, name, spec['options'])
            reused = result['ready']
            missing_dependency = result.get('error_code') == 'missing_dependency'
            if not result['ready'] and prepare and not missing_dependency:
                self.item(key, label, 'preparing', '按框架规则下载并加载；大型数据可能耗时较长', True)
                result = self.probe(kind, name, spec['options'], True)
                missing_dependency = result.get('error_code') == 'missing_dependency'
            online = bool(result.get('online_media'))
            detail = result['detail']
            if online:
                detail += f"；{result['online_media']} 个媒体引用为在线地址，运行时仍需联网，未验证远端媒体可用性"
            self.item(key, label, ('online' if online else 'ready') if result['ready'] else 'blocked' if missing_dependency else 'missing', detail, True,
                      error_details=result.get('error_details', ''), error_code=result.get('error_code'),
                      disposition=('reused' if reused else 'prepared') if result['ready'] else None)

    def work(self, spec, prepare):
        try:
            if self.current['scope'] == 'environment':
                self.check_environment(spec, prepare)
            else:
                self.check_data(spec, prepare)
            with self.lock:
                self.current['ready'] = all(i['state'] in ('ready', 'online') for i in self.current['items'])
                self.current['offline_ready'] = all(i['state'] == 'ready' for i in self.current['items'])
                missing = sum(i['state'] not in ('ready', 'online') for i in self.current['items'])
                reused = sum(i.get('disposition') == 'reused' for i in self.current['items'])
                prepared = sum(i.get('disposition') == 'prepared' for i in self.current['items'])
                summary = f'复用已有 {reused} 项，本次准备 {prepared} 项'
                message = ('已就绪：' + summary) if self.current['ready'] else f'{summary}；{missing} 项尚未就绪'
                if not prepare:
                    message = '检查完成：' + ('全部已就绪，可直接复用' if self.current['ready'] else f'{missing} 项尚未就绪') + '；未执行安装或下载'
                self.current.update(status='completed', finished=time.time(), stamp=dependency_stamp(), message=message)
                if self.stopping.is_set():
                    self.current.update(status='interrupted', ready=False, message='服务已关闭，请重新检查或准备')
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
                    self.command(['git', 'lfs', 'pull', '--include=thvv/perf/datasets_clean/perf_zh_' + bucket + '.jsonl', '--exclude='], timeout=3600)
                else:
                    self.log('请先安装 Git LFS，随后重试准备。\n')
                ok = not engine.missing_perf_data(single)
            self.item(key, label, 'ready' if ok else 'missing', '数据文件存在且首条记录格式有效' if ok else '文件缺失、格式无效或仍为 Git LFS 指针；可拉取准备（需要 Git LFS）', True,
                      disposition=('reused' if reused else 'prepared') if ok else None)

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=6)
        with self.lock:
            if self.current and self.current['status'] == 'running':
                self.current.update(status='interrupted', ready=False, message='服务已关闭，请重新检查或准备')
                self.persist()
