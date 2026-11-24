"""Single administrator login; credentials and sessions stay in the local data directory."""
from __future__ import annotations

from collections import deque
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time

from fastapi import HTTPException


COOKIE = 'thvv_session'
SESSION_SECONDS = 12 * 60 * 60
ITERATIONS = 600_000
MAX_ATTEMPTS = 10
ATTEMPT_WINDOW = 60


def configure(directory: Path, password: str):
    """Write only a salted password hash. Restart the service after changing it."""
    if not password or len(password) > 1024:
        raise ValueError('密码不能为空，且不能超过 1024 个字符')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS)
    data = {'username': 'admin', 'algorithm': 'pbkdf2_sha256', 'iterations': ITERATIONS,
            'salt': salt.hex(), 'digest': digest.hex()}
    fd, name = tempfile.mkstemp(prefix='.auth-', dir=directory)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, directory / 'auth.json')
    finally:
        Path(name).unlink(missing_ok=True)


class Auth:
    def __init__(self, store):
        self.store = store
        try:
            config = json.loads((store.directory / 'auth.json').read_text())
            if config['username'] != 'admin' or config['algorithm'] != 'pbkdf2_sha256' or config['iterations'] != ITERATIONS:
                raise ValueError('unsupported credential format')
            self.salt, self.digest = bytes.fromhex(config['salt']), bytes.fromhex(config['digest'])
            if len(self.salt) != 16 or len(self.digest) != 32:
                raise ValueError('invalid credential hash')
        except (OSError, ValueError, KeyError, TypeError):
            raise RuntimeError('登录配置缺失或无效，请先运行 .venv-web/bin/python -m web.auth 设置 admin 密码') from None
        os.chmod(store.directory / 'auth.json', 0o600)
        self.version = hashlib.sha256(self.salt + self.digest).hexdigest()
        self.lock = threading.Lock()
        self.failures = deque()
        store.execute('CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, expires REAL NOT NULL, credential_version TEXT NOT NULL)')
        store.execute('DELETE FROM sessions WHERE expires<=? OR credential_version!=?', (time.time(), self.version))

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def authenticated(self, token):
        if not token or len(token) != 43 or not all(c.isascii() and (c.isalnum() or c in '-_') for c in token):
            return False
        return bool(self.store.rows('SELECT 1 FROM sessions WHERE token_hash=? AND expires>? AND credential_version=?',
                                    (self.token_hash(token), time.time(), self.version)))

    def login(self, username, password, previous=None):
        # A bounded global window fits this single-account, local-only console.
        # Serialize expensive hashes so concurrent failures cannot bypass the limit.
        with self.lock:
            clock = time.monotonic()
            while self.failures and self.failures[0] <= clock - ATTEMPT_WINDOW:
                self.failures.popleft()
            if len(self.failures) >= MAX_ATTEMPTS:
                retry = max(1, int(ATTEMPT_WINDOW - (clock - self.failures[0])) + 1)
                raise HTTPException(429, '登录尝试过于频繁，请稍后重试', headers={'Retry-After': str(retry)})
            digest = hashlib.pbkdf2_hmac('sha256', password.encode(), self.salt, ITERATIONS)
            valid_password = hmac.compare_digest(digest, self.digest)
            valid_username = hmac.compare_digest(username.encode(), b'admin')
            if not (valid_password and valid_username):
                self.failures.append(time.monotonic())
                raise HTTPException(401, '账号或密码错误')
            self.failures.clear()
            self.logout(previous)
            token = secrets.token_urlsafe(32)
            self.store.execute('DELETE FROM sessions WHERE expires<=?', (time.time(),))
            self.store.execute('INSERT INTO sessions VALUES(?,?,?)', (self.token_hash(token), time.time() + SESSION_SECONDS, self.version))
            return token

    def logout(self, token):
        if token and len(token) == 43:
            self.store.execute('DELETE FROM sessions WHERE token_hash=?', (self.token_hash(token),))


def main():
    from .engine import ROOT
    directory = Path(os.environ.get('THVV_WEB_DATA', ROOT / '.thvv-web'))
    if not os.isatty(0):
        raise SystemExit('请在终端中运行此命令，交互式设置密码。')
    password = getpass.getpass('设置 admin 登录密码：')
    if password != getpass.getpass('再次输入密码：'):
        raise SystemExit('两次密码不一致，未修改登录配置。')
    configure(directory, password)
    print('admin 登录配置已保存。若服务正在运行，请重启后生效；原有登录将失效。')


if __name__ == '__main__':
    main()
