# Lazydeck

Steam Deck — NAS Network Cache with rclone
Mount your NAS on the Steam Deck with automatic local caching, so the last games you played remain available even when you're away from home — without downloading anything manually.

---

## How It Works

- While at home, your NAS is mounted via NFS and all file access is transparently cached locally
- When you open a ROM through your emulator, it gets stored in `~/.cache/rclone/` on the Deck's internal storage
- When you leave home and lose network access, the rclone FUSE mount continues to serve the cached files as if the NAS were still available
- Files are evicted from cache automatically when the size limit is reached (least recently used first)

---

## What Survives SteamOS Updates

SteamOS has a read-only root filesystem that gets wiped on every update. This setup is designed to survive updates:

| Component | Location | Survives updates? |
|---|---|---|
| rclone binary | `~/.local/bin/rclone` | ✅ Yes |
| rclone config | `~/.config/rclone/rclone.conf` | ✅ Yes |
| rclone systemd service | `~/.config/systemd/user/` | ✅ Yes |
| rclone cache | `~/.cache/rclone/` | ✅ Yes |
| NFS automount units | `/etc/systemd/system/` | ✅ Yes (`/etc` is persistent) |
| Mount points | `/var/mnt/nas`, `/var/mnt/nas2` | ⚠️ Recreate after update (see below) |

---

## Requirements

- Steam Deck running SteamOS 3.x
- A NAS with NFS shares enabled
- Network connection during initial setup

---

## Step 1 — Create NFS Mount Points

```bash
sudo mkdir -p /var/mnt/nas
sudo mkdir -p /var/mnt/nas2
```

> ⚠️ `/var/mnt` is **not** persistent across SteamOS updates. Add this to a boot script or recreate manually after updates (see Step 6).

---

## Step 2 — Create systemd NFS Automount Units

These files live in `/etc/systemd/system/` which **is** persistent.

### `/etc/systemd/system/var-mnt-nas.mount`

```ini
[Unit]
Description=Mount NFS Share
After=network-online.target

[Mount]
What=192.168.0.40:/volume2/steamdeck
Where=/var/mnt/nas
Type=nfs
Options=_netdev,timeo=5,retrans=1
TimeoutSec=10
```

### `/etc/systemd/system/var-mnt-nas.mount.d/override.conf`

```ini
[Unit]
Description=Mount NFS Share
After=network-online.target
Wants=network-online.target
ConditionPathExists=!/etc/no-network.mount

[Mount]
What=192.168.0.40:/volume2/steamdeck
Where=/var/mnt/nas
Type=nfs
Options=_netdev,noauto,x-systemd.automount,timeo=5,retrans=1,nofail
TimeoutSec=10

[Install]
WantedBy=multi-user.target
```

Repeat for `nas2` replacing paths and share names accordingly.

The `nofail` and `noauto` options ensure the system **never blocks on boot** if the NAS is unreachable. The `ConditionPathExists=!/etc/no-network.mount` flag allows you to manually disable network mounts by creating that file.

Enable the mounts:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now var-mnt-nas.mount
sudo systemctl enable --now var-mnt-nas2.mount
```

---

## Step 3 — Install rclone

Install the binary to `~/.local/bin/` so it persists across SteamOS updates:

```bash
mkdir -p ~/.local/bin

curl -L "https://github.com/rclone/rclone/releases/download/v1.73.1/rclone-v1.73.1-linux-amd64.zip" \
  -o /tmp/rclone.zip

unzip /tmp/rclone.zip -d /tmp/rclone
cp /tmp/rclone/rclone-v1.73.1-linux-amd64/rclone ~/.local/bin/
chmod +x ~/.local/bin/rclone

~/.local/bin/rclone version
```

---

## Step 4 — Configure rclone Remotes

```bash
~/.local/bin/rclone config
```

Create two remotes of type `local`:

| Name | Type | Path |
|---|---|---|
| `nas` | local | `/var/mnt/nas` |
| `nas2` | local | `/var/mnt/nas2` |

> **Important:** For `local` remotes, rclone ignores the configured path when you use `remote:` syntax. Always pass the full path explicitly when mounting: `nas:/var/mnt/nas`

---

## Step 5 — Create Mount Points and Cache Directory

```bash
mkdir -p ~/mnt/roms
mkdir -p ~/mnt/software
mkdir -p ~/.cache/rclone
```

---

## Step 6 — Create the rclone systemd User Service

```bash
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/rclone-nas.service << 'EOF'
[Unit]
Description=rclone cache mount NAS
After=var-mnt-nas.mount
Requires=var-mnt-nas.mount

[Service]
Type=notify
ExecStart=/home/deck/.local/bin/rclone mount nas:/var/mnt/nas /home/deck/mnt/roms \
  --vfs-cache-mode full \
  --vfs-cache-max-size 1G \
  --vfs-cache-max-age 720h \
  --cache-dir /home/deck/.cache/rclone \
  --log-level INFO
ExecStop=/bin/fusermount -u /home/deck/mnt/roms
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
```

Enable and start:

```bash
systemctl --user daemon-reload
systemctl --user enable --now rclone-nas.service
systemctl --user status rclone-nas.service
```

---

## Step 7 — Point Your Emulators to the Cache Mount

In RetroArch, EmulationStation DE, or RetroDeck, set your ROM paths to:

```
/home/deck/mnt/roms
```

This is the rclone FUSE mount — it transparently reads from the NAS when online and from the local cache when offline.

---

## Playing Games Offline

1. **While at home:** play normally. Every ROM you open gets cached automatically in `~/.cache/rclone/`
2. **Before leaving:** optionally verify what's cached: `du -sh ~/.cache/rclone/`
3. **Away from home:** the Deck will fail to reach the NAS, but rclone keeps serving cached files through `~/mnt/roms` — your emulators work as normal for anything already cached
4. **Cache limit:** set to 1GB by default. Least recently used files are evicted automatically when the limit is reached. Adjust `--vfs-cache-max-size` in the service file to your preference

---

## After a SteamOS Update

The only thing lost after an update is the `/var/mnt/nas` directory (not the mount config, not rclone, not the cache). Recreate it with:

```bash
sudo mkdir -p /var/mnt/nas
sudo mkdir -p /var/mnt/nas2
sudo systemctl daemon-reload
sudo systemctl start var-mnt-nas.mount
systemctl --user restart rclone-nas.service
```

---

## Verify Cache Is Working

```bash
# Check cache size before opening a file
du -sh ~/.cache/rclone/

# Open a ROM, then check again
du -sh ~/.cache/rclone/

# Simulate offline: stop the NAS mount
sudo systemctl stop var-mnt-nas.mount

# Files should still be listed
ls ~/mnt/roms
```

---

## Troubleshooting

**rclone service fails with "directory already mounted"**
```bash
fusermount -u ~/mnt/roms
systemctl --user restart rclone-nas.service
```

**NAS mount point missing after update**
```bash
sudo mkdir -p /var/mnt/nas && sudo systemctl start var-mnt-nas.mount
```

**Check rclone logs**
```bash
journalctl --user -u rclone-nas.service -n 50
```

**Check NFS mount status**
```bash
sudo systemctl status var-mnt-nas.mount
```

---

## Cache Size Reference

| Cache Size | Approximate ROMs |
|---|---|
| 1 GB | ~5 PS1 games, ~20 SNES games |
| 5 GB | ~5 PS2/GameCube games |
| 20 GB | ~10 PS2/GameCube games |

Adjust in `~/.config/systemd/user/rclone-nas.service` and restart the service.
