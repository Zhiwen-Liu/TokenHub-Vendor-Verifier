from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import html
import io
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import sqlite3
import subprocess
import tarfile
import threading
import uuid
import zipfile

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from typing import Literal

from . import connectivity, engine
from .auth import Auth, COOKIE, SESSION_SECONDS
from .environment import Environment
from .progress import task_progress


PERF_METRICS = ['TTFT (ms)', 'TPOT (ms)', 'ITL (ms)', 'Output Throughput (tok/s)',
                'Total Throughput (tok/s)', 'Req Throughput (req/s)', 'Avg Latency (s)']


def now():
    return datetime.now(timezone.utc).isoformat()


class LoginInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


class ObjectInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    api_key: str | None = Field(default=None, max_length=8192)


class TaskInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    object_id: str = ''
    model: str = Field(min_length=1, max_length=300)
    mode: Literal['perf', 'eval']
    protocol: Literal['openai', 'anthropic'] = 'openai'
    datasets: list[str] = []
    all: bool = False
    options: dict[str, str] = {}
    judge_api_key: str | None = Field(default=None, max_length=8192)


class ConnectionCheckInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    object_id: str = ''
    replay_id: str | None = None
    model: str = Field(min_length=1, max_length=300)
    protocol: Literal['openai', 'anthropic'] = 'openai'


class JudgeCheckInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = Field(default='', max_length=300)
    url: str = Field(default='', max_length=2000)
    api_key: str | None = Field(default=None, max_length=8192)
    replay_id: str | None = None


class EnvironmentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['perf', 'eval']
    model: str = Field(default='', max_length=300)
    datasets: list[str] = []
    all: bool = False
    options: dict[str, str] = {}
    scope: Literal['environment', 'data'] = 'environment'


class CleanupInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan_id: str


class BaselineEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = Field(min_length=1, max_length=200)
    dataset: str = Field(min_length=1, max_length=200)
    score: float


class PerfBaselineEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = Field(min_length=1, max_length=200)
    scenario: str = Field(min_length=1, max_length=200)
    metric: str = Field(min_length=1, max_length=200)
    value: float


class BaselineImport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task_id: str = Field(min_length=1)
    model: str = Field(default='', max_length=200)


class StopItemInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_id: str = Field(min_length=1)


