# The Addon Shelf

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
- **Safe changes:** refuses to touch addons while the game is running, keeps the previous two versions of every
  addon, and can restore them.
- **A real desktop application:** Python 3.11+ with PySide6 (Qt WebEngine) for the window; Pillow is optional and only used to show addon icons.

## Run

```
python3 wowaddons.py            # starts the application
python3 wowaddons.py --selftest # built-in checks
```

The addon folder (`_forever_/Interface/AddOns`) is found under `~/Games` on first start.

## CurseForge

CurseForge is only read through the official API (`api.curseforge.com`) with a key you paste into Settings; the key is stored
in `~/.config/wowaddons/curseforge.key` and is only ever sent as the API header. The app follows the API terms:

- **Author consent.** Files are downloaded only from the URL the API returns. If an author has turned off third-party
  distribution, the API gives no URL and the app does not fetch the file by any other route on its own. Such addons show a
  "Get on CurseForge" button: when you click it, CurseForge's own download page for that file opens in the app, visibly, and
  the file that page delivers is installed. Nothing is mirrored or re-hosted.
- **Light on the servers.** One batch request checks every installed addon; searches are one request each; nothing polls.
- **No stored API data.** What the API returns is kept in memory for the session only. On disk the app keeps your addon
  list: each addon's project id, folders, installed version and the id of the installed file.
- Every addon links back to its CurseForge page.

Addons you download yourself can be dropped onto the window, or installed from Settings → "Install from a file you
downloaded".

## Themes and artwork

Settings offers faction, class and race themes plus plain colour schemes. The emblems in `art/` are original images
generated for this project with a local image model (`tools/paint_emblems.py`, ComfyUI + Z-Image Turbo); none of them is
game artwork.

## Not affiliated

This is an independent, non-commercial tool. It is not affiliated with or endorsed by Blizzard Entertainment,
CurseForge/Overwolf or WoWInterface. It contains no game artwork.

## License

Apache-2.0
