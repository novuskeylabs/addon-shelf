#!/usr/bin/env python3
"""Addon manager for the WoW Forever client. Stdlib only.
Run `wowaddons` for the window, `wowaddons --selftest` for the built-in checks."""
import json, os, re, shutil, subprocess, sys, tempfile, threading, time, urllib.error, urllib.parse, urllib.request, zipfile, secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path.home()
CONF = HOME / ".config/wowaddons"
CACHE = HOME / ".cache/wowaddons"
BACKUPS = HOME / ".local/share/wowaddons/backups"
STATE = CONF / "state.json"
CF_KEY = CONF / "curseforge.key"
HERE = Path(__file__).resolve().parent
UA = "wowaddons/1.0 (personal addon manager)"
CF_FOREVER = 88568          # CurseForge game-version type for the Forever client
MAX_ZIP = 2 << 30
# Where the addons installed before this tool existed came from (folder -> source key).
SEED = {"DialogueUI": "gh:Peterodox/YUI-Dialogue", "AllTheThings": "gh:ATTWoWAddon/AllTheThings",
        "Lueur": "cf:1732324", "OpulentCastingBars": "cf:1471957", "Auctionator": "cf:6124", "Disenchantry": "cf:1704637",
        "ForeverDungeonJournal": "cf:1707971", "ForeverLibrary": "cf:1721133", "Wowdee": "cf:1731877",
        "Mastercraft": "cf:1711528", "ForeverXPbar": "cf:1701700", "ForeverChallengeXP": "cf:1701700",
        "WaypointUI": "cf:1275146", "Attune": "cf:414434", "ForeverWisp": "cf:1698137", "EasyMarksForever": "cf:1723093",
        "DynamicCam": "cf:101120", "GearQuestForever": "cf:1698950", "Leatrix_Plus": "cf:94855", "OpenMap": "cf:1735937",
        "TalentsForeverBook": "cf:1700435", "WhatsTraining": "cf:324944"}
LOCK = threading.Lock()


_DIR = None


def addons_dir():
    """Found once (the search walks ~/Games), then remembered in state.json."""
    global _DIR
    if _DIR is None:
        st = load()
        if not st.get("addons_dir") or not Path(st["addons_dir"]).is_dir():
            r = subprocess.run(["find", str(HOME / "Games"), "-maxdepth", "8", "-type", "d", "-path", "*/_forever_/Interface/AddOns",
                                "-print", "-quit"], capture_output=True, text=True)
            if not r.stdout.strip():
                raise SystemExit("Could not find _forever_/Interface/AddOns under ~/Games")
            st["addons_dir"] = r.stdout.strip()
            save(st)
        _DIR = Path(st["addons_dir"])
    return _DIR


def load():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"addons": {}}


def save(st):
    CONF.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(STATE)


def http(url, headers=None, raw=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = r.read(MAX_ZIP + 1)          # never buffer more than the cap
    if len(data) > MAX_ZIP:
        raise RuntimeError("Download is unexpectedly large")
    return data if raw else json.loads(data)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower()).lstrip("v")


# ---------- local folders ----------
def toc(folder):
    out = {}
    d = addons_dir() / folder
    tocs = sorted(d.glob("*.toc"), key=lambda p: (p.stem != folder, len(p.name)))
    if tocs:
        for line in tocs[0].read_text(errors="ignore").splitlines():
            m = re.match(r"##\s*([^:]+?)\s*:\s*(.*)", line)
            if m:
                out.setdefault(m.group(1).lower(), re.sub(r"\|c[0-9a-fA-F]{8}|\|r", "", m.group(2)).strip())
    return out


def folder_size(names):
    total = 0
    for n in names:
        for root, _, files in os.walk(addons_dir() / n):
            total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
    return total


CATEGORIES = ["Quests & Leveling", "Map & Navigation", "Collections", "Professions", "Auctions & Economy", "Class & Talents",
              "Combat", "Dungeons & Raids", "Interface", "Other"]
