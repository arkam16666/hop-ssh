"""One SSH session inside Hop: an embedded terminal and an SFTP file browser."""
import configparser
import os
import signal
import subprocess
import threading
import time

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, Vte

import hopcore as core

FONT = 'JetBrainsMono Nerd Font 11'
ATTRS = 'standard::name,standard::display-name,standard::type,standard::size,' \
        'standard::is-hidden,standard::symbolic-icon,standard::content-type,time::modified'


# ── helpers ──────────────────────────────────────────────────────────────────

def terminal_palette():
    """Colors of the palette Ptyxis uses right now, so the terminal follows `theme`."""
    try:
        settings = Gio.Settings.new('org.gnome.Ptyxis')
        uuid = settings.get_string('default-profile-uuid')
        prof = Gio.Settings.new_with_path('org.gnome.Ptyxis.Profile', f'/org/gnome/Ptyxis/Profiles/{uuid}/')
        name = prof.get_string('palette')
        data = Gio.resources_lookup_data(f'/org/gnome/Ptyxis/palettes/{name}.palette', 0).get_data().decode()
    except Exception:
        try:
            path = '/usr/bin/ptyxis'
            data = subprocess.run(['gresource', 'extract', path, f'/org/gnome/Ptyxis/palettes/{name}.palette'],
                                  capture_output=True, text=True, timeout=3).stdout
        except Exception:
            data = ''
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    try:
        cp.read_string(data)
    except configparser.Error:
        return None
    sec = next((s for s in ('Dark', 'Palette') if cp.has_section(s) and cp.has_option(s, 'Background')), None)
    if not sec:
        return None

    def rgba(v):
        c = Gdk.RGBA()
        c.parse(v)
        return c
    s = cp[sec]
    pal = [rgba(s.get(f'Color{i}', '#888888')) for i in range(16)]
    return rgba(s['Foreground']), rgba(s['Background']), pal


def human_size(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024


def unique_child(parent, name):
    """parent/name, or parent/'name (1).ext' … if it already exists."""
    f = parent.get_child(name)
    if not f.query_exists(None):
        return f
    stem, ext = os.path.splitext(name)
    i = 1
    while True:
        f = parent.get_child(f'{stem} ({i}){ext}')
        if not f.query_exists(None):
            return f
        i += 1


def tree_size(f, cancel):
    info = f.query_info('standard::type,standard::size', Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, cancel)
    if info.get_file_type() != Gio.FileType.DIRECTORY:
        return info.get_size()
    total = 0
    for child in f.enumerate_children('standard::name', Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, cancel):
        total += tree_size(f.get_child(child.get_name()), cancel)
    return total


def copy_tree(src, dst, cancel, on_bytes):
    info = src.query_info('standard::type', Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, cancel)
    if info.get_file_type() == Gio.FileType.DIRECTORY:
        try:
            dst.make_directory(cancel)
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.EXISTS):
                raise
        for child in src.enumerate_children('standard::name', Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, cancel):
            n = child.get_name()
            copy_tree(src.get_child(n), dst.get_child(n), cancel, on_bytes)
    else:
        last = [0]

        def prog(cur, _total):
            on_bytes(cur - last[0])
            last[0] = cur
        src.copy(dst, Gio.FileCopyFlags.OVERWRITE, cancel, prog)


def delete_tree(f, cancel=None):
    info = f.query_info('standard::type', Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, cancel)
    if info.get_file_type() == Gio.FileType.DIRECTORY:
        for child in f.enumerate_children('standard::name', Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, cancel):
            delete_tree(f.get_child(child.get_name()), cancel)
    f.delete(cancel)


# ── terminal ─────────────────────────────────────────────────────────────────

