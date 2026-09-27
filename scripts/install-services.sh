#!/usr/bin/env bash
#
# Install autostart + auto-restart for this app and for qBittorrent (flatpak).
#
#   bash scripts/install-services.sh
#
# Both are installed as systemd user services. If your desktop does not
# expose graphical-session.target (older Plasma/GNOME setups), qBittorrent
# is installed as an XDG autostart entry instead, which still starts at
# login but cannot restart after a crash.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"
TEMPLATE_REPO="%h/qbittorrent-remote-download"

say() { printf '%s\n' "$*"; }

if [ ! -x "$REPO_DIR/.venv/bin/python" ]; then
    say "No Python virtualenv found in $REPO_DIR."
    say ""
    say "Create it first:"
    say "  cd $REPO_DIR"
    say "  python3 -m venv .venv"
    say "  .venv/bin/pip install -r requirements.txt"
    exit 1
fi

mkdir -p "$UNIT_DIR"

# --- this app ---------------------------------------------------------------
sed "s|$TEMPLATE_REPO|$REPO_DIR|g" "$REPO_DIR/scripts/qbt-remote.service" \
    > "$UNIT_DIR/qbt-remote.service"
systemctl --user daemon-reload
systemctl --user enable --now qbt-remote.service
say "Installed and started qbt-remote.service (restarts automatically)."

# --- qBittorrent ------------------------------------------------------------
if systemctl --user is-active --quiet graphical-session.target; then
    cp "$REPO_DIR/scripts/qbittorrent.service" "$UNIT_DIR/qbittorrent.service"
    systemctl --user daemon-reload
    systemctl --user enable --now qbittorrent.service
    rm -f "$AUTOSTART_DIR/org.qbittorrent.qBittorrent.desktop"
    say "Installed and started qbittorrent.service (restarts automatically)."
else
    mkdir -p "$AUTOSTART_DIR"
    cp "$REPO_DIR/scripts/qbittorrent-autostart.desktop" "$AUTOSTART_DIR/"
    say "Installed XDG autostart for qBittorrent (starts at login)."
    say "Your desktop does not expose graphical-session.target, so qBittorrent"
    say "cannot be restarted automatically after a crash on this setup."
fi

say ""
say "Check it with:   systemctl --user status qbt-remote"
say "Logs:            journalctl --user -u qbt-remote -f"
say ""
say "If this machine should keep running without a login, enable auto-login"
say "in the KDE login screen settings so both start after a reboot."
