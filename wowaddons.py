#!/usr/bin/env python3
"""Addon manager for the WoW Forever client. The logic is stdlib only; the window is PySide6 (Qt WebEngine).
Run `wowaddons` for the application, `wowaddons --selftest` for the built-in checks."""
import calendar, fcntl, json, os, re, socket, zlib, shutil, subprocess, sys, tempfile, threading, time, urllib.error, urllib.parse, urllib.request, zipfile, secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STARTED = time.time()
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
SRC_ART = {}        # key -> the source's logo address for that addon (session only)
SRC_CAT = {}        # key -> the source's own category for that addon (session only)
LIVE = {}           # key -> {latest, latest_id, page, update, error, restricted}; never written to disk
RESTART = None      # set by the application window: restarts the app (used after a new key is saved)
NOTE = {}           # "net": why the last update check could not reach the sources
FETCH = None        # set by the application window: FETCH(url) -> Path of the file CurseForge's download page delivered


_DIR = None


class GameMissing(RuntimeError):
    """World of Warcraft: Forever (or its AddOns folder) cannot be found."""


def addons_dir():
    """The Forever client's AddOns folder. Found once (the search walks ~/Games), remembered in state.json, and
    looked for again if it disappears (game moved, reinstalled or uninstalled)."""
    global _DIR
    if _DIR is not None and _DIR.is_dir():
        return _DIR
    _DIR = None
    st = load()
    if st.get("addons_dir") and Path(st["addons_dir"]).is_dir():
        _DIR = Path(st["addons_dir"])
        return _DIR
    r = subprocess.run(["find", str(HOME / "Games"), "-maxdepth", "7", "-type", "d", "-name", "_forever_", "-print", "-quit"],
                       capture_output=True, text=True)
    if not r.stdout.strip():
        raise GameMissing("World of Warcraft: Forever was not found on this computer. Install it, or point the app at its "
                          "Interface/AddOns folder in Settings")
    found = Path(r.stdout.strip()) / "Interface" / "AddOns"
    found.mkdir(parents=True, exist_ok=True)            # a fresh game install has no AddOns folder yet
    st["addons_dir"] = str(found)
    save(st)
    _DIR = found
    return _DIR


def set_addons_dir(path):
    """Point the app at an AddOns folder chosen by the user."""
    global _DIR
    p = Path(path.strip()).expanduser()
    if not p.is_dir() or p.name.lower() != "addons":
        raise RuntimeError("That is not an AddOns folder. It should end in .../_forever_/Interface/AddOns")
    with LOCK:
        st = load()
        st["addons_dir"] = str(p)
        save(st)
    _DIR = p
    _MEMO.clear()


def load():
    for f in (STATE, STATE.with_suffix(".bak")):          # the previous good copy is kept beside it
        try:
            st = json.loads(f.read_text())
            if isinstance(st, dict) and isinstance(st.get("addons"), dict):
                return st
        except (OSError, ValueError):
            continue
    return {"addons": {}}


def save(st):
    CONF.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    try:
        json.loads(STATE.read_text())                     # only a readable file is worth keeping as the fallback
        shutil.copy2(STATE, STATE.with_suffix(".bak"))
    except (OSError, ValueError):
        pass
    tmp.replace(STATE)


SITES = {"api.curseforge.com": "CurseForge", "edge.forgecdn.net": "CurseForge", "mediafilez.forgecdn.net": "CurseForge",
         "api.mmoui.com": "WoWInterface", "cdn.wowinterface.com": "WoWInterface", "api.github.com": "GitHub", "github.com": "GitHub"}


class Unreachable(RuntimeError):
    """No connection, or the source did not answer."""


class Refused(RuntimeError):
    """The source answered with an error."""
    def __init__(self, code, site):
        self.code = code
        super().__init__({404: f"{site} no longer has that", 429: f"{site} is limiting requests. Try again in a minute"}.get(
            code, f"{site} is having problems (error {code}). Try again later" if code >= 500 else f"{site} refused the request (error {code})"))


def reach(req, timeout):
    """urlopen with failures turned into messages a person can act on."""
    site = SITES.get(urllib.parse.urlparse(req.full_url).hostname, urllib.parse.urlparse(req.full_url).hostname)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise Refused(e.code, site) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError):
        raise Unreachable(f"No internet connection, or {site} cannot be reached") from None


