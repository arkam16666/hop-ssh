#!/usr/bin/env bash
# Hop installer — everything goes into your home directory, no sudo needed.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SHARE="$HOME/.local/share/hop"
BIN="$HOME/.local/bin"
TYPELIBS="$HOME/.local/lib/girepository-1.0"
FONTS="$HOME/.local/share/fonts/hop"
APP_ID="io.github.hopssh.Hop"

say() { printf '\033[1;35m✿\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }

# ── requirements ───────────────────────────────────────────────────────────
python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1")' 2>/dev/null || {
  warn "Needs GTK4 + libadwaita for Python:  sudo apt install python3-gi gir1.2-adw-1"; exit 1; }
command -v ssh >/dev/null || { warn "Needs OpenSSH client:  sudo apt install openssh-client"; exit 1; }
[ -x /usr/libexec/gvfsd-sftp ] || warn "File transfer needs gvfs SFTP:  sudo apt install gvfs-backends"
command -v fzf >/dev/null || warn "The terminal picker (hop) needs fzf:  sudo apt install fzf"

# GTK4 VTE (terminal) and GtkSourceView 5 (editor) Python bindings.
# Ubuntu ships the libraries but not always the bindings; fetch the matching .deb without sudo.
need_typelib() {  # namespace version package
  GI_TYPELIB_PATH="$TYPELIBS" python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('$1','$2'); from gi.repository import $1" 2>/dev/null && return
  say "Fetching $3 (Python binding for $1)…"
  if command -v apt-get >/dev/null; then
    tmp=$(mktemp -d); (cd "$tmp" && apt-get download "$3" >/dev/null 2>&1 && dpkg-deb -x ./*.deb x) || true
    mkdir -p "$TYPELIBS"
    find "$tmp/x" -name "$1-$2.typelib" -exec cp {} "$TYPELIBS/" \; 2>/dev/null || true
    rm -rf "$tmp"
  fi
  GI_TYPELIB_PATH="$TYPELIBS" python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('$1','$2')" 2>/dev/null \
    || warn "Couldn't get $1 $2 — install it with:  sudo apt install $3"
}
need_typelib Vte 3.91 gir1.2-vte-3.91
need_typelib GtkSource 5 gir1.2-gtksource-5

# ── files ──────────────────────────────────────────────────────────────────
say "Installing to $SHARE"
mkdir -p "$SHARE" "$BIN" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
cp "$HERE"/src/hop/*.py "$SHARE/"
install -m755 "$HERE/bin/hop" "$HERE/bin/hop-gui" "$BIN/"
cp "$HERE/data/hop.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg"
sed "s|@BIN@|$BIN|g; s|@APP_ID@|$APP_ID|g" "$HERE/data/hop.desktop.in" > "$HOME/.local/share/applications/$APP_ID.desktop"
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true

# ── fonts (Nunito + Mali for the UI, JetBrainsMono Nerd Font for terminal/icons) ──
fetch_font() {  # file url
  [ -f "$FONTS/$1" ] || curl -fsSL -o "$FONTS/$1" "$2" || warn "Couldn't download font $1"
}
mkdir -p "$FONTS"
say "Fetching fonts…"
fetch_font 'Nunito[wght].ttf' 'https://github.com/google/fonts/raw/main/ofl/nunito/Nunito%5Bwght%5D.ttf'
for w in Regular Medium SemiBold Bold; do
  fetch_font "Mali-$w.ttf" "https://github.com/google/fonts/raw/main/ofl/mali/Mali-$w.ttf"
done
if ! fc-list | grep -q 'JetBrainsMono Nerd Font'; then
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/jb.tar.xz" https://github.com/ryanoasis/nerd-fonts/releases/latest/download/JetBrainsMono.tar.xz \
    && tar -xJf "$tmp/jb.tar.xz" -C "$FONTS" --wildcards '*.ttf' || warn "Couldn't download JetBrainsMono Nerd Font"
  rm -rf "$tmp"
fi
fc-cache -f "$FONTS" >/dev/null 2>&1 || true

case ":$PATH:" in *":$BIN:"*) ;; *) warn "Add $BIN to your PATH to use the 'hop' command";; esac
say "Done! Open “Hop” from your app menu, or run:  hop  (terminal)  ·  hop gui  (window)"
