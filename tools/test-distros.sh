#!/bin/sh
# Installs and smoke-tests the app on each supported distribution family, in throwaway containers.
#   tools/test-distros.sh              every family
#   tools/test-distros.sh fedora arch  only those
# Each container gets only Python plus the libraries install.sh itself suggests for that family, so this also proves
# those suggestions are complete. Then, as an ordinary user: ./install.sh, the built-in checks, and tools/smoke.py
# (the real window without a screen). Needs docker or podman. Nothing is installed on this computer.
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
RUN=$(command -v docker || command -v podman) || { echo "This needs docker or podman"; exit 2; }
CACHE=${XDG_CACHE_HOME:-$HOME/.cache}/linuxforever-addon-shelf-tests
mkdir -p "$CACHE/pip" "$CACHE/logs"
# EXTRA="pkg ..." adds packages on top of the suggested set, to try out a candidate fix for a failing family.
libs() { printf "%s " "${EXTRA:-}"; sed -n "s/.*echo \"  $1[^:]*: sudo [a-z-]* [a-zS-]* //p" "$HERE/install.sh" | tr -d '"' | tail -1; }

family() {  # name -> image | command that installs Python and the suggested libraries
    case $1 in
        debian)   echo "debian:stable-slim|apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv $(libs 'Debian 13+')" ;;
        ubuntu)   echo "ubuntu:24.04|apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv $(libs 'Debian 13+')" ;;
        fedora)   echo "fedora:latest|dnf install -y -q python3 $(libs Fedora)" ;;
        arch)     echo "archlinux:latest|pacman -Syu --noconfirm --needed python $(libs Arch)" ;;
        opensuse) echo "opensuse/tumbleweed:latest|zypper -n -q install python3 $(libs openSUSE)" ;;
        *)        echo "" ;;
    esac
}

# Inside the container: copy the app, then drop to an ordinary user (the same user id as yours, so the shared download
# cache stays yours) for everything the installer and the app do.
INSIDE='set -e
python3 - <<PY
import os, pwd, shutil, subprocess, sys
uid, gid = int(os.environ["TUID"]), int(os.environ["TGID"])
shutil.copytree("/src", "/home/t/app", ignore=shutil.ignore_patterns("raw", "big", ".git", "__pycache__"))
try:
    pwd.getpwuid(uid)
except KeyError:
    open("/etc/passwd", "a").write("t:x:%d:%d::/home/t:/bin/sh\\n" % (uid, gid))
for d, _, fs in os.walk("/home/t"):
    os.chown(d, uid, gid); [os.chown(os.path.join(d, f), uid, gid) for f in fs]
os.setgroups([]); os.setgid(gid); os.setuid(uid)
env = {**os.environ, "HOME": "/home/t", "USER": "t", "PIP_CACHE_DIR": "/pipcache", "QTWEBENGINE_DISABLE_SANDBOX": "1"}
steps = [("install", "cd ~/app && ./install.sh"),
         ("built-in checks", "~/.local/bin/linuxforever-addon-shelf --selftest"),
         ("window, typing, dialogs, install", "cd ~/app && ~/.local/share/linuxforever-addon-shelf/venv/bin/python tools/smoke.py")]
for name, cmd in steps:
    r = subprocess.run(["sh", "-c", cmd], env=env, cwd="/home/t")
    print("STEP", name, "ok" if r.returncode == 0 else "FAILED (%d)" % r.returncode, flush=True)
    if r.returncode: sys.exit(1)
PY'

[ $# -gt 0 ] || set -- debian ubuntu fedora arch opensuse
fail=0
for f in "$@"; do
    spec=$(family "$f"); [ -n "$spec" ] || { echo "$f: unknown family"; fail=1; continue; }
    image=${spec%%|*}; prep=${spec#*|}
    log="$CACHE/logs/$f.log"
    if timeout "${LIMIT:-480}" "$RUN" run --rm --shm-size=1g -e TUID="$(id -u)" -e TGID="$(id -g)" -v "$HERE:/src:ro" -v "$CACHE/pip:/pipcache" "$image" \
        sh -c "mkdir -p /home/t && ($prep) >/tmp/prep.log 2>&1 || { tail -20 /tmp/prep.log; exit 3; }; $INSIDE" >"$log" 2>&1
    then echo "$f: PASS  ($(grep -c '^STEP .* ok$' "$log") of 3 steps; $image)"
    elif [ $? -eq 124 ]; then echo "$f: STALLED  (no result within ${LIMIT:-480} s, usually a slow package mirror; run it again)"; "$RUN" ps -q --filter "ancestor=$image" | xargs -r "$RUN" kill >/dev/null 2>&1; fail=1
    else echo "$f: FAIL  (see $log)"; grep -E '^STEP|smoke:|rror' "$log" | tail -6 | sed 's/^/    /'; fail=1
    fi
done
exit $fail