def http(url, headers=None, raw=False, body=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with reach(req, 15) as r:
            data = r.read(MAX_ZIP + 1)          # never buffer more than the cap
    except (socket.timeout, TimeoutError, ConnectionError):
        raise Unreachable("The connection dropped while reading the answer") from None
    if len(data) > MAX_ZIP:
        raise RuntimeError("Download is unexpectedly large")
    try:
        return data if raw else json.loads(data)
    except ValueError:
        raise RuntimeError("The source sent an answer that could not be read. Try again later") from None


def download(url, path):
    """Stream a file to disk (addons can be hundreds of MB) and refuse anything over the cap."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    size = 0
    try:
        with reach(req, 60) as r, open(path, "wb") as out:
            while chunk := r.read(1 << 20):
                size += len(chunk)
                if size > MAX_ZIP:
                    raise RuntimeError("Download is unexpectedly large")
                out.write(chunk)
    except (socket.timeout, TimeoutError, ConnectionError):
        raise Unreachable("The connection dropped during the download. Nothing was changed; try again") from None


def primary(a):
    """The folder that names the addon (Bagnon, not BagBrother or Bagnon_Bank)."""
    return min(a["folders"], key=len)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower()).lstrip("v")


# ---------- local folders ----------
_MEMO = {}


def stamp(folder):
    """Changes whenever the addon folder or its .toc is replaced."""
    d = addons_dir() / folder
    try:
        return (d.stat().st_mtime_ns, max((t.stat().st_mtime_ns for t in d.glob("*.toc")), default=0))
    except OSError:
        return None


def memo(kind, folder, fn):
    key, now = (kind, folder), stamp(folder)
    if _MEMO.get(key, (None,))[0] != now:
        _MEMO[key] = (now, fn())
    return _MEMO[key][1]


def toc(folder):
    return memo("toc", folder, lambda: read_toc(folder))


def slurp(p):
    """A .toc's text, or "" if it cannot be read (a folder, no permission, damaged)."""
    try:
        return p.read_text(encoding="utf-8-sig", errors="ignore") if p.is_file() else ""
    except OSError:
        return ""


def damage(a):
    """Why an installed addon looks broken, or "" if it looks fine."""
    for f in a["folders"]:
        d = addons_dir() / f
        tocs = [t for t in d.glob("*.toc")] if d.is_dir() else []
        if not tocs:
            return f"The folder {f} has no description (.toc) file, so the game cannot load it"
        if not any(re.search(r"(?mi)^##\s*Interface\s*:", slurp(t)) for t in tocs):
            return f"The description (.toc) file in {f} is empty or unreadable, so the game cannot load it"
    return ""


def read_toc(folder):
    out = {}
    d = addons_dir() / folder
    def rank(p):                                    # the Forever-specific .toc describes this client best
        forever = bool(re.search(r"[_-]forever$", p.stem, re.I))
        txt = slurp(p)
        m = re.search(r"(?mi)^##\s*Interface\s*:\s*(.*)$", txt)
        return (not forever, not (m and re.search(r"\b160\d\d\b", m.group(1))), p.stem != folder, len(p.name))
    tocs = sorted(d.glob("*.toc"), key=rank)
    if tocs:
        for line in slurp(tocs[0]).splitlines():
            m = re.match(r"##\s*([^:]+?)\s*:\s*(.*)", line)
            if m:
                out.setdefault(m.group(1).lower(), re.sub(r"\s+", " ", re.sub(r"\|c[0-9a-fA-F]{8}|\|r|\|T[^|]*\|t", "", m.group(2)).replace("|n", " ")).strip())
    if out.get("title"):                            # "Questie v12.6.5" -> "Questie": the version has its own field
        out["title"] = re.sub(r"\s+v?\d+(\.\d+)+\S*$", "", out["title"]).strip() or out["title"]
    return out


def forever_ok(path):
    """Does this addon folder declare support for the Forever client (interface 160xx, or a Forever .toc)?"""
    for t in Path(path).glob("*.toc"):
        m = re.search(r"(?mi)^##\s*Interface\s*:\s*(.*)$", slurp(t))
        if (m and re.search(r"\b160\d\d\b", m.group(1))) or re.search(r"[_-]forever\.toc$", t.name, re.I):
            return True
    return False


def folder_size(names):
    def walk(n):
        total = 0
        for root, _, files in os.walk(addons_dir() / n):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass                                # a dangling link or unreadable file
        return total
    return sum(memo("size", n, lambda n=n: walk(n)) for n in names)


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
    ref = toc(primary(a)).get("icontexture", "").split("[")[0].strip().replace("\\", "/")
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
    try:
        stale = not out.exists() or out.stat().st_mtime < src.stat().st_mtime
    except OSError:
        return None
    if stale:
        try:
            from PIL import Image
            out.parent.mkdir(parents=True, exist_ok=True)
            if src.stat().st_size > 8 << 20:                    # an icon, not an arbitrary image
                return None
            Image.MAX_IMAGE_PIXELS = 4096 * 4096
            im = Image.open(src, formats=["BLP", "TGA", "PNG", "JPEG"]).convert("RGBA")
            im.thumbnail((160, 160))
            im.save(out, "PNG")
        except Exception:
            return None
    try:
        return out.read_bytes()
    except OSError:
        return None


# Which part of the game an addon takes over. Two installed addons that take over the same part can fight each other.
# (label, pattern for the addon's name/folders, pattern for its description or None when the description is too loose a signal)
JOBS = [("manage bags and inventory", r"\bbags?\b|bagnon|inventory", r"\bbags?\b|inventory (window|frame|addon)"),
        ("automate looting", r"\bloot", r"auto[- ]?loot|faster loot|loot(s|ing)? (faster|instantly|automatically)"),
        ("replace the action bars", r"action ?bars?|bartender|dominos", r"action ?bars?"),
        ("change cast bars", r"cast(ing)? ?bars?", r"cast(ing)? ?bars?"),
        ("restyle unit frames", r"unit ?frames?|blizzframes|\bouf\b", r"unit ?frames?|player ?frame|target ?frame"),
        ("restyle nameplates", r"name ?plates?|blizzplates", r"name ?plates?"),
        ("move or resize the default interface", r"move ?any|\bmover\b", r"move (and|or|,) ?(re)?(size|scale)|reposition (any|blizzard|default)"),
        ("put quest markers on the map", r"questie|quest ?helper", r"quest helper|available quests? on (the |your )?(world ?)?map"),
        ("replace the world map", r"\bmaps?\b", None), ("add waypoints", r"waypoint|tomtom", None),
        ("scan for rares", r"rare", None), ("replace the auction house window", r"auction", None),
        ("show an experience bar", r"\bxp ?bar|experience ?bar", r"\bxp bar|experience bar"),
        ("control the camera", r"\bcam(era)?\b|dynamiccam", None), ("restyle dialogue and quest text", r"dialog(ue)?", r"dialog(ue)? (ui|window|frame)|quest (text|dialog)"),
        ("show a damage meter", r"details|recount|skada|damage ?meter", r"damage ?meter"), ("give boss warnings", r"\bdbm\b|deadly ?boss|bigwigs", None),
        ("bundle many interface tweaks", r"leatrix[_ ]?plus|all in (1|one)", None),
        ("change tooltips", r"tooltip", None), ("change chat", r"\bchat", None), ("change the minimap", r"minimap", None)]


# Jobs that touch the same frames even though they are named differently: (job, job, how to say it).
RELATED = [("move or resize the default interface", "replace the action bars", "one moves the default action bars, the other replaces them"),
           ("move or resize the default interface", "restyle unit frames", "one moves the default unit frames, the other restyles them"),
           ("move or resize the default interface", "change cast bars", "one moves the default cast bar, the other changes it"),
           ("bundle many interface tweaks", "automate looting", "the tweak bundle has its own faster-looting option")]


def jobs_of(a):
    def find():
        t = toc(primary(a))
        names = " ".join([a["name"], t.get("title", ""), *a["folders"]]).lower()
        notes = t.get("notes", "").lower()
        deps = " ".join(t.get(k, "") for k in ("dependencies", "requireddeps", "optionaldeps", "loadwith")).lower()
        return ({label for label, pn, pd in JOBS if re.search(pn, names) or (pd and re.search(pd, notes))},
                {d.strip() for d in deps.split(",") if d.strip()})
    return memo("jobs", primary(a), find)


def conflicts(addons):
    """Pairs of installed addons that appear to do the same job: [{a, b, why}]. A companion addon is not a conflict."""
    info = {k: jobs_of(a) for k, a in addons.items()}
    out = []
    keys = sorted(addons, key=lambda k: addons[k]["name"].lower())
    for i, x in enumerate(keys):
        for y in keys[i + 1:]:
            why = ["both " + j for j in sorted(info[x][0] & info[y][0])]
            why += [say for p, q, say in RELATED if (p in info[x][0] and q in info[y][0]) or (p in info[y][0] and q in info[x][0])]
            fx, fy = {f.lower() for f in addons[x]["folders"]}, {f.lower() for f in addons[y]["folders"]}
            if why and not (info[x][1] & fy) and not (info[y][1] & fx):
                out.append({"a": x, "b": y, "why": why})
    return out


def shelf_categories():
    """Category names for the installed view: CurseForge's own when it is connected, otherwise the built-in guesses."""
    if not cf_key() or _MEMO.get("cats_down", 0) > time.time():
        return CATEGORIES
    try:
        return [c["name"] for c in categories() if c["id"] != "wowi"] + ["Other"]
    except Exception:
        _MEMO["cats_down"] = time.time() + 120          # offline or refused: use the built-in names for two minutes
        return CATEGORIES


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
        before = json.dumps(st, sort_keys=True)
        for key in [k for k in st["addons"] if k.startswith("local:")]:       # link entries recorded before the source was known
            pid = toc(key[6:]).get("x-curse-project-id", "") if (addons_dir() / key[6:]).is_dir() else ""
            target = SEED.get(key[6:]) or (pid.isdigit() and "cf:" + pid)
            if target:
                old = st["addons"].pop(key)
                if target in st["addons"]:
                    st["addons"][target]["folders"] = sorted(set(st["addons"][target]["folders"]) | set(old["folders"]))
                else:
                    st["addons"][target] = old
        owned = {f for a in st["addons"].values() for f in a["folders"]}
        for parent, folders in groups.items():
            if parent in owned or not memo("forever", parent, lambda p=parent: forever_ok(addons_dir() / p)):
                continue
            t = toc(parent)
            key = SEED.get(parent) or (t.get("x-curse-project-id") and "cf:" + t["x-curse-project-id"]) or "local:" + parent
            if key in st["addons"]:                                             # second folder of one project
                st["addons"][key]["folders"] = sorted(set(st["addons"][key]["folders"]) | set(folders))
            else:
                st["addons"][key] = {"name": t.get("title") or parent, "folders": folders, "version": t.get("version", "")}
        for key, a in list(st["addons"].items()):
            for stale in ("latest", "page", "update", "error"):                 # source data lives in memory only
                a.pop(stale, None)
            a["folders"] = [f for f in a["folders"] if (addons_dir() / f).is_dir()]
            if a["folders"]:                                                    # names and versions follow the files on disk
                t = toc(primary(a))
                a["name"], a["version"] = t.get("title") or a["name"], t.get("version") or a.get("version", "")
            if not a["folders"]:
                del st["addons"][key]
        if json.dumps(st, sort_keys=True) != before:
            save(st)
        return st


def wow_running():
    return subprocess.run(["pgrep", "-f", r"Wow\.exe"], capture_output=True).returncode == 0


# ---------- sources ----------
def cf_key():
    return CF_KEY.read_text().strip() if CF_KEY.exists() else ""


def cf(path, body=None, full=False):
    try:
        r = http("https://api.curseforge.com/v1" + path, {"x-api-key": cf_key(), "Accept": "application/json",
                                                           "Content-Type": "application/json"}, body=body)
        return r if full else r["data"]
    except Refused as e:
        if e.code == 403:
            raise RuntimeError("CurseForge refused the saved key (mistyped, or not approved yet). Check it in Settings") from None
        raise


def gh(path):
    if shutil.which("gh"):
        try:
            r = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=20)
            if r.returncode == 0:
                return json.loads(r.stdout)
        except (subprocess.TimeoutExpired, ValueError):
            pass
    return http("https://api.github.com/" + path, {"Accept": "application/vnd.github+json"})