class TerminalView(Gtk.Box):
    def __init__(self, session):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.session, self.pid = session, None

        self.banner = Adw.Banner(title='การเชื่อมต่อถูกปิดแล้ว', button_label='เชื่อมต่อใหม่')
        self.banner.connect('button-clicked', lambda *_: self.spawn())
        self.append(self.banner)

        self.term = Vte.Terminal(hexpand=True, vexpand=True)
        self.term.set_font(Pango.FontDescription.from_string(FONT))
        self.term.set_scrollback_lines(20000)
        self.term.set_mouse_autohide(True)
        self.term.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
        self.term.set_cursor_shape(Vte.CursorShape.IBEAM)
        self.term.set_bold_is_bright(True)
        colors = terminal_palette()
        if colors:
            self.term.set_colors(*colors)
        self.term.connect('child-exited', self.on_exit)
        
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self.on_key)
        self.term.add_controller(keys)

        frame = Gtk.Box(css_classes=['hop-term-frame'], hexpand=True, vexpand=True)
        frame.append(self.term)
        self.append(frame)
        self.spawn()

    def spawn(self):
        self.banner.set_revealed(False)
        self.term.reset(True, True)
        self.term.spawn_async(Vte.PtyFlags.DEFAULT, os.path.expanduser('~'),
                              core.ssh_argv(self.session.alias), None, GLib.SpawnFlags.SEARCH_PATH,
                              None, None, -1, None, self.on_spawned)

    def on_spawned(self, term, pid, error, *args):
        if error:
            self.banner.set_title(f'เปิด ssh ไม่ได้: {error.message}')
            self.banner.set_revealed(True)
        else:
            self.pid = pid

    def on_exit(self, term, status):
        self.pid = None
        self.banner.set_title('การเชื่อมต่อถูกปิดแล้ว')
        self.banner.set_revealed(True)

    def on_key(self, ctl, keyval, keycode, state):
        mods = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
        cs = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        k = Gdk.keyval_to_lower(keyval)
        if mods == cs and k == Gdk.KEY_c:
            self.term.copy_clipboard_format(Vte.Format.TEXT)
            return True
        if mods == cs and k == Gdk.KEY_v:
            self.term.paste_clipboard()
            return True
        if mods & Gdk.ModifierType.CONTROL_MASK and k in (Gdk.KEY_plus, Gdk.KEY_equal):
            self.term.set_font_scale(min(self.term.get_font_scale() + 0.1, 3))
            return True
        if mods & Gdk.ModifierType.CONTROL_MASK and k == Gdk.KEY_minus:
            self.term.set_font_scale(max(self.term.get_font_scale() - 0.1, 0.5))
            return True
        if mods & Gdk.ModifierType.CONTROL_MASK and k == Gdk.KEY_0:
            self.term.set_font_scale(1)
            return True
        return False

    def run(self, cmd, enter=True):
        self.term.feed_child((cmd + ('\n' if enter else '')).encode())

    def shutdown(self):
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass


# ── files ────────────────────────────────────────────────────────────────────

