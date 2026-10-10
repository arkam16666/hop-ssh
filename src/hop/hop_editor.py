"""Edit a remote file over SFTP in a GtkSourceView tab."""
import os
from xml.sax.saxutils import escape

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, GtkSource, Pango

from hop_session import FONT, terminal_palette

GtkSource.init()
TEXT_TYPES = ('application/json', 'application/xml', 'application/x-shellscript', 'application/javascript',
              'application/x-yaml', 'application/toml', 'application/x-php', 'application/sql',
              'application/x-desktop', 'application/x-ruby', 'application/x-perl', 'application/x-python',
              'application/x-trash', 'application/x-zerosize')
MAX_SIZE = 5 * 1024 * 1024


def is_text(content_type, size, name):
    if size > MAX_SIZE:
        return False
    if content_type and (content_type.startswith('text/') or content_type in TEXT_TYPES):
        return True
    base = os.path.basename(name)
    return base.startswith('.') or '.' not in base or base.endswith(('.conf', '.cfg', '.ini', '.env', '.log'))


def hex_of(rgba):
    return '#{:02x}{:02x}{:02x}'.format(*(round(c * 255) for c in (rgba.red, rgba.green, rgba.blue)))


def install_scheme():
    """A GtkSourceView style built from the terminal palette, so the editor follows `theme`."""
    colors = terminal_palette()
    if not colors:
        return 'Adwaita-dark'
    fg, bg, p = colors
    c = [hex_of(x) for x in p]
    fg, bg = hex_of(fg), hex_of(bg)
    styles = {
        'text': (fg, bg), 'selection': (None, c[4] + '55'), 'current-line': (None, c[0] + '40'),
        'line-numbers': (c[8], bg), 'current-line-number': (c[4], None), 'cursor': (fg, None),
        'bracket-match': (c[3], c[0] + '80'), 'search-match': (None, c[3] + '55'),
        'def:comment': (c[8], None), 'def:shebang': (c[8], None), 'def:doc-comment-element': (c[8], None),
        'def:keyword': (c[5], None), 'def:statement': (c[5], None), 'def:builtin': (c[6], None),
        'def:string': (c[2], None), 'def:special-char': (c[6], None), 'def:character': (c[2], None),
        'def:number': (c[3], None), 'def:floating-point': (c[3], None), 'def:decimal': (c[3], None),
        'def:base-n-integer': (c[3], None), 'def:boolean': (c[3], None), 'def:constant': (c[3], None),
        'def:function': (c[4], None), 'def:identifier': (fg, None), 'def:type': (c[6], None),
        'def:preprocessor': (c[1], None), 'def:operator': (c[6], None), 'def:variable': (c[1], None),
        'def:error': (c[1], None), 'def:note': (c[3], None), 'def:underlined': (c[4], None),
        'def:heading': (c[4], None), 'def:emphasis': (c[5], None), 'def:strong-emphasis': (c[3], None),
        'def:link-text': (c[4], None), 'def:net-address': (c[4], None),
    }
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<style-scheme id="hop" name="Hop (theme)" version="1.0" kind="dark">']
    for name, (f, b) in styles.items():
        attrs = (f' foreground="{f}"' if f else '') + (f' background="{b}"' if b else '')
        bold = ' bold="true"' if name in ('def:keyword', 'def:heading', 'def:strong-emphasis') else ''
        italic = ' italic="true"' if name == 'def:comment' else ''
        lines.append(f'  <style name="{escape(name)}"{attrs}{bold}{italic}/>')
    lines.append('</style-scheme>')
    d = os.path.expanduser('~/.local/share/gtksourceview-5/styles')
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'hop.xml'), 'w') as fh:
        fh.write('\n'.join(lines))
    mgr = GtkSource.StyleSchemeManager.get_default()
    if d not in mgr.get_search_path():
        mgr.prepend_search_path(d)
    mgr.force_rescan()
    return 'hop'