def wowi_list():
    return memo_time("wowi", 6 * 3600, wowi_fetch)


def memo_time(name, ttl, fn):
    hit = _MEMO.get(name)
    if not hit or time.time() - hit[0] > ttl:
        _MEMO[name] = hit = (time.time(), fn())
    return hit[1]


def wowi_fetch():
    f = CACHE / "wowinterface.json"
    if not f.exists() or time.time() - f.stat().st_mtime > 6 * 3600:
        try:
            data = http("https://api.mmoui.com/v3/game/WOW/filelist.json", raw=True)
            json.loads(data)
            CACHE.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        except RuntimeError:
            if not f.exists():
                raise                                   # offline with nothing saved
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
        new = cf_newest(cf(f"/mods/{ref}"))             # the same choice the update check makes
        return {**new, "url": cf(f"/mods/{ref}/files/{new['id']}").get("downloadUrl")}
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


def cf_pick(mod):
    """The project's newest Forever file: a full release when there is one, otherwise the newest beta or alpha."""
    idx = [f for f in mod.get("latestFilesIndexes") or [] if f.get("gameVersionTypeId") == CF_FOREVER]
    return max([f for f in idx if f.get("releaseType") == 1] or idx, key=lambda f: f["fileId"], default=None)


def cf_newest(mod):
    """Newest Forever file of a project from the data a search or batch lookup already carries."""
    f = cf_pick(mod)
    if not f:
        raise RuntimeError("No Forever version published")
    return {"version": re.sub(r"\.zip$", "", f["filename"]), "id": str(f["fileId"]), "page": mod["links"]["websiteUrl"],
            "name": mod["name"], "restricted": mod.get("allowModDistribution") is False, "category": cf_category(mod),
            "thumb": (mod.get("logo") or {}).get("thumbnailUrl", ""), "released": epoch(mod.get("dateReleased"))}


def is_current(a, new):
    if a.get("remote_id"):
        if a["remote_id"].isdigit() and new["id"].isdigit():        # file numbers only go up: never offer an older file
            return int(a["remote_id"]) >= int(new["id"])
        return a["remote_id"] == new["id"]
    v = norm(a.get("version"))            # adopted addon: compare the .toc version with the file name
    if v and v in norm(new["version"]):
        return True
    # one zip can serve several game versions under another version number: files put on disk after the
    # project's last release cannot be older than it
    put = (stamp(primary(a)) or (0, 0))[1] / 1e9 if a.get("folders") else 0
    return bool(new.get("released")) and put > new["released"]


def wowi_cats():
    def fetch():
        f = CACHE / "wowinterface-cats.json"
        if not f.exists() or time.time() - f.stat().st_mtime > 7 * 86400:
            try:
                data = http("https://api.mmoui.com/v3/game/WOW/categorylist.json", raw=True)
                json.loads(data)
                CACHE.mkdir(parents=True, exist_ok=True)
                f.write_bytes(data)
            except RuntimeError:
                if not f.exists():
                    return {}                           # names are a nicety: fall back to "Other"
        return {c["UICATID"]: c["UICATTitle"] for c in json.loads(f.read_text())}
    return memo_time("wowi_cats", 3600, fetch)


def epoch(iso):
    try:
        return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return 0


# one catalog row per source, same shape everywhere
def cf_category(m):
    """The project's primary CurseForge category; a sub-category (Class > Warrior) counts as its parent."""
    cats = m.get("categories") or []
    c = next((c for c in cats if c.get("id") == m.get("primaryCategoryId")), cats[0] if cats else None)
    if not c:
        return "Other"
    if c.get("parentCategoryId") not in (None, c.get("classId")):
        try:
            names = {x["id"]: x["name"] for x in memo_time("cats", 12 * 3600, lambda: cf("/categories?gameId=1"))}
            return names.get(c["parentCategoryId"], c["name"])
        except Exception:
            pass
    return c["name"]


def row_cf(m):
    cat = cf_category(m)
    ver = (cf_pick(m) or {}).get("filename", "")
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
    return memo_time(("details", key), 600, lambda: read_details(key))