# ponytail: keyword guess from the addon's own name/notes; the user can override per addon in its page
KEYWORDS = [("Auctions & Economy", "auction|econom|vendor|gold"), ("Professions", "profession|craft|disenchant|gather|recipe"),
            ("Map & Navigation", "map|waypoint|coord|navigat|pin"), ("Collections", "collect|transmog|mount|pet |achievement"),
            ("Class & Talents", "talent|class|spell|train"), ("Dungeons & Raids", "dungeon|raid|boss|journal"),
            ("Combat", "combat|cast|nameplate|damage|threat|target|mark"),
            ("Quests & Leveling", "quest|level|xp|experience|attun|gear|book|lore|library"),
            ("Interface", "ui|interface|dialog|camera|frame|chat|bag|tooltip|highlight|tweak|quality")]


def guess_category(t, name):
    declared = (t.get("category-enus") or t.get("category") or t.get("x-category") or "").lower()
    for text in (declared, f"{name} {t.get('title', '')}".lower(), t.get("notes", "").lower()):   # most reliable first
        for cat, pat in KEYWORDS:
            if text and re.search(pat, text):
                return cat
    return "Other"


def icon_png(key):
    """PNG bytes of the addon's own icon (the file its .toc names), or None. Needs Pillow; without it tiles show a monogram."""
    a = load()["addons"].get(key)
    if not a:
        return None
    out = CACHE / "icons" / (re.sub(r"[^\w.-]", "_", key) + ".png")
    src = None
    ref = toc(a["folders"][0]).get("icontexture", "").split("[")[0].strip().replace("\\", "/")
    m = re.match(r"interface/addons/(.+)", ref, re.I)
    if m:
        base = addons_dir()
        for part in m.group(1).split("/"):                      # WoW paths are case-insensitive, the disk is not
            names = {c.name.lower(): c for c in base.iterdir()} if base.is_dir() else {}
            base = names.get(part.lower()) or next((names[part.lower() + e] for e in (".blp", ".tga", ".png", ".jpg")
                                                    if part.lower() + e in names), base / "\0missing")
        src = base if base.is_file() and base.resolve().is_relative_to(addons_dir().resolve()) else None
    if not src:
        return None
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        try:
            from PIL import Image
            out.parent.mkdir(parents=True, exist_ok=True)
            im = Image.open(src).convert("RGBA")
            im.thumbnail((160, 160))
            im.save(out, "PNG")
        except Exception:
            return None
    return out.read_bytes()


def group_folders():
    """GearQuestForever_DRUID belongs to GearQuestForever: {parent: [folders]}"""
    names = sorted(p.name for p in addons_dir().iterdir() if p.is_dir())
    groups = {}
    for n in names:
        parent = next((p for p in names if p != n and n.startswith(p + "_")), n)
        groups.setdefault(parent, []).append(n)
    return groups


def sync():
    """Bring state in line with what is on disk; adopt known sources."""
    with LOCK:
        st, groups = load(), group_folders()
        for key in [k for k in st["addons"] if k.startswith("local:")]:       # link entries recorded before the source was known
            target = SEED.get(key[6:])
            if target:
                old = st["addons"].pop(key)
                if target in st["addons"]:
                    st["addons"][target]["folders"] = sorted(set(st["addons"][target]["folders"]) | set(old["folders"]))
                else:
                    st["addons"][target] = old
        owned = {f for a in st["addons"].values() for f in a["folders"]}
        for parent, folders in groups.items():
            if parent in owned:
                continue
            t = toc(parent)
            key = SEED.get(parent) or (t.get("x-curse-project-id") and "cf:" + t["x-curse-project-id"]) or "local:" + parent
            if key in st["addons"]:                                             # second folder of one project
                st["addons"][key]["folders"] = sorted(set(st["addons"][key]["folders"]) | set(folders))
            else:
                st["addons"][key] = {"name": t.get("title") or parent, "folders": folders, "version": t.get("version", "")}
        for key, a in list(st["addons"].items()):
            a["folders"] = [f for f in a["folders"] if (addons_dir() / f).is_dir()]
            if not a["folders"]:
                del st["addons"][key]
        save(st)
        return st


