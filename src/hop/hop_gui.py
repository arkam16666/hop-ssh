#!/usr/bin/env python3
"""Hop — SSH host manager with built-in terminal and file transfer.
Hosts live in ~/.ssh/config (shared with the `hop` command)."""
import os
import shlex
import sys
import threading

# The GTK4 VTE typelib is installed per-user (~/.local/lib/girepository-1.0)
_TYPELIBS = os.path.expanduser('~/.local/lib/girepository-1.0')
os.environ['GI_TYPELIB_PATH'] = _TYPELIBS + (':' + os.environ['GI_TYPELIB_PATH'] if os.environ.get('GI_TYPELIB_PATH') else '')

import gi  # noqa: E402
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Vte', '3.91')
gi.require_version('GtkSource', '5')
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hopcore as core  # noqa: E402
from hop_session import Session  # noqa: E402
from hop_editor import EditorPage  # noqa: E402

APP_ID = 'io.github.hopssh.Hop'

CSS = b"""
/* cute rounded fonts: Nunito for Latin, Mali for Thai (Hop only) */
window, popover, dialog, toast, .dialog-sheet { font-family: "Nunito", "Mali", sans-serif; font-size: 11pt; }
.monospace, .hop-path, textview, .hop-broadcast entry { font-family: "JetBrainsMono Nerd Font", monospace; }
.title-1, .title-2, .title-3, .heading, .hop-hero, .hop-stat, .hop-big { font-family: "Nunito", "Mali", sans-serif; }
.hop-dot { font-size: 12px; }
.hop-dot.online  { color: #9ece6a; }
.hop-dot.offline { color: #f7768e; }
.hop-dot.checking { opacity: 0.4; }
.hop-latency { font-size: 0.85em; font-feature-settings: "tnum"; }
.hop-pill { background: alpha(currentColor, 0.07); border-radius: 99px; padding: 3px 10px; }
.hop-chip { background: alpha(currentColor, 0.08); border-radius: 99px; padding: 3px 10px; font-size: 0.85em; }
.hop-os-glyph {
  font-family: "JetBrainsMono Nerd Font Mono"; font-size: 36px; line-height: 1;
  background: linear-gradient(135deg, alpha(@accent_bg_color, 0.95), alpha(@accent_bg_color, 0.45));
  color: @accent_fg_color; border-radius: 16px; min-width: 52px; min-height: 52px;
}
.hop-os-glyph.big { font-size: 44px; min-width: 64px; min-height: 64px; border-radius: 20px; }
.hop-card {
  background: alpha(currentColor, 0.045); border-radius: 20px; padding: 16px;
  border: 1px solid alpha(currentColor, 0.07);
  transition: all 200ms ease;
}
.hop-card:hover { border-color: alpha(@accent_color, 0.7); background: alpha(@accent_color, 0.07);
  box-shadow: 0 6px 24px alpha(@accent_color, 0.18); }
.hop-card .title-3 { font-weight: 800; }
.hop-connect { border-radius: 99px; padding: 4px 18px; font-weight: 700; }
.hop-hero { font-size: 2.2em; font-weight: 900; letter-spacing: -1px; }
.hop-hero-box {
  border-radius: 22px; padding: 22px 26px;
  background: linear-gradient(120deg, alpha(@accent_bg_color, 0.35), alpha(@accent_bg_color, 0.05) 70%);
  border: 1px solid alpha(@accent_color, 0.25);
}
.hop-stat { font-size: 1.6em; font-weight: 900; }
.hop-group-title { font-weight: 800; opacity: 0.7; margin: 10px 4px 0; }
flowboxchild { padding: 0; border-radius: 20px; }
.hop-session-bar { padding: 4px; border-bottom: 1px solid alpha(currentColor, 0.08); }
.hop-term-frame { padding: 6px 4px 0 10px; }
.hop-files row { padding: 2px 6px; }
.hop-file-icon.dir { color: @accent_color; }
.hop-path { font-family: monospace; }
.hop-drop {
  background-color: alpha(@accent_bg_color, 0.18);
  border: 2px dashed @accent_color; border-radius: 16px; margin: 12px;
  font-size: 1.3em; font-weight: 800; color: @accent_color;
}
.hop-transfers { border-top: 1px solid alpha(currentColor, 0.1); background: alpha(currentColor, 0.03); padding: 4px 0; }
.hop-dash-card { padding: 16px; border-radius: 18px; }
.hop-big { font-size: 1.9em; font-weight: 900; font-feature-settings: "tnum"; }
.hop-accent-text { color: @accent_color; font-weight: 700; }
.hop-broadcast { padding: 10px 14px; border-top: 1px solid alpha(@accent_color, 0.4);
  background: alpha(@accent_bg_color, 0.10); }
.hop-broadcast entry { font-family: monospace; }
"""


