#!/usr/bin/env bash
# Installs the launcher, desktop entry and icon for the current user.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP=io.github.felsenuboot.YubiOath
mkdir -p ~/.local/bin ~/.local/share/applications ~/.local/share/icons/hicolor/scalable/apps
ln -sf "$HERE/bin/yubioath-gtk" ~/.local/bin/yubioath-gtk
install -m644 "$HERE/data/$APP.desktop" ~/.local/share/applications/
install -m644 "$HERE/yubioath_gtk/icons/hicolor/scalable/apps/$APP.svg" ~/.local/share/icons/hicolor/scalable/apps/
gtk4-update-icon-cache -q ~/.local/share/icons/hicolor 2>/dev/null || true
update-desktop-database -q ~/.local/share/applications 2>/dev/null || true
echo "Installed. Run 'yubioath-gtk' or launch YubiOath from your app launcher."
