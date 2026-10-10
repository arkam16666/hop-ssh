"""Hop core: read/write ~/.ssh/config host blocks, extra metadata, reachability checks.

~/.ssh/config stays the source of truth, so plain `ssh <alias>` keeps working.
Only simple `Host <alias>` blocks (no wildcards) are managed; everything else in
the file is preserved byte for byte.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

SSH_CONFIG = os.path.expanduser('~/.ssh/config')
META_FILE = os.path.expanduser('~/.config/hop/meta.json')
MANAGED_KEYS = ('HostName', 'User', 'Port', 'IdentityFile')


@dataclass
class Host:
    alias: str
    hostname: str = ''
    user: str = ''
    port: str = ''
    identity: str = ''
    extra: list = field(default_factory=list)   # other option lines, kept verbatim
    group: str = ''
    note: str = ''
    last_used: float = 0

    @property
    def target(self):
        host = self.hostname or self.alias
        user = f'{self.user}@' if self.user else ''
        port = f':{self.port}' if self.port and self.port != '22' else ''
        return f'{user}{host}{port}'


# ── ~/.ssh/config parsing ─────────────────────────────────────────────────────

_HEADER = re.compile(r'^\s*(Host|Match)\s+(.*?)\s*$', re.I)
_OPTION = re.compile(r'^\s*([A-Za-z]+)\s*(?:=\s*|\s+)(.*?)\s*$')


def _read_lines():
    try:
        with open(SSH_CONFIG) as fh:
            return fh.read().splitlines()
    except FileNotFoundError:
        return []


def _blocks(lines):
    """Split into [(header_or_None, [lines])]; the first block is the preamble."""
    blocks = [(None, [])]
    for line in lines:
        m = _HEADER.match(line)
        if m:
            blocks.append((m, [line]))
        else:
            blocks[-1][1].append(line)
    return blocks


def _is_managed(header):
    if not header or header.group(1).lower() != 'host':
        return False
    pats = header.group(2).split()
    return len(pats) == 1 and not any(c in pats[0] for c in '*?!')


def load_meta():
    try:
        with open(META_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_meta(meta):
    os.makedirs(os.path.dirname(META_FILE), exist_ok=True)
    tmp = META_FILE + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, META_FILE)


def load_hosts():
    meta = load_meta()
    hosts = []
    for header, lines in _blocks(_read_lines()):
        if not _is_managed(header):
            continue
        h = Host(alias=header.group(2).split()[0])
        for line in lines[1:]:
            m = _OPTION.match(line)
            if not m:
                if line.strip():
                    h.extra.append(line)
                continue
            key, val = m.group(1), m.group(2)
            canon = next((k for k in MANAGED_KEYS if k.lower() == key.lower()), None)
            if canon == 'HostName':
                h.hostname = val
            elif canon == 'User':
                h.user = val
            elif canon == 'Port':
                h.port = val
            elif canon == 'IdentityFile' and not h.identity:
                h.identity = val
            else:
                h.extra.append(line)
        m = meta.get(h.alias, {})
        h.group, h.note, h.last_used = m.get('group', ''), m.get('note', ''), m.get('last_used', 0)
        hosts.append(h)
    return hosts


def _render(h):
    out = [f'Host {h.alias}']
    if h.hostname:
        out.append(f'  HostName {h.hostname}')
    if h.user:
        out.append(f'  User {h.user}')
    if h.port and h.port != '22':
        out.append(f'  Port {h.port}')
    if h.identity:
        out.append(f'  IdentityFile {h.identity}')
    out += h.extra
    return out


def _write(lines):
    os.makedirs(os.path.dirname(SSH_CONFIG), mode=0o700, exist_ok=True)
    if os.path.exists(SSH_CONFIG):
        shutil.copy2(SSH_CONFIG, SSH_CONFIG + '.hop-bak')
    tmp = SSH_CONFIG + '.hop-tmp'
    with open(tmp, 'w') as fh:
        fh.write('\n'.join(lines).rstrip('\n') + '\n')
    os.chmod(tmp, 0o600)
    os.replace(tmp, SSH_CONFIG)


def validate_alias(alias):
    if not alias or not re.fullmatch(r'[A-Za-z0-9._-]+', alias):
        return 'ชื่อใช้ได้แค่ a-z 0-9 . _ - และห้ามมีช่องว่าง'
    return None


def save_host(h, old_alias=None):
    """Insert or replace a host block (matched by old_alias or h.alias)."""
    err = validate_alias(h.alias)
    if err:
        raise ValueError(err)
    key = old_alias or h.alias
    blocks = _blocks(_read_lines())
    if h.alias != key and any(_is_managed(b) and b.group(2).split()[0] == h.alias for b, _ in blocks):
        raise ValueError(f'มี host ชื่อ {h.alias} อยู่แล้ว')
    out, replaced = [], False
    for header, lines in blocks:
        if _is_managed(header) and header.group(2).split()[0] == key:
            trailing = []
            while lines and not lines[-1].strip():
                trailing.insert(0, lines.pop())
            out += _render(h) + (trailing or [''])
            replaced = True
        else:
            out += lines
    if not replaced:
        while out and not out[-1].strip():
            out.pop()
        out += ([''] if out else []) + _render(h)
    _write(out)

    meta = load_meta()
    prev = meta.pop(key, {})
    meta[h.alias] = {**prev, 'group': h.group, 'note': h.note}
    save_meta(meta)


def delete_host(alias):
    out = []
    for header, lines in _blocks(_read_lines()):
        if _is_managed(header) and header.group(2).split()[0] == alias:
            continue
        out += lines
    _write(out)
    meta = load_meta()
    meta.pop(alias, None)
    save_meta(meta)


def touch(alias):
    meta = load_meta()
    meta.setdefault(alias, {})['last_used'] = time.time()
    save_meta(meta)


# ── reachability ─────────────────────────────────────────────────────────────

def resolved(alias):
    """Effective hostname/port after ssh's own config resolution (ssh -G)."""
    try:
        out = subprocess.run(['ssh', '-G', alias], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return alias, 22
    cfg = dict(line.split(' ', 1) for line in out.splitlines() if ' ' in line)
    return cfg.get('hostname', alias), int(cfg.get('port', 22))


def ping(alias, timeout=1.5):
    """Latency in ms to the SSH port, or None when unreachable."""
    host, port = resolved(alias)
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return int((time.monotonic() - start) * 1000)
    except OSError:
        return None


def ping_all(aliases, timeout=1.5):
    if not aliases:
        return {}
    with ThreadPoolExecutor(max_workers=min(32, len(aliases))) as pool:
        return dict(zip(aliases, pool.map(lambda a: ping(a, timeout), aliases)))


def ssh_command(alias):
    return ['ssh', alias]


def open_in_terminal(alias, command=None):
    """Open a new Ptyxis window running ssh (or `command`)."""
    import shlex
    cmd = command or f'ssh {shlex.quote(alias)}'
    touch(alias)
    return subprocess.Popen(['ptyxis', '--new-window', '-T', f'⇢ {alias}', '-x', cmd],
                            start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ── shared connection (one login, reused by terminal, dashboard, tunnels) ──────

CONTROL_DIR = os.path.expanduser('~/.cache/hop')
SSH_OPTS = ['-o', 'ControlMaster=auto', '-o', f'ControlPath={CONTROL_DIR}/%C', '-o', 'ControlPersist=120',
            '-o', 'ServerAliveInterval=30']
os.makedirs(CONTROL_DIR, mode=0o700, exist_ok=True)


def ssh_argv(alias, *extra):
    return ['ssh', *SSH_OPTS, *extra, alias]


# ── per-host extras stored in meta.json ──────────────────────────────────────

def get_meta(alias, key, default=None):
    return load_meta().get(alias, {}).get(key, default)


def set_meta(alias, key, value):
    meta = load_meta()
    meta.setdefault(alias, {})[key] = value
    save_meta(meta)


def fetch_os(alias, timeout=6):
    """ID / PRETTY_NAME from /etc/os-release (key auth or an open shared connection only)."""
    try:
        out = subprocess.run(ssh_argv(alias, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=4') +
                             ['cat /etc/os-release 2>/dev/null; uname -s'],
                             capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    info = dict(l.split('=', 1) for l in out.splitlines() if '=' in l)
    os_id = info.get('ID', '').strip('"') or (out.strip().splitlines() or [''])[-1].lower()
    if not os_id:
        return None
    return {'id': os_id, 'name': info.get('PRETTY_NAME', os_id).strip('"')}


OS_GLYPHS = {
    'ubuntu': '', 'debian': '', 'arch': '', 'fedora': '', 'centos': '',
    'alpine': '', 'raspbian': '', 'linuxmint': '', 'opensuse': '',
    'opensuse-leap': '', 'opensuse-tumbleweed': '', 'rhel': '', 'manjaro': '',
    'nixos': '', 'darwin': '', 'freebsd': '', 'rocky': '', 'almalinux': '',
    'pop': '', 'kali': '', 'gentoo': '', 'void': '',
}


def os_glyph(os_id):
    return OS_GLYPHS.get((os_id or '').lower(), '' if os_id else '')   # tux / server


# ── snippets ─────────────────────────────────────────────────────────────────

SNIPPETS_FILE = os.path.expanduser('~/.config/hop/snippets.json')
DEFAULT_SNIPPETS = [
    {'name': 'พื้นที่ดิสก์', 'cmd': 'df -h'},
    {'name': 'RAM', 'cmd': 'free -h'},
    {'name': 'Docker containers', 'cmd': 'docker ps --format "table {{.Names}}\\t{{.Status}}\\t{{.Ports}}"'},
    {'name': 'Process ที่กิน CPU', 'cmd': 'ps -eo pid,comm,%cpu,%mem --sort=-%cpu | head -15'},
    {'name': 'Log ระบบล่าสุด', 'cmd': 'journalctl -n 50 --no-pager'},
    {'name': 'Port ที่เปิดอยู่', 'cmd': 'ss -tulpn'},
    {'name': 'อัปเดตระบบ', 'cmd': 'sudo apt update && sudo apt upgrade'},
]


def load_snippets():
    try:
        with open(SNIPPETS_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return [dict(s) for s in DEFAULT_SNIPPETS]


def save_snippets(snips):
    os.makedirs(os.path.dirname(SNIPPETS_FILE), exist_ok=True)
    with open(SNIPPETS_FILE, 'w') as fh:
        json.dump(snips, fh, indent=2, ensure_ascii=False)


# ── tunnels (port forwarding) ────────────────────────────────────────────────

def load_tunnels(alias):
    return get_meta(alias, 'tunnels', [])


def save_tunnels(alias, tunnels):
    set_meta(alias, 'tunnels', tunnels)