def initials(alias):
    parts = [p for p in alias.replace('.', '-').replace('_', '-').split('-') if p]
    if parts and parts[0].isdigit():
        return parts[-1][:2]
    return ''.join(p[0] for p in parts[:2]).upper() or '?'


class HostDialog(Adw.Dialog):
    def __init__(self, win, host=None):
        super().__init__(title='แก้ไขเซิร์ฟเวอร์' if host else 'เพิ่มเซิร์ฟเวอร์', content_width=460)
        self.win, self.old = win, host.alias if host else None
        h = host or core.Host(alias='', user=os.getenv('USER', ''))

        tb = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label='ยกเลิก')
        cancel.connect('clicked', lambda *_: self.close())
        self.save_btn = Gtk.Button(label='บันทึก', css_classes=['suggested-action'])
        self.save_btn.connect('clicked', self.on_save)
        header.pack_start(cancel)
        header.pack_end(self.save_btn)
        tb.add_top_bar(header)

        page = Adw.PreferencesPage()
        conn = Adw.PreferencesGroup(title='การเชื่อมต่อ')
        self.alias = Adw.EntryRow(title='ชื่อเรียก (alias)', text=h.alias)
        self.hostname = Adw.EntryRow(title='IP / hostname', text=h.hostname)
        self.user = Adw.EntryRow(title='User', text=h.user)
        self.port = Adw.SpinRow.new_with_range(1, 65535, 1)
        self.port.set_title('Port')
        self.port.set_value(int(h.port or 22))
        for r in (self.alias, self.hostname, self.user, self.port):
            conn.add(r)
        self.alias.connect('changed', self.validate)

        auth = Adw.PreferencesGroup(title='SSH key', description='เว้นว่างไว้ = ใช้ key ปกติของเครื่อง')
        self.identity = Adw.EntryRow(title='ไฟล์ key', text=h.identity)
        pick = Gtk.Button(icon_name='document-open-symbolic', valign=Gtk.Align.CENTER, css_classes=['flat'])
        pick.connect('clicked', self.pick_key)
        self.identity.add_suffix(pick)
        auth.add(self.identity)

        org = Adw.PreferencesGroup(title='จัดกลุ่ม')
        self.group = Adw.EntryRow(title='กลุ่ม (เช่น Home, Work)', text=h.group)
        self.note = Adw.EntryRow(title='โน้ต', text=h.note)
        org.add(self.group)
        org.add(self.note)

        for g in (conn, auth, org):
            page.add(g)
        tb.set_content(page)
        self.set_child(tb)
        self.validate()

    def validate(self, *_):
        err = core.validate_alias(self.alias.get_text().strip())
        self.save_btn.set_sensitive(err is None)
        if err and self.alias.get_text():
            self.alias.add_css_class('error')
        else:
            self.alias.remove_css_class('error')

    def pick_key(self, *_):
        dlg = Gtk.FileDialog(title='เลือกไฟล์ SSH key',
                             initial_folder=Gio.File.new_for_path(os.path.expanduser('~/.ssh')))

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            path = f.get_path()
            home = os.path.expanduser('~')
            self.identity.set_text('~' + path[len(home):] if path.startswith(home) else path)
        dlg.open(self.win, None, done)

    def on_save(self, *_):
        alias = self.alias.get_text().strip()
        h = next((x for x in core.load_hosts() if x.alias == self.old), None) or core.Host(alias=alias)
        h.alias = alias
        h.hostname = self.hostname.get_text().strip() or alias
        h.user = self.user.get_text().strip()
        h.port = str(int(self.port.get_value()))
        h.identity = self.identity.get_text().strip()
        h.group = self.group.get_text().strip()
        h.note = self.note.get_text().strip()
        try:
            core.save_host(h, old_alias=self.old)
        except (ValueError, OSError) as e:
            self.win.toast(str(e))
            return
        self.close()
        self.win.toast(f'บันทึก {alias} แล้ว')
        self.win.reload()


