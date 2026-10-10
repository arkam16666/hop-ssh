#!/usr/bin/env bash
# Removes Hop. Your servers stay in ~/.ssh/config; Hop's own settings in ~/.config/hop are kept
# unless you pass --purge.
set -eu
APP_ID="io.github.hopssh.Hop"
rm -rf "$HOME/.local/share/hop" "$HOME/.local/share/fonts/hop"
rm -f "$HOME/.local/bin/hop" "$HOME/.local/bin/hop-gui" \
      "$HOME/.local/share/applications/$APP_ID.desktop" \
      "$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg" \
      "$HOME/.local/share/gtksourceview-5/styles/hop.xml"
[ "${1:-}" = "--purge" ] && rm -rf "$HOME/.config/hop" "$HOME/.cache/hop"
echo "Hop removed. Servers in ~/.ssh/config were not touched."
