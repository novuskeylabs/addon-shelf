#!/usr/bin/env python3
"""Smoke test: runs the real app without a screen, in throwaway folders, and checks that the window's page loads,
the search box takes typing, the close dialog works and an addon installs. Used by tools/test-distros.sh on every
distribution family, and runnable on its own:  python3 tools/smoke.py   (exit code 0 = all checks passed)"""
import importlib.util, json, os, shutil, sys, tempfile, zipfile
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
box = Path(tempfile.mkdtemp(prefix="shelf-smoke-"))
src = (APP / "wowaddons.py").read_text()
TAIL = "    sys.exit(app.exec())\n"
PROFILE = 'QWebEngineProfile("wowaddons", app)'            # the test must not share the installed app's browser profile
assert src.count(TAIL) == 1 and PROFILE in src, "wowaddons.py changed shape: update tools/smoke.py"
HOOK = '''
    from PySide6.QtTest import QTest
    from PySide6.QtCore import QPoint
    ok, want = {}, ["page", "typing", "dialog", "setting", "close"]
    js = lambda code, cb: view.page().runJavaScript(code, cb)
    def t1():
        js("JSON.stringify({tiles:document.querySelectorAll('#tiles .tile').length,fn:[typeof draw,typeof find,typeof askClose].join()})", t2)
    def t2(r):
        d = json.loads(r or "{}")
        ok["page"] = d.get("tiles") == 1 and d.get("fn") == "function,function,function"
        ok["page"] or print("smoke page probe:", d, flush=True)
        js("show('cat');(r=>r.left+40+','+(r.top+r.height/2))(document.querySelector('#q').getBoundingClientRect())", t3)
    def t3(r):
        x, y = [int(float(v)) for v in r.split(",")]
        QTest.mouseClick(view.focusProxy() or view, Qt.LeftButton, Qt.NoModifier, QPoint(x, y))
        QTimer.singleShot(500, lambda: (QTest.keyClicks(app.focusWidget() or view, "abc"),
                                        QTimer.singleShot(500, lambda: js("document.querySelector('#q').value+'|'+document.activeElement.id", t4))))
    tries = []
    def t4(r):
        if r != "abc|q" and len(tries) < 12:            # a slow machine may still be delivering the keys
            tries.append(r)
            return QTimer.singleShot(500, lambda: js("document.querySelector('#q').value+'|'+document.activeElement.id", t4))
        ok["typing"] = r == "abc|q"
        ok["typing"] or print("smoke typing probe:", repr(r), "| focus widget:", type(app.focusWidget()).__name__,
                              "| window active:", view.isActiveWindow(), "| proxy:", type(view.focusProxy()).__name__, flush=True)
        js("askClose();const o=document.querySelector('#closedlg').open;document.querySelector('#closedlg [data-c=cancel]').click();o&&!document.querySelector('#closedlg').open", t5)
    def t5(r):
        ok["dialog"] = r is True
        js("api('on_close',{value:'tray'}).then(()=>1);1", lambda _: QTimer.singleShot(1200, t6))
    def t6():
        ok["setting"] = json.loads(STATE.read_text()).get("on_close") == "tray"
        app.aboutToQuit.connect(lambda: ok.__setitem__("close", True))
        view.close()                                    # no tray without a screen: closing must end the app
    QTimer.singleShot(4000, t1)
    QTimer.singleShot(45000, leave)
    app.exec()
    bad = [k for k in want if not ok.get(k)]
    print("smoke:", "PASS" if not bad else "FAIL " + ",".join(bad), flush=True)
    os._exit(1 if bad else 0)
'''
(box / "wa.py").write_text(src.replace(PROFILE, "QWebEngineProfile(app)").replace(TAIL, HOOK))
spec = importlib.util.spec_from_file_location("wa", box / "wa.py")
w = importlib.util.module_from_spec(spec); sys.modules["wa"] = w; spec.loader.exec_module(w)
w.HERE = APP
addons = box / "AddOns"; addons.mkdir()
w.HOME, w.CONF, w.CACHE, w.BACKUPS, w.SETTINGS = box, box / "conf", box / "cache", box / "bk", box / "set"
w.STATE, w.CF_KEY = w.CONF / "state.json", w.CONF / "curseforge.key"
w.CONF.mkdir()
w.STATE.write_text(json.dumps({"addons_dir": str(addons), "addons": {}})); w._DIR = addons
w.check = lambda: w.view()                                  # no network in the test
w.wow_running = lambda: False

z = box / "Demo.zip"                                        # backend: a Forever addon installs, a non-Forever one is refused
with zipfile.ZipFile(z, "w") as f:
    f.writestr("Demo/Demo.toc", "## Interface: 16001\n## Title: Demo (Retail & Forever)\n## Version: 1.0\n")
assert w.install_zip(z) and (addons / "Demo/Demo.toc").exists(), "install failed"
assert [a["name"] for a in w.view()["addons"]] == ["Demo"], "client list was not trimmed from the name"
with zipfile.ZipFile(z, "w") as f:
    f.writestr("Old/Old.toc", "## Interface: 120001\n## Title: Old\n")
try:
    w.install_zip(z); raise SystemExit("smoke: FAIL a non-Forever addon was installed")
except RuntimeError:
    pass
try:
    w.main()
finally:
    shutil.rmtree(box, ignore_errors=True)