class ServerCard(Gtk.FlowBoxChild):
    def __init__(self, win, h):
        super().__init__()
        self.win, self.h = win, h
        os_info = core.get_meta(h.alias, 'os') or {}

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=['hop-card'])
        top = Gtk.Box(spacing=12)
        self.glyph = Gtk.Label(label=core.os_glyph(os_info.get('id')), css_classes=['hop-os-glyph'],
                               valign=Gtk.Align.CENTER)
        top.append(self.glyph)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True, spacing=2)
        col.append(Gtk.Label(label=h.alias, xalign=0, css_classes=['title-3'], ellipsize=Pango.EllipsizeMode.END))
        col.append(Gtk.Label(label=h.target, xalign=0, css_classes=['caption', 'dim-label', 'monospace'],
                             ellipsize=Pango.EllipsizeMode.END))
        top.append(col)
        pill = Gtk.Box(spacing=6, css_classes=['hop-pill'], valign=Gtk.Align.START)
        self.dot = Gtk.Label(label='●', css_classes=['hop-dot', 'checking'])
        self.latency = Gtk.Label(label='…', css_classes=['hop-latency'])
        pill.append(self.dot)
        pill.append(self.latency)
        top.append(pill)
        card.append(top)

        info = os_info.get('name') or ''
        if h.note:
            info = f'{info}  ·  {h.note}' if info else h.note
        self.info = Gtk.Label(label=info or ' ', xalign=0, css_classes=['caption', 'dim-label'],
                              ellipsize=Pango.EllipsizeMode.END)
        card.append(self.info)

        btns = Gtk.Box(spacing=6)
        go = Gtk.Button(label='Connect', css_classes=['suggested-action', 'hop-connect'], hexpand=True)
        go.connect('clicked', lambda *_: win.open_session(h.alias))
        btns.append(go)
        for icon, tip, page in (('folder-remote-symbolic', 'ไฟล์', 'files'),
                                ('utilities-system-monitor-symbolic', 'Dashboard', 'dash')):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, css_classes=['circular'])
            b.connect('clicked', lambda *_, p=page: win.open_session(h.alias, p))
            btns.append(b)
        tun = Gtk.Button(icon_name='network-vpn-symbolic', tooltip_text='Port forwarding', css_classes=['circular'])
        tun.connect('clicked', lambda *_: win.open_tunnels(h.alias))
        btns.append(tun)

        menu = Gio.Menu()
        for label, action in (('แก้ไข', 'edit'), ('คัดลอกคำสั่ง ssh', 'copy'), ('เปิดใน terminal แยก', 'external'),
                              ('ส่ง key ไปเซิร์ฟเวอร์ (ไม่ต้องใส่รหัสอีก)', 'key'), ('ลบ', 'delete')):
            menu.append(label, f'host.{action}')
        btns.append(Gtk.MenuButton(icon_name='view-more-symbolic', menu_model=menu, css_classes=['circular', 'flat']))
        card.append(btns)
        self.set_child(card)

        ag = Gio.SimpleActionGroup()
        for name, fn in (('edit', lambda *_: HostDialog(win, h).present(win)),
                         ('copy', lambda *_: win.copy(f'ssh {shlex.quote(h.alias)}')),
                         ('external', lambda *_: core.open_in_terminal(h.alias)),
                         ('key', lambda *_: core.open_in_terminal(
                             h.alias, f'ssh-copy-id {shlex.quote(h.alias)}; echo; read -p "กด Enter เพื่อปิด"')),
                         ('delete', lambda *_: win.confirm_delete(h.alias))):
            a = Gio.SimpleAction.new(name, None)
            a.connect('activate', fn)
            ag.add_action(a)
        self.insert_action_group('host', ag)

        dbl = Gtk.GestureClick(button=1)
        dbl.connect('pressed', lambda g, n, *_: n == 2 and win.open_session(h.alias))
        card.add_controller(dbl)

    def set_status(self, ms):
        for c in ('checking', 'online', 'offline'):
            self.dot.remove_css_class(c)
        if ms is None:
            self.dot.add_css_class('offline')
            self.latency.set_label('offline')
        else:
            self.dot.add_css_class('online')
            self.latency.set_label(f'{ms} ms')

    def set_os(self, info):
        self.glyph.set_label(core.os_glyph(info.get('id')))
        text = info.get('name', '')
        if self.h.note:
            text = f'{text}  ·  {self.h.note}'
        self.info.set_label(text)


# ── snippets manager ─────────────────────────────────────────────────────────