def wow_running():
    return subprocess.run(["pgrep", "-f", r"Wow\.exe"], capture_output=True).returncode == 0


# ---------- sources ----------
def cf_key():
    return CF_KEY.read_text().strip() if CF_KEY.exists() else ""


def cf(path):
    try:
        return http("https://api.curseforge.com/v1" + path, {"x-api-key": cf_key(), "Accept": "application/json"})["data"]
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise RuntimeError("CurseForge has not approved your key yet. Until it does, download the addon file from CurseForge and drop it on this window") from None
        raise


def gh(path):
    if shutil.which("gh"):
        r = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=40)
        if r.returncode == 0:
            return json.loads(r.stdout)
    return http("https://api.github.com/" + path, {"Accept": "application/vnd.github+json"})


def wowi_list():
    f = CACHE / "wowinterface.json"
    if not f.exists() or time.time() - f.stat().st_mtime > 6 * 3600:
        CACHE.mkdir(parents=True, exist_ok=True)
        f.write_bytes(http("https://api.mmoui.com/v3/game/WOW/filelist.json", raw=True))
    return json.loads(f.read_text())


def wowi_forever(a):
    compat = " ".join(f"{c.get('name')} {c.get('version')}" for c in a.get("UICompatibility") or [])
    return "forever" in (a.get("UIName", "") + compat).lower() or " 1.60" in compat


def latest(key):
    """-> {version, id, url, page} for the newest Forever-compatible file."""
    src, ref = key.split(":", 1)
    if src == "cf":
        if not cf_key():
            raise RuntimeError("Add a CurseForge key in Settings to check this addon")
        files = cf(f"/mods/{ref}/files?gameVersionTypeId={CF_FOREVER}&pageSize=1")
        if not files:
            raise RuntimeError("No Forever version published")
        f, mod = files[0], cf(f"/mods/{ref}")
        return {"version": f["displayName"], "id": str(f["id"]), "url": f.get("downloadUrl"),
                "page": mod["links"]["websiteUrl"], "name": mod["name"]}
    if src == "gh":
        rel = gh(f"repos/{ref}/releases/latest")
        zips = [a for a in rel.get("assets", []) if a["name"].lower().endswith(".zip")]
        # ponytail: first asset that is not flavour-specific; add a per-addon asset pattern if a repo needs it
        pick = next((a for a in zips if not re.search(r"classic|bcc|wrath|cata|mists|nolib", a["name"], re.I)), zips[0] if zips else None)
        return {"version": rel["tag_name"], "id": rel["tag_name"], "url": pick["browser_download_url"] if pick else rel["zipball_url"],
                "page": rel["html_url"], "name": ref.split("/")[1]}
    if src == "wowi":
        d = http(f"https://api.mmoui.com/v3/game/WOW/filedetails/{ref}.json")[0]
        return {"version": d["UIVersion"], "id": d["UIVersion"], "url": d["UIDownload"],
                "page": d.get("UIFileInfoURL", ""), "name": d["UIName"]}
    raise RuntimeError("This addon has no known source yet. Find it in the catalog to link it.")


def is_current(a, new):
    if a.get("remote_id"):
        return a["remote_id"] == new["id"]
    v = norm(a.get("version"))            # adopted addon: compare the .toc version with the file name
    return bool(v) and v in norm(new["version"])


def wowi_cats():
    f = CACHE / "wowinterface-cats.json"
    if not f.exists() or time.time() - f.stat().st_mtime > 7 * 86400:
        CACHE.mkdir(parents=True, exist_ok=True)
        f.write_bytes(http("https://api.mmoui.com/v3/game/WOW/categorylist.json", raw=True))
    return {c["UICATID"]: c["UICATTitle"] for c in json.loads(f.read_text())}