def read_details(key):
    src, ref = key.split(":", 1)
    if src == "cf":
        if not cf_key():
            raise RuntimeError("Add a CurseForge key in Settings to see this addon")
        mod = cf(f"/mods/{ref}")
        r = row_cf(mod)
        r["html"] = cf(f"/mods/{ref}/description")
    elif src == "wowi":
        r = row_wowi(next(a for a in wowi_list() if a["UID"] == ref))
        wd = http(f"https://api.mmoui.com/v3/game/WOW/filedetails/{ref}.json")[0]
        r["html"] = bbcode(wd.get("UIDescription"))
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
            f = cf_pick(mod)
            r["changelog"] = cf(f"/mods/{ref}/files/{f['fileId']}/changelog") if f else ""
        elif src == "wowi":
            r["changelog"] = bbcode(wd.get("UIChangeLog"))
        elif shutil.which("gh"):
            out = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.html+json", f"repos/{ref}/releases/latest",
                                  "-q", ".body_html"], capture_output=True, text=True, timeout=40)
            r["changelog"] = out.stdout if out.returncode == 0 else ""
    except Exception:
        pass                                        # release notes are a nicety; the details still show
    return r


# The same addon under another source's id (installed from GitHub, listed on CurseForge).
ALSO = {"cf:267285": "gh:ATTWoWAddon/AllTheThings", "cf:989270": "gh:Peterodox/YUI-Dialogue"}
FIRST = ["Quests & Leveling", "Map & Minimap", "Bags & Inventory", "Action Bars", "Unit Frames", "Combat", "Boss Encounters",
         "Auction & Economy", "Professions", "Class", "Tooltip", "Chat & Communication", "Buffs & Debuffs", "PvP"]


def categories():
    """CurseForge's top-level WoW categories (kept for the session), most used first, then WoWInterface."""
    if not cf_key():
        return [{"id": "wowi", "name": "From WoWInterface"}]
    cats = memo_time("cats", 12 * 3600, lambda: cf("/categories?gameId=1"))
    top = [c for c in cats if c.get("parentCategoryId") == c.get("classId") and not c.get("isClass")]
    top.sort(key=lambda c: (FIRST.index(c["name"]) if c["name"] in FIRST else len(FIRST), c["name"]))
    return [{"id": str(c["id"]), "name": c["name"]} for c in top] + [{"id": "wowi", "name": "From WoWInterface"}]


def browse(cat, index, size):
    """One page of a category, most downloaded first: {rows, total}."""
    index, size = max(0, int(index)), min(50, max(1, int(size)))

    def page():
        if cat == "wowi":
            rows = sorted((row_wowi(a) for a in wowi_list() if wowi_forever(a)), key=lambda r: -r["downloads"])
            return {"rows": rows[index:index + size], "total": len(rows)}
        r = cf(f"/mods/search?gameId=1&categoryId={int(cat)}&gameVersionTypeId={CF_FOREVER}&sortField=6&sortOrder=desc"
               f"&index={index}&pageSize={size}", full=True)
        return {"rows": [row_cf(m) for m in r["data"]], "total": r["pagination"]["totalCount"]}
    return memo_time(("browse", cat, index, size), 900, page)      # paging back and forth costs no new requests


