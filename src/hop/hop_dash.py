"""Live server dashboard: one ssh process streams /proc stats every 2 s."""
import math
import time
from collections import deque

import gi
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango

import hopcore as core

INTERVAL = 2
HISTORY = 60

# POSIX sh, fed on stdin — no quoting games, works on any Linux box
SCRIPT = r'''
echo "INFO host $(hostname)"
. /etc/os-release 2>/dev/null; echo "INFO os ${PRETTY_NAME:-$(uname -s)}"; echo "INFO id ${ID:-linux}"
echo "INFO kernel $(uname -r)"; echo "INFO cpus $(nproc 2>/dev/null || echo 1)"
while :; do
  echo @@
  head -1 /proc/stat
  grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo
  echo "LOAD $(cut -d' ' -f1-3 /proc/loadavg)"
  echo "UP $(cut -d' ' -f1 /proc/uptime)"
  awk '$1 !~ /^(lo|Inter|face)/ && NF > 9 {rx += $2; tx += $10} END {print "NET", rx+0, tx+0}' /proc/net/dev
  df -P -x tmpfs -x devtmpfs -x squashfs -x overlay -x efivarfs 2>/dev/null | awk 'NR > 1 {print "DF", $2, $3, $6}'
  ps -eo pid=,pcpu=,pmem=,comm= --sort=-pcpu 2>/dev/null | head -6 | sed 's/^ */PS /'
  echo @@END
  sleep 2
done
'''


def human_bytes(n, rate=False):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(n) < 1024 or unit == 'TB':
            s = f'{n:.0f} {unit}' if unit in ('B', 'KB') else f'{n:.1f} {unit}'
            return s + ('/s' if rate else '')
        n /= 1024


def human_uptime(sec):
    d, rem = divmod(int(sec), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f'{d} วัน {h} ชม.' if d else (f'{h} ชม. {m} นาที' if h else f'{m} นาที')


class Spark(Gtk.Widget):
    """Smooth area chart for one or two series, drawn with GSK (no cairo needed)."""
    def __init__(self, series, colors, fixed_max=None, height=70):
        super().__init__(hexpand=True, height_request=height)
        self.series, self.colors, self.fixed_max = series, colors, fixed_max

    def do_snapshot(self, snap):
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0:
            return
        peak = self.fixed_max or max([1e-9] + [v for s in self.series for v in s])
        step = w / (HISTORY - 1)
        for series, (r, g, b) in zip(self.series, self.colors):
            pts = list(series)
            if len(pts) < 2:
                continue
            x0 = w - step * (len(pts) - 1)
            xy = [(x0 + i * step, h - 2 - (h - 6) * min(v / peak, 1)) for i, v in enumerate(pts)]

            def curve(pb):
                pb.move_to(*xy[0])
                for (xa, ya), (xb, yb) in zip(xy, xy[1:]):
                    mx = (xa + xb) / 2
                    pb.cubic_to(mx, ya, mx, yb, xb, yb)
            line = Gsk.PathBuilder.new()
            curve(line)
            area = Gsk.PathBuilder.new()
            curve(area)
            area.line_to(xy[-1][0], h)
            area.line_to(xy[0][0], h)
            area.close()

            snap.push_fill(area.to_path(), Gsk.FillRule.WINDING)
            top, bottom = Gsk.ColorStop(), Gsk.ColorStop()
            top.offset, top.color = 0, Gdk.RGBA(red=r, green=g, blue=b, alpha=0.35)
            bottom.offset, bottom.color = 1, Gdk.RGBA(red=r, green=g, blue=b, alpha=0)
            rect = Graphene.Rect().init(0, 0, w, h)
            snap.append_linear_gradient(rect, Graphene.Point().init(0, 0), Graphene.Point().init(0, h),
                                        [top, bottom])
            snap.pop()
            snap.append_stroke(line.to_path(), Gsk.Stroke.new(2), Gdk.RGBA(red=r, green=g, blue=b, alpha=1))


def accent_rgb():
    try:
        c = Adw.StyleManager.get_default().get_accent_color_rgba()
        return c.red, c.green, c.blue
    except AttributeError:
        return 0.48, 0.64, 0.97


class Card(Gtk.Box):
    def __init__(self, icon, title):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=['card', 'hop-dash-card'])
        head = Gtk.Box(spacing=8)
        head.append(Gtk.Image(icon_name=icon, css_classes=['dim-label']))
        head.append(Gtk.Label(label=title, xalign=0, hexpand=True, css_classes=['heading', 'dim-label']))
        self.side = Gtk.Label(xalign=1, css_classes=['caption', 'dim-label'], width_chars=18, max_width_chars=18,
                              ellipsize=Pango.EllipsizeMode.START)
        head.append(self.side)
        self.append(head)
        self.big = Gtk.Label(xalign=0, css_classes=['hop-big'], width_chars=1, max_width_chars=1, hexpand=True,
                             ellipsize=Pango.EllipsizeMode.END)
        self.append(self.big)