def epoch(iso):
    try:
        return int(time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone)
    except (TypeError, ValueError):
        return 0


# one catalog row per source, same shape everywhere
def row_cf(m):
    cats = m.get("categories") or []
    cat = next((c["name"] for c in cats if c.get("id") == m.get("primaryCategoryId")), cats[0]["name"] if cats else "Other")
    ver = next((f.get("filename", "") for f in m.get("latestFilesIndexes") or [] if f.get("gameVersionTypeId") == CF_FOREVER), "")
    return {"key": f"cf:{m['id']}", "source": "CurseForge", "name": m["name"], "summary": m.get("summary", ""),
            "author": ", ".join(a["name"] for a in m.get("authors", [])[:2]), "downloads": int(m.get("downloadCount") or 0),
            "thumb": (m.get("logo") or {}).get("thumbnailUrl", ""), "page": m["links"]["websiteUrl"], "category": cat,
            "updated": epoch(m.get("dateReleased") or m.get("dateModified")), "version": re.sub(r"\.zip$", "", ver),
            "shots": [x["url"] for x in m.get("screenshots") or []]}


def row_wowi(a):
    return {"key": f"wowi:{a['UID']}", "source": "WoWInterface", "name": a["UIName"], "summary": "",
            "author": a.get("UIAuthorName", ""), "downloads": int(a.get("UIDownloadTotal") or 0),
            "thumb": (a.get("UIIMG_Thumbs") or [""])[0], "page": a.get("UIFileInfoURL", ""),
            "category": wowi_cats().get(a.get("UICATID"), "Other"), "updated": int(a.get("UIDate") or 0) // 1000,
            "version": a.get("UIVersion", ""), "shots": a.get("UIIMGs") or []}


def row_gh(r):
    return {"key": f"gh:{r['full_name']}", "source": "GitHub", "name": r["name"], "summary": r.get("description") or "",
            "author": r["owner"]["login"], "downloads": r.get("stargazers_count", 0), "stars": True,
            "thumb": r["owner"].get("avatar_url", ""), "page": r["html_url"], "category": "GitHub projects",
            "updated": epoch(r.get("pushed_at")), "version": "", "shots": []}


def bbcode(text):
    """WoWInterface descriptions are BBCode; turn the common tags into HTML, drop the rest."""
    import html
    t = html.escape(text or "")
    for tag in "biu":
        t = re.sub(rf"\[{tag}\](.*?)\[/{tag}\]", rf"<{tag}>\1</{tag}>", t, flags=re.S | re.I)
    t = re.sub(r"\[url=(?:&quot;)?(https?://[^\]&]+)(?:&quot;)?\](.*?)\[/url\]", r'<a href="\1">\2</a>', t, flags=re.S | re.I)
    t = re.sub(r"\[img\](https?://[^\[]+)\[/img\]", r'<img src="\1">', t, flags=re.I)
    t = re.sub(r"\[\*\]", "• ", t)
    t = re.sub(r"\[/?[a-z]+(=[^\]]*)?\]", "", t, flags=re.I)
    return t.replace("\n", "<br>")


