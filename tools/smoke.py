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
real_check = w.check
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
# required companions (Questie needs QuestieDB): installed along with the addon, never over an existing folder, and
# flagged by the update check when missing
def fake_zip(path, folder, ver="1.0"):
    with zipfile.ZipFile(path, "w") as f:
        f.writestr(f"{folder}/{folder}.toc", f"## Interface: 16001\n## Title: {folder}\n## Version: {ver}\n")
MODS = {10: ("Needy", [11, 12]), 11: ("Helper", []), 12: ("Bundled", [])}
def fake_mod(i):
    name, deps = MODS[i]
    return {"id": i, "name": name, "slug": name.lower(), "links": {"websiteUrl": "https://example.invalid/" + name}, "allowModDistribution": True,
            "latestFilesIndexes": [{"fileId": i * 100, "releaseType": 1, "gameVersionTypeId": w.CF_FOREVER, "filename": name + "-1.0.zip"}],
            "latestFiles": [{"id": i * 100, "dependencies": [{"modId": d, "relationType": 3} for d in deps] + [{"modId": 99, "relationType": 1}]}]}
def fake_cf(path, body=None, full=False):
    if path == "/mods": return [fake_mod(i) for i in body["modIds"] if i in MODS]
    parts = path.strip("/").split("/")
    if len(parts) == 2: return fake_mod(int(parts[1]))
    m = fake_mod(int(parts[1])); return {**m["latestFiles"][0], "downloadUrl": "fake://" + m["name"]}
real_cf, real_download, real_key = w.cf, w.download, w.cf_key
w.cf, w.cf_key = fake_cf, lambda: "test-key"
w.download = lambda url, dest: fake_zip(dest, url.split("//")[1])
(addons / "Bundled").mkdir(); (addons / "Bundled/Bundled.toc").write_text("## Interface: 16001\n## Title: Bundled\n## Version: 0.1\n")
w.install("cf:10")
st = json.loads(w.STATE.read_text())["addons"]
assert "cf:10" in st and "cf:11" in st, "the required companion was not installed along with the addon"
assert "cf:12" not in st and "0.1" in (addons / "Bundled/Bundled.toc").read_text(), "a companion that is already present must be left alone"
assert w.WITH.get("cf:10") == ["Helper"], w.WITH
w.remove("cf:11")
real_check()
assert [n["name"] for n in w.LIVE["cf:10"].get("needs", [])] == ["Helper"], "the update check must flag a missing companion"
w.install("cf:11")
assert not w.LIVE["cf:10"].get("needs"), "installing the companion clears the flag"
for k in ("cf:10", "cf:11"): w.remove(k)
shutil.rmtree(addons / "Bundled")
w.cf, w.download, w.cf_key = real_cf, real_download, real_key
w.LIVE.clear(); w.WITH.clear()

# background checks: back-off after failures, nothing lost when the network is down or a source refuses, no overlap
import time, urllib.error, urllib.request
A = w.AUTO
A["last"] = time.time()
assert 1700 < w.auto_wait() <= 1800, w.auto_wait()                     # 30 minutes by default
A["fails"] = 1; assert 3500 < w.auto_wait() <= 3600
A["fails"] = 6; assert 7100 < w.auto_wait() <= 7200                    # capped at two hours
A["fails"] = 0
w.CF_KEY.write_text("test-key")
st = json.loads(w.STATE.read_text()); st["addons"]["cf:1"] = st["addons"].pop(next(iter(st["addons"]))); w.STATE.write_text(json.dumps(st))
w.LIVE["cf:1"] = {"latest": "Demo-2.0", "latest_id": "2", "page": "", "update": True, "restricted": False, "error": ""}
real_open = urllib.request.urlopen
w.check = real_check                                        # the real check, against a network that fails
def offline(*a, **k): raise urllib.error.URLError("network is unreachable")
urllib.request.urlopen = offline
r = w.auto_once(); assert r is False and A["fails"] == 1, ("an unreachable source must count as a failed check", r, A["fails"], dict(w.NOTE), dict(w.LIVE), list(json.loads(w.STATE.read_text())["addons"]))
assert w.LIVE["cf:1"]["update"] is True and w.NOTE.get("net"), "a failed check must keep what was known"
def blocked(*a, **k): raise urllib.error.HTTPError("https://api.curseforge.com/v1/mods", 403, "Forbidden", {}, None)
urllib.request.urlopen = blocked
assert w.auto_once() is False and A["fails"] == 2 and w.LIVE["cf:1"]["update"] is True, "a refused request must keep what was known"
urllib.request.urlopen = real_open
def boom(): raise RuntimeError("anything at all")
w.check = boom
assert w.auto_once() is False and A["fails"] == 3, "a crash inside a check must be contained"
w.check = lambda: w.view()                                              # no network in the rest of the test
w.NOTE.clear()
assert w.auto_once() == ["Demo"] and A["fails"] == 0, "a good check reports the new update and resets the back-off"
assert w.auto_once() == [], "the same update is announced once"
with w.WORK:
    assert w.auto_once() is None, "no background check while addons are being changed"
st = json.loads(w.STATE.read_text()); st["auto_check"] = 0; w.STATE.write_text(json.dumps(st))
assert w.auto_wait() is None, "off means off"
st["auto_check"] = 30; w.STATE.write_text(json.dumps(st))
w.LIVE.clear()

try:
    w.main()
finally:
    shutil.rmtree(box, ignore_errors=True)
