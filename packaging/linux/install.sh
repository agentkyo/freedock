#!/usr/bin/env bash
# FreeDock installer for Linux (Arch/CachyOS, Fedora, Debian/Ubuntu, ...).
# Installs into ~/.local/share/freedock/venv, adds the udev rule (asks for sudo once),
# a menu entry and optionally autostart.  Re-run to update.
set -euo pipefail

REPO="https://github.com/agentkyo/freedock"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$HERE/../.." 2>/dev/null && pwd || true)"
PREFIX="$HOME/.local/share/freedock"
VENV="$PREFIX/venv"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
PYV=$(python3 -c 'import sys; print(sys.version_info >= (3, 11))')
[ "$PYV" = "True" ] || { echo "Python 3.11+ is required"; exit 1; }

if command -v pacman >/dev/null; then
    say "Arch/CachyOS detected - installing system libraries (hidapi, libusb)"
    sudo pacman -S --needed --noconfirm hidapi libusb python-pip >/dev/null || true
elif command -v apt-get >/dev/null; then
    sudo apt-get install -y libhidapi-hidraw0 libusb-1.0-0 python3-venv >/dev/null || true
elif command -v dnf >/dev/null; then
    sudo dnf install -y hidapi libusb1 >/dev/null || true
fi

say "Creating virtualenv in $VENV"
mkdir -p "$PREFIX"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
if [ -f "$SRC/pyproject.toml" ]; then
    "$VENV/bin/pip" install -q "$SRC"
else
    "$VENV/bin/pip" install -q "git+$REPO"
fi

say "Installing udev rule (needs sudo)"
RULES="$HERE/70-freedock.rules"
[ -f "$RULES" ] || { RULES="$(mktemp)"; curl -fsSL "$REPO/raw/main/packaging/linux/70-freedock.rules" -o "$RULES"; }
sudo install -m 644 "$RULES" /etc/udev/rules.d/70-freedock.rules
echo uinput | sudo tee /etc/modules-load.d/freedock-uinput.conf >/dev/null
sudo modprobe uinput || true
sudo udevadm control --reload-rules
sudo udevadm trigger

say "Adding the launcher"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
ln -sf "$VENV/bin/freedock" "$HOME/.local/bin/freedock"
DESKTOP="$HERE/freedock.desktop"
if [ -f "$DESKTOP" ]; then
    sed "s|^Exec=freedock|Exec=$VENV/bin/freedock|" "$DESKTOP" > "$HOME/.local/share/applications/freedock.desktop"
fi

read -r -p "Start FreeDock with your session (tray icon)? [Y/n] " yn
if [[ ! "$yn" =~ ^[Nn] ]]; then
    mkdir -p "$HOME/.config/autostart"
    sed "s|^Exec=freedock|Exec=$VENV/bin/freedock --minimized|" "$DESKTOP" > "$HOME/.config/autostart/freedock.desktop"
fi

say "Done!  Unplug and re-plug the N4, then run:  freedock"
say "Headless instead of the tray app:  systemctl --user enable --now freedock  (see packaging/linux/freedock.service)"