def search(q):
    q, out, errors = q.strip(), [], []

    down = []

    def run(name, fn):
        try:
            out.extend(fn())
        except Unreachable:
            down.append(name)
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
        m = re.search(r"github\.com/([\w.-]+/[\w.-]+)", q)   # a pasted link only; it is checked for Forever support on install
        return [row_gh(gh("repos/" + m.group(1).removesuffix(".git")))] if m else []

    threads = [threading.Thread(target=run, args=a) for a in (("CurseForge", s_cf), ("WoWInterface", s_wowi), ("GitHub", s_gh))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    if down:
        errors.append("No internet connection, or " + " and ".join(sorted(down)) + " cannot be reached")
    return {"results": out, "errors": errors}


# ---------- install / remove / rollback ----------
class NeedsBrowser(Exception):
    """The author only allows downloads on CurseForge itself: the file has to come through its download page."""
    def __init__(self, key, new):
        super().__init__("This author only allows downloads through CurseForge's own page")
        self.info = {"key": key, "url": f"{new['page']}/download/{new['id']}", "remote_id": new["id"], "latest": new["version"]}


def unpack(zpath, dest):
    """Extract into dest and return the addon folders (each has a .toc named after it)."""
    try:
        with zipfile.ZipFile(zpath) as z:
            root, members = dest.resolve(), z.infolist()
            if len(members) > 200_000 or sum(m.file_size for m in members) > 2 * MAX_ZIP:
                raise RuntimeError("Archive is unreasonably large when unpacked")
            for m in members:
                target = (dest / m.filename).resolve()
                if not target.is_relative_to(root) or m.external_attr >> 28 == 0xA:
                    raise RuntimeError("Archive has an unsafe path: " + m.filename)
            z.extractall(dest)
    except (zipfile.BadZipFile, zlib.error, EOFError):
        raise RuntimeError("The file is damaged or incomplete (not a valid .zip). Nothing was changed; download it again") from None
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


def unpack_forever(zpath, dest):
    found = unpack(zpath, dest)
    if not any(forever_ok(d) for d in found):
        raise RuntimeError("This file is not built for WoW Forever, so it was not installed")
    return found


def saved_versions(key):
    """Backup slots of an addon that actually hold something, oldest first."""
    return [d for d in sorted((BACKUPS / re.sub(r"[^\w.-]", "_", key)).glob("*")) if d.is_dir() and any(d.iterdir())]


def backup(key, folders):
    """Move the given addon folders out of the game into a dated slot. Returns the slot, or None if there was nothing to keep."""
    home = BACKUPS / re.sub(r"[^\w.-]", "_", key)
    slot = home / (time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000:03d}")
    slot.mkdir(parents=True)
    for f in folders:
        if (addons_dir() / f).exists():
            shutil.move(addons_dir() / f, slot / f)
    for d in sorted(home.iterdir()):                    # tidy: empty slots go, and only the two newest are kept
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    for d in saved_versions(key)[:-2]:
        shutil.rmtree(d)
    return slot if slot.exists() else None


def swap_in(key, found, replace):
    """Replace `replace` (kept as a backup) with the unpacked folders `found`. If anything fails, the old folders go back."""
    slot = backup(key, replace)
    try:
        for d in found:
            shutil.move(d, addons_dir() / d.name)
    except Exception:
        for d in found:
            shutil.rmtree(addons_dir() / d.name, ignore_errors=True)
        if slot:
            for old in list(slot.iterdir()):
                shutil.move(old, addons_dir() / old.name)
            slot.rmdir()
        raise


def install(key):
    if wow_running():
        raise RuntimeError("Close World of Warcraft first: it overwrites addon settings on exit")
    new = latest(key)
    if not new["url"]:
        raise NeedsBrowser(key, new)
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
        zpath = Path(tmp) / "a.zip"
        download(new["url"], zpath)
        found = unpack_forever(zpath, Path(tmp) / "x")
        names = [d.name for d in found]
        with LOCK:
            st = load()
            # an addon installed by hand earlier is adopted: same folders, new owner
            clash = [k for k, a in st["addons"].items() if k != key and set(a["folders"]) & set(names)]
            prev = st["addons"].get(key, {}).get("folders", [])
            prev_cat = st["addons"].get(key, {}).get("category")
            swap_in(key, found, sorted(set(prev) | set(names) | {f for k in clash for f in st["addons"][k]["folders"]}))
            for k in clash:
                del st["addons"][k]
            t = toc(min(names, key=len))
            st["addons"][key] = {"name": t.get("title") or new["name"], "folders": sorted(names),
                                 "version": t.get("version") or new["version"], "remote_id": new["id"],
                                 **({"category": prev_cat} if prev_cat else {})}
            save(st)
    LIVE[key] = {"latest": new["version"], "latest_id": new["id"], "page": new["page"], "update": False, "error": ""}
    return st["addons"][key]


def downloads():
    d = HOME / "Downloads"                            # browsers may save into a subfolder of Downloads
    out = []
    for z in [*d.glob("*.zip"), *d.glob("*/*.zip")]:
        try:
            out.append({"name": z.name, "path": str(z), "when": int(z.stat().st_mtime), "size": z.stat().st_size})
        except OSError:
            pass                                        # gone or unreadable between listing and looking
    return sorted(out, key=lambda x: -x["when"])[:12]


def install_file(path):
    """Install a zip from the Downloads folder (picked in Settings or offered as a new download)."""
    z = Path(path).resolve()
    if not z.is_relative_to((HOME / "Downloads").resolve()) or z.suffix != ".zip":
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


def install_zip(z, source=None):
    """Unpack a local zip; an addon already on the shelf keeps its source and gets a backup.
    `source` is the catalog key the file was fetched for (a CurseForge download page)."""
    if wow_running():
        raise RuntimeError("Close World of Warcraft first: it overwrites addon settings on exit")
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
        found = unpack_forever(z, Path(tmp) / "x")
        names = sorted(d.name for d in found)
        with LOCK:
            st = load()
            owners = [k for k, a in st["addons"].items() if set(a["folders"]) & set(names)]
            key = source or (owners[0] if owners else "local:" + min(names, key=len))
            swap_in(key, found, sorted(set(names) | {f for k in owners for f in st["addons"][k]["folders"]}))
            old = st["addons"].get(key, {})
            for k in owners:
                del st["addons"][k]
            t = toc(min(names, key=len))
            st["addons"][key] = {**old, "name": t.get("title") or names[0], "folders": names, "version": t.get("version", "")}
            st["addons"][key].pop("remote_id", None)
            save(st)
    LIVE.pop(key, None)
    return st["addons"][key]["name"]


# ---------- an addon's saved settings (the game's SavedVariables files, named after the addon's folders) ----------
SETTINGS = BACKUPS.parent / "settings"


def wtf():
    return addons_dir().parent.parent / "WTF" / "Account"


def settings_files(a):
    """Every settings file the game keeps for this addon: per account and per character, with the game's .bak copies."""
    out = []
    for f in a["folders"]:
        for pat in (f"*/SavedVariables/{f}.lua*", f"*/*/*/SavedVariables/{f}.lua*"):
            out += [p for p in wtf().glob(pat) if p.is_file()]
    return sorted(set(out))


def settings_slots(key):
    return [d for d in sorted((SETTINGS / re.sub(r"[^\w.-]", "_", key)).glob("*")) if d.is_dir() and any(d.rglob("*.lua*"))]


def reset_settings(key):
    """Set the addon's saved settings aside so it starts as if freshly installed. Returns how many files were moved."""
    if wow_running():
        raise RuntimeError("Close World of Warcraft first: it writes addon settings back when it exits")
    with LOCK:
        files = settings_files(load()["addons"][key])
        if not files:
            raise RuntimeError("This addon has no saved settings yet")
        home = SETTINGS / re.sub(r"[^\w.-]", "_", key)
        slot = home / time.strftime("%Y%m%d-%H%M%S")
        for f in files:
            dest = slot / f.relative_to(wtf())                  # same layout as the game's folder, so it can go straight back
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(f, dest)
        for old in settings_slots(key)[:-3]:                    # keep the three most recent resets
            shutil.rmtree(old)
    return len(files)


def restore_settings(key):
    """Undo the last reset: put the saved settings back (replacing any the addon has written since)."""
    if wow_running():
        raise RuntimeError("Close World of Warcraft first: it writes addon settings back when it exits")
    with LOCK:
        slots = settings_slots(key)
        if not slots:
            raise RuntimeError("There are no set-aside settings for this addon")
        slot, n = slots[-1], 0
        for f in [p for p in slot.rglob("*") if p.is_file()]:
            dest = wtf() / f.relative_to(slot)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(f, dest)                                # replaces a newer file of the same name
            n += 1
        shutil.rmtree(slot)
    return n


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
    slots = saved_versions(key)
    if not slots:
        raise RuntimeError("No earlier version is saved for this addon")
    with LOCK:
        st, slot = load(), slots[-1]
        names = sorted(d.name for d in slot.iterdir())
        for f in set(names) | set(st["addons"].get(key, {}).get("folders", [])):
            shutil.rmtree(addons_dir() / f, ignore_errors=True)
        for d in list(slot.iterdir()):
            shutil.move(d, addons_dir() / d.name)
        slot.rmdir()
        t = toc(min(names, key=len))
        keep = {"category": st["addons"][key]["category"]} if st["addons"].get(key, {}).get("category") else {}
        st["addons"][key] = {"name": t.get("title") or names[0], "folders": names, "version": t.get("version", ""), **keep}
        save(st)
    LIVE.pop(key, None)


def check():
    try:
        st = sync()
    except GameMissing:
        return view()
    NOTE.pop("net", None)

    def note(key, a, new):
        LIVE[key] = {"latest": new["version"], "latest_id": new["id"], "page": new["page"], "update": not is_current(a, new),
                     "restricted": bool(new.get("restricted")), "error": ""}
        if new.get("category"):
            SRC_CAT[key] = new["category"]
        if new.get("thumb"):
            SRC_ART[key] = new["thumb"]

    def failed(key, e):
        if isinstance(e, Unreachable):                  # said once for everything, not on every addon
            NOTE["net"] = str(e)
            LIVE[key] = {**LIVE.get(key, {}), "error": ""}
        else:
            LIVE[key] = {"update": False, "error": str(e)}

    def one(key, a):
        try:
            note(key, a, latest(key))
        except Exception as e:
            failed(key, e)

    cfs = {k: a for k, a in st["addons"].items() if k.startswith("cf:")}
    others = [threading.Thread(target=one, args=kv) for kv in st["addons"].items() if kv[0].split(":")[0] in ("gh", "wowi")]
    [t.start() for t in others]
    if cfs:
        try:                                            # all projects in a single request
            if not cf_key():
                raise RuntimeError("Add a CurseForge key in Settings to check this addon")
            twins = {v: k for k, v in ALSO.items() if v in st["addons"]}       # GitHub installs that CurseForge also lists
            mods = {f"cf:{m['id']}": m for m in cf("/mods", {"modIds": [int(k[3:]) for k in [*cfs, *twins.values()]]})}
            for mine, theirs in twins.items():                                  # borrow CurseForge's category for them
                if theirs in mods:
                    SRC_CAT[mine] = cf_category(mods[theirs])
                    SRC_ART[mine] = (mods[theirs].get("logo") or {}).get("thumbnailUrl", "")
            for k, a in cfs.items():
                try:
                    if k not in mods:
                        raise RuntimeError("CurseForge no longer lists this addon")
                    note(k, a, cf_newest(mods[k]))
                except Exception as e:
                    failed(k, e)
        except Exception as e:
            for k in cfs:
                failed(k, e)
    [t.join() for t in others]
    return view(st)


SOURCES = {"cf": "CurseForge", "gh": "GitHub", "wowi": "WoWInterface", "local": "No source"}


def view(st=None):
    try:
        st = st or sync()
        groups = group_folders()
    except GameMissing as e:
        return {"addons": [], "game_missing": str(e), "wow_running": False, "has_cf_key": bool(cf_key()), "addons_dir": "",
                "new_downloads": [], "categories": CATEGORIES, "conflicts": [], "unmanaged": [], "also": {}, "net": ""}
    names = shelf_categories()
    saved = {}                                          # one pass over the game's settings folders for all addons
    for pat in ("*/SavedVariables/*.lua*", "*/*/*/SavedVariables/*.lua*"):
        for f in wtf().glob(pat):
            saved[f.name.split(".lua")[0]] = saved.get(f.name.split(".lua")[0], 0) + 1

    def row(k, a):
        base = {"key": k, "source": SOURCES[k.split(":")[0]], **a, **LIVE.get(k, {})}
        try:
            t = toc(primary(a))
            return {**base, "size": folder_size(a["folders"]), "notes": t.get("notes", ""),
                    "category": a.get("category") or SRC_CAT.get(k) or guess_category(t, a["name"]),
                    "icon": memo("icon", primary(a), lambda: bool(icon_png(k))), "art": SRC_ART.get(k, ""),
                    "can_rollback": bool(saved_versions(k)), "settings": sum(saved.get(f, 0) for f in a["folders"]),
                    "settings_saved": bool(settings_slots(k)), "damaged": memo("damage", primary(a), lambda: damage(a))}
        except Exception as e:                          # one unreadable addon must not hide the rest
            return {**base, "size": 0, "notes": "", "category": a.get("category") or "Other", "icon": False, "art": "",
                    "can_rollback": False, "settings": 0, "settings_saved": False,
                    "damaged": f"This addon's files could not be read ({type(e).__name__})"}

    rows = sorted((row(k, a) for k, a in st["addons"].items()), key=lambda r: r["name"].lower())
    try:
        fresh = [d for d in downloads() if time.time() - d["when"] < 1800 and d["size"] > 0
                 and not any(Path(d["path"] + e).exists() for e in (".crdownload", ".part"))]
    except OSError:
        fresh = []
    owned = {f for a in st["addons"].values() for f in a["folders"]}
    def why(p):
        tocs = list((addons_dir() / p).glob("*.toc"))
        if not tocs:
            return "it has no description (.toc) file"
        if not any(re.search(r"(?mi)^##\s*Interface\s*:", slurp(t)) for t in tocs):
            return "its description (.toc) file is empty or unreadable"
        return "it is not built for WoW Forever"
    loose = [{"name": p, "why": why(p)} for p in sorted(groups) if p not in owned]
    return {"addons": rows, "wow_running": wow_running(), "has_cf_key": bool(cf_key()), "addons_dir": str(addons_dir()),
            "new_downloads": fresh, "categories": names, "conflicts": conflicts(st["addons"]), "unmanaged": loose,
            "also": {k: v for k, v in ALSO.items() if v in st["addons"] and k not in st["addons"]}, "net": NOTE.get("net", "")}


# ---------- updating the app itself from the repository it was cloned from ----------
def git(*args, at=None, timeout=20):
    r = subprocess.run(["git", "-C", str(at or HERE), *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else "git failed")
    return r.stdout.strip()


def app_status(fetch=False, at=None):
    """Where this copy of the app stands against its repository: {version, date, source, behind, ahead, changed, news, note}."""
    if not shutil.which("git"):
        return {"note": "Updating needs git, which is not installed on this computer"}
    try:
        git("rev-parse", "--is-inside-work-tree", at=at)
        out = {"version": git("rev-parse", "--short", "HEAD", at=at), "date": git("log", "-1", "--format=%cd", "--date=short", at=at),
               "source": re.sub(r"\.git$", "", git("remote", "get-url", "origin", at=at))}
    except RuntimeError:
        return {"note": "This copy was not installed from its repository, so it cannot update itself. Download a fresh copy to update"}
    if fetch:
        try:
            git("fetch", "--quiet", "origin", at=at, timeout=45)
        except (RuntimeError, subprocess.TimeoutExpired):
            return {**out, "note": "Could not reach the repository. Check your internet connection and try again"}
    try:
        branch = git("rev-parse", "--abbrev-ref", "origin/HEAD", at=at)
    except RuntimeError:
        branch = "origin/main"
    try:
        ahead, behind = map(int, git("rev-list", "--left-right", "--count", f"HEAD...{branch}", at=at).split())
        out.update(ahead=ahead, behind=behind, branch=branch, changed=bool(git("status", "--porcelain", "--untracked-files=no", at=at)),
                   news=git("log", "--format=%s", f"HEAD..{branch}", at=at).splitlines()[:12])
    except RuntimeError as e:
        out["note"] = f"Could not compare with the repository ({e})"
    return out


def app_update(at=None):
    """Move this copy to the repository's newest version. Only ever fast-forwards: local work is never overwritten."""
    st = app_status(fetch=True, at=at)
    if st.get("note"):
        raise RuntimeError(st["note"])
    if st["changed"]:
        raise RuntimeError("This copy has been edited by hand, so it was left alone. Undo those edits (or commit them) and try again")
    if st["ahead"]:
        raise RuntimeError("This copy has changes that are not in the repository yet, so there is nothing safe to pull")
    if not st["behind"]:
        raise RuntimeError("Already on the newest version")
    git("merge", "--ff-only", st["branch"], at=at, timeout=60)
    return app_status(at=at)


# ---------- window ----------
class H(BaseHTTPRequestHandler):
    token = ""

    def log_message(self, *a):
        pass

    def send(self, obj, code=200, ctype="application/json", cache=None):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if cache:
            self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_api(self, body):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        # other websites in the browser must not be able to drive this server
        if self.headers.get("Host", "").split(":")[0] != "127.0.0.1" or not secrets.compare_digest(self.headers.get("X-Token", ""), H.token):
            return self.send({"error": "forbidden"}, 403)
        try:
            route = u.path.removeprefix("/api/")
            if route == "state":
                r = view()
            elif route == "check":
                r = check()
            elif route == "search":
                r = search(q.get("q", [""])[0])
            elif route == "categories":
                r = {"categories": categories()}
            elif route == "browse":
                r = browse(q["cat"][0], q.get("index", ["0"])[0], q.get("size", ["10"])[0])
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
                if body["category"] not in [*shelf_categories(), *CATEGORIES]:
                    raise RuntimeError("Unknown category")
                with LOCK:
                    st = load()
                    st["addons"][body["key"]]["category"] = body["category"]
                    save(st)
                r = view()
            elif route == "app":
                r = app_status(fetch=q.get("fetch", ["0"])[0] == "1")
            elif route == "app_update":
                r = {**app_update(), "restarting": bool(RESTART)}
                if RESTART:
                    threading.Timer(1.5, RESTART).start()
            elif route == "folder":
                set_addons_dir(body["path"])
                r = view()
            elif route == "key":
                if not body["key"].strip().isascii() or " " in body["key"].strip():
                    raise RuntimeError("That looks like the hidden (dotted) version of the key. Use the copy button next to it on console.curseforge.com, then paste again")
                CONF.mkdir(parents=True, exist_ok=True)
                CF_KEY.touch(mode=0o600)
                CF_KEY.write_text(body["key"].strip())
                r = {"restarting": bool(RESTART)}
                if RESTART:
                    threading.Timer(1.2, RESTART).start()       # after this reply has reached the window
            elif route in ("reset_settings", "restore_settings"):
                n = {"reset_settings": reset_settings, "restore_settings": restore_settings}[route](body["key"])
                r = {**view(), "files": n}
            elif route in ("install", "remove", "rollback"):
                {"install": install, "remove": remove, "rollback": rollback}[route](body["key"])
                r = view()
            elif route == "fetch":                      # author-restricted file: through CurseForge's own download page, in the app
                if not body["url"].startswith("https://www.curseforge.com/") or FETCH is None:
                    raise RuntimeError("This download needs the desktop application")
                cf("/games/1")                          # fail at once, in plain words, if CurseForge cannot be reached
                z = FETCH(body["url"])
                try:
                    name = install_zip(z, source=body["key"])
                finally:
                    z.unlink(missing_ok=True)
                with LOCK:
                    st = load()
                    st["addons"][body["key"]]["remote_id"] = body["remote_id"]     # which release this is
                    save(st)
                LIVE[body["key"]] = {**LIVE.get(body["key"], {}), "latest": body["latest"], "update": False, "error": ""}
                r = {**view(), "installed": name}
            else:
                return self.send({"error": "unknown"}, 404)
            self.send(r)
        except NeedsBrowser as e:
            self.send({"error": str(e), "browser": e.info}, 409)
        except KeyError:
            self.send({"error": "That addon is no longer on your shelf. Refresh and try again"}, 400)
        except PermissionError as e:
            self.send({"error": f"No permission to change {e.filename or 'the addon folder'}. Nothing was changed"}, 400)
        except OSError as e:
            self.send({"error": "The disk is full. Nothing was changed" if e.errno == 28 else f"A file operation failed ({e.strerror or e}). Nothing was changed"}, 400)
        except Exception as e:
            self.send({"error": str(e)}, 400)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self.handle_api({})
        u = urllib.parse.urlparse(self.path)
        if u.path == "/icon":                                   # <img> cannot send headers, so the token rides in the query
            q = urllib.parse.parse_qs(u.query)
            if not secrets.compare_digest(q.get("t", [""])[0], H.token) or self.headers.get("Host", "").split(":")[0] != "127.0.0.1":
                return self.send(b"", 403, "text/plain")
            png = icon_png(q.get("key", [""])[0])
            return self.send(png, ctype="image/png", cache="private, max-age=3600") if png else self.send(b"", 404, "text/plain")
        m = re.fullmatch(r"/fonts/([A-Za-z0-9-]+\.(?:ttf|woff2))", u.path)   # fonts ship with the app: no font server needed
        if m and (HERE / "fonts" / m.group(1)).is_file():
            return self.send((HERE / "fonts" / m.group(1)).read_bytes(), ctype="font/ttf" if m.group(1).endswith("ttf") else "font/woff2",
                             cache="private, max-age=604800")
        m = re.fullmatch(r"/art/([a-z]+(?:-s)?)\.webp", u.path)       # theme artwork shipped with the app
        if m and (HERE / "art" / (m.group(1) + ".webp")).is_file():
            return self.send((HERE / "art" / (m.group(1) + ".webp")).read_bytes(), ctype="image/webp", cache="private, max-age=86400")
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


def main():
    """The application: a Qt window showing the interface, with its own downloader for CurseForge's download pages."""
    try:
        from PySide6.QtCore import QEvent, QObject, QTimer, QUrl, Signal, Qt
        from PySide6.QtGui import QDesktopServices, QIcon
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton,
                                       QSystemTrayIcon, QVBoxLayout)
    except ImportError as e:
        sys.exit(f"The Addon Shelf needs PySide6 with Qt WebEngine to show its window ({e}). On Arch: sudo pacman -S pyside6 qt6-webengine")
    CONF.mkdir(parents=True, exist_ok=True)
    guard = open(CONF / "lock", "w")                    # one copy at a time: two would overwrite each other's records
    try:
        if not os.environ.get("WOWADDONS_SHOT"):
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        box = QApplication(sys.argv[:1])
        QMessageBox.information(None, "The Addon Shelf", "The Addon Shelf is already open.")
        sys.exit(0)

    H.token = secrets.token_urlsafe(24)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    app = QApplication(sys.argv[:1])
    app.setApplicationName("The Addon Shelf")
    app.setDesktopFileName("wowaddons")
    app.setWindowIcon(QIcon(str(HERE / "icon.svg")))
    # keeps CurseForge's cookies and the chosen theme between runs; a capture run gets throwaway storage so it
    # cannot collide with the copy the user has open
    profile = QWebEngineProfile(app) if os.environ.get("WOWADDONS_SHOT") else QWebEngineProfile("wowaddons", app)

    class Page(QWebEnginePage):
        def createWindow(self, _type):                          # links the user clicks open in their browser
            tmp = QWebEnginePage(self)
            tmp.urlChanged.connect(lambda u: (QDesktopServices.openUrl(u), tmp.deleteLater()))
            return tmp

    view = QWebEngineView()
    view.setPage(Page(profile, view))
    view.setWindowTitle("The Addon Shelf for WoW Forever")
    view.resize(1500, 950)
    # Never pull focus back to the app: pages loading must not activate the window, and after a slow start (when the
    # user has likely moved on to something else) the window appears without taking focus at all.
    view.page().settings().setAttribute(QWebEngineSettings.WebAttribute.FocusOnNavigationEnabled, False)
    if time.time() - STARTED > 2.0:
        view.setAttribute(Qt.WA_ShowWithoutActivating)
    view.load(QUrl(f"http://127.0.0.1:{srv.server_address[1]}/"))
    view.show()

    class Fetcher(QObject):
        """Shows CurseForge's own download page for an addon the user asked for, and hands back the file it delivers."""
        wanted = Signal(str, object)
        again = Signal()

        def __init__(self):
            super().__init__()
            self.box = QFrame(view)                             # a small panel in the corner of the app, not a window
            self.box.setStyleSheet("QFrame{background:#14161c;border:1px solid #8a8f9c;border-radius:10px}"
                                   "QLabel{color:#eceff5;border:0;font-size:14px}"
                                   "QPushButton{color:#eceff5;border:1px solid #8a8f9c;border-radius:6px;padding:2px 10px}")
            self.title = QLabel("CurseForge")
            stop = QPushButton("Cancel", clicked=self.closed)
            bar = QHBoxLayout()
            bar.addWidget(self.title, 1)
            bar.addWidget(stop)
            self.web = QWebEngineView(self.box)
            self.web.setPage(QWebEnginePage(profile, self.web))
            self.web.page().settings().setAttribute(QWebEngineSettings.WebAttribute.FocusOnNavigationEnabled, False)
            lay = QVBoxLayout(self.box)
            lay.setContentsMargins(10, 8, 10, 10)
            lay.addLayout(bar)
            lay.addWidget(self.web)
            self.box.hide()
            self.job = None
            view.installEventFilter(self)
            profile.downloadRequested.connect(self.incoming)
            self.wanted.connect(self.start, Qt.QueuedConnection)
            self.again.connect(lambda: os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())]), Qt.QueuedConnection)

        def place(self):
            w, h = min(560, view.width() - 40), min(400, view.height() - 120)
            self.box.setGeometry(view.width() - w - 20, view.height() - h - 20, w, h)

        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Resize and self.box.isVisible():
                self.place()
            return False

        def start(self, url, job):
            self.job = job
            self.title.setText("This author only offers the file on CurseForge. It downloads from this page:")
            self.web.load(QUrl(url))
            self.place()
            self.box.show()
            self.box.raise_()

        def closed(self):
            if self.job:
                job, self.job = self.job, None
                job["done"].set()
            self.box.hide()
            self.web.setUrl(QUrl("about:blank"))

        def incoming(self, dl):
            if not self.job:
                return dl.cancel()
            job = self.job
            (CACHE / "uploads").mkdir(parents=True, exist_ok=True)
            dl.setDownloadDirectory(str(CACHE / "uploads"))
            dl.setDownloadFileName(re.sub(r"[^\w.-]", "_", dl.downloadFileName()) or "addon.zip")
            dl.isFinishedChanged.connect(lambda: self.finish(dl, job))
            dl.accept()

        def finish(self, dl, job):
            if not dl.isFinished() or job["done"].is_set():
                return
            self.job = None
            self.box.hide()
            self.web.setUrl(QUrl("about:blank"))
            job["path"] = Path(dl.downloadDirectory()) / dl.downloadFileName()
            job["done"].set()

    fetcher = Fetcher()
    busy = threading.Lock()

    def fetch(url):                                             # called from a request thread
        with busy:
            job = {"done": threading.Event(), "path": None}
            fetcher.wanted.emit(url, job)
            if not job["done"].wait(180) or not job["path"] or not job["path"].exists():
                fetcher.job = None
                raise RuntimeError("No file arrived from CurseForge's page")
            return job["path"]

    global FETCH, RESTART
    FETCH, RESTART = fetch, fetcher.again.emit

    # system tray: a way back to the window, a quick update check, and quit. Closing the window still quits the app.
    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        def raise_window():
            view.show()
            view.raise_()
            view.activateWindow()

        def toggle(reason):
            if reason == QSystemTrayIcon.Trigger:                   # a plain click on the icon
                view.hide() if view.isVisible() and view.isActiveWindow() else raise_window()

        def check_now():
            raise_window()
            view.page().runJavaScript("show('lib');document.querySelector('#chk').click()")

        tray = QSystemTrayIcon(QIcon(str(HERE / "icon.svg")), app)
        menu = QMenu()
        menu.addAction("Open The Addon Shelf", raise_window)
        menu.addAction("Check for updates", check_now)
        menu.addSeparator()
        menu.addAction("Quit", app.quit)
        tray.setContextMenu(menu)
        tray.setToolTip("The Addon Shelf for WoW Forever")
        tray.activated.connect(toggle)
        tray.show()

    def titled(title):                                              # the page puts the update count in its title
        view.setWindowTitle(title or "The Addon Shelf for WoW Forever")
        if tray:
            tray.setToolTip(title or "The Addon Shelf for WoW Forever")
    view.titleChanged.connect(titled)
    shot = os.environ.get("WOWADDONS_SHOT")                     # debugging aid: save a picture of the window, then quit
    if shot:                                                    # WOWADDONS_JS runs first, to put the window in the state to capture
        if os.environ.get("WOWADDONS_JS"):
            QTimer.singleShot(4000, lambda: view.page().runJavaScript(
                "(()=>{try{return String(eval(%s))}catch(e){return 'ERR '+e}})()" % json.dumps(os.environ["WOWADDONS_JS"]),
                lambda r: print("js result:", r, flush=True)))
        QTimer.singleShot(8000, lambda: (view.grab().save(shot), app.quit()))
    sys.exit(app.exec())


