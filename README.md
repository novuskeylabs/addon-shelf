# LinuxForever Addon Shelf

A desktop addon manager for the World of Warcraft: Forever client on Linux. Your installed addons are shown as tiles
grouped by category; a catalog browses and searches several sources at once. Only Forever-compatible addons are shown,
installed or managed.

- **Sources:** CurseForge (official API, your own key), WoWInterface (public API) and GitHub releases.
- **Catalog:** grouped by category and popularity, with downloads, last update and version; click an addon for
  screenshots and the source's description.
- **Possible conflicts:** a matrix of installed addons that take over the same part of the interface.
- **Reset to default settings:** sets an addon's saved settings aside (with undo) so it starts fresh.
- **When things go wrong:** plain messages when offline, damaged addons are flagged with a repair option, and a missing
  game folder is explained with a way to point at it.
- **Safe changes:** while the game is running, new addons can be added (they load at its next start) but installed
  ones are left alone; the previous two versions of every addon are kept and can be restored.
- **Finds the game for you:** on first start, and whenever the game goes missing, it scans the usual places (Lutris,
  Bottles, Heroic, Steam/Proton, plain Wine, their Flatpak versions, extra drives). You can also choose the folder in
  Settings.
- **System tray:** the window's close button can close the app or keep it running in the tray; it asks the first time,
  can remember the answer, and Settings changes it. Opening the app again brings the running copy's window back.
- **Background checks:** while open or in the tray it looks for addon updates every 30 minutes and tells you when
  something new is out. A lost connection or a blocked source changes nothing on screen and is retried later.
- **Updates itself:** Settings can pull the newest version from this repository.

## Install

```
git clone https://github.com/novuskeylabs/addon-shelf.git
cd addon-shelf
./install.sh
```

The installer works the same on any mainstream distribution (Debian, Ubuntu, Mint, Fedora, Arch, openSUSE and their
relatives). It needs Python 3.9 or newer and does not use root: the window toolkit (PySide6 / Qt WebEngine) and Pillow
go into a private environment under `~/.local/share/linuxforever-addon-shelf`, so nothing can conflict with your
distribution's packages. It adds a menu entry and a `linuxforever-addon-shelf` launcher. If your system lacks a
graphics library Qt needs, the installer names it and the package to install. `./install.sh --uninstall` removes
everything it added.

Not covered by the installer: distributions built on musl instead of glibc (such as Alpine), where the toolkit has to
come from the distribution's own packages.

To run without installing, with PySide6 already on your system:

```
python3 wowaddons.py            # starts the application
python3 wowaddons.py --selftest # built-in checks
```

## Tested on

`tools/test-distros.sh` installs the app in a fresh container of each family, as an ordinary user and with only the
libraries the installer suggests, then runs the built-in checks and `tools/smoke.py` (the real window without a screen:
the page loads, the search box takes typing, the close dialog works, an addon installs and a non-Forever one is refused).
It passes on Debian 13, Ubuntu 24.04, Fedora, Arch and openSUSE Tumbleweed. Run it after any change:

```
tools/test-distros.sh              # every family (needs docker or podman)
python3 tools/smoke.py             # just this computer
```

## CurseForge

CurseForge is only read through the official API (`api.curseforge.com`) with a key you paste into Settings; the key is stored
in `~/.config/wowaddons/curseforge.key` and is only ever sent as the API header. The app follows the API terms:

- **Author consent.** Files are downloaded only from the URL the API returns. If an author has turned off third-party
  distribution, the API gives no URL and the app does not fetch the file by any other route on its own. Such addons show a
  "Get on CurseForge" button: when you click it, CurseForge's own download page for that file opens in the app, visibly, and
  the file that page delivers is installed. "Update all" does the same for each such addon in turn, one visible page at a
  time, after the ordinary updates. Nothing is mirrored or re-hosted.
- **Light on the servers.** One batch request checks every installed addon; searches are one request each. Background
  checks run at most every 30 minutes (hourly, three-hourly or off in Settings) and wait longer after a failed one.
- **No stored API data.** What the API returns is kept in memory for the session only. On disk the app keeps your addon
  list: each addon's project id, folders, installed version and the id of the installed file.
- Every addon links back to its CurseForge page.

Addons you download yourself can be dropped onto the window, or installed from Settings → "Install from a file you
downloaded".

## Themes and artwork

Settings offers faction, class and race themes plus plain colour schemes. The app icon and the emblems in `art/` are original images
generated for this project with a local image model (`tools/paint_emblems.py`, ComfyUI + Z-Image Turbo); none of them is
game artwork. The bundled fonts (IM Fell English, Alegreya) are under the SIL Open Font License.

## Not affiliated

This is an independent, non-commercial tool. It is not affiliated with or endorsed by Blizzard Entertainment,
CurseForge/Overwolf or WoWInterface. It contains no game artwork.

## License

Apache-2.0