class SnippetsDialog(Adw.Dialog):
    def __init__(self, win):
        super().__init__(title='⚡ Snippets', content_width=560, content_height=560)
        self.win = win
        tb = Adw.ToolbarView()
        tb.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        add = Adw.PreferencesGroup(title='เพิ่มคำสั่งใหม่')
        self.name = Adw.EntryRow(title='ชื่อ (เช่น Restart nginx)')
        self.cmd = Adw.EntryRow(title='คำสั่ง (เช่น sudo systemctl restart nginx)')
        btn = Gtk.Button(label='เพิ่ม', css_classes=['suggested-action', 'pill'], halign=Gtk.Align.END, margin_top=8)
        btn.connect('clicked', self.on_add)
        self.cmd.connect('entry-activated', self.on_add)
        add.add(self.name)
        add.add(self.cmd)
        add.add(btn)
        self.list = Adw.PreferencesGroup(title='คำสั่งที่บันทึกไว้',
                                         description='กดปุ่ม ⭐ ในแท็บเซิร์ฟเวอร์เพื่อรันได้เลย')
        page.add(add)
        page.add(self.list)
        tb.set_content(page)
        self.set_child(tb)
        self.rows = []
        self.fill()

    def fill(self):
        for r in self.rows:
            self.list.remove(r)
        self.rows = []
        for i, sn in enumerate(core.load_snippets()):
            row = Adw.ActionRow(title=GLib.markup_escape_text(sn['name']),
                                subtitle=GLib.markup_escape_text(sn['cmd']), subtitle_selectable=True)
            for icon, tip, fn in (('go-up-symbolic', 'เลื่อนขึ้น', lambda *_, i=i: self.move(i, -1)),
                                  ('user-trash-symbolic', 'ลบ', lambda *_, i=i: self.remove(i))):
                b = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER, css_classes=['flat'])
                b.connect('clicked', fn)
                row.add_suffix(b)
            self.list.add(row)
            self.rows.append(row)

    def on_add(self, *_):
        name, cmd = self.name.get_text().strip(), self.cmd.get_text().strip()
        if not cmd:
            return
        snips = core.load_snippets()
        snips.append({'name': name or cmd, 'cmd': cmd})
        core.save_snippets(snips)
        self.name.set_text('')
        self.cmd.set_text('')
        self.fill()

    def remove(self, i):
        snips = core.load_snippets()
        snips.pop(i)
        core.save_snippets(snips)
        self.fill()

    def move(self, i, d):
        snips = core.load_snippets()
        j = i + d
        if 0 <= j < len(snips):
            snips[i], snips[j] = snips[j], snips[i]
            core.save_snippets(snips)
            self.fill()


# ── port forwarding ──────────────────────────────────────────────────────────

class Tunnels:
    """Running `ssh -N -L` processes, keyed by (alias, local port)."""
    def __init__(self, win):
        self.win, self.procs = win, {}

    def running(self, alias, local):
        return (alias, int(local)) in self.procs

    def start(self, alias, t, on_change):
        key = (alias, int(t['local']))
        if key in self.procs:
            return
        # own connection (not the shared master), so switching it off really closes the port
        argv = ['ssh', '-N', '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ExitOnForwardFailure=yes',
                '-o', 'BatchMode=yes', '-o', 'ServerAliveInterval=30',
                '-L', f"127.0.0.1:{t['local']}:{t.get('rhost') or 'localhost'}:{t['rport']}", alias]
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as e:
            self.win.toast(e.message)
            on_change()
            return
        self.procs[key] = proc

        def exited(p, res):
            try:
                p.wait_finish(res)
            except GLib.Error:
                pass
            if self.procs.get(key) is p:
                del self.procs[key]
                err = p.get_stderr_pipe().read_bytes(2048, None).get_data().decode(errors='replace').strip()
                msg = err.splitlines()[-1] if err else 'tunnel ปิดไปแล้ว'
                if 'Permission denied' in msg:
                    msg = 'ต้องใช้ SSH key หรือเปิด Terminal ของเครื่องนี้ค้างไว้ก่อน'
                self.win.toast(f"Tunnel :{t['local']} — {msg}")
            on_change()
        proc.wait_async(None, exited)
        self.win.toast(f"เปิด tunnel localhost:{t['local']} → {alias}:{t['rport']}")
        on_change()

    def stop(self, alias, local):
        p = self.procs.pop((alias, int(local)), None)
        if p:
            p.force_exit()

    def stop_all(self):
        for p in self.procs.values():
            p.force_exit()
        self.procs.clear()


