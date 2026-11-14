#!/bin/sh
# LinuxForever Addon Shelf: set up everything the app needs in a private environment.
# Nothing is installed system-wide and no root access is used, so it cannot conflict with your distribution's packages.
#   ./install.sh              install or repair
#   ./install.sh --uninstall  remove the private environment, the launcher and the menu entry (your addons are untouched)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
ENV="$DATA/linuxforever-addon-shelf/venv"
BIN="$HOME/.local/bin"
LAUNCHER="$BIN/linuxforever-addon-shelf"
ENTRY="$DATA/applications/linuxforever-addon-shelf.desktop"

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$DATA/linuxforever-addon-shelf" "$LAUNCHER" "$ENTRY"
    echo "Removed. Your addons, settings and the app folder ($HERE) were left alone."
    exit 0
fi

# 1. a Python new enough to run the app (3.9+), with the venv module
PY=
for c in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        PY=$c; break
    fi
done
if [ -z "$PY" ]; then
    echo "Python 3.9 or newer is needed and was not found. Install it with your package manager, for example:"
    echo "  Debian/Ubuntu/Mint: sudo apt install python3 python3-venv     Fedora: sudo dnf install python3"
    echo "  Arch/Manjaro: sudo pacman -S python                          openSUSE: sudo zypper install python3"
    exit 1
fi
if ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then
    echo "This Python cannot create private environments yet. On Debian, Ubuntu and Mint run:  sudo apt install python3-venv"
    echo "Then run ./install.sh again."
    exit 1
fi

# 2. the private environment, with the window toolkit (Qt) and the image library inside it
echo "Setting up a private environment in $ENV (about 600 MB, one time)..."
mkdir -p "$DATA/linuxforever-addon-shelf"
[ -x "$ENV/bin/python" ] || "$PY" -m venv "$ENV"
"$ENV/bin/python" -m pip install --quiet --upgrade pip
"$ENV/bin/python" -m pip install --quiet --upgrade "PySide6>=6.6" Pillow

# 3. does the window toolkit load? If not, say exactly which system libraries are missing.
if ! QT_QPA_PLATFORM=offscreen "$ENV/bin/python" -c 'from PySide6.QtWebEngineWidgets import QWebEngineView' 2>/tmp/lfas-import.$$; then
    echo
    echo "The window toolkit was installed but cannot start because some system libraries are missing:"
    QTLIB=$("$ENV/bin/python" -c 'import PySide6, os; print(os.path.join(os.path.dirname(PySide6.__file__), "Qt", "lib"))')
    for lib in libQt6WebEngineCore.so.6 libQt6Gui.so.6 libQt6Widgets.so.6; do
        [ -f "$QTLIB/$lib" ] && ldd "$QTLIB/$lib" 2>/dev/null | sed -n 's/^[[:space:]]*\([^ ]*\) => not found.*/  \1/p'
    done | sort -u
    tail -n 1 /tmp/lfas-import.$$ | sed 's/^/  /'
    rm -f /tmp/lfas-import.$$
    echo "Install them with your package manager, then run ./install.sh again. Usual package sets:"
    echo "  Debian 13+/Ubuntu 24.04+/Mint 22+: sudo apt install libnss3 libxkbcommon0 libegl1 libgl1 libfontconfig1 libdbus-1-3 libasound2t64 libglib2.0-0t64 libgssapi-krb5-2 libx11-6 libxext6 libxfixes3 libxcomposite1 libxdamage1 libxrandr2 libxtst6 libxkbfile1 libxcb-cursor0"
    echo "  Older Debian/Ubuntu/Mint: the same list with libasound2 and libglib2.0-0 in place of the two names ending in t64"
    echo "  Fedora: sudo dnf install nss libxkbcommon mesa-libEGL mesa-libGL fontconfig dbus-libs alsa-lib glib2 krb5-libs libX11 libXext libXfixes libXcomposite libXdamage libXrandr libXtst libxkbfile xcb-util-cursor"
    echo "  Arch/Manjaro: sudo pacman -S nss libxkbcommon libglvnd mesa fontconfig ttf-dejavu dbus alsa-lib glib2 krb5 libx11 libxext libxfixes libxcomposite libxdamage libxrandr libxtst libxkbfile xcb-util-cursor"
    echo "  openSUSE: sudo zypper install mozilla-nss libxkbcommon0 Mesa-libEGL1 Mesa-libGL1 fontconfig dejavu-fonts libdbus-1-3 libasound2 libglib-2_0-0 libgthread-2_0-0 krb5 libX11-6 libXext6 libXfixes3 libXcomposite1 libXdamage1 libXrandr2 libXtst6 libxkbfile1 libxcb-cursor0"
    exit 1
fi
rm -f /tmp/lfas-import.$$

# 4. a launcher and a menu entry
mkdir -p "$BIN" "$DATA/applications"
cat > "$LAUNCHER" <<LAUNCH
#!/bin/sh
exec "$ENV/bin/python" "$HERE/wowaddons.py" "\$@"
LAUNCH
chmod +x "$LAUNCHER"
cat > "$ENTRY" <<DESKTOP
[Desktop Entry]
Type=Application
Name=LinuxForever Addon Shelf
Comment=Manage World of Warcraft: Forever addons
Exec=$LAUNCHER
Icon=$HERE/icon.png
Categories=Game;Utility;
StartupWMClass=wowaddons
Terminal=false
DESKTOP
"$ENV/bin/python" "$HERE/wowaddons.py" --selftest >/dev/null && echo "Built-in checks passed."
echo "Done. Start it from your application menu (LinuxForever Addon Shelf) or run: $LAUNCHER"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: $BIN is not on your PATH, so use the menu entry or the full path above." ;; esac
command -v git >/dev/null 2>&1 || echo "Optional: install git to let the app update itself from Settings."