class EditorPage(Gtk.Box):
    def __init__(self, win, alias, gfile, path):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.alias, self.file, self.path = win, alias, gfile, path
        self.page, self.etag, self.loaded = None, None, False
        self.name = os.path.basename(path)

        bar = Gtk.Box(spacing=8, margin_top=6, margin_bottom=6, margin_start=12, margin_end=8)
        bar.append(Gtk.Image(icon_name='document-edit-symbolic', css_classes=['dim-label']))
        bar.append(Gtk.Label(label=f'{alias}:{path}', xalign=0, hexpand=True, css_classes=['monospace', 'dim-label'],
                             ellipsize=Pango.EllipsizeMode.START))
        self.pos = Gtk.Label(css_classes=['caption', 'dim-label', 'monospace'])
        bar.append(self.pos)
        self.lang_lbl = Gtk.Label(css_classes=['hop-chip'])
        bar.append(self.lang_lbl)
        wrap = Gtk.ToggleButton(icon_name='format-justify-left-symbolic', tooltip_text='ตัดบรรทัด',
                                css_classes=['flat'])
        bar.append(wrap)
        reload_btn = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='โหลดใหม่จากเซิร์ฟเวอร์',
                                css_classes=['flat'])
        reload_btn.connect('clicked', lambda *_: self.load())
        bar.append(reload_btn)
        self.save_btn = Gtk.Button(label='บันทึก', tooltip_text='Ctrl+S', css_classes=['suggested-action'],
                                   sensitive=False)
        self.save_btn.connect('clicked', lambda *_: self.save())
        bar.append(self.save_btn)
        self.append(bar)
        self.append(Gtk.Separator())

        self.buf = GtkSource.Buffer()
        scheme = GtkSource.StyleSchemeManager.get_default().get_scheme(install_scheme())
        if scheme:
            self.buf.set_style_scheme(scheme)
        self.view = GtkSource.View(buffer=self.buf, monospace=True, show_line_numbers=True,
                                   highlight_current_line=True, auto_indent=True, tab_width=4,
                                   insert_spaces_instead_of_tabs=True, smart_backspace=True,
                                   top_margin=8, bottom_margin=40, left_margin=8)
        css = Gtk.CssProvider()
        desc = Pango.FontDescription.from_string(FONT)
        css.load_from_data(f'textview {{ font-family: "{desc.get_family()}"; font-size: 11pt; }}'.encode())
        self.view.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        wrap.connect('toggled', lambda b: self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if b.get_active()
                                                                    else Gtk.WrapMode.NONE))
        self.buf.connect('modified-changed', self.on_modified)
        self.buf.connect('notify::cursor-position', self.on_cursor)
        sw = Gtk.ScrolledWindow(child=self.view, vexpand=True)
        self.append(sw)

        keys = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
        keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string('<Control>s'),
                                       action=Gtk.CallbackAction.new(lambda *_: (self.save(), True)[1])))
        self.add_controller(keys)
        self.load()

    def set_title(self):
        if self.page:
            self.page.set_title(('● ' if self.buf.get_modified() else '') + self.name)

    def on_modified(self, *_):
        self.save_btn.set_sensitive(self.buf.get_modified())
        self.set_title()

    def on_cursor(self, *_):
        it = self.buf.get_iter_at_offset(self.buf.props.cursor_position)
        self.pos.set_label(f'Ln {it.get_line() + 1}, Col {it.get_line_offset() + 1}')

    def load(self):
        def done(f, res):
            try:
                ok, data, etag = f.load_contents_finish(res)
            except GLib.Error as e:
                self.win.toast(f'เปิด {self.name} ไม่ได้: {e.message}')
                return
            try:
                text = data.decode('utf-8')
            except UnicodeDecodeError:
                self.win.toast(f'{self.name} ไม่ใช่ไฟล์ข้อความ (UTF-8)')
                return
            self.etag = etag
            ct = Gio.content_type_guess(self.name, data[:4096])[0]
            lang = GtkSource.LanguageManager.get_default().guess_language(self.name, ct)
            self.buf.set_language(lang)
            self.lang_lbl.set_label(lang.get_name() if lang else 'Text')
            self.buf.begin_irreversible_action()
            self.buf.set_text(text)
            self.buf.end_irreversible_action()
            self.buf.set_modified(False)
            self.buf.place_cursor(self.buf.get_start_iter())
            self.loaded = True
            self.view.grab_focus()
        self.file.load_contents_async(None, done)

    def save(self):
        if not self.loaded:
            return
        text = self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), True)
        self.save_btn.set_sensitive(False)

        def done(f, res):
            try:
                _, self.etag = f.replace_contents_finish(res)
            except GLib.Error as e:
                self.save_btn.set_sensitive(True)
                self.win.toast(f'บันทึกไม่ได้: {e.message}')
                return
            self.buf.set_modified(False)
            self.win.toast(f'บันทึก {self.name} แล้ว ✓')
        self.file.replace_contents_bytes_async(GLib.Bytes.new(text.encode('utf-8')), None, False,
                                               Gio.FileCreateFlags.NONE, None, done)

    def confirm_close(self, then):
        """Calls then(True) when it's fine to close."""
        if not self.buf.get_modified():
            then(True)
            return
        d = Adw.AlertDialog(heading=f'บันทึก {self.name} ก่อนปิดมั้ย?', body='ถ้าไม่บันทึก ที่แก้ไว้จะหายไป')
        d.add_response('cancel', 'ยกเลิก')
        d.add_response('discard', 'ไม่บันทึก')
        d.add_response('save', 'บันทึก')
        d.set_response_appearance('discard', Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_response_appearance('save', Adw.ResponseAppearance.SUGGESTED)

        def done(dlg, res):
            r = dlg.choose_finish(res)
            if r == 'save':
                self.save()
                then(True)
            else:
                then(r == 'discard')
        d.choose(self.win, None, done)

    def shutdown(self):
        pass
