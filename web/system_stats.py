"""System resource monitoring via /proc — no third-party dependencies."""
from __future__ import annotations

import os
import platform
import re
import subprocess
import threading
import time


_cache = {'data': None, 'time': 0}
_cache_lock = threading.Lock()
_inflight = threading.Event()
CACHE_TTL = 1.0


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ''


_cpu_cache = {'result': {'total_percent': 0, 'cores': [], 'count': 0}, 'prev': None}
_cpu_sampler_started = False


def _parse_stat():
    lines = _read('/proc/stat').splitlines()
    result = {}
    for line in lines:
        if line.startswith('cpu'):
            parts = line.split()
            name = parts[0]
            values = [int(v) for v in parts[1:8]]
            idle = values[3] + values[4]
            total = sum(values)
            result[name] = (idle, total)
    return result


def _cpu_sampler():
    prev = _parse_stat()
    while True:
        time.sleep(1)
        cur = _parse_stat()
        out = {}
        for name in cur:
            if name in prev:
                idle_d = cur[name][0] - prev[name][0]
                total_d = cur[name][1] - prev[name][1]
                out[name] = round(100 * (1 - idle_d / total_d), 1) if total_d > 0 else 0.0
        total = out.pop('cpu', 0.0)
        cores = [out[k] for k in sorted(out.keys(), key=lambda x: int(x.replace('cpu', '')))]
        _cpu_cache['result'] = {'total_percent': total, 'cores': cores, 'count': len(cores)}
        prev = cur


def cpu_percent(interval=None):
    global _cpu_sampler_started
    if not _cpu_sampler_started:
        _cpu_sampler_started = True
        t = threading.Thread(target=_cpu_sampler, daemon=True)
        t.start()
        time.sleep(0.1)  # brief wait for first sample only
    return _cpu_cache['result']


def memory_info():
    data = {}
    for line in _read('/proc/meminfo').splitlines():
        m = re.match(r'(\w+):\s+(\d+)', line)
        if m:
            data[m.group(1)] = int(m.group(2)) * 1024
    total = data.get('MemTotal', 0)
    free = data.get('MemFree', 0)
    available = data.get('MemAvailable', 0)
    buffers = data.get('Buffers', 0)
    cached = data.get('Cached', 0)
    used = total - free - buffers - cached
    swap_total = data.get('SwapTotal', 0)
    swap_free = data.get('SwapFree', 0)
    return {'total': total, 'used': max(used, 0), 'free': free,
            'available': available, 'buffers': buffers, 'cached': cached,
            'swap_total': swap_total, 'swap_used': swap_total - swap_free}