class Dashboard(Gtk.ScrolledWindow):
    def __init__(self, session):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.session, self.proc, self.cancel = session, None, None
        self.prev_cpu = self.prev_net = None
        self.cpu_hist = deque(maxlen=HISTORY)
        self.mem_hist = deque(maxlen=HISTORY)
        self.rx_hist = deque(maxlen=HISTORY)
        self.tx_hist = deque(maxlen=HISTORY)
        self.frame = []

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                       margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        clamp = Adw.Clamp(maximum_size=1100, child=root)
        self.set_child(clamp)

        # header
        top = Gtk.Box(spacing=14)
        self.glyph = Gtk.Label(label=core.os_glyph(None), css_classes=['hop-os-glyph', 'big'])
        top.append(self.glyph)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        self.host_lbl = Gtk.Label(label=session.alias, xalign=0, css_classes=['title-2'])
        self.os_lbl = Gtk.Label(label='กำลังเชื่อมต่อ…', xalign=0, css_classes=['dim-label'],
                                ellipsize=Pango.EllipsizeMode.END)
        col.append(self.host_lbl)
        col.append(self.os_lbl)
        top.append(col)
        self.up_lbl = Gtk.Label(xalign=0.5, css_classes=['hop-chip'], width_chars=14)
        self.load_lbl = Gtk.Label(xalign=0.5, css_classes=['hop-chip'], width_chars=18)
        top.append(self.load_lbl)
        top.append(self.up_lbl)
        root.append(top)

        grid = Gtk.Grid(column_spacing=14, row_spacing=14, column_homogeneous=True)
        acc = accent_rgb()
        self.cpu = Card('computer-symbolic', 'CPU')
        self.cpu_spark = Spark([self.cpu_hist], [acc], fixed_max=100)
        self.cpu.append(self.cpu_spark)
        self.mem = Card('drive-harddisk-solidstate-symbolic', 'RAM')
        self.mem_spark = Spark([self.mem_hist], [(0.62, 0.81, 0.42)], fixed_max=100)
        self.mem.append(self.mem_spark)
        self.mem_bar = Gtk.LevelBar(min_value=0, max_value=1)
        self.swap_lbl = Gtk.Label(xalign=0, css_classes=['caption', 'dim-label'], width_chars=1, max_width_chars=1,
                                  hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.mem.append(self.swap_lbl)
        self.net = Card('network-transmit-receive-symbolic', 'Network')
        self.net_spark = Spark([self.rx_hist, self.tx_hist], [(0.49, 0.81, 1.0), (0.97, 0.46, 0.56)])
        self.net.append(self.net_spark)
        legend = Gtk.Label(use_markup=True, xalign=0, css_classes=['caption'],
                           label='<span foreground="#7dcfff">●</span> รับ (↓)   <span foreground="#f7768e">●</span> ส่ง (↑)')
        self.net.append(legend)
        grid.attach(self.cpu, 0, 0, 1, 1)
        grid.attach(self.mem, 1, 0, 1, 1)
        grid.attach(self.net, 2, 0, 1, 1)
        root.append(grid)

        bottom = Gtk.Grid(column_spacing=14, row_spacing=14, column_homogeneous=True)
        self.disks = Card('drive-harddisk-symbolic', 'ดิสก์')
        self.disk_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.disks.big.set_visible(False)
        self.disks.append(self.disk_box)
        self.procs = Card('utilities-system-monitor-symbolic', 'Process ที่ใช้ CPU มากสุด')
        self.procs.big.set_visible(False)
        self.proc_grid = Gtk.Grid(column_spacing=16, row_spacing=6)
        self.procs.append(self.proc_grid)
        bottom.attach(self.disks, 0, 0, 1, 1)
        bottom.attach(self.procs, 1, 0, 1, 1)
        root.append(bottom)

        self.error = Adw.Banner(button_label='ลองใหม่')
        self.error.connect('button-clicked', lambda *_: self.start())
        root.prepend(self.error)

    # ── process ─────────────────────────────────────────────────────────────
    def start(self):
        if self.proc:
            return
        self.error.set_revealed(False)
        self.cancel = Gio.Cancellable()
        flags = Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        try:
            self.proc = Gio.Subprocess.new(core.ssh_argv(self.session.alias, '-T', '-o', 'BatchMode=yes') + ['sh', '-s'], flags)
        except GLib.Error as e:
            self.fail(e.message)
            return
        self.proc.get_stdin_pipe().write_all(SCRIPT.encode(), None)
        self.proc.get_stdin_pipe().close(None)
        self.stream = Gio.DataInputStream.new(self.proc.get_stdout_pipe())
        self.proc.wait_async(None, self.on_exit)
        self.read_next()

    def stop(self):
        if self.proc:
            if self.cancel:
                self.cancel.cancel()
            self.proc.force_exit()
            self.proc = None

    def on_exit(self, proc, res):
        if self.proc is not proc:
            return
        try:
            proc.wait_finish(res)
        except GLib.Error:
            pass
        err = proc.get_stderr_pipe().read_bytes(4096, None).get_data().decode(errors='replace').strip()
        self.proc = None
        if proc.get_if_exited() and proc.get_exit_status() != 0:
            self.fail(err.splitlines()[-1] if err else 'การเชื่อมต่อหลุด')

    def fail(self, msg):
        if 'Permission denied' in msg or 'BatchMode' in msg:
            msg = 'ต้องล็อกอินก่อน — เปิดแท็บ Terminal ใส่รหัสผ่านครั้งเดียว แล้วกดลองใหม่'
        self.error.set_title(f'Dashboard: {msg}')
        self.error.set_revealed(True)

    def read_next(self):
        if self.proc and self.cancel:
            self.stream.read_line_async(GLib.PRIORITY_DEFAULT, self.cancel, self.on_line)

    def on_line(self, stream, res):
        try:
            line, _ = stream.read_line_finish_utf8(res)
        except GLib.Error:
            return
        if line is None:
            return
        self.handle(line)
        self.read_next()

    # ── parsing ─────────────────────────────────────────────────────────────
    def handle(self, line):
        if line.startswith('INFO '):
            _, key, val = (line.split(' ', 2) + [''])[:3]
            if key == 'host':
                self.host_lbl.set_label(val)
            elif key == 'os':
                self.os_name = val
                self.os_lbl.set_label(val)
            elif key == 'id':
                self.glyph.set_label(core.os_glyph(val))
                core.set_meta(self.session.alias, 'os', {'id': val, 'name': getattr(self, 'os_name', val)})
            elif key == 'kernel':
                self.os_lbl.set_label(f'{self.os_lbl.get_label()}  ·  {val}')
            elif key == 'cpus':
                self.cpu.side.set_label(f'{val} cores')
        elif line == '@@':
            self.frame = []
        elif line == '@@END':
            self.apply(self.frame)
        else:
            self.frame.append(line)

    def apply(self, lines):
        mem, disks, procs = {}, [], []
        for l in lines:
            p = l.split()
            if not p:
                continue
            if p[0] == 'cpu':
                vals = list(map(int, p[1:]))
                idle, total = vals[3] + (vals[4] if len(vals) > 4 else 0), sum(vals)
                if self.prev_cpu:
                    dt, di = total - self.prev_cpu[0], idle - self.prev_cpu[1]
                    pct = 100 * (dt - di) / dt if dt else 0
                    self.cpu_hist.append(pct)
                    self.cpu.big.set_label(f'{pct:.0f}%')
                self.prev_cpu = (total, idle)
            elif p[0].endswith(':') and len(p) >= 2:
                mem[p[0][:-1]] = int(p[1]) * 1024
            elif p[0] == 'LOAD':
                self.load_lbl.set_label(f'load {" ".join(p[1:4])}')
            elif p[0] == 'UP':
                self.up_lbl.set_label(f'⏱ {human_uptime(float(p[1]))}')
            elif p[0] == 'NET':
                rx, tx, now = int(p[1]), int(p[2]), time.monotonic()
                if self.prev_net:
                    dt = max(now - self.prev_net[2], 0.1)
                    r, t = (rx - self.prev_net[0]) / dt, (tx - self.prev_net[1]) / dt
                    self.rx_hist.append(max(r, 0))
                    self.tx_hist.append(max(t, 0))
                    self.net.big.set_label(f'↓ {human_bytes(r, True)}   ↑ {human_bytes(t, True)}')
                self.prev_net = (rx, tx, now)
            elif p[0] == 'DF' and len(p) >= 4:
                disks.append((p[3], int(p[1]) * 1024, int(p[2]) * 1024))
            elif p[0] == 'PS' and len(p) >= 5:
                procs.append(p[1:5])

        if 'MemTotal' in mem:
            total, avail = mem['MemTotal'], mem.get('MemAvailable', 0)
            used = total - avail
            self.mem_hist.append(100 * used / total)
            self.mem.big.set_label(f'{100 * used / total:.0f}%')
            self.mem.side.set_label(f'{human_bytes(used)} / {human_bytes(total)}')
            st, sf = mem.get('SwapTotal', 0), mem.get('SwapFree', 0)
            self.swap_lbl.set_label(f'Swap {human_bytes(st - sf)} / {human_bytes(st)}' if st else 'ไม่มี swap')

        self.show_disks(disks)
        self.show_procs(procs)
        for s in (self.cpu_spark, self.mem_spark, self.net_spark):
            s.queue_draw()

    def show_disks(self, disks):
        child = self.disk_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.disk_box.remove(child)
            child = nxt
        for mount, size, used in disks[:6]:
            if size <= 0:
                continue
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            row = Gtk.Box()
            row.append(Gtk.Label(label=mount, xalign=0, hexpand=True, css_classes=['monospace'], width_chars=1,
                                 ellipsize=Pango.EllipsizeMode.MIDDLE))
            row.append(Gtk.Label(label=f'{human_bytes(used)} / {human_bytes(size)}  ({100 * used / size:.0f}%)',
                                 css_classes=['caption', 'dim-label', 'numeric'], width_chars=26, xalign=1))
            bar = Gtk.LevelBar(min_value=0, max_value=1, value=used / size)
            bar.add_offset_value('low', 0.7)
            bar.add_offset_value('high', 0.9)
            box.append(row)
            box.append(bar)
            self.disk_box.append(box)

    def show_procs(self, procs):
        child = self.proc_grid.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.proc_grid.remove(child)
            child = nxt
        for c, head in enumerate(('PID', 'ชื่อ', 'CPU', 'RAM')):
            self.proc_grid.attach(Gtk.Label(label=head, xalign=0 if c < 2 else 1,
                                            css_classes=['caption-heading', 'dim-label']), c, 0, 1, 1)
        rows = (procs + [['', '', '', '']] * 6)[:6]
        widths = (7, 1, 7, 7)
        for r, (pid, cpu, mem, name) in enumerate(rows, start=1):
            vals = (pid, name, f'{float(cpu):.1f}%' if cpu else '', f'{float(mem):.1f}%' if mem else '')
            for c, (val, extra) in enumerate(zip(vals, (['dim-label', 'monospace'], ['monospace'],
                                                        ['hop-accent-text', 'numeric'], ['numeric']))):
                lbl = Gtk.Label(label=val or ' ', xalign=0 if c < 2 else 1, css_classes=extra, hexpand=c == 1,
                                width_chars=widths[c], max_width_chars=widths[c] if c == 1 else -1,
                                ellipsize=Pango.EllipsizeMode.END)
                self.proc_grid.attach(lbl, c, r, 1, 1)