def details(key):
    src, ref = key.split(":", 1)
    if src == "cf":
        if not cf_key():
            raise RuntimeError("Add a CurseForge key in Settings to see this addon")
        r = row_cf(cf(f"/mods/{ref}"))
        r["html"] = cf(f"/mods/{ref}/description")
    elif src == "wowi":
        r = row_wowi(next(a for a in wowi_list() if a["UID"] == ref))
        r["html"] = bbcode(http(f"https://api.mmoui.com/v3/game/WOW/filedetails/{ref}.json")[0].get("UIDescription"))
    else:
        r = row_gh(gh("repos/" + ref))
        try:
            rel = gh(f"repos/{ref}/releases/latest")
            r["version"], r["updated"] = rel["tag_name"], epoch(rel.get("published_at")) or r["updated"]
        except Exception:
            pass
        out = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.html", f"repos/{ref}/readme"],
                             capture_output=True, text=True, timeout=40) if shutil.which("gh") else None
        r["html"] = out.stdout if out and out.returncode == 0 else ""
    r["changelog"] = ""
    try:
        if src == "cf":
            f = cf(f"/mods/{ref}/files?gameVersionTypeId={CF_FOREVER}&pageSize=1")
            r["changelog"] = cf(f"/mods/{ref}/files/{f[0]['id']}/changelog") if f else ""
        elif src == "wowi":
            r["changelog"] = bbcode(http(f"https://api.mmoui.com/v3/game/WOW/filedetails/{ref}.json")[0].get("UIChangeLog"))
        elif shutil.which("gh"):
            out = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.html+json", f"repos/{ref}/releases/latest",
                                  "-q", ".body_html"], capture_output=True, text=True, timeout=40)
            r["changelog"] = out.stdout if out.returncode == 0 else ""
    except Exception:
        pass                                        # release notes are a nicety; the details still show
    r["installed"] = key in load()["addons"]
    return r