_DISK_FS = {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs', 'f2fs'}


def _real_mounts():
    """Yield (mountpoint, fstype) for real local block-device mounts."""
    seen = set()
    for line in _read('/proc/mounts').splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mount, fstype = parts[0], parts[1], parts[2]
        mount = mount.replace('\\040', ' ').replace('\\011', '\t')
        if not device.startswith('/dev/') or fstype not in _DISK_FS:
            continue
        if device in seen:  # skip bind mounts of the same device
            continue
        seen.add(device)
        yield mount, fstype


def disk_info():
    results = []
    mounts = [('/', None), *_real_mounts()]
    seen = set()
    for mount, fstype in mounts:
        if mount in seen:
            continue
        try:
            st = os.statvfs(mount)
        except OSError:
            continue
        total = st.f_frsize * st.f_blocks
        if total <= 0:
            continue
        # used excludes ext4/xfs reserved blocks (matches `df`), free is what an
        # unprivileged process can still write.
        used = st.f_frsize * (st.f_blocks - st.f_bfree)
        free = st.f_frsize * st.f_bavail
        entry = {'mount': mount, 'total': total, 'used': used, 'free': free}
        if fstype:
            entry['fstype'] = fstype
        results.append(entry)
        seen.add(mount)
    return results


def load_average():
    parts = _read('/proc/loadavg').split()
    if len(parts) >= 3:
        return {'1m': float(parts[0]), '5m': float(parts[1]), '15m': float(parts[2])}
    return {'1m': 0, '5m': 0, '15m': 0}


def uptime_seconds():
    parts = _read('/proc/uptime').split()
    return float(parts[0]) if parts else 0


def system_info():
    info = {'arch': platform.machine(), 'cpu_count': os.cpu_count() or 0, 'os': '', 'hostname': platform.node()}
    for line in _read('/etc/os-release').splitlines():
        if line.startswith('PRETTY_NAME='):
            info['os'] = line.split('=', 1)[1].strip('"')
            break
    return info


def docker_containers():
    try:
        # docker stats streams per-container samples (~2s each); skip when nothing runs.
        check = subprocess.run(['docker', 'ps', '-q'], capture_output=True, text=True, timeout=5)
        if check.returncode != 0 or not check.stdout.strip():
            return []
        result = subprocess.run(
            ['docker', 'stats', '--no-stream', '--format',
             '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}'],
            capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return []
        containers = []
        for line in result.stdout.strip().splitlines():
            parts = line.split('\t')
            if len(parts) >= 4:
                containers.append({
                    'name': parts[0],
                    'cpu_percent': parts[1],
                    'mem_usage': parts[2],
                    'pids': parts[3],
                })
        return containers
    except (subprocess.TimeoutExpired, OSError):
        return []


def process_info(pids):
    hz = os.sysconf('SC_CLK_TCK')
    page_size = os.sysconf('SC_PAGE_SIZE')
    results = []
    for pid, label in pids:
        try:
            stat = _read(f'/proc/{pid}/stat')
            if not stat:
                continue
            # Parse comm (may contain spaces/parens)
            i = stat.index('(')
            j = stat.rindex(')')
            comm = stat[i + 1:j]
            fields = stat[j + 2:].split()
            utime = int(fields[11])
            stime = int(fields[12])
            rss_pages = int(fields[21])
            threads = int(fields[17])
            cpu_seconds = round((utime + stime) / hz, 1)
            rss_bytes = rss_pages * page_size
            results.append({
                'pid': pid, 'label': label, 'comm': comm,
                'cpu_seconds': cpu_seconds, 'rss_bytes': rss_bytes,
                'threads': threads,
            })
        except (OSError, ValueError, IndexError):
            continue
    return results


def network_info():
    """Read /proc/net/dev for per-interface traffic stats."""
    interfaces = []
    for line in _read('/proc/net/dev').splitlines()[2:]:
        parts = line.split()
        if len(parts) < 17:
            continue
        name = parts[0].rstrip(':')
        if name == 'lo' or name.startswith(('docker', 'br-', 'veth', 'virbr')):
            continue
        interfaces.append({
            'name': name,
            'rx_bytes': int(parts[1]),
            'rx_packets': int(parts[2]),
            'rx_errors': int(parts[3]),
            'rx_dropped': int(parts[4]),
            'tx_bytes': int(parts[9]),
            'tx_packets': int(parts[10]),
            'tx_errors': int(parts[11]),
            'tx_dropped': int(parts[12]),
        })
    try:
        result = subprocess.run(['ss', '-s'], capture_output=True, text=True, timeout=5)
        tcp_line = ''
        for line in result.stdout.splitlines():
            if line.startswith('TCP:'):
                tcp_line = line.strip()
                break
    except (subprocess.TimeoutExpired, OSError):
        tcp_line = ''
    return {'interfaces': interfaces, 'tcp_summary': tcp_line}


def collect_light():
    """Top-bar stats: CPU/memory/disk/load only — no docker, no per-process table."""
    data = {
        'cpu': cpu_percent(),
        'memory': memory_info(),
        'disk': disk_info(),
        'load': load_average(),
        'uptime': uptime_seconds(),
        'system': system_info(),
        'docker': [],
        'processes': [],
        'timestamp': time.time(),
    }
    return data


def collect_all(active_pids=None):
    def _compute():
        return {
            'cpu': cpu_percent(),
            'memory': memory_info(),
            'disk': disk_info(),
            'network': network_info(),
            'load': load_average(),
            'uptime': uptime_seconds(),
            'system': system_info(),
            'docker': docker_containers(),
            'processes': process_info(active_pids or []),
            'timestamp': time.time(),
        }

    with _cache_lock:
        if _cache['data'] and time.monotonic() - _cache['time'] < CACHE_TTL:
            return _cache['data']
        compute_here = not _inflight.is_set()
        if compute_here:
            _inflight.set()
    if compute_here:
        try:
            data = _compute()
        except Exception:
            with _cache_lock:
                _inflight.clear()
            raise
        with _cache_lock:
            _cache['data'] = data
            _cache['time'] = time.monotonic()
            _inflight.clear()
        return data
    # Another request is already computing; wait and reuse its result.
    _inflight.wait(timeout=15)
    with _cache_lock:
        return _cache['data']
