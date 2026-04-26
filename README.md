# Lazydeck

![Python](https://img.shields.io/badge/python-3.8%2B-blue) ![Platform](https://img.shields.io/badge/platform-Steam%20Deck%20%2F%20SteamOS-informational) ![License](https://img.shields.io/badge/license-MIT-green)

Steam Deck — NAS Network Cache with rclone
Mount your NAS on the Steam Deck with automatic local caching, so the last games you played remain available even when you're away from home — without downloading anything manually.

---

## How it works

While at home on your local network, RoamDeck mounts your NAS via NFS and transparently caches every file you open onto the Deck's internal storage. When you leave home, the cached files stay available — your emulators see no difference.

- **No manual downloads.** Play a game once and it's cached automatically.
- **Configurable size limit.** Set 1 GB, 5 GB, 20 GB — oldest unused files are evicted first.
- **Survives SteamOS updates.** Everything lives in `/home/deck`, which is never wiped.

---

## Requirements

- Steam Deck running SteamOS 3.x
- A NAS with NFS shares enabled
- Network connection during setup

---

## Quick start

```bash
# 1. Clone the repo
git clone https://github.com/yourname/RoamDeck.git
cd RoamDeck

# 2. Run the installer
bash run.sh
```

The graphical wizard will guide you through the rest.

---

## What the installer does

| Step | What happens |
|---|---|
| NAS configuration | Sets NFS share paths and local mount points |
| Cache settings | Configures size limit, expiry, and local cache folder |
| Installation | Downloads rclone, creates systemd units, enables services |
| Done | Generates a repair script for after SteamOS updates |

---

## After a SteamOS update

SteamOS updates wipe `/var/mnt`. Run the repair script that was created during installation:

```bash
bash ~/repair-nas-cache.sh
```

Or re-run the installer — it skips steps that are already done.

---

## [MODS] Adding a language to the installer UI

Users that want to fork or contribute, please copy `lang/TEMPLATE.json`, rename it to your language code (e.g. `de.json`, `es.json`), and translate the values. The installer picks it up automatically on next launch.

---

## Details

For a full technical breakdown of the setup — systemd unit structure, what survives updates, cache behaviour, and manual configuration — see [DETAILS.md](DETAILS.md).

---

## License

MIT