def search(q):
    q, out, errors = q.strip(), [], []

    def run(name, fn):
        try:
            out.extend(fn())
        except Exception as e:                      # one source failing must not hide the others
            errors.append(f"{name}: {e}")

    def s_cf():
        if not cf_key():
            errors.append("CurseForge is not included yet: add your key in Settings")
            return []
        return [row_cf(m) for m in cf("/mods/search?gameId=1&sortField=2&sortOrder=desc&pageSize=50&gameVersionTypeId=%d&searchFilter=%s"
                                      % (CF_FOREVER, urllib.parse.quote(q)))]

    def s_wowi():
        items = [a for a in wowi_list() if wowi_forever(a) and q.lower() in a["UIName"].lower()]
        return [row_wowi(a) for a in items[:80]]

    def s_gh():
        if not q:
            return []
        m = re.search(r"github\.com/([\w.-]+/[\w.-]+)", q)
        repos = [gh("repos/" + m.group(1).removesuffix(".git"))] if m else gh(
            "search/repositories?per_page=12&q=" + urllib.parse.quote(q + " wow addon in:name,description,topics"))["items"]
        return [row_gh(r) for r in repos]

    threads = [threading.Thread(target=run, args=a) for a in (("CurseForge", s_cf), ("WoWInterface", s_wowi), ("GitHub", s_gh))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    have = set(load()["addons"])
    for r in out:
        r["installed"] = r["key"] in have
    return {"results": out, "errors": errors}


# ---------- install / remove / rollback ----------
def unpack(zpath, dest):
    """Extract into dest and return the addon folders (each has a .toc named after it)."""
    with zipfile.ZipFile(zpath) as z:
        root, members = dest.resolve(), z.infolist()
        if len(members) > 200_000 or sum(m.file_size for m in members) > 2 * MAX_ZIP:
            raise RuntimeError("Archive is unreasonably large when unpacked")
        for m in members:
            target = (dest / m.filename).resolve()
            if not target.is_relative_to(root) or m.external_attr >> 28 == 0xA:
                raise RuntimeError("Archive has an unsafe path: " + m.filename)
        z.extractall(dest)
    base = dest
    for _ in range(3):
        dirs = [d for d in base.iterdir() if d.is_dir()]
        good = [d for d in dirs if (d / (d.name + ".toc")).exists() or list(d.glob(d.name + "[_-]*.toc"))]
        if good:
            return good
        tocs = sorted(base.glob("*.toc"), key=lambda p: len(p.name))
        if tocs and base != dest:                       # GitHub source zip: repo-main/Addon.toc
            name = re.sub(r"[_-](Mainline|Classic|Vanilla|TBC|Wrath|Cata|Mists|Forever)$", "", tocs[0].stem, flags=re.I)
            return [base.rename(base.with_name(name))]
        if len(dirs) != 1:
            break
        base = dirs[0]
    raise RuntimeError("No addon folders found in the download")


def backup(key, folders):
    slot = BACKUPS / re.sub(r"[^\w.-]", "_", key) / time.strftime("%Y%m%d-%H%M%S")
    slot.mkdir(parents=True)
    for f in folders:
        if (addons_dir() / f).exists():
            shutil.move(addons_dir() / f, slot / f)
    old = sorted(slot.parent.iterdir())[:-2]            # keep the two newest
    [shutil.rmtree(o) for o in old]
    return slot


def install(key):
    if wow_running():
        raise RuntimeError("Close World of Warcraft first: it overwrites addon settings on exit")
    new = latest(key)
    if not new["url"]:
        raise RuntimeError("The author does not allow downloads outside CurseForge. Get it from " + new["page"])
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
        zpath = Path(tmp) / "a.zip"
        data = http(new["url"], raw=True)
        zpath.write_bytes(data)
        found = unpack(zpath, Path(tmp) / "x")
        names = [d.name for d in found]
        with LOCK:
            st = load()
            # an addon installed by hand earlier is adopted: same folders, new owner
            clash = [k for k, a in st["addons"].items() if k != key and set(a["folders"]) & set(names)]
            prev = st["addons"].get(key, {}).get("folders", [])
            backup(key, sorted(set(prev) | set(names) | {f for k in clash for f in st["addons"][k]["folders"]}))
            for k in clash:
                del st["addons"][k]
            for d in found:
                shutil.move(d, addons_dir() / d.name)
            t = toc(names[0])
            st["addons"][key] = {"name": t.get("title") or new["name"], "folders": sorted(names),
                                 "version": t.get("version") or new["version"], "remote_id": new["id"], "page": new["page"],
                                 "latest": new["version"], "update": False}
            save(st)
    return st["addons"][key]


def downloads():
    zips = sorted((HOME / "Downloads").glob("*.zip"), key=lambda p: -p.stat().st_mtime)[:12]
    return [{"name": z.name, "path": str(z), "when": int(z.stat().st_mtime), "size": z.stat().st_size} for z in zips]


def install_file(path):
    """Install a zip from the Downloads folder (picked in Settings or offered as a new download)."""
    z = Path(path).resolve()
    if z.parent != (HOME / "Downloads").resolve() or z.suffix != ".zip":
        raise RuntimeError("Only .zip files in your Downloads folder can be installed this way")
    return install_zip(z)


def install_upload(name, data):
    """Install a zip dropped onto the window."""
    if not name.lower().endswith(".zip"):
        raise RuntimeError("Only .zip files can be installed")
    up = CACHE / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    z = up / re.sub(r"[^\w.-]", "_", Path(name).name)
    z.write_bytes(data)
    try:
        return install_zip(z)
    finally:
        z.unlink(missing_ok=True)


def install_zip(z):
    """Unpack a local zip; an addon already on the shelf keeps its source and gets a backup."""
    if wow_running():
        raise RuntimeError("Close World of Warcraft first: it overwrites addon settings on exit")
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
        found = unpack(z, Path(tmp) / "x")
        names = sorted(d.name for d in found)
        with LOCK:
            st = load()
            owners = [k for k, a in st["addons"].items() if set(a["folders"]) & set(names)]
            key = owners[0] if owners else "local:" + min(names, key=len)
            backup(key, sorted(set(names) | {f for k in owners for f in st["addons"][k]["folders"]}))
            old = st["addons"].get(key, {})
            for k in owners:
                del st["addons"][k]
            for d in found:
                shutil.move(d, addons_dir() / d.name)
            t = toc(min(names, key=len))
            st["addons"][key] = {**old, "name": t.get("title") or names[0], "folders": names, "version": t.get("version", ""),
                                 "update": False}
            st["addons"][key].pop("remote_id", None)
            save(st)
    return st["addons"][key]["name"]


def remove(key):
    if wow_running():
        raise RuntimeError("Close World of Warcraft first")
    with LOCK:
        st = load()
        backup(key, st["addons"][key]["folders"])
        del st["addons"][key]
        save(st)


def rollback(key):
    if wow_running():
        raise RuntimeError("Close World of Warcraft first")
    slots = sorted((BACKUPS / re.sub(r"[^\w.-]", "_", key)).glob("*"))
    if not slots:
        raise RuntimeError("No earlier version is saved for this addon")
    with LOCK:
        st, slot = load(), slots[-1]
        names = [d.name for d in slot.iterdir()]
        for f in set(names) | set(st["addons"].get(key, {}).get("folders", [])):
            shutil.rmtree(addons_dir() / f, ignore_errors=True)
        for d in list(slot.iterdir()):
            shutil.move(d, addons_dir() / d.name)
        slot.rmdir()
        t = toc(names[0])
        st["addons"][key] = {"name": t.get("title") or names[0], "folders": sorted(names), "version": t.get("version", ""),
                             "update": False}
        save(st)


def check():
    st = sync()

    def one(key, a):
        try:
            new = latest(key)
            a.update(latest=new["version"], page=new["page"], update=not is_current(a, new), error="")
        except Exception as e:
            a.update(update=False, error=str(e))

    ts = [threading.Thread(target=one, args=kv) for kv in st["addons"].items() if not kv[0].startswith("local:")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    with LOCK:
        save(st)
    return view(st)


def view(st=None):
    st = st or sync()
    rows = [{"key": k, "source": {"cf": "CurseForge", "gh": "GitHub", "wowi": "WoWInterface", "local": "No source"}[k.split(":")[0]],
             "size": folder_size(a["folders"]), "notes": toc(a["folders"][0]).get("notes", ""),
             "category": a.get("category") or guess_category(toc(a["folders"][0]), a["name"]), "icon": bool(icon_png(k)),
             "can_rollback": any((BACKUPS / re.sub(r"[^\w.-]", "_", k)).glob("*")), **a} for k, a in st["addons"].items()]
    rows.sort(key=lambda r: r["name"].lower())
    fresh = [d for d in downloads() if time.time() - d["when"] < 1800]
    return {"addons": rows, "wow_running": wow_running(), "has_cf_key": bool(cf_key()), "addons_dir": str(addons_dir()),
            "new_downloads": fresh, "categories": CATEGORIES}


# ---------- window ----------
class H(BaseHTTPRequestHandler):
    token, last = "", time.time()

    def log_message(self, *a):
        pass

    def send(self, obj, code=200, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_api(self, body):
        H.last = time.time()
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        # other websites in the browser must not be able to drive this server
        if self.headers.get("Host", "").split(":")[0] != "127.0.0.1" or self.headers.get("X-Token") != H.token:
            return self.send({"error": "forbidden"}, 403)
        try:
            route = u.path.removeprefix("/api/")
            if route == "state":
                r = view()
            elif route == "check":
                r = check()
            elif route == "search":
                r = search(q.get("q", [""])[0])
            elif route == "details":
                r = details(q["key"][0])
            elif route == "downloads":
                r = {"files": downloads()}
            elif route == "install_file":
                name = install_file(body["path"])
                r = {**view(), "installed": name}
            elif route == "upload":
                name = install_upload(body["name"], body["data"])
                r = {**view(), "installed": name}
            elif route == "category":
                if body["category"] not in CATEGORIES:
                    raise RuntimeError("Unknown category")
                with LOCK:
                    st = load()
                    st["addons"][body["key"]]["category"] = body["category"]
                    save(st)
                r = view()
            elif route == "ping":
                r = {}
            elif route == "key":
                if not body["key"].strip().isascii() or " " in body["key"].strip():
                    raise RuntimeError("That looks like the hidden (dotted) version of the key. Use the copy button next to it on console.curseforge.com, then paste again")
                CONF.mkdir(parents=True, exist_ok=True)
                CF_KEY.touch(mode=0o600)
                CF_KEY.write_text(body["key"].strip())
                r = {}
            elif route in ("install", "remove", "rollback"):
                {"install": install, "remove": remove, "rollback": rollback}[route](body["key"])
                r = view()
            elif route == "update_all":
                failed = {}
                for k, a in load()["addons"].items():
                    if a.get("update"):
                        try:
                            install(k)
                        except Exception as e:
                            failed[a["name"]] = str(e)
                r = {**view(), "failed": failed}
            else:
                return self.send({"error": "unknown"}, 404)
            self.send(r)
        except Exception as e:
            self.send({"error": str(e)}, 400)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self.handle_api({})
        u = urllib.parse.urlparse(self.path)
        if u.path == "/icon":                                   # <img> cannot send headers, so the token rides in the query
            q = urllib.parse.parse_qs(u.query)
            if q.get("t", [""])[0] != H.token or self.headers.get("Host", "").split(":")[0] != "127.0.0.1":
                return self.send(b"", 403, "text/plain")
            png = icon_png(q.get("key", [""])[0])
            return self.send(png, ctype="image/png") if png else self.send(b"", 404, "text/plain")
        if self.path.split("?")[0] == "/":
            page = (HERE / "index.html").read_text().replace("__TOKEN__", H.token)
            return self.send(page.encode(), ctype="text/html; charset=utf-8")
        self.send(b"", 404, "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_ZIP:
            return self.send({"error": "That file is too large"}, 413)
        raw = self.rfile.read(n)
        if self.path == "/api/upload":
            return self.handle_api({"name": urllib.parse.unquote(self.headers.get("X-Name", "")), "data": raw})
        self.handle_api(json.loads(raw or b"{}"))


def serve():
    H.token = secrets.token_urlsafe(24)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    browser = shutil.which("google-chrome-stable") or shutil.which("chromium")
    subprocess.Popen([browser, f"--app={url}", "--class=wowaddons"] if browser else ["xdg-open", url],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Addon manager at", url)
    while time.time() - H.last < 180:      # the page pings every 30 s; exit after the window closes
        time.sleep(5)


def selftest():
    assert norm("v1.0.7-b") == norm("1.0.7 b") == "107b"
    assert is_current({"version": "1.0.7 b"}, {"id": "x", "version": "DialogueUI-1.0.7-b.zip"})
    assert not is_current({"version": "1.4.0"}, {"id": "x", "version": "Lueur-1.7.0.zip"})
    assert not is_current({"remote_id": "1"}, {"id": "2", "version": ""})
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        for name, members in {"plain": ["Foo/Foo.toc", "Foo_Opt/Foo_Opt.toc"], "wrapped": ["rel/Foo/Foo.toc"],
                              "source": ["Foo-main/Foo.toc", "Foo-main/a.lua"], "flavour": ["Foo/Foo_Vanilla.toc"]}.items():
            with zipfile.ZipFile(t / f"{name}.zip", "w") as z:
                [z.writestr(m, "") for m in members]
            got = sorted(d.name for d in unpack(t / f"{name}.zip", t / name))
            assert got == (["Foo", "Foo_Opt"] if name == "plain" else ["Foo"]), (name, got)
        with zipfile.ZipFile(t / "evil.zip", "w") as z:
            z.writestr("../../evil.toc", "")
        try:
            unpack(t / "evil.zip", t / "evil")
            raise AssertionError("path traversal accepted")
        except RuntimeError:
            pass
    assert bbcode('[b]Hi[/b] <x>\n[url="https://a.b/c"]go[/url][size=3]z[/size]') == '<b>Hi</b> &lt;x&gt;<br><a href="https://a.b/c">go</a>z'
    assert epoch("1970-01-02T00:00:00Z") == 86400
    assert guess_category({"category": "Quests"}, "Attune") == "Quests & Leveling"
    assert guess_category({"notes": "Auction house helper"}, "X") == "Auctions & Economy"
    assert guess_category({}, "Zzz") == "Other"
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else serve()