class BaselineRollback(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target: str = Field(min_length=1, max_length=200)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        key_path = self.directory / 'encryption.key'
        if not key_path.exists():
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(Fernet.generate_key())
        self.cipher = Fernet(key_path.read_bytes())
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / 'state.sqlite', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS objects(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,url TEXT NOT NULL,secret TEXT NOT NULL,updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,payload TEXT NOT NULL,secrets TEXT NOT NULL,status TEXT NOT NULL,created TEXT NOT NULL,started TEXT,finished TEXT,error TEXT,exit_code INTEGER);
        CREATE TABLE IF NOT EXISTS baselines(model TEXT NOT NULL,dataset TEXT NOT NULL,score REAL NOT NULL,source TEXT NOT NULL DEFAULT 'manual',updated TEXT NOT NULL,PRIMARY KEY(model,dataset));
        CREATE TABLE IF NOT EXISTS perf_baselines(model TEXT NOT NULL,scenario TEXT NOT NULL,metric TEXT NOT NULL,value REAL NOT NULL,source TEXT NOT NULL DEFAULT 'manual',updated TEXT NOT NULL,PRIMARY KEY(model,scenario,metric));
        CREATE TABLE IF NOT EXISTS baseline_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,label TEXT NOT NULL,eval_data TEXT NOT NULL DEFAULT '[]',perf_data TEXT NOT NULL DEFAULT '[]');
        ''')
        os.chmod(self.directory / 'state.sqlite', 0o600)

    def execute(self, sql, params=()):
        with self.lock, self.db:
            return self.db.execute(sql, params)

    def rows(self, sql, params=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, params).fetchall()]

    def encrypt(self, obj):
        return self.cipher.encrypt(json.dumps(obj).encode()).decode()

    def decrypt(self, value):
        return json.loads(self.cipher.decrypt(value.encode()))

    def task(self, task_id, private=False):
        rows = self.rows('SELECT * FROM tasks WHERE id=?', (task_id,))
        if not rows:
            raise HTTPException(404, '任务不存在')
        row = rows[0]
        row.update(json.loads(row.pop('payload')))
        secret = row.pop('secrets')
        if private:
            row['secrets'] = self.decrypt(secret)
        return row

    def snapshot_baselines(self, label):
        with self.lock, self.db:
            eval_data = json.dumps([dict(r) for r in self.db.execute(
                'SELECT model,dataset,score,source,updated FROM baselines').fetchall()])
            perf_data = json.dumps([dict(r) for r in self.db.execute(
                'SELECT model,scenario,metric,value,source,updated FROM perf_baselines').fetchall()])
            self.db.execute('INSERT INTO baseline_snapshots(created_at,label,eval_data,perf_data) VALUES(?,?,?,?)',
                            (now(), label, eval_data, perf_data))
            self.db.execute('DELETE FROM baseline_snapshots WHERE id NOT IN '
                            '(SELECT id FROM baseline_snapshots ORDER BY id DESC LIMIT 200)')

    def rollback_baselines(self, snapshot_id):
        with self.lock, self.db:
            rows = [dict(r) for r in self.db.execute(
                'SELECT * FROM baseline_snapshots WHERE id=?', (snapshot_id,)).fetchall()]
            if not rows:
                return None
            snap = rows[0]
            eval_before = json.dumps([dict(r) for r in self.db.execute(
                'SELECT model,dataset,score,source,updated FROM baselines').fetchall()])
            perf_before = json.dumps([dict(r) for r in self.db.execute(
                'SELECT model,scenario,metric,value,source,updated FROM perf_baselines').fetchall()])
            self.db.execute('INSERT INTO baseline_snapshots(created_at,label,eval_data,perf_data) VALUES(?,?,?,?)',
                            (now(), '回退前自动备份', eval_before, perf_before))
            self.db.execute('DELETE FROM baselines')
            for r in json.loads(snap['eval_data']):
                self.db.execute('INSERT INTO baselines VALUES(?,?,?,?,?)',
                                (r['model'], r['dataset'], r['score'], r['source'], r['updated']))
            self.db.execute('DELETE FROM perf_baselines')
            for r in json.loads(snap['perf_data']):
                self.db.execute('INSERT INTO perf_baselines VALUES(?,?,?,?,?,?)',
                                (r['model'], r['scenario'], r['metric'], r['value'], r['source'], r['updated']))
            self.db.execute('DELETE FROM baseline_snapshots WHERE id NOT IN '
                            '(SELECT id FROM baseline_snapshots ORDER BY id DESC LIMIT 200)')
            return snap


def redact(text, secrets):
    text = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text)
    text = re.sub(r'\x1b\].*?\x07', '', text)
    for value in sorted((s for s in secrets.values() if s), key=len, reverse=True):
        for representation in (value, json.dumps(value)[1:-1], html.escape(value, quote=True)):
            text = text.replace(representation, '[REDACTED]')
    return text


def publish_artifacts(workspace, target, secrets):
    final = target
    target = target.with_name(target.name + '.building')
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(exist_ok=True)
    for mode in ('perf', 'eval'):
        source = workspace / mode / 'results'
        if not source.exists():
            continue
        for path in source.rglob('*'):
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
                continue
            suffix = path.suffix.lower()
            if suffix not in {'.html', '.json', '.jsonl', '.csv', '.log', '.yaml', '.yml', '.txt', '.xlsx'}:
                continue
            dest = target / mode / path.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if suffix == '.xlsx':
                with zipfile.ZipFile(path) as src, zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as out:
                    for name in src.namelist():
                        data = src.read(name)
                        if name.endswith(('.xml', '.rels')):
                            data = redact(data.decode('utf-8'), secrets).encode('utf-8')
                        out.writestr(name, data)
            else:
                dest.write_text(redact(path.read_text(errors='replace'), secrets), encoding='utf-8')
    files = [p for p in target.rglob('*') if p.is_file()]
    if files:
        with tarfile.open(target / 'results.tar.gz', 'w:gz') as archive:
            for path in files:
                archive.add(path, arcname=path.relative_to(target))
    if final.exists():
        shutil.rmtree(final)
    target.replace(final)


class Runner:
    def __init__(self, store):
        self.store, self.jobs = store, queue.Queue()
        self.stop_event = threading.Event()
        self.cancel_event = threading.Event()
        self.process = None
        self.current_task_id = None
        self.paused = False

    def start(self):
        for row in self.store.rows("SELECT id FROM tasks WHERE status='running'"):
            task = self.store.task(row['id'], private=True)
            folder = self.store.directory / 'tasks' / row['id']
            if (folder / 'work').exists():
                try:
                    publish_artifacts(folder / 'work', folder / 'artifacts', task['secrets'])
                except Exception:
                    pass
        self.store.execute("UPDATE tasks SET status='interrupted',finished=?,error=? WHERE status='running'", (now(), "服务重启，原任务已中断，可点击继续恢复执行"))
        for t in self.store.rows("SELECT id FROM tasks WHERE status='queued' ORDER BY created"):
            self.jobs.put(t['id'])
        self.thread = threading.Thread(target=self.work, daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.process and self.process.poll() is None:
            if self.paused:
                try:
                    os.killpg(self.process.pid, signal.SIGCONT)
                except (OSError, ProcessLookupError):
                    pass
                self.paused = False
            os.killpg(self.process.pid, signal.SIGTERM)
        self.jobs.put(None)
        self.thread.join(timeout=10)
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.thread.join(timeout=5)

    def work(self):
        while not self.stop_event.is_set():
            task_id = self.jobs.get()
            if task_id is None:
                break
            self.run(task_id)

    def run(self, task_id):
        task = self.store.task(task_id, private=True)
        folder = self.store.directory / 'tasks' / task_id
        workspace = folder / 'work'
        is_resume = workspace.exists() and (workspace / 'eval').exists()
        folder.mkdir(parents=True, exist_ok=True)
        self.current_task_id = task_id
        self.cancel_event.clear()
        self.store.execute("UPDATE tasks SET status='running',started=? WHERE id=?", (now(), task_id))
        status, error, code = 'failed', '', None
        try:
            cmd, env = engine.prepare_run(task, workspace, task['secrets'])
            if is_resume and task['spec']['mode'] == 'eval':
                results_dir = str(workspace / 'eval' / 'results')
                cmd.extend(['--use-cache', results_dir])
            retry_of = task.get('retry_of')
            if retry_of:
                src_eval = self.store.directory / 'tasks' / retry_of / 'artifacts' / 'eval'
                cache_root = workspace / 'eval' / 'results' / '_retry_cache'
                if src_eval.is_dir():
                    for run_dir in src_eval.iterdir():
                        if not run_dir.is_dir():
                            continue
                        for ds_dir in run_dir.iterdir():
                            if ds_dir.is_dir() and (ds_dir / 'predictions').is_dir():
                                shutil.copytree(ds_dir, cache_root / ds_dir.name, dirs_exist_ok=True)
                if cache_root.is_dir() and any(cache_root.iterdir()):
                    cmd.extend(['--use-cache', str(cache_root)])
            from thvv.prepare_linux import runtime_env
            env = runtime_env(env, self.store.directory)
            with (folder / 'run.log').open('a' if is_resume else 'w') as log:
                if is_resume:
                    log.write('\n\n--- 续跑恢复 ---\n\n')
                self.process = subprocess.Popen(cmd, cwd=workspace, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', start_new_session=True)
                for line in self.process.stdout:
                    log.write(redact(line, task['secrets']))
                    log.flush()
                code = self.process.wait()
            publish_artifacts(workspace, folder / 'artifacts', task['secrets'])
            status = 'succeeded' if code == 0 else 'failed'
            if not any((folder / 'artifacts').glob('**/*.json')):
                status, error = 'failed', '框架未生成结果摘要，请查看运行日志'
            if self.stop_event.is_set():
                status, error = 'interrupted', '服务关闭导致任务中断'
            if self.cancel_event.is_set():
                status, error = 'interrupted', '任务已被用户终止'
            if status == 'failed' and not error:
                error = '框架执行失败，请查看运行日志'
        except Exception as exc:
            error = redact(str(exc), task['secrets'])
        finally:
            interrupted = self.stop_event.is_set() or self.cancel_event.is_set()
            if status == 'succeeded':
                shutil.rmtree(workspace, ignore_errors=True)
            self.process = None
            self.current_task_id = None
            self.store.execute('UPDATE tasks SET status=?,finished=?,error=?,exit_code=? WHERE id=?', (status, now(), error, code, task_id))

    def stop_task(self, task_id):
        if self.current_task_id == task_id and self.process and self.process.poll() is None:
            if self.paused:
                try:
                    os.killpg(self.process.pid, signal.SIGCONT)
                except (OSError, ProcessLookupError):
                    pass
                self.paused = False
            self.cancel_event.set()
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            return True
        rows = self.store.rows("SELECT id FROM tasks WHERE id=? AND status='queued'", (task_id,))
        if rows:
            self.store.execute("UPDATE tasks SET status='interrupted',finished=?,error='任务已被用户取消' WHERE id=?", (now(), task_id))
            return True
        return False

    def pause_task(self, task_id):
        if self.current_task_id != task_id or not self.process or self.process.poll() is not None:
            return False
        if self.paused:
            return True
        try:
            os.killpg(self.process.pid, signal.SIGSTOP)
            self.paused = True
            return True
        except (OSError, ProcessLookupError):
            return False

    def resume_task(self, task_id):
        if self.current_task_id != task_id or not self.process or self.process.poll() is not None:
            return False
        if not self.paused:
            return True
        try:
            os.killpg(self.process.pid, signal.SIGCONT)
            self.paused = False
            return True
        except (OSError, ProcessLookupError):
            return False

    def resume_interrupted(self, task_id):
        """Resume an interrupted task using --use-cache with existing workspace."""
        task = self.store.task(task_id, private=True)
        if task['status'] != 'interrupted':
            return False
        folder = self.store.directory / 'tasks' / task_id
        workspace = folder / 'work'
        if not workspace.exists():
            return False
        self.store.execute("UPDATE tasks SET status='queued',error='' WHERE id=?", (task_id,))
        self.jobs.put(task_id)
        return True

def create_app(data_dir=None, run_jobs=True):
    store = Store(data_dir or os.environ.get('THVV_WEB_DATA', engine.ROOT / '.thvv-web'))
    try:
        auth = Auth(store)
    except Exception:
        store.db.close()
        raise
    runner = Runner(store)
    operation_gate = threading.RLock()
    environment = Environment(store.directory, operation_gate, lambda: bool(store.rows("SELECT id FROM tasks WHERE status IN ('queued','running') LIMIT 1")))

    if not store.rows('SELECT 1 FROM baselines LIMIT 1'):
        seed_path = Path(__file__).parent / 'static' / 'baseline.json'
        if seed_path.exists():
            seed = json.loads(seed_path.read_text())
            seed_time = datetime.now(timezone.utc).isoformat()
            for row in seed.get('rows', []):
                for model, score in row.get('scores', {}).items():
                    store.execute('INSERT OR IGNORE INTO baselines VALUES(?,?,?,?,?)',
                                  (model, row['dataset_id'], score, 'seed', seed_time))

    @asynccontextmanager
    async def lifespan(app):
        lease = (store.directory / 'server.lock').open('a')
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if run_jobs:
                runner.start()
            yield
        finally:
            environment.close()
            if run_jobs and hasattr(runner, 'thread'):
                runner.close()
            lease.close()
            store.db.close()

    app = FastAPI(lifespan=lifespan)
    app.state.store, app.state.runner = store, runner
    app.state.environment = environment
    app.state.auth = auth
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['*'])

    @app.middleware('http')
    async def local_mutation_guard(request: Request, call_next):
        public = (request.method in ('GET', 'HEAD') and request.url.path in ('/login', '/login.js', '/login.css')) or (request.method == 'POST' and request.url.path == '/api/auth/login')
        if not public and not auth.authenticated(request.cookies.get(COOKIE)):
            response = JSONResponse({'detail': '请先登录'}, 401) if request.url.path.startswith('/api/') else RedirectResponse('/login', 303)
        elif request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and (
            request.headers.get('x-thvv-request') != '1' or
            (request.headers.get('origin') is not None and request.headers['origin'] != str(request.base_url).rstrip('/'))
        ):
            response = JSONResponse({'detail': '请通过本地测试页面操作'}, 403)
        else:
            response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'detail': str(exc)}, 422)

    @app.exception_handler(RequestValidationError)
    async def invalid_fields(request, exc):
        # Pydantic's default errors include the input, which may be an API key.
        return JSONResponse({'detail': '参数格式不正确，请检查表单字段'}, 422)

    @app.get('/login', include_in_schema=False)
    def login_page(request: Request):
        if auth.authenticated(request.cookies.get(COOKIE)):
            return RedirectResponse('/', 303)
        return FileResponse(Path(__file__).parent / 'static' / 'login.html', headers={
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})

    @app.post('/api/auth/login')
    def login(data: LoginInput, request: Request):
        token = auth.login(data.username, data.password, request.cookies.get(COOKIE))
        response = JSONResponse({'username': 'admin'})
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                            secure=request.url.scheme == 'https', samesite='strict', path='/')
        return response

    @app.post('/api/auth/logout')
    def logout(request: Request):
        auth.logout(request.cookies.get(COOKIE))
        response = JSONResponse({'logged_out': True})
        response.delete_cookie(COOKIE, httponly=True, secure=request.url.scheme == 'https', samesite='strict', path='/')
        return response

    @app.get('/api/meta')
    def meta():
        datasets, excluded = engine.registry()
        return {'datasets': datasets, 'all_excluded': excluded, 'engine': engine.readiness(), 'framework_commit': '6e7a150', 'default_protocol': 'openai', 'judge_defaults': engine.judge_defaults(), 'perf_metrics': PERF_METRICS}

    @app.get('/api/objects')
    def objects():
        return [{**{k: row[k] for k in ('id', 'name', 'url', 'updated')}, 'has_api_key': bool(store.decrypt(row['secret']).get('api_key'))} for row in store.rows('SELECT * FROM objects ORDER BY updated DESC')]

    def put_object(data, object_id=None):
        name = data.name.strip()
        if not name:
            raise ValueError('请填写对象名称')
        url = engine.base_url(data.url)
        old = store.rows('SELECT * FROM objects WHERE id=?', (object_id,)) if object_id else []
        if object_id and not old:
            raise HTTPException(404, '测试对象不存在')
        key = data.api_key if data.api_key is not None else store.decrypt(old[0]['secret']).get('api_key', '') if old else ''
        if any(ord(c) < 32 for c in key):
            raise ValueError('密钥不能包含换行或控制字符')
        object_id = object_id or uuid.uuid4().hex
        try:
            store.execute('INSERT INTO objects VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,url=excluded.url,secret=excluded.secret,updated=excluded.updated', (object_id, name, url, store.encrypt({'api_key': key}), now()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, '对象名称已存在') from None
        return next(o for o in objects() if o['id'] == object_id)

    @app.post('/api/objects', status_code=201)
    def add_object(data: ObjectInput):
        return put_object(data)

    @app.patch('/api/objects/{object_id}')
    def edit_object(object_id: str, data: ObjectInput):
        return put_object(data, object_id)

    @app.delete('/api/objects/{object_id}')
    def delete_object(object_id: str):
        store.execute('DELETE FROM objects WHERE id=?', (object_id,))
        return {'deleted': True}

    def resolve_object(object_id, source=None):
        if source:
            original = store.task(source, private=True)
            return original['object'], original['secrets']
        rows = store.rows('SELECT * FROM objects WHERE id=?', (object_id,))
        if not rows:
            raise ValueError('请选择已保存的测试对象')
        return {k: rows[0][k] for k in ('id', 'name', 'url')}, store.decrypt(rows[0]['secret'])

    @app.post('/api/connectivity')
    def check_connection(data: ConnectionCheckInput):
        model = data.model.strip()
        if not model or any(ord(c) < 32 for c in model):
            raise ValueError('请填写有效模型名称')
        obj, secrets = resolve_object(data.object_id, data.replay_id)
        return connectivity.check(obj['url'], model, data.protocol, secrets.get('api_key', ''))

    def judge_check(options, key):
        if not key or not key.strip():
            raise ValueError('请填写裁判模型 API 密钥；重放时可留空沿用原任务密钥')
        if any(ord(c) < 32 for c in key):
            raise ValueError('裁判模型密钥不能包含控制字符')
        config = engine.judge_config(options)
        return connectivity.check(config['url'], config['model'], 'openai', key)

    @app.post('/api/judge-connectivity')
    def check_judge_connection(data: JudgeCheckInput):
        key = data.api_key
        if key is None and data.replay_id:
            key = store.task(data.replay_id, private=True)['secrets'].get('judge_api_key', '')
        return judge_check({'judge-model': data.model, 'judge-url': data.url}, key)

    def environment_spec(data):
        raw = data.model_dump()
        scope = raw.pop('scope')
        if scope == 'environment':
            registry, excluded = engine.registry()
            chosen = [d for d in registry if d not in excluded] if raw['all'] else raw['datasets']
            if set(chosen) - registry.keys():
                raise ValueError('请选择框架支持的数据集')
            return {'mode': raw['mode'], 'model': raw['model'], 'datasets': chosen if raw['mode'] == 'eval' else [], 'options': {}}
        raw.update(object_id='', model=raw['model'].strip() or 'environment-check', protocol='openai')
        # Sandbox connectivity is separate; data loading never starts a sandbox or a model.
        if 'swe_bench_pro' in raw['datasets'] and not raw['options'].get('sandbox'):
            raw['options']['sandbox'] = '{}'
        return engine.normalize_spec(raw)

    @app.post('/api/environment/check', status_code=202)
    def check_environment(data: EnvironmentInput):
        return environment.start(environment_spec(data), scope=data.scope)

    @app.post('/api/environment/prepare', status_code=202)
    def prepare_environment(data: EnvironmentInput):
        return environment.start(environment_spec(data), prepare=True, scope=data.scope)

    @app.post('/api/environment/stop')
    def stop_environment():
        return environment.stop()

    @app.post('/api/environment/stop-item')
    def stop_environment_item(data: StopItemInput):
        return environment.stop_item(data.item_id)

    @app.post('/api/environment/readiness')
    def environment_readiness(data: EnvironmentInput):
        return environment.readiness(environment_spec(data.model_copy(update={'scope': 'data'})))

    @app.post('/api/environment/cleanup-preview')
    def cleanup_preview(data: EnvironmentInput):
        selected = data.all or data.datasets if data.mode == 'eval' else data.options.get('bucket') or data.options.get('perf-command') == 'bench-all'
        spec = environment_spec(data if selected else data.model_copy(update={'scope': 'environment'}))
        return environment.preview_cleanup(data.scope, spec)

    @app.post('/api/environment/cleanup', status_code=202)
    def cleanup_environment(data: CleanupInput):
        return environment.clean(data.plan_id)

    @app.get('/api/environment')
    def environment_status():
        return environment.snapshot()

    @app.get('/api/system/stats')
    def system_stats(light: bool = False):
        from .system_stats import collect_all, collect_light
        if light:
            return collect_light()
        pids = []
        if runner.process and runner.process.poll() is None:
            pids.append((runner.process.pid, '测试任务'))
        if environment.process and hasattr(environment.process, 'pid'):
            try:
                if environment.process.poll() is None:
                    pids.append((environment.process.pid, '环境准备'))
            except (OSError, AttributeError):
                pass
        with environment.workers_lock:
            for key, worker in environment.workers.items():
                if worker.process and worker.process.poll() is None:
                    pids.append((worker.process.pid, key))
        return collect_all(active_pids=pids)

    def enqueue(data, source=None, retry_of=None):
        # A preparation cannot start between preflight validation and task creation.
        with operation_gate:
            return enqueue_checked(data, source, retry_of=retry_of)

    def enqueue_checked(data, source=None, retry_of=None):
        active = store.rows("SELECT id FROM tasks WHERE status IN ('queued','running') LIMIT 1")
        if active:
            raise HTTPException(409, '已有测试任务正在进行（' + active[0]['id'][:8] + '），请等待结束后再开始测试')
        spec = engine.normalize_spec(data.model_dump())
        obj, secrets = resolve_object(spec['object_id'], source)
        if data.judge_api_key is not None:
            secrets['judge_api_key'] = data.judge_api_key
        if any(any(ord(c) < 32 for c in v) for v in secrets.values()):
            raise ValueError('密钥不能包含控制字符')
        needs_judge = engine.requires_judge(spec)
        if needs_judge and not secrets.get('judge_api_key', '').strip():
            raise ValueError('所选数据集需要裁判模型 API 密钥')
        if not engine.readiness()['ready']:
            raise HTTPException(409, '评测依赖尚未安装，请运行 web/install-engine.sh 后重试')
        missing = engine.missing_perf_data(spec)
        if missing:
            raise HTTPException(409, '压测数据尚未下载：' + '、'.join(missing) + '。请按 WEB_README.md 下载对应 Git LFS 数据')
        if environment.busy() or not environment.validated(spec):
            raise HTTPException(409, '请先完成运行环境和当前数据的检查；依赖变更后重查环境，数据检查超过 5 分钟后重查数据')
        connection = connectivity.check(obj['url'], spec['model'], spec['protocol'], secrets.get('api_key', ''))
        if not connection['ready']:
            raise HTTPException(409, '开始前连通性检查失败：' + connection['message'])
        if needs_judge:
            judge = judge_check(spec['options'], secrets['judge_api_key'])
            if not judge['ready']:
                raise HTTPException(409, '裁判模型连通性检查失败，任务未启动：' + judge['message'])
        task_id = uuid.uuid4().hex
        payload = {'spec': spec, 'object': obj, 'replay_of': source if not retry_of else None,
                   'retry_of': retry_of, 'framework_commit': '6e7a150'}
        store.execute('INSERT INTO tasks(id,payload,secrets,status,created) VALUES(?,?,?,?,?)', (task_id, json.dumps(payload), store.encrypt(secrets), 'queued', now()))
        if run_jobs:
            runner.jobs.put(task_id)
        return store.task(task_id)

    @app.post('/api/tasks', status_code=201)
    def start_task(data: TaskInput):
        return enqueue(data)

    @app.post('/api/tasks/{task_id}/replay', status_code=201)
    def replay(task_id: str, data: TaskInput):
        return enqueue(data, task_id)

    def retryable_failed(task, folder):
        """A succeeded eval task keeps predictions and has skipped samples."""
        if task['status'] != 'succeeded' or task['spec']['mode'] != 'eval':
            return False
        eval_dir = folder / 'artifacts' / 'eval'
        if not eval_dir.is_dir() or not any(eval_dir.rglob('predictions')):
            return False
        skipped = 0
        for run_dir in eval_dir.iterdir():
            if not run_dir.is_dir():
                continue
            summary_path = run_dir / 'all_eval_summary.json'
            if not summary_path.is_file():
                continue
            try:
                summary = json.loads(summary_path.read_text())
                skipped += sum(r.get('skipped_count') or 0 for r in summary.get('results', []))
            except (ValueError, OSError):
                continue
        return skipped > 0

    @app.post('/api/tasks/{task_id}/retry-failed', status_code=201)
    def retry_failed(task_id: str):
        source = store.task(task_id)
        if not retryable_failed(source, store.directory / 'tasks' / task_id):
            raise HTTPException(409, '仅支持有失败样本且预测结果仍保留的已完成评测')
        data = TaskInput(object_id=source['spec']['object_id'], model=source['spec']['model'],
                         mode=source['spec']['mode'], protocol=source['spec']['protocol'],
                         datasets=source['spec']['datasets'], all=source['spec']['all'],
                         options=source['spec']['options'])
        return enqueue(data, task_id, retry_of=task_id)

    @app.get('/api/tasks')
    def tasks():
        result = []
        for row in store.rows('SELECT id FROM tasks ORDER BY created DESC'):
            task = store.task(row['id'])
            if task['status'] in ('queued', 'running'):
                task['progress'] = task_progress(task, store.directory / 'tasks' / task['id'])
            task['paused'] = runner.paused and runner.current_task_id == task['id']
            task['can_continue'] = task['status'] == 'interrupted' and (store.directory / 'tasks' / task['id'] / 'work').exists()
            task['can_retry_failed'] = retryable_failed(task, store.directory / 'tasks' / task['id'])
            result.append(task)
        return result

    def task_folder(task_id):
        store.task(task_id)
        return store.directory / 'tasks' / task_id

    @app.get('/api/tasks/{task_id}')
    def task_detail(task_id: str):
        task = store.task(task_id)
        folder = task_folder(task_id)
        task['progress'] = task_progress(task, folder)
        task['paused'] = runner.paused and runner.current_task_id == task_id
        task['can_continue'] = task['status'] == 'interrupted' and (folder / 'work').exists()
        task['can_retry_failed'] = retryable_failed(task, folder)
        root = folder / 'artifacts'
        task['artifacts'] = [{'path': str(p.relative_to(root)), 'size': p.stat().st_size} for p in sorted(root.rglob('*')) if p.is_file()]
        summaries = []
        for p in root.glob('**/all_eval_summary.json'):
            summaries.extend(json.loads(p.read_text(), parse_constant=lambda _: None).get('results', []))
        task['eval_results'] = summaries
        task['perf_results'] = [json.loads(p.read_text(), parse_constant=lambda _: None) for p in root.glob('**/benchmark_summary.json')]
        return task

    @app.get('/api/tasks/{task_id}/logs')
    def logs(task_id: str):
        path = task_folder(task_id) / 'run.log'
        text = ''
        if path.exists():
            with path.open('rb') as f:
                f.seek(max(0, path.stat().st_size - 100_000))
                text = f.read().decode(errors='replace')
        return {'text': text}

    @app.get('/api/tasks/{task_id}/artifacts/{name:path}')
    def artifact(task_id: str, name: str):
        root = (task_folder(task_id) / 'artifacts').resolve()
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, '文件不存在')
        if path.suffix == '.html':
            return Response(path.read_bytes(), media_type='text/html', headers={'Content-Security-Policy': "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; form-action 'none'"})
        return FileResponse(path, filename=path.name)

    @app.delete('/api/tasks/{task_id}')
    def delete_task(task_id: str):
        task = store.task(task_id)
        if task['status'] in ('queued', 'running'):
            raise HTTPException(409, '任务仍在运行，结束后可删除')
        shutil.rmtree(task_folder(task_id), ignore_errors=False) if task_folder(task_id).exists() else None
        store.execute('DELETE FROM tasks WHERE id=?', (task_id,))
        return {'deleted': True}

    @app.post('/api/tasks/{task_id}/stop')
    def stop_task(task_id: str):
        task = store.task(task_id)
        if task['status'] not in ('queued', 'running'):
            raise HTTPException(409, '任务不在运行或排队状态')
        if runner.stop_task(task_id):
            return {'stopped': True}
        raise HTTPException(409, '未能终止任务')

    @app.post('/api/tasks/{task_id}/pause')
    def pause_task(task_id: str):
        task = store.task(task_id)
        if task['status'] != 'running':
            raise HTTPException(409, '任务不在运行状态')
        if runner.pause_task(task_id):
            return {'paused': True}
        raise HTTPException(409, '未能暂停任务')

    @app.post('/api/tasks/{task_id}/resume')
    def resume_task(task_id: str):
        task = store.task(task_id)
        if task['status'] != 'running':
            raise HTTPException(409, '任务不在运行状态')
        if runner.resume_task(task_id):
            return {'resumed': True}
        raise HTTPException(409, '未能恢复任务')

    @app.post('/api/tasks/{task_id}/continue')
    def continue_task(task_id: str):
        task = store.task(task_id)
        if task['status'] != 'interrupted':
            raise HTTPException(409, '只有已中断的任务可以继续')
        folder = store.directory / 'tasks' / task_id / 'work'
        if not folder.exists():
            raise HTTPException(409, '任务工作目录已被清理，无法续跑，请使用重放')
        if runner.resume_interrupted(task_id):
            return {'continued': True}
        raise HTTPException(409, '未能恢复任务')

    @app.get('/api/baselines')
    def list_baselines():
        rows = store.rows('SELECT model,dataset,score,source,updated FROM baselines ORDER BY dataset,model')
        by_dataset = {}
        for r in rows:
            by_dataset.setdefault(r['dataset'], []).append(r)
        datasets = []
        for ds_id, entries in by_dataset.items():
            datasets.append({
                'dataset_id': ds_id,
                'entries': sorted(entries, key=lambda e: -e['score']),
                'updated': max(e['updated'] for e in entries),
            })
        models = sorted(set(r['model'] for r in rows))
        return {'datasets': datasets, 'models': models}

    @app.get('/api/baselines/lookup')
    def baseline_lookup():
        rows = store.rows('SELECT dataset,model,score FROM baselines')
        by_dataset = {}
        for r in rows:
            by_dataset.setdefault(r['dataset'], {})[r['model']] = r['score']
        return {'rows': [{'dataset_id': ds, 'scores': scores, 'tolerance': '±2-4%'}
                         for ds, scores in by_dataset.items()]}

    @app.post('/api/baselines', status_code=201)
    def upsert_baseline(data: BaselineEntry):
        store.snapshot_baselines('手动录入')
        now = datetime.now(timezone.utc).isoformat()
        store.execute('INSERT OR REPLACE INTO baselines VALUES(?,?,?,?,?)',
                      (data.model.strip(), data.dataset.strip(), data.score, 'manual', now))
        return {'ok': True}

    @app.post('/api/baselines/import')
    def import_baseline(data: BaselineImport):
        task = store.task(data.task_id)
        if task['status'] != 'succeeded':
            raise HTTPException(409, '只能从已成功的任务导入基线')
        folder = store.directory / 'tasks' / data.task_id
        model_name = data.model.strip() or task['spec']['model']
        now = datetime.now(timezone.utc).isoformat()
        source = f'task:{data.task_id}'
        eval_entries = []
        for p in (folder / 'artifacts').glob('**/all_eval_summary.json'):
            for r in json.loads(p.read_text(), parse_constant=lambda _: None).get('results', []):
                if r.get('score') is None or not r.get('success'):
                    continue
                score = float(r['score'])
                if 0 <= score <= 1:
                    score = score * 100
                eval_entries.append((model_name, r['dataset'], round(score, 2), source, now))
        perf_entries = []
        for p in (folder / 'artifacts').glob('**/benchmark_summary.json'):
            try:
                perf = json.loads(p.read_text(), parse_constant=lambda _: None)
            except (ValueError, OSError):
                continue
            concurrency = perf.get('Concurrency', '?')
            bucket = task['spec']['options'].get('bucket', '?')
            if task['spec']['options'].get('perf-command') == 'bench-all':
                parts = p.relative_to(folder / 'artifacts').parts
                for part in parts:
                    if part.startswith('perf-') and not part.startswith('perf-report'):
                        bucket = part.split('-')[1] if len(part.split('-')) > 1 else '?'
                        break
            scenario = f"{bucket}\u00b7\u5e76\u53d1{concurrency}"
            for metric in PERF_METRICS:
                if metric in perf and perf[metric] is not None:
                    perf_entries.append((model_name, scenario, metric, round(float(perf[metric]), 4), source, now))
        if not eval_entries and not perf_entries:
            raise HTTPException(409, '该任务无可导入的结果')
        store.snapshot_baselines(f'任务导入: {data.task_id[:8]}')
        for e in eval_entries:
            store.execute('INSERT OR REPLACE INTO baselines VALUES(?,?,?,?,?)', e)
        for e in perf_entries:
            store.execute('INSERT OR REPLACE INTO perf_baselines VALUES(?,?,?,?,?,?)', e)
        return {'imported': len(eval_entries), 'perf_imported': len(perf_entries), 'model': model_name}

    @app.delete('/api/baselines/{model}/{dataset}')
    def delete_baseline(model: str, dataset: str):
        store.snapshot_baselines('删除基线')
        store.execute('DELETE FROM baselines WHERE model=? AND dataset=?', (model, dataset))
        return {'ok': True}

    @app.get('/api/perf-baselines')
    def list_perf_baselines():
        rows = store.rows('SELECT model,scenario,metric,value,source,updated FROM perf_baselines ORDER BY scenario,model,metric')
        scenarios = sorted(set(r['scenario'] for r in rows))
        models = sorted(set(r['model'] for r in rows))
        metrics = sorted(set(r['metric'] for r in rows))
        return {'rows': rows, 'scenarios': scenarios, 'models': models, 'metrics': metrics}

    @app.post('/api/perf-baselines', status_code=201)
    def upsert_perf_baseline(data: PerfBaselineEntry):
        store.snapshot_baselines('手动录入性能基线')
        ts = datetime.now(timezone.utc).isoformat()
        store.execute('INSERT OR REPLACE INTO perf_baselines VALUES(?,?,?,?,?,?)',
                      (data.model.strip(), data.scenario.strip(), data.metric.strip(), data.value, 'manual', ts))
        return {'ok': True}

    @app.delete('/api/perf-baselines/{model}/{scenario}/{metric}')
    def delete_perf_baseline(model: str, scenario: str, metric: str):
        store.snapshot_baselines('删除性能基线')
        store.execute('DELETE FROM perf_baselines WHERE model=? AND scenario=? AND metric=?', (model, scenario, metric))
        return {'ok': True}

    @app.get('/api/baselines/snapshots')
    def list_snapshots():
        rows = store.rows('SELECT id,created_at,label,eval_data,perf_data FROM baseline_snapshots ORDER BY id DESC LIMIT 50')
        return {'snapshots': rows}

    @app.post('/api/baselines/rollback')
    def rollback(data: BaselineRollback):
        target = data.target
        if target == 'previous':
            snap = store.rows('SELECT id,created_at,label FROM baseline_snapshots ORDER BY id DESC LIMIT 1')
            if not snap:
                raise HTTPException(409, '没有可回退的快照')
            result = store.rollback_baselines(snap[0]['id'])
        elif target.startswith('snapshot:'):
            try:
                snap_id = int(target.split(':', 1)[1])
            except (ValueError, IndexError):
                raise HTTPException(422, '无效的快照 ID')
            result = store.rollback_baselines(snap_id)
        elif target in ('1day', '7days'):
            days = 1 if target == '1day' else 7
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            snap = store.rows(
                'SELECT id,created_at,label FROM baseline_snapshots WHERE created_at<=? ORDER BY id DESC LIMIT 1',
                (cutoff,))
            if not snap:
                raise HTTPException(409, f'没有 {days} 天前的快照可回退')
            result = store.rollback_baselines(snap[0]['id'])
        else:
            raise HTTPException(422, '无效的回退目标')
        if not result:
            raise HTTPException(409, '回退失败')
        return {'ok': True, 'restored_from': result['created_at'], 'label': result['label']}

    app.mount('/', StaticFiles(directory=Path(__file__).parent / 'static', html=True), name='web')
    return app