class TransferRow(Gtk.Box):
    def __init__(self, label, cancel):
        super().__init__(spacing=10, margin_top=4, margin_bottom=4, margin_start=12, margin_end=8)
        self.cancel = cancel
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        self.label = Gtk.Label(label=label, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.bar = Gtk.ProgressBar()
        self.info = Gtk.Label(xalign=0, css_classes=['caption', 'dim-label'])
        box.append(self.label)
        box.append(self.bar)
        box.append(self.info)
        self.append(box)
        self.btn = Gtk.Button(icon_name='process-stop-symbolic', valign=Gtk.Align.CENTER,
                              css_classes=['flat', 'circular'], tooltip_text='ยกเลิก')
        self.btn.connect('clicked', lambda *_: cancel.cancel())
        self.append(self.btn)


class FilesView(Gtk.Box):
    def __init__(self, session):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.session = session
        self.root = Gio.File.new_for_uri(f'sftp://{session.alias}/')
        self.cwd, self.home, self.mounted, self.show_hidden = '/', None, False, False
        self.history = []

        # toolbar
        bar = Gtk.Box(spacing=6, margin_top=8, margin_bottom=8, margin_start=10, margin_end=10)
        def btn(icon, tip, cb, flat=True):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, css_classes=['flat'] if flat else [])
            b.connect('clicked', lambda *_: cb())
            bar.append(b)
            return b
        self.back_btn = btn('go-previous-symbolic', 'ย้อนกลับ', self.go_back)
        btn('go-up-symbolic', 'โฟลเดอร์แม่', self.go_up)
        btn('go-home-symbolic', 'Home', lambda: self.navigate(self.home or '/'))
        self.path = Gtk.Entry(hexpand=True, css_classes=['hop-path'])
        self.path.connect('activate', lambda e: self.navigate(e.get_text().strip() or '/'))
        bar.append(self.path)
        btn('view-refresh-symbolic', 'รีเฟรช', lambda: self.navigate(self.cwd, push=False))
        hidden = Gtk.ToggleButton(icon_name='view-reveal-symbolic', tooltip_text='แสดงไฟล์ซ่อน', css_classes=['flat'])
        hidden.connect('toggled', lambda b: (setattr(self, 'show_hidden', b.get_active()),
                                             self.navigate(self.cwd, push=False)))
        bar.append(hidden)
        btn('folder-new-symbolic', 'สร้างโฟลเดอร์', self.new_folder)
        btn('utilities-terminal-symbolic', 'cd ไปโฟลเดอร์นี้ใน Terminal', lambda: self.session.cd_terminal(self.cwd, switch=True))

        up_menu = Gio.Menu()
        up_menu.append('อัปโหลดไฟล์…', 'files.upload-files')
        up_menu.append('อัปโหลดโฟลเดอร์…', 'files.upload-folder')
        upload = Adw.SplitButton(label='อัปโหลด', menu_model=up_menu, css_classes=['suggested-action'])
        upload.connect('clicked', lambda *_: self.pick_upload(False))
        bar.append(upload)
        self.append(bar)
        self.append(Gtk.Separator())

        ag = Gio.SimpleActionGroup()
        for name, cb in (('upload-files', lambda *_: self.pick_upload(False)),
                         ('upload-folder', lambda *_: self.pick_upload(True))):
            a = Gio.SimpleAction.new(name, None)
            a.connect('activate', cb)
            ag.add_action(a)
        self.insert_action_group('files', ag)

        # content
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        spin = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=12)
        spin.append(Adw.Spinner(width_request=32, height_request=32, halign=Gtk.Align.CENTER))
        spin.append(Gtk.Label(label='กำลังเชื่อมต่อ SFTP…', css_classes=['dim-label']))
        self.stack.add_named(spin, 'loading')
        self.error = Adw.StatusPage(icon_name='dialog-warning-symbolic', title='เปิดไฟล์ไม่ได้')
        retry = Gtk.Button(label='ลองใหม่', halign=Gtk.Align.CENTER, css_classes=['pill'])
        retry.connect('clicked', lambda *_: self.start())
        self.error.set_child(retry)
        self.stack.add_named(self.error, 'error')
        self.stack.add_named(Adw.StatusPage(icon_name='folder-symbolic', title='โฟลเดอร์ว่าง',
                                            description='ลากไฟล์มาวางตรงนี้เพื่ออัปโหลด'), 'empty')

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['hop-files'])
        self.list.connect('row-activated', lambda lb, row: self.open_entry(row.entry))
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.list)
        self.stack.add_named(sw, 'list')

        drop_area = Gtk.Overlay(child=self.stack, vexpand=True)
        self.drop_hint = Gtk.Label(label='⬆  วางเพื่ออัปโหลดไปที่โฟลเดอร์นี้', css_classes=['hop-drop'],
                                   visible=False, can_target=False, halign=Gtk.Align.FILL, valign=Gtk.Align.FILL)
        drop_area.add_overlay(self.drop_hint)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect('enter', lambda *_: (self.drop_hint.set_visible(True), Gdk.DragAction.COPY)[1])
        drop.connect('leave', lambda *_: self.drop_hint.set_visible(False))
        drop.connect('drop', self.on_drop)
        drop_area.add_controller(drop)
        self.append(drop_area)

        # transfers
        self.transfers = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=['hop-transfers'])
        self.trans_rev = Gtk.Revealer(child=self.transfers, transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        self.append(self.trans_rev)

    # ── mount & listing ────────────────────────────────────────────────────
    def start(self):
        self.stack.set_visible_child_name('loading')
        op = Gtk.MountOperation(parent=self.get_root())
        self.root.mount_enclosing_volume(Gio.MountMountFlags.NONE, op, None, self.on_mounted)

    def on_mounted(self, f, res):
        try:
            f.mount_enclosing_volume_finish(res)
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.ALREADY_MOUNTED):
                self.error.set_description(e.message)
                self.stack.set_visible_child_name('error')
                return
        self.mounted = True

        def find_home():
            home = None
            try:
                out = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                                      self.session.alias, 'printf %s "$HOME"'],
                                     capture_output=True, text=True, timeout=8)
                if out.returncode == 0 and out.stdout.startswith('/'):
                    home = out.stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                pass
            GLib.idle_add(self.set_home, home or '/')
        threading.Thread(target=find_home, daemon=True).start()

    def set_home(self, home):
        self.home = home
        self.navigate(home)

    def rfile(self, path):
        return self.root.resolve_relative_path(path.lstrip('/')) if path.strip('/') else self.root

    def navigate(self, path, push=True):
        if not self.mounted:
            return
        path = os.path.normpath(path) if path else '/'
        if push and path != self.cwd:
            self.history.append(self.cwd)
        self.back_btn.set_sensitive(bool(self.history))
        target = self.rfile(path)

        def work():
            try:
                entries = []
                for info in target.enumerate_children(ATTRS, Gio.FileQueryInfoFlags.NONE, None):
                    entries.append(info)
                GLib.idle_add(self.show_entries, path, entries)
            except GLib.Error as e:
                GLib.idle_add(self.session.toast, f'เปิด {path} ไม่ได้: {e.message}')
        threading.Thread(target=work, daemon=True).start()

    def show_entries(self, path, entries):
        changed = path != self.cwd
        self.cwd = path
        self.path.set_text(path)
        if changed:
            self.session.on_files_navigated(path)
        self.list.remove_all()
        shown = [e for e in entries if self.show_hidden or not e.get_name().startswith('.')]
        shown.sort(key=lambda e: (e.get_file_type() != Gio.FileType.DIRECTORY, e.get_display_name().casefold()))
        for info in shown:
            self.list.append(self.make_row(info))
        self.stack.set_visible_child_name('list' if shown else 'empty')

    def make_row(self, info):
        is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
        dt = info.get_modification_date_time()
        when = dt.to_local().format('%d %b %Y %H:%M') if dt else ''
        sub = when if is_dir else f'{human_size(info.get_size())}  ·  {when}'
        row = Adw.ActionRow(title=GLib.markup_escape_text(info.get_display_name()), subtitle=sub, activatable=True)
        icon = info.get_symbolic_icon() or Gio.ThemedIcon.new('text-x-generic-symbolic')
        row.add_prefix(Gtk.Image(gicon=icon, css_classes=['hop-file-icon', 'dir' if is_dir else 'file']))
        if is_dir:
            row.add_suffix(Gtk.Image(icon_name='go-next-symbolic', css_classes=['dim-label']))
        row.entry = (info.get_name(), is_dir, info.get_attribute_string('standard::content-type'), info.get_size())

        dl = Gtk.Button(icon_name='folder-download-symbolic', tooltip_text='ดาวน์โหลด',
                        valign=Gtk.Align.CENTER, css_classes=['flat'])
        dl.connect('clicked', lambda *_: self.download(info.get_name()))
        menu = Gio.Menu()
        if not is_dir:
            menu.append('แก้ไข', 'row.edit')
        menu.append('ดาวน์โหลด', 'row.download')
        menu.append('เปลี่ยนชื่อ', 'row.rename')
        menu.append('ลบ', 'row.delete')
        more = Gtk.MenuButton(icon_name='view-more-symbolic', menu_model=menu, valign=Gtk.Align.CENTER,
                              css_classes=['flat'])
        row.add_suffix(dl)
        row.add_suffix(more)
        ag = Gio.SimpleActionGroup()
        name = info.get_name()
        for act, cb in (('edit', lambda *_: self.edit(name)),
                        ('download', lambda *_: self.download(name)),
                        ('rename', lambda *_: self.rename(name)),
                        ('delete', lambda *_: self.delete(name, is_dir))):
            a = Gio.SimpleAction.new(act, None)
            a.connect('activate', cb)
            ag.add_action(a)
        row.insert_action_group('row', ag)

        click = Gtk.GestureClick(button=3)
        click.connect('pressed', lambda *_: more.popup())
        row.add_controller(click)
        return row

    def open_entry(self, entry):
        from hop_editor import is_text
        name, is_dir, ctype, size = entry
        if is_dir:
            self.navigate(os.path.join(self.cwd, name))
        elif is_text(ctype, size, name):
            self.edit(name)
        else:
            self.download(name, open_after=True)

    def edit(self, name):
        path = os.path.join(self.cwd, name)
        self.session.win.open_editor(self.session.alias, self.rfile(path), path)

    def go_up(self):
        self.navigate(os.path.dirname(self.cwd.rstrip('/')) or '/')

    def go_back(self):
        if self.history:
            self.navigate(self.history.pop(), push=False)
            self.back_btn.set_sensitive(bool(self.history))

    # ── operations ─────────────────────────────────────────────────────────
    def transfer(self, label, src, dst_parent, name, done_msg, on_done=None):
        cancel = Gio.Cancellable()
        row = TransferRow(label, cancel)
        self.transfers.append(row)
        self.trans_rev.set_reveal_child(True)
        state = {'done': 0, 'total': 0, 't0': time.monotonic()}

        def tick():
            total = state['total'] or 1
            row.bar.set_fraction(min(state['done'] / total, 1))
            rate = state['done'] / max(time.monotonic() - state['t0'], 0.001)
            row.info.set_text(f'{human_size(state["done"])} / {human_size(state["total"])}  ·  {human_size(rate)}/s')
            return False

        def on_bytes(n):
            state['done'] += n
            GLib.idle_add(tick)

        def work():
            err, dst = None, None
            try:
                state['total'] = tree_size(src, cancel)
                dst = unique_child(dst_parent, name)
                copy_tree(src, dst, cancel, on_bytes)
            except GLib.Error as e:
                err = 'ยกเลิกแล้ว' if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED) else e.message
            GLib.idle_add(finish, err, dst)

        def finish(err, dst):
            tick()
            self.transfers.remove(row)
            if not self.transfers.get_first_child():
                self.trans_rev.set_reveal_child(False)
            if err:
                self.session.toast(f'{label}: {err}')
            else:
                self.session.toast(done_msg, dst if on_done else None, on_done)
            self.navigate(self.cwd, push=False)
            return False
        threading.Thread(target=work, daemon=True).start()

    def download(self, name, open_after=False):
        dest_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.expanduser('~/Downloads')
        os.makedirs(dest_dir, exist_ok=True)

        def launch(f):
            Gtk.FileLauncher(file=f).launch(self.get_root(), None, None)
        self.transfer(f'⬇ {name}', self.rfile(os.path.join(self.cwd, name)), Gio.File.new_for_path(dest_dir),
                      name, f'ดาวน์โหลด {name} ไปที่ Downloads แล้ว', launch)
        if open_after:
            self.session.toast(f'กำลังดาวน์โหลด {name}…')

    def upload_files(self, files):
        for f in files:
            name = f.get_basename()
            self.transfer(f'⬆ {name}', f, self.rfile(self.cwd), name, f'อัปโหลด {name} แล้ว')

    def pick_upload(self, folder):
        dlg = Gtk.FileDialog(title='เลือกโฟลเดอร์ที่จะอัปโหลด' if folder else 'เลือกไฟล์ที่จะอัปโหลด')
        win = self.get_root()
        if folder:
            def done(d, res):
                try:
                    self.upload_files([d.select_folder_finish(res)])
                except GLib.Error:
                    pass
            dlg.select_folder(win, None, done)
        else:
            def done(d, res):
                try:
                    model = d.open_multiple_finish(res)
                except GLib.Error:
                    return
                self.upload_files([model.get_item(i) for i in range(model.get_n_items())])
            dlg.open_multiple(win, None, done)

    def on_drop(self, target, value, x, y):
        self.drop_hint.set_visible(False)
        files = value.get_files()
        if files:
            self.upload_files(files)
        return True

    def ask_text(self, heading, initial, confirm, cb):
        d = Adw.AlertDialog(heading=heading)
        entry = Gtk.Entry(text=initial, activates_default=True)
        d.set_extra_child(entry)
        d.add_response('cancel', 'ยกเลิก')
        d.add_response('ok', confirm)
        d.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response('ok')

        def done(dlg, res):
            if dlg.choose_finish(res) == 'ok' and entry.get_text().strip():
                cb(entry.get_text().strip())
        d.choose(self.get_root(), None, done)
        entry.grab_focus()

    def run_bg(self, fn, ok_msg):
        def work():
            try:
                fn()
                GLib.idle_add(self.session.toast, ok_msg)
            except GLib.Error as e:
                GLib.idle_add(self.session.toast, e.message)
            GLib.idle_add(lambda: self.navigate(self.cwd, push=False))
        threading.Thread(target=work, daemon=True).start()

    def new_folder(self):
        self.ask_text('สร้างโฟลเดอร์ใหม่', 'New Folder', 'สร้าง',
                      lambda n: self.run_bg(lambda: self.rfile(os.path.join(self.cwd, n)).make_directory(None),
                                            f'สร้าง {n} แล้ว'))

    def rename(self, name):
        self.ask_text(f'เปลี่ยนชื่อ {name}', name, 'เปลี่ยนชื่อ',
                      lambda n: self.run_bg(lambda: self.rfile(os.path.join(self.cwd, name)).set_display_name(n, None),
                                            f'เปลี่ยนชื่อเป็น {n} แล้ว'))

    def delete(self, name, is_dir):
        d = Adw.AlertDialog(heading=f'ลบ {name}?',
                            body='ลบบนเซิร์ฟเวอร์ถาวร กู้คืนไม่ได้' + (' (รวมทุกอย่างในโฟลเดอร์)' if is_dir else ''))
        d.add_response('cancel', 'ยกเลิก')
        d.add_response('delete', 'ลบ')
        d.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)

        def done(dlg, res):
            if dlg.choose_finish(res) == 'delete':
                self.run_bg(lambda: delete_tree(self.rfile(os.path.join(self.cwd, name))), f'ลบ {name} แล้ว')
        d.choose(self.get_root(), None, done)