class TunnelsDialog(Adw.Dialog):
    def __init__(self, win, alias):
        super().__init__(title=f'🚇 Port forwarding — {alias}', content_width=560, content_height=600)
        self.win, self.alias = win, alias
        tb = Adw.ToolbarView()
        tb.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()

        add = Adw.PreferencesGroup(title='เพิ่ม tunnel',
                                   description='เปิดพอร์ตบนเซิร์ฟเวอร์ให้เข้าได้จากเครื่องนี้ผ่าน localhost\n'
                                               'เช่น เว็บที่รันบนเซิร์ฟเวอร์พอร์ต 3000 → เปิด http://localhost:3000')
        self.name = Adw.EntryRow(title='ชื่อ (เช่น Web app, Postgres)')
        self.local = Adw.SpinRow.new_with_range(1, 65535, 1)
        self.local.set_title('พอร์ตบนเครื่องนี้ (localhost)')
        self.local.set_value(8080)
        self.rhost = Adw.EntryRow(title='Host ปลายทาง (มองจากเซิร์ฟเวอร์)', text='localhost')
        self.rport = Adw.SpinRow.new_with_range(1, 65535, 1)
        self.rport.set_title('พอร์ตปลายทาง')
        self.rport.set_value(80)
        self.rport.connect('notify::value', lambda r, _: self.local.set_value(r.get_value()) if self.local.get_value() == 8080 else None)
        btn = Gtk.Button(label='เพิ่ม', css_classes=['suggested-action', 'pill'], halign=Gtk.Align.END, margin_top=8)
        btn.connect('clicked', self.on_add)
        for w in (self.name, self.rport, self.local, self.rhost):
            add.add(w)
        add.add(btn)

        self.list = Adw.PreferencesGroup(title='Tunnels')
        page.add(self.list)
        page.add(add)
        tb.set_content(page)
        self.set_child(tb)
        self.rows = []
        self.fill()

    def fill(self):
        for r in self.rows:
            self.list.remove(r)
        self.rows = []
        tunnels = core.load_tunnels(self.alias)
        if not tunnels:
            empty = Adw.ActionRow(title='ยังไม่มี tunnel', subtitle='เพิ่มด้านล่าง')
            self.list.add(empty)
            self.rows.append(empty)
        for i, t in enumerate(tunnels):
            on = self.win.tunnels.running(self.alias, t['local'])
            row = Adw.ActionRow(title=GLib.markup_escape_text(t.get('name') or f"พอร์ต {t['rport']}"),
                                subtitle=f"localhost:{t['local']}  →  {t.get('rhost') or 'localhost'}:{t['rport']}")
            row.add_prefix(Gtk.Label(label='●', css_classes=['hop-dot', 'online' if on else 'checking']))
            open_btn = Gtk.Button(icon_name='web-browser-symbolic', tooltip_text='เปิดในเบราว์เซอร์',
                                  valign=Gtk.Align.CENTER, css_classes=['flat'], sensitive=on)
            open_btn.connect('clicked', lambda *_, t=t: Gtk.UriLauncher(uri=f"http://localhost:{t['local']}").launch(self.win, None, None))
            row.add_suffix(open_btn)
            sw = Gtk.Switch(active=on, valign=Gtk.Align.CENTER)
            sw.connect('notify::active', lambda s, _, t=t: self.toggle(t, s.get_active()))
            row.add_suffix(sw)
            rm = Gtk.Button(icon_name='user-trash-symbolic', valign=Gtk.Align.CENTER, css_classes=['flat'])
            rm.connect('clicked', lambda *_, i=i: self.remove(i))
            row.add_suffix(rm)
            self.list.add(row)
            self.rows.append(row)

    def toggle(self, t, on):
        if on == self.win.tunnels.running(self.alias, t['local']):
            return
        if on:
            self.win.tunnels.start(self.alias, t, lambda: GLib.idle_add(self.fill))
        else:
            self.win.tunnels.stop(self.alias, t['local'])
            self.fill()

    def on_add(self, *_):
        t = {'name': self.name.get_text().strip(), 'local': int(self.local.get_value()),
             'rhost': self.rhost.get_text().strip() or 'localhost', 'rport': int(self.rport.get_value())}
        tunnels = [x for x in core.load_tunnels(self.alias) if int(x['local']) != t['local']]
        tunnels.append(t)
        core.save_tunnels(self.alias, tunnels)
        self.name.set_text('')
        self.fill()

    def remove(self, i):
        tunnels = core.load_tunnels(self.alias)
        t = tunnels.pop(i)
        self.win.tunnels.stop(self.alias, t['local'])
        core.save_tunnels(self.alias, tunnels)
        self.fill()


# ── main window ──────────────────────────────────────────────────────────────

class HopWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Hop', default_width=1180, default_height=760)
        self.cards, self.servers_page = {}, None
        self.tunnels = Tunnels(self)
        self.status = {}

        self.toasts = Adw.ToastOverlay()
        tb = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.RAISED)
        header = Adw.HeaderBar()
        add = Gtk.Button(icon_name='list-add-symbolic', tooltip_text='เพิ่มเซิร์ฟเวอร์ (Ctrl+N)')
        add.connect('clicked', lambda *_: HostDialog(self).present(self))
        header.pack_start(add)
        snips = Gtk.Button(icon_name='starred-symbolic', tooltip_text='Snippets')
        snips.connect('clicked', lambda *_: self.open_snippets())
        header.pack_start(snips)
        self.bc_btn = Gtk.ToggleButton(icon_name='network-transmit-symbolic',
                                       tooltip_text='สั่งหลายเครื่องพร้อมกัน (Broadcast)')
        header.pack_end(self.bc_btn)
        self.search_btn = Gtk.ToggleButton(icon_name='system-search-symbolic', tooltip_text='ค้นหา (Ctrl+F)')
        header.pack_end(self.search_btn)
        self.refresh_btn = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='เช็กสถานะ (Ctrl+R)')
        self.refresh_btn.connect('clicked', lambda *_: self.check_all())
        header.pack_end(self.refresh_btn)
        tb.add_top_bar(header)

        self.tabview = Adw.TabView()
        self.tabview.connect('close-page', self.on_close_page)
        self.tabview.connect('notify::selected-page', self.on_tab_changed)
        self.tabview.connect('page-attached', lambda *_: self.refresh_broadcast_targets())
        self.tabview.connect('page-detached', lambda *_: self.refresh_broadcast_targets())
        tb.add_top_bar(Adw.TabBar(view=self.tabview, autohide=True))

        # servers page
        servers = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.search = Gtk.SearchEntry(placeholder_text='ค้นหาชื่อ, IP, กลุ่ม…', hexpand=True)
        self.search.connect('search-changed', lambda *_: self.reload(check=False))
        self.search_bar = Gtk.SearchBar(child=Adw.Clamp(child=self.search, maximum_size=520))
        self.search_bar.connect_entry(self.search)
        self.search_btn.bind_property('active', self.search_bar, 'search-mode-enabled',
                                      GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        servers.append(self.search_bar)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                                margin_top=20, margin_bottom=24, margin_start=24, margin_end=24)
        servers.append(Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                          child=Adw.Clamp(maximum_size=1240, child=self.list_box)))
        self.servers_page = self.tabview.append_pinned(servers)
        self.servers_page.set_title('เซิร์ฟเวอร์')
        self.servers_page.set_icon(Gio.ThemedIcon.new('network-server-symbolic'))

        # broadcast bar
        bc = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=['hop-broadcast'])
        row = Gtk.Box(spacing=8)
        row.append(Gtk.Image(icon_name='network-transmit-symbolic'))
        self.bc_entry = Gtk.Entry(hexpand=True, placeholder_text='พิมพ์คำสั่ง แล้วกด Enter เพื่อส่งไปทุกเครื่องที่เลือก')
        self.bc_entry.connect('activate', self.send_broadcast)
        row.append(self.bc_entry)
        send = Gtk.Button(label='ส่ง', css_classes=['suggested-action'])
        send.connect('clicked', self.send_broadcast)
        row.append(send)
        bc.append(row)
        self.bc_targets = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=12,
                                      column_spacing=6, row_spacing=6)
        bc.append(self.bc_targets)
        self.bc_rev = Gtk.Revealer(child=bc, transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        self.bc_btn.bind_property('active', self.bc_rev, 'reveal-child', GObject.BindingFlags.SYNC_CREATE)
        self.bc_btn.connect('toggled', lambda b: b.get_active() and (self.refresh_broadcast_targets(),
                                                                      self.bc_entry.grab_focus()))
        tb.add_bottom_bar(self.bc_rev)

        tb.set_content(self.tabview)
        self.toasts.set_child(tb)
        self.set_content(self.toasts)

        def on_servers(fn):
            def cb(*_):
                if self.tabview.get_selected_page() != self.servers_page:
                    return False
                fn()
                return True
            return cb
        ctl = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)
        for accel, fn in (('<Control>n', on_servers(lambda: HostDialog(self).present(self))),
                          ('<Control>r', on_servers(self.check_all)),
                          ('<Control>f', on_servers(lambda: self.search_btn.set_active(True))),
                          ('<Control><Shift>w', lambda *_: self.close_current()),
                          ('<Control><Shift>b', lambda *_: (self.bc_btn.set_active(not self.bc_btn.get_active()), True)[1]),
                          ('<Control>Page_Down', lambda *_: (self.tabview.select_next_page(), True)[1]),
                          ('<Control>Page_Up', lambda *_: (self.tabview.select_previous_page(), True)[1])):
            ctl.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(accel),
                                          action=Gtk.CallbackAction.new(fn)))
        self.add_controller(ctl)
        self.connect('close-request', self.on_close_request)

        self.reload()
        GLib.timeout_add_seconds(30, lambda: (self.check_all(), True)[1])

    # ── servers ─────────────────────────────────────────────────────────────
    def reload(self, check=True):
        child = self.list_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.list_box.remove(child)
            child = nxt
        hosts = core.load_hosts()
        q = self.search.get_text().strip().lower()
        shown = [h for h in hosts if not q or q in f'{h.alias} {h.target} {h.group} {h.note}'.lower()]
        shown.sort(key=lambda h: (h.group.lower() or '~', -h.last_used, h.alias))

        hero = Gtk.Box(spacing=24, css_classes=['hop-hero-box'])
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        col.append(Gtk.Label(label='⇢ hop ✿', xalign=0, css_classes=['hop-hero']))
        col.append(Gtk.Label(label='SSH · ไฟล์ · Dashboard · Tunnels — จบในที่เดียว', xalign=0,
                             css_classes=['dim-label']))
        hero.append(col)
        online = sum(1 for a in self.status if self.status[a] is not None and any(h.alias == a for h in hosts))
        for num, label in ((len(hosts), 'เซิร์ฟเวอร์'), (online, 'ออนไลน์'), (len(self.tunnels.procs), 'tunnels')):
            st = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
            st.append(Gtk.Label(label=str(num), css_classes=['hop-stat']))
            st.append(Gtk.Label(label=label, css_classes=['caption', 'dim-label']))
            hero.append(st)
        self.online_lbl = hero.get_last_child().get_prev_sibling().get_first_child()
        self.list_box.append(hero)

        if not hosts:
            empty = Adw.StatusPage(icon_name='network-server-symbolic', title='ยังไม่มีเซิร์ฟเวอร์',
                                   description='กด + เพื่อเพิ่มเซิร์ฟเวอร์แรก')
            b = Gtk.Button(label='เพิ่มเซิร์ฟเวอร์', halign=Gtk.Align.CENTER, css_classes=['suggested-action', 'pill'])
            b.connect('clicked', lambda *_: HostDialog(self).present(self))
            empty.set_child(b)
            self.list_box.append(empty)
        elif not shown:
            self.list_box.append(Adw.StatusPage(icon_name='system-search-symbolic', title='ไม่เจอ'))

        self.cards, groups = {}, {}
        for h in shown:
            fb = groups.get(h.group)
            if fb is None:
                self.list_box.append(Gtk.Label(label=h.group or 'ทั่วไป', xalign=0, css_classes=['hop-group-title']))
                fb = groups[h.group] = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                                   min_children_per_line=1, max_children_per_line=3,
                                                   column_spacing=14, row_spacing=14)
                self.list_box.append(fb)
            card = ServerCard(self, h)
            if h.alias in self.status:
                card.set_status(self.status[h.alias])
            fb.append(card)
            self.cards[h.alias] = card
        if check:
            self.check_all()

    def check_all(self):
        aliases = list(self.cards)

        def work():
            res = core.ping_all(aliases)
            GLib.idle_add(self.apply_status, res)
            for a in aliases:
                if res.get(a) is not None and not core.get_meta(a, 'os'):
                    info = core.fetch_os(a)
                    if info:
                        core.set_meta(a, 'os', info)
                        GLib.idle_add(lambda a=a, info=info: a in self.cards and self.cards[a].set_os(info))
        threading.Thread(target=work, daemon=True).start()

    def apply_status(self, res):
        self.status.update(res)
        for alias, ms in res.items():
            if alias in self.cards:
                self.cards[alias].set_status(ms)
        self.online_lbl.set_label(str(sum(1 for a in self.cards if self.status.get(a) is not None)))
        return False

    # ── tabs ────────────────────────────────────────────────────────────────
    def open_session(self, alias, start='term'):
        core.touch(alias)
        session = Session(self, alias, start)
        page = self.tabview.append(session)
        session.page = page
        page.set_title(alias)
        page.set_icon(Gio.ThemedIcon.new('utilities-terminal-symbolic'))
        self.tabview.set_selected_page(page)

    def open_editor(self, alias, gfile, path):
        for i in range(self.tabview.get_n_pages()):
            p = self.tabview.get_nth_page(i)
            c = p.get_child()
            if isinstance(c, EditorPage) and c.alias == alias and c.path == path:
                self.tabview.set_selected_page(p)
                return
        ed = EditorPage(self, alias, gfile, path)
        page = self.tabview.append(ed)
        ed.page = page
        ed.set_title()
        page.set_icon(Gio.ThemedIcon.new('document-edit-symbolic'))
        page.set_tooltip(f'{alias}:{path}')
        self.tabview.set_selected_page(page)

    def open_tunnels(self, alias):
        TunnelsDialog(self, alias).present(self)

    def open_snippets(self):
        SnippetsDialog(self).present(self)

    def on_close_page(self, view, page):
        child = page.get_child()
        if page == self.servers_page:
            view.close_page_finish(page, False)
        elif isinstance(child, EditorPage):
            child.confirm_close(lambda ok: view.close_page_finish(page, ok))
        else:
            if isinstance(child, Session):
                child.shutdown()
            view.close_page_finish(page, True)
        return True

    def close_current(self):
        page = self.tabview.get_selected_page()
        if page and page != self.servers_page:
            self.tabview.close_page(page)
        return True

    def on_tab_changed(self, *_):
        page = self.tabview.get_selected_page()
        on_servers = page is None or page == self.servers_page or not page.get_child() or \
            not isinstance(page.get_child(), (Session, EditorPage))
        self.search_btn.set_visible(on_servers)
        self.refresh_btn.set_visible(on_servers)
        self.set_title(f'Hop — {page.get_title()}' if page and not on_servers else 'Hop')
        if page and isinstance(page.get_child(), Session):
            GLib.idle_add(page.get_child().on_switch)

    def on_close_request(self, *_):
        self.tunnels.stop_all()
        for i in range(self.tabview.get_n_pages()):
            c = self.tabview.get_nth_page(i).get_child()
            if isinstance(c, Session):
                c.shutdown()
        return False

    # ── broadcast ───────────────────────────────────────────────────────────
    def sessions(self):
        out = []
        for i in range(self.tabview.get_n_pages()):
            c = self.tabview.get_nth_page(i).get_child()
            if isinstance(c, Session):
                out.append(c)
        return out

    def refresh_broadcast_targets(self):
        if not hasattr(self, 'bc_targets'):
            return
        prev = {getattr(c.get_child(), 'session', None): c.get_child().get_active()
                for c in list(self._children(self.bc_targets))}
        self.bc_targets.remove_all()
        sessions = self.sessions()
        if not sessions:
            self.bc_targets.append(Gtk.Label(label='ยังไม่มีแท็บเซิร์ฟเวอร์ที่เปิดอยู่ — กด Connect ก่อน',
                                             css_classes=['dim-label']))
        for s in sessions:
            b = Gtk.ToggleButton(label=s.alias, active=prev.get(s, True), css_classes=['pill'])
            b.session = s
            self.bc_targets.append(b)

    @staticmethod
    def _children(flow):
        c = flow.get_first_child()
        while c:
            if isinstance(c.get_child(), Gtk.ToggleButton):
                yield c
            c = c.get_next_sibling()

    def send_broadcast(self, *_):
        cmd = self.bc_entry.get_text()
        if not cmd.strip():
            return
        targets = [c.get_child().session for c in self._children(self.bc_targets) if c.get_child().get_active()]
        for s in targets:
            s.broadcast(cmd)
        self.bc_entry.set_text('')
        self.toast(f'ส่งไป {len(targets)} เครื่อง' if targets else 'ไม่ได้เลือกเครื่องไหน')

    # ── misc ────────────────────────────────────────────────────────────────
    def copy(self, text):
        Gdk.Display.get_default().get_clipboard().set(text)
        self.toast('คัดลอกแล้ว')

    def confirm_delete(self, alias):
        d = Adw.AlertDialog(heading=f'ลบ {alias}?',
                            body='จะลบออกจาก ~/.ssh/config (มีไฟล์สำรองที่ ~/.ssh/config.hop-bak)')
        d.add_response('cancel', 'ยกเลิก')
        d.add_response('delete', 'ลบ')
        d.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)

        def done(dlg, res):
            if dlg.choose_finish(res) == 'delete':
                core.delete_host(alias)
                self.toast(f'ลบ {alias} แล้ว')
                self.reload(check=False)
        d.choose(self, None, done)

    def toast(self, text, payload=None, action=None):
        t = Adw.Toast(title=GLib.markup_escape_text(text), timeout=3)
        if action and payload is not None:
            t.set_button_label('เปิด')
            t.connect('button-clicked', lambda *_: action(payload))
        self.toasts.add_toast(t)
        return False


class HopApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        win = self.props.active_window or HopWindow(self)
        win.present()


if __name__ == '__main__':
    sys.exit(HopApp().run(sys.argv))