def selftest():
    assert norm("v1.0.7-b") == norm("1.0.7 b") == "107b"
    assert is_current({"version": "1.0.7 b"}, {"id": "x", "version": "DialogueUI-1.0.7-b.zip"})
    assert not is_current({"version": "1.4.0"}, {"id": "x", "version": "Lueur-1.7.0.zip"})
    assert not is_current({"remote_id": "1"}, {"id": "2", "version": ""})
    assert is_current({"remote_id": "9"}, {"id": "2", "version": ""})          # a newer beta is not "out of date" against an older release
    T = CF_FOREVER
    assert cf_pick({"latestFilesIndexes": [{"fileId": 5, "releaseType": 2, "gameVersionTypeId": T},
                                           {"fileId": 3, "releaseType": 1, "gameVersionTypeId": T},
                                           {"fileId": 9, "releaseType": 1, "gameVersionTypeId": 1}]})["fileId"] == 3
    assert cf_pick({"latestFilesIndexes": []}) is None
    assert not is_current({"version": "1.0"}, {"id": "x", "version": "Foo-2.0", "released": 0})
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
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "A.toc").write_text("\ufeff## Interface: 120105, 16001\n", encoding="utf-8")
        assert forever_ok(t)
        (t / "A.toc").write_text("## Interface: 120105\n")
        assert not forever_ok(t)
    lab = lambda name: {label for label, pn, pd in JOBS if re.search(pn, name.lower())}
    assert lab("Bagnon") & lab("OneBag3 bags")
    assert not lab("Questie") & lab("Bartender4")
    assert guess_category({"category": "Quests"}, "Attune") == "Quests & Leveling"
    assert guess_category({"notes": "Auction house helper"}, "X") == "Auctions & Economy"
    assert guess_category({}, "Zzz") == "Other"
    with tempfile.TemporaryDirectory() as t:
        global _DIR
        keep, _DIR = _DIR, Path(t)
        (Path(t) / "Q").mkdir()
        (Path(t) / "Q" / "Q.toc").write_text("## Interface: 00000\n## Title: Q|cFFFF0000 game client not supported|r\n")
        (Path(t) / "Q" / "Q_Forever.toc").write_text("## Interface: 16001\n## Title: Q|cFF00FF00 v12.6.5|r\n## Version: 12.6.5\n")
        assert read_toc("Q")["title"] == "Q" and read_toc("Q")["version"] == "12.6.5", read_toc("Q")
        _DIR = keep
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