# ── the session tab ──────────────────────────────────────────────────────────

class Session(Gtk.Box):
    def __init__(self, win, alias, start_page='term'):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        from hop_dash import Dashboard
        self.win, self.alias, self.page = win, alias, None
        self.split = False

        self.terminal = TerminalView(self)
        self.files = FilesView(self)
        self.dash = Dashboard(self)

        # terminal page holds a paned so files can sit next to it in split view
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True,
                               resize_start_child=True, shrink_end_child=False)
        self.paned.set_start_child(self.terminal)
        self.files_holder = Gtk.Box(vexpand=True)
        self.files_holder.append(self.files)

        self.stack = Adw.ViewStack(vexpand=True)
        self.stack.add_titled_with_icon(self.paned, 'term', 'Terminal', 'utilities-terminal-symbolic')
        self.stack.add_titled_with_icon(self.files_holder, 'files', 'ไฟล์', 'folder-remote-symbolic')
        self.stack.add_titled_with_icon(self.dash, 'dash', 'Dashboard', 'utilities-system-monitor-symbolic')

        bar = Gtk.CenterBox(css_classes=['hop-session-bar'])
        left = Gtk.Box(spacing=4, margin_start=6)
        self.snip_btn = Gtk.MenuButton(icon_name='starred-symbolic', tooltip_text='Snippets — คำสั่งด่วน',
                                       css_classes=['flat'], popover=self.build_snippets_popover())
        left.append(self.snip_btn)
        tun = Gtk.Button(icon_name='network-vpn-symbolic', tooltip_text='Port forwarding', css_classes=['flat'])
        tun.connect('clicked', lambda *_: win.open_tunnels(alias))
        left.append(tun)
        bar.set_start_widget(left)
        bar.set_center_widget(Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        right = Gtk.Box(spacing=4, margin_end=6)
        self.sync_btn = Gtk.ToggleButton(icon_name='emblem-synchronizing-symbolic', active=True, visible=False,
                                         tooltip_text='เปิดโฟลเดอร์ไหน Terminal cd ตาม', css_classes=['flat'])
        right.append(self.sync_btn)
        self.split_btn = Gtk.ToggleButton(icon_name='view-dual-symbolic', tooltip_text='Split view: Terminal + ไฟล์',
                                          css_classes=['flat'])
        self.split_btn.connect('toggled', self.on_split)
        right.append(self.split_btn)
        bar.set_end_widget(right)
        self.append(bar)
        self.append(self.stack)

        self.files_started = False
        self.stack.connect('notify::visible-child-name', self.on_switch)
        self.stack.set_visible_child_name(start_page)
        GLib.idle_add(self.on_switch)

    # ── snippets ────────────────────────────────────────────────────────────
    def build_snippets_popover(self):
        pop = Gtk.Popover(css_classes=['menu'])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6, width_request=320)
        box.append(Gtk.Label(label='⚡ Snippets', xalign=0, css_classes=['heading'], margin_start=6))
        self.snip_list = Gtk.ListBox(css_classes=['boxed-list'], selection_mode=Gtk.SelectionMode.NONE)
        self.snip_list.connect('row-activated', lambda lb, row: (pop.popdown(), self.run_snippet(row.cmd)))
        box.append(self.snip_list)
        manage = Gtk.Button(label='จัดการ snippets…', css_classes=['flat'])
        manage.connect('clicked', lambda *_: (pop.popdown(), self.win.open_snippets()))
        box.append(manage)
        pop.set_child(box)
        pop.connect('show', lambda *_: self.fill_snippets())
        return pop

    def fill_snippets(self):
        self.snip_list.remove_all()
        for sn in core.load_snippets():
            row = Adw.ActionRow(title=GLib.markup_escape_text(sn['name']),
                                subtitle=GLib.markup_escape_text(sn['cmd']), activatable=True,
                                subtitle_lines=1)
            row.add_suffix(Gtk.Image(icon_name='media-playback-start-symbolic', css_classes=['dim-label']))
            row.cmd = sn['cmd']
            self.snip_list.append(row)

    def run_snippet(self, cmd):
        if self.stack.get_visible_child_name() != 'term':
            self.stack.set_visible_child_name('term')
        self.terminal.run(cmd)
        self.terminal.term.grab_focus()

    # ── split view / cd sync ────────────────────────────────────────────────
    def on_split(self, btn):
        self.split = btn.get_active()
        self.sync_btn.set_visible(self.split)
        if self.split:
            self.files_holder.remove(self.files)
            self.paned.set_end_child(self.files)
            self.files.set_size_request(380, -1)
            w = self.get_width() or 1100
            self.paned.set_position(int(w * 0.58))
            self.stack.set_visible_child_name('term')
            if not self.files_started:
                self.files_started = True
                self.files.start()
        else:
            self.paned.set_end_child(None)
            self.files.set_size_request(-1, -1)
            self.files_holder.append(self.files)

    def on_files_navigated(self, path):
        if self.split and self.sync_btn.get_active():
            self.cd_terminal(path)

    def cd_terminal(self, path, switch=False):
        quoted = "'" + path.replace("'", "'\\''") + "'"
        self.terminal.run(f' cd {quoted}')
        if switch and not self.split:
            self.stack.set_visible_child_name('term')

    # ── page switching ──────────────────────────────────────────────────────
    def on_switch(self, *_):
        name = self.stack.get_visible_child_name()
        if name == 'files' and not self.files_started:
            self.files_started = True
            self.files.start()
        if name == 'dash':
            self.dash.start()
        else:
            self.dash.stop()
        if name == 'term':
            self.terminal.term.grab_focus()
        return False

    def broadcast(self, cmd):
        self.terminal.run(cmd)

    def set_subtitle(self, text):
        if self.page:
            self.page.set_tooltip(text)

    def toast(self, text, payload=None, action=None):
        self.win.toast(text, payload, action)
        return False

    def shutdown(self):
        self.dash.stop()
        self.terminal.shutdown()
