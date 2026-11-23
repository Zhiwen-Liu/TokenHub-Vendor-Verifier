"""Selected-test readiness and preparation, isolated from model test jobs."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

from . import engine
from .diagnostics import failure_details


def runtime_spec(spec):
    # No endpoint or credential is needed for downloading dependencies and data.
    return {'mode': spec['mode'], 'model': spec.get('model', ''),
            'datasets': spec.get('datasets', []) if spec['mode'] == 'eval' else [],
            'options': {k: v for k, v in spec.get('options', {}).items()
                        if k in ('bucket', 'buckets', 'perf-command', 'tokenizer', 'limit')}}


def fingerprint(spec):
    return hashlib.sha256(json.dumps(runtime_spec(spec), sort_keys=True).encode()).hexdigest()


def selected_buckets(spec):
    opts = spec['options']
    return [opts['bucket']] if opts.get('perf-command', 'bench') == 'bench' else opts.get('buckets', ' '.join(engine.BUCKETS[:-1])).split()


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
        self.core_report = {}
        path = self.directory / 'status.json'
        if path.exists():
            self.current = json.loads(path.read_text())
            if self.current['status'] == 'running':
                self.current.update(status='interrupted', ready=False, message='服务关闭，环境检查或准备已中断，请重新执行')
                self.persist()

    def persist(self):
        target = self.directory / 'status.json'
        tmp = target.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.current, ensure_ascii=False))
        tmp.replace(target)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.current)

    def busy(self):
        with self.lock:
            return bool(self.current and self.current['status'] == 'running')

    def validated(self, spec):
        with self.lock:
            return bool(self.current and self.current['status'] == 'completed' and self.current['ready']
                        and self.current['fingerprint'] == fingerprint(spec)
                        and time.time() - self.current.get('finished', 0) < 300)

    def start(self, spec, prepare=False):
        with self.gate, self.lock:
            if self.busy():
                raise ValueError('环境检查或准备正在进行，请等待结束')
            if prepare and self.tasks_active():
                raise ValueError('请等当前测试任务结束后再准备环境，避免安装过程影响正在运行的测试')
            job_id = uuid.uuid4().hex
            self.current = {'id': job_id, 'status': 'running', 'action': 'prepare' if prepare else 'check',
                            'fingerprint': fingerprint(spec), 'ready': False, 'items': [], 'log': '',
                            'message': '正在检查所选测试需要的组件与数据', 'started': time.time()}
            self.persist()
            self.thread = threading.Thread(target=self.work, args=(runtime_spec(spec), prepare), daemon=True)
            self.thread.start()
            return self.snapshot()

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
                self.process = subprocess.Popen(args, cwd=engine.ROOT, env=env or engine.engine_env(),
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
        if not ok and prepare:
            self.item(key, name, 'preparing', '正在安装框架要求的可选依赖', True)
            installed = self.command([str(engine.engine_python()), '-m', 'pip', 'install', '-r', 'web/engine-requirements.txt', requirement], timeout=1800)
            report = self.dependency_report('optional', requirement, module)
            ok = installed and report['ready']
        self.item(key, name, 'ready' if ok else 'missing', '版本与组件加载检查通过' if ok else '组件缺失、版本不符或加载失败，点击“准备缺失项”修复。', True,
                  versions=report.get('versions', []), error_details=report.get('error_details', ''))

    def work(self, spec, prepare):
        try:
            self.item('core', '评测框架核心依赖', 'checking', '检查版本与实际导入', True)
            core = self.core_ready()
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
                      '版本、导入及依赖冲突检查通过' if core else '依赖缺失、版本不符或加载失败，点击“准备缺失项”修复。', True,
                      versions=self.core_report.get('versions', []), error_details=self.core_report.get('error_details', ''))
            registry, _ = engine.registry()
            chosen = [registry[d] for d in spec['datasets']]
            if any(d.get('requires_docker') or d.get('requires_sandbox') for d in chosen):
                ok = bool(shutil.which('docker')) and self.command(['docker', 'info', '--format', '{{.ServerVersion}}'], timeout=15)
                self.item('docker', 'Docker 服务', 'online' if ok else 'manual', '服务可用；任务镜像仍按框架在运行时准备，未验证全部镜像可用性' if ok else '请安装并启动 Docker，再重新检查（网页不安装系统服务）')
            if 'msr_vtt' in spec['datasets']:
                ok = bool(shutil.which('java')) and self.command(['java', '-version'], timeout=10)
                self.item('java', 'Java（MSR-VTT METEOR 评分）', 'ready' if ok else 'manual', 'Java 可用' if ok else '请安装 Java 后重新检查')
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
                if not result['ready'] and prepare:
                    self.item(key, label, 'preparing', '按框架规则下载并加载；大型数据可能耗时较长', True)
                    result = self.probe(kind, name, spec['options'], True)
                online = bool(result.get('online_media'))
                detail = result['detail']
                if online:
                    detail += f"；{result['online_media']} 个媒体引用为在线地址，运行时仍需联网，未验证远端媒体可用性"
                self.item(key, label, ('online' if online else 'ready') if result['ready'] else 'missing', detail, True,
                          error_details=result.get('error_details', ''), error_code=result.get('error_code'))
            # Optional package installation may have changed transitive dependencies.
            if prepare and core and not self.command([str(engine.engine_python()), '-m', 'pip', 'check'], timeout=30):
                self.item('core', '评测框架核心依赖', 'missing', '安装后存在依赖冲突，请查看日志', True)
            with self.lock:
                self.current['ready'] = all(i['state'] in ('ready', 'online') for i in self.current['items'])
                self.current['offline_ready'] = all(i['state'] == 'ready' for i in self.current['items'])
                missing = sum(i['state'] not in ('ready', 'online') for i in self.current['items'])
                self.current.update(status='completed', finished=time.time(), message='检查完成：所选环境可用' if self.current['ready'] else f'检查完成：{missing} 项尚未就绪')
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
            if not ok and prepare:
                self.item(key, label, 'preparing', '正在拉取本档 Git LFS 数据', True)
                if self.command(['git', 'lfs', 'version'], timeout=10):
                    self.command(['git', 'lfs', 'pull', '--include=thvv/perf/datasets_clean/perf_zh_' + bucket + '.jsonl', '--exclude='], timeout=3600)
                else:
                    self.log('请先安装 Git LFS，随后重试准备。\n')
                ok = not engine.missing_perf_data(single)
            self.item(key, label, 'ready' if ok else 'missing', '数据文件存在且首条记录格式有效' if ok else '文件缺失、格式无效或仍为 Git LFS 指针；可拉取准备（需要 Git LFS）', True)

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=6)
        with self.lock:
            if self.current and self.current['status'] == 'running':
                self.current.update(status='interrupted', ready=False, message='服务已关闭，请重新检查或准备')
                self.persist()
