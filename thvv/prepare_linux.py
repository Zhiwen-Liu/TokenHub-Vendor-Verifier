"""Linux deployment helpers, also used by the web console's explicit prepare action."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen


API = 'https://api.azul.com/metadata/v1/zulu/packages/'
JAVA_DIRECTORY = 'runtimes/java-8'


def runtime_env(env, directory):
    env = dict(env)
    home = Path(directory) / JAVA_DIRECTORY
    if (home / 'bin/java').is_file():
        env['JAVA_HOME'] = str(home.absolute())
    if env.get('JAVA_HOME') and (Path(env['JAVA_HOME']) / 'bin/java').is_file():
        env['PATH'] = str(Path(env['JAVA_HOME']) / 'bin') + os.pathsep + env.get('PATH', '')
    return env


def inspect_component(component, env=None):
    env = dict(os.environ) if env is None else env
    executable = shutil.which(component, path=env.get('PATH'))
    automatic = platform.system() == 'Linux'
    result = {'ready': False, 'automatic': automatic, 'version': '', 'code': 'missing'}
    if not executable:
        return {**result, 'detail': f'{component} 未安装；可点击“准备缺失项”' if automatic else f'{component} 未安装；自动准备适用于 Linux 部署'}
    args = [executable, '-version'] if component == 'java' else [executable, 'info', '--format', '{{.ServerVersion}}']
    try:
        run = subprocess.run(args, env=env, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return {**result, 'code': 'timeout', 'detail': f'{component} 检查未完成或超时，请确认服务状态'}
    output = run.stdout + run.stderr
    if component == 'java':
        match = re.search(r'(?:openjdk|java) version "([^"]+)"', output)
        version = match[1] if match else ''
        ready = run.returncode == 0 and version.startswith('1.8.')
        return {**result, 'ready': ready, 'version': version, 'code': 'ready' if ready else 'version',
                'detail': f'Java {version} 可用，符合评分组件的 Java 8 要求' if ready else 'MSR-VTT 评分组件要求 Java 8；可在 Linux 上自动准备独立运行环境'}
    if run.returncode == 0 and re.fullmatch(r'[0-9][\w.+~-]*', run.stdout.strip()):
        return {**result, 'ready': True, 'version': run.stdout.strip(), 'code': 'ready',
                'detail': 'Docker 服务可用；任务镜像仍由框架在运行时准备'}
    denied = 'permission denied' in output.lower() or 'operation not permitted' in output.lower()
    return {**result, 'code': 'permission' if denied else 'stopped',
            'detail': '当前服务账号无权访问 Docker，请管理员授权该账号并重新启动网页服务' if denied else 'Docker 服务未连接；Linux 上点击“准备缺失项”可尝试启动本机服务'}


def java_platform():
    if platform.system() != 'Linux':
        raise ValueError('自动安装适用于 Linux 部署，当前系统只进行检测')
    arch = {'x86_64': 'x86_64', 'aarch64': 'aarch64', 'arm64': 'aarch64'}.get(platform.machine())
    if not arch or platform.libc_ver()[0] != 'glibc':
        raise ValueError('自动准备 Java 支持 glibc Linux 的 x86_64 / aarch64；当前平台请手动安装 Java 8')
    return arch


def fetch_json(url):
    with urlopen(url, timeout=30) as response:
        return json.loads(response.read(2_000_000))


def install_java(directory):
    arch = java_platform()
    if not hasattr(tarfile, 'data_filter'):
        raise ValueError('安全解压 Java 需要 Python 3.11.8 或更新版本，请先更新网页运行环境')
    query = urlencode({'java_version': '8', 'os': 'linux', 'arch': arch,
                       'java_package_type': 'jre', 'javafx_bundled': 'false', 'archive_type': 'tar.gz',
                       'release_status': 'ga', 'availability_types': 'CA', 'latest': 'true', 'lib_c_type': 'glibc'})
    packages = fetch_json(API + '?' + query)
    if not packages:
        raise ValueError('官方源没有适用于当前平台的 Java 8 运行环境')
    package_id = packages[0]['package_uuid']
    if not re.fullmatch(r'[a-f0-9-]{36}', package_id):
        raise ValueError('Java 下载元数据无效')
    package = fetch_json(API + package_id)
    url, digest = package['download_url'], package['sha256_hash']
    target = urlsplit(url)
    if (target.scheme != 'https' or target.netloc != 'cdn.azul.com' or not target.path.startswith('/zulu/bin/')
            or not target.path.endswith('.tar.gz') or not re.fullmatch(r'[a-f0-9]{64}', digest)
            or package.get('java_version', [None])[0] != 8 or package.get('os') != 'linux'
            or package.get('availability_type') != 'CA' or package.get('lib_c_type') != 'glibc'
            or package.get('java_package_type') != 'jre' or package.get('hw_bitness') != 64
            or package.get('arch') != ('arm' if arch == 'aarch64' else 'x86')):
        raise ValueError('Java 下载地址、版本或校验信息无效')
    parent = Path(directory) / 'runtimes'
    parent.mkdir(parents=True, exist_ok=True)
    if (parent / 'java-8').is_symlink():
        raise ValueError('Java 目标目录是符号链接，请先手动检查')
    print('从 Azul 官方源下载 Java 8，校验 SHA-256 后安装到项目数据目录。', flush=True)
    with tempfile.TemporaryDirectory(prefix='.java-download-', dir=parent) as temporary:
        staging = Path(temporary)
        archive = staging / 'java.tar.gz'
        checksum, size = hashlib.sha256(), 0
        with urlopen(url, timeout=60) as response, archive.open('wb') as output:
            if urlsplit(response.url).scheme != 'https' or urlsplit(response.url).netloc != 'cdn.azul.com':
                raise ValueError('Java 下载发生未授权的跨域跳转')
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise ValueError('Java 下载文件超过大小限制')
                checksum.update(chunk)
                output.write(chunk)
        if checksum.hexdigest() != digest:
            raise ValueError('Java 下载校验失败，未安装，请重试准备')
        unpack = staging / 'unpacked'
        unpack.mkdir()
        with tarfile.open(archive, 'r:gz') as package_archive:
            if sum(m.size for m in package_archive.getmembers()) > 1024 * 1024 * 1024:
                raise ValueError('Java 解压大小超过限制')
            # Refuse archive traversal and links escaping the destination directory.
            package_archive.extractall(unpack, filter='data')
        homes = [p.parent.parent for p in unpack.glob('*/bin/java')]
        if len(homes) != 1:
            raise ValueError('Java 安装包目录结构不符合预期')
        home = homes[0]
        checked = inspect_component('java', runtime_env({**os.environ, 'JAVA_HOME': str(home)}, staging))
        if not checked['ready']:
            raise ValueError('下载的 Java 无法在当前系统运行，未替换已有环境')
        (home / '.thvv-managed.json').write_text(json.dumps({'source': url, 'sha256': digest, 'version': checked['version']}))
        destination, previous = parent / 'java-8', staging / 'previous'
        if destination.exists():
            destination.rename(previous)
        try:
            home.rename(destination)
        except OSError:
            if previous.exists():
                previous.rename(destination)
            raise
    print('Java 安装与版本验证完成。', flush=True)


def prepare_component(component, directory, env):
    result = inspect_component(component, env)
    if result['ready']:
        return {**result, 'disposition': 'reused'}
    if platform.system() != 'Linux':
        return {**result, 'automatic': False, 'detail': '自动准备适用于 Linux 部署；当前系统请手动准备后重新检查'}
    if component == 'java':
        install_java(directory)
    else:
        if result['code'] == 'permission':
            return {**result, 'automatic': False}
        if env.get('DOCKER_HOST') not in (None, '', 'unix:///var/run/docker.sock', 'unix:///run/docker.sock') or env.get('DOCKER_CONTEXT') not in (None, '', 'default'):
            return {**result, 'automatic': False, 'detail': '当前配置了独立的 Docker 连接，请在对应主机或用户环境启动服务'}
        if not Path('/run/systemd/system').is_dir():
            return {**result, 'automatic': False, 'detail': '当前 Linux 未使用 systemd，需由宿主机管理员准备 Docker 服务'}
        if os.geteuid() != 0:
            sudo = shutil.which('sudo')
            if not sudo or subprocess.run([sudo, '-n', 'true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                return {**result, 'automatic': False, 'detail': '安装或启动 Docker 需要管理员权限；请管理员运行 sudo bash thvv/prepare-docker.sh，然后重新检查'}
        command = ['bash', str(Path(__file__).with_name('prepare-docker.sh'))]
        if os.geteuid() != 0:
            command = [sudo, '-n', '--', *command]
        subprocess.run(command, env=env, check=True)
    checked = inspect_component(component, runtime_env(env, directory))
    if component == 'docker' and checked.get('code') == 'permission':
        checked['automatic'] = False
    return {**checked, 'disposition': 'prepared' if checked['ready'] else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'prepare'])
    parser.add_argument('component', choices=['java', 'docker'])
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('THVV_WEB_DATA', Path(__file__).resolve().parents[1] / '.thvv-web')))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    env = runtime_env(os.environ, args.data_dir)
    try:
        result = prepare_component(args.component, args.data_dir, env) if args.action == 'prepare' else inspect_component(args.component, env)
    except Exception as exc:
        print(f'准备失败：{exc}', flush=True)
        result = {'ready': False, 'automatic': platform.system() == 'Linux', 'code': 'failed',
                  'detail': '自动准备未完成，请展开日志查看原因，修复网络或安装问题后重试'}
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    raise SystemExit(0 if result['ready'] else 1)


if __name__ == '__main__':
    main()
