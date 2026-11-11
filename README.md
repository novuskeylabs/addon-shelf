# The Addon Shelf

A small desktop addon manager for the World of Warcraft: Forever client on Linux. Your installed addons sit on a
bookshelf; a catalog searches several sources at once.

- **Sources:** CurseForge (official API, your own key), WoWInterface (public API) and GitHub releases.
- **Catalog:** grouped by category and popularity, with downloads, last update and version; click an addon for
  screenshots and the source's description.
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

CurseForge data is only read through the official API (`api.curseforge.com`) with a key you paste into Settings; it is
stored in `~/.config/wowaddons/curseforge.key` and never leaves your machine except as the API header. Files are
downloaded from the URL the API returns. If an author has turned off third-party distribution, the API gives no URL; the
app then loads that addon's CurseForge download page in its own window, exactly as a browser would, and installs the file
the page delivers. Nothing is mirrored or re-hosted. Every addon links back to its CurseForge page.

Addons you download yourself can be dropped onto the window, or installed from Settings → "Install from a file you
downloaded".

## Not affiliated

This is an independent, non-commercial tool. It is not affiliated with or endorsed by Blizzard Entertainment,
CurseForge/Overwolf or WoWInterface. It contains no game artwork.

## License

Apache-2.0
