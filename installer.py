#!/usr/bin/env python3
"""
Steam Deck NAS Cache Installer
Graphical wizard to configure NFS automount + rclone VFS cache.
Language files live in ./lang/*.json — copy one to add a new language.
"""

import json
import os
import re
import subprocess
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, font, messagebox, ttk
except ImportError:
    print("tkinter not found. Install it with: sudo pacman -S tk")
    sys.exit(1)

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────
RCLONE_VERSION = "v1.73.1"
RCLONE_URL = (
    f"https://github.com/rclone/rclone/releases/download/"
    f"{RCLONE_VERSION}/rclone-{RCLONE_VERSION}-linux-amd64.zip"
)
HOME = Path.home()
LANG_DIR = Path(__file__).parent / "lang"

BG = "#1a1a2e"
BG2 = "#16213e"
ACCENT = "#0f3460"
HIGHLIGHT = "#e94560"
FG = "#eaeaea"
FG2 = "#a0a0b0"
GREEN = "#4caf50"
YELLOW = "#ffc107"
FONT_FAMILY = "DejaVu Sans"


# ──────────────────────────────────────────────
# Language loader
# ──────────────────────────────────────────────
def load_languages():
    langs = {}
    if not LANG_DIR.exists():
        return langs
    for f in sorted(LANG_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            langs[f.stem] = data
        except Exception:
            pass
    return langs


# ──────────────────────────────────────────────
# Validators
# ──────────────────────────────────────────────
def valid_ip(ip):
    pattern = r"^\d{1,3}(\.\d{1,3}){3}$"
    if not re.match(pattern, ip):
        return False
    return all(0 <= int(p) <= 255 for p in ip.split("."))


def valid_cache_size(s):
    return bool(re.match(r"^\d+[GgMm]$", s.strip()))


# ──────────────────────────────────────────────
# Installer logic (runs in thread)
# ──────────────────────────────────────────────
class Installer:
    def __init__(self, config, log_fn, done_fn):
        self.c = config
        self.log = log_fn
        self.done = done_fn

    def run(self):
        try:
            self._create_dirs()
            self._install_rclone()
            self._configure_rclone()
            self._create_nfs_units()
            self._create_rclone_service()
            self._enable_services()
            self._write_repair_script()
            self.done(success=True)
        except Exception as e:
            self.log(f"\n✗ ERROR: {e}", color="red")
            self.done(success=False)

    # ── steps ──────────────────────────────────

    def _create_dirs(self):
        t = self.c["t"]
        self.log(t["install_step_dirs"])
        dirs = [
            Path(self.c["cache_dir"]),
            Path(self.c["cache_mount"]),
            HOME / ".local" / "bin",
            HOME / ".config" / "systemd" / "user",
            HOME / ".config" / "rclone",
        ]
        if self.c.get("share2"):
            dirs.append(Path(self.c["mount2"]))

        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
            self.log(f"  mkdir {d}")

        # /var/mnt dirs need sudo
        sudo_dirs = [self.c["mount1"]]
        if self.c.get("share2"):
            sudo_dirs.append(self.c["mount2"])

        for d in sudo_dirs:
            self._sudo(["mkdir", "-p", d])
            self.log(f"  sudo mkdir {d}")

    def _install_rclone(self):
        t = self.c["t"]
        rclone_bin = HOME / ".local" / "bin" / "rclone"
        if rclone_bin.exists():
            self.log(f"  rclone already installed at {rclone_bin}, skipping download.")
            return

        self.log(t["install_step_rclone"])
        tmp_zip = Path("/tmp/rclone_installer.zip")
        tmp_dir = Path("/tmp/rclone_installer")

        self.log(f"  Downloading {RCLONE_URL}")
        urllib.request.urlretrieve(RCLONE_URL, tmp_zip)

        if tmp_dir.exists():
            import shutil
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir()

        with zipfile.ZipFile(tmp_zip, "r") as z:
            z.extractall(tmp_dir)

        extracted = list(tmp_dir.glob("rclone-*/rclone"))
        if not extracted:
            raise RuntimeError("rclone binary not found in zip")

        import shutil
        shutil.copy2(extracted[0], rclone_bin)
        rclone_bin.chmod(0o755)
        self.log(f"  Installed rclone to {rclone_bin}")

    def _configure_rclone(self):
        t = self.c["t"]
        self.log(t["install_step_rclone_config"])
        conf_path = HOME / ".config" / "rclone" / "rclone.conf"

        lines = [
            "[nas]",
            "type = local",
            f"path = {self.c['mount1']}",
            "",
        ]
        if self.c.get("share2"):
            lines += [
                "[nas2]",
                "type = local",
                f"path = {self.c['mount2']}",
                "",
            ]

        conf_path.write_text("\n".join(lines), encoding="utf-8")
        self.log(f"  Written {conf_path}")

    def _create_nfs_units(self):
        t = self.c["t"]
        self.log(t["install_step_systemd_nfs"])
        self._write_nfs_unit(
            self.c["nas_ip"],
            self.c["share1"],
            self.c["mount1"],
            "var-mnt-nas",
            "Mount NFS Share",
        )
        if self.c.get("share2"):
            self._write_nfs_unit(
                self.c["nas_ip"],
                self.c["share2"],
                self.c["mount2"],
                "var-mnt-nas2",
                "Mount NFS Share 2",
            )

    def _write_nfs_unit(self, ip, share, where, unit_name, desc):
        systemd_dir = Path("/etc/systemd/system")
        unit_file = f"{unit_name}.mount"
        override_dir = systemd_dir / f"{unit_file}.d"

        mount_content = f"""[Unit]
Description={desc}
After=network-online.target

[Mount]
What={ip}:{share}
Where={where}
Type=nfs
Options=_netdev,timeo=5,retrans=1
TimeoutSec=10
"""
        override_content = f"""[Unit]
Description={desc}
After=network-online.target
Wants=network-online.target
ConditionPathExists=!/etc/no-network.mount

[Mount]
What={ip}:{share}
Where={where}
Type=nfs
Options=_netdev,noauto,x-systemd.automount,timeo=5,retrans=1,nofail
TimeoutSec=10

[Install]
WantedBy=multi-user.target
"""
        # Write via sudo tee
        self._sudo_write(systemd_dir / unit_file, mount_content)
        self._sudo(["mkdir", "-p", str(override_dir)])
        self._sudo_write(override_dir / "override.conf", override_content)
        self.log(f"  Written {systemd_dir / unit_file}")

    def _create_rclone_service(self):
        t = self.c["t"]
        self.log(t["install_step_systemd_rclone"])
        rclone_bin = HOME / ".local" / "bin" / "rclone"
        mount1 = self.c["mount1"]
        roms_subfolder = self.c.get("roms_folder", "").strip()
        nas_path = f"{mount1}/{roms_subfolder}".rstrip("/") if roms_subfolder else mount1

        service = f"""[Unit]
Description=rclone cache mount NAS
After=var-mnt-nas.mount
Requires=var-mnt-nas.mount

[Service]
Type=notify
ExecStart={rclone_bin} mount nas:{nas_path} {self.c['cache_mount']} \\
  --vfs-cache-mode full \\
  --vfs-cache-max-size {self.c['cache_size']} \\
  --vfs-cache-max-age {self.c['cache_age']}h \\
  --cache-dir {self.c['cache_dir']} \\
  --log-level INFO
ExecStop=/bin/fusermount -u {self.c['cache_mount']}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""
        svc_path = HOME / ".config" / "systemd" / "user" / "rclone-nas.service"
        svc_path.write_text(service, encoding="utf-8")
        self.log(f"  Written {svc_path}")

    def _enable_services(self):
        t = self.c["t"]
        self.log(t["install_step_enable"])
        self._sudo(["systemctl", "daemon-reload"])
        self._sudo(["systemctl", "enable", "--now", "var-mnt-nas.mount"])
        if self.c.get("share2"):
            self._sudo(["systemctl", "enable", "--now", "var-mnt-nas2.mount"])

        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", "rclone-nas.service"],
            check=False,
        )
        self.log("  Services enabled.")

    def _write_repair_script(self):
        script = HOME / "repair-nas-cache.sh"
        mount1 = self.c["mount1"]
        mount2 = self.c.get("mount2", "")
        lines = [
            "#!/bin/bash",
            "# Run this after a SteamOS update to restore NAS mount points",
            f"sudo mkdir -p {mount1}",
        ]
        if mount2:
            lines.append(f"sudo mkdir -p {mount2}")
        lines += [
            "sudo systemctl daemon-reload",
            f"sudo systemctl start var-mnt-nas.mount",
            "systemctl --user restart rclone-nas.service",
            'echo "Done! NAS cache restored."',
        ]
        script.write_text("\n".join(lines) + "\n", encoding="utf-8")
        script.chmod(0o755)
        self.log(f"  Repair script: {script}")

    # ── helpers ────────────────────────────────

    def _sudo(self, cmd):
        result = subprocess.run(
            ["sudo"] + cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"sudo {' '.join(cmd)} failed:\n{result.stderr}")
        return result

    def _sudo_write(self, path, content):
        proc = subprocess.run(
            ["sudo", "tee", str(path)],
            input=content,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to write {path}:\n{proc.stderr}")


# ──────────────────────────────────────────────
# GUI
# ──────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.languages = load_languages()
        self.t = {}  # current translations
        self.config_data = {}

        self.title("NAS Cache Installer")
        self.configure(bg=BG)
        self.resizable(False, False)
        self._center(700, 540)

        self._setup_styles()
        self.frames = {}
        self._show_language_select()

    def _center(self, w, h):
        self.update_idletasks()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, font=(FONT_FAMILY, 11))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG, font=(FONT_FAMILY, 11))
        style.configure("Hint.TLabel", background=BG, foreground=FG2, font=(FONT_FAMILY, 9))
        style.configure("Title.TLabel", background=BG, foreground=FG, font=(FONT_FAMILY, 16, "bold"))
        style.configure("Step.TLabel", background=ACCENT, foreground=FG, font=(FONT_FAMILY, 10))
        style.configure(
            "Accent.TButton",
            background=HIGHLIGHT,
            foreground=FG,
            font=(FONT_FAMILY, 11, "bold"),
            padding=(14, 8),
        )
        style.map("Accent.TButton", background=[("active", "#c73652")])
        style.configure(
            "Plain.TButton",
            background=ACCENT,
            foreground=FG,
            font=(FONT_FAMILY, 11),
            padding=(14, 8),
        )
        style.map("Plain.TButton", background=[("active", "#1a4a80")])
        style.configure("TEntry", fieldbackground=BG2, foreground=FG, insertcolor=FG)
        style.configure(
            "TProgressbar",
            troughcolor=BG2,
            background=HIGHLIGHT,
            thickness=6,
        )

    def _clear(self):
        for w in self.winfo_children():
            w.destroy()

    # ── step bar ───────────────────────────────

    def _step_bar(self, parent, steps, current):
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 16))
        for i, name in enumerate(steps):
            active = i == current
            past = i < current
            color = HIGHLIGHT if active else (GREEN if past else FG2)
            lbl = tk.Label(
                bar,
                text=f"{'●' if active else ('✓' if past else '○')} {name}",
                bg=BG,
                fg=color,
                font=(FONT_FAMILY, 9, "bold" if active else "normal"),
            )
            lbl.pack(side="left", padx=8)
            if i < len(steps) - 1:
                tk.Label(bar, text="─", bg=BG, fg=FG2).pack(side="left")

    def _steps(self):
        t = self.t
        return [
            t.get("step_welcome", "Welcome"),
            t.get("step_nas", "NAS"),
            t.get("step_cache", "Cache"),
            t.get("step_install", "Install"),
            t.get("step_done", "Done"),
        ]

    # ── header ─────────────────────────────────

    def _header(self, parent, title, step_index):
        tk.Label(parent, text=self.t.get("app_title", "Installer"),
                 bg=BG, fg=FG2, font=(FONT_FAMILY, 9)).pack(anchor="w")
        self._step_bar(parent, self._steps(), step_index)
        tk.Label(parent, text=title, bg=BG, fg=FG,
                 font=(FONT_FAMILY, 15, "bold")).pack(anchor="w", pady=(0, 12))
        ttk.Separator(parent).pack(fill="x", pady=(0, 16))

    # ── field helper ───────────────────────────

    def _field(self, parent, label, hint, default="", browse=False):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label).pack(anchor="w")
        sub = ttk.Frame(row)
        sub.pack(fill="x")
        var = tk.StringVar(value=default)
        entry = ttk.Entry(sub, textvariable=var, font=(FONT_FAMILY, 11))
        entry.pack(side="left", fill="x", expand=True)
        if browse:
            ttk.Button(
                sub,
                text=self.t.get("btn_browse", "Browse"),
                style="Plain.TButton",
                command=lambda: var.set(filedialog.askdirectory() or var.get()),
            ).pack(side="left", padx=(6, 0))
        ttk.Label(row, text=hint, style="Hint.TLabel").pack(anchor="w")
        return var

    # ──────────────────────────────────────────
    # Screen 0 — Language select
    # ──────────────────────────────────────────
    def _show_language_select(self):
        self._clear()
        outer = ttk.Frame(self)
        outer.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(outer, text="🌐", bg=BG, fg=FG, font=(FONT_FAMILY, 36)).pack(pady=(0, 8))
        tk.Label(outer, text="Select language / Choisir la langue / Selecionar idioma",
                 bg=BG, fg=FG2, font=(FONT_FAMILY, 11)).pack(pady=(0, 24))

        if not self.languages:
            tk.Label(outer, text="No language files found in ./lang/",
                     bg=BG, fg=HIGHLIGHT).pack()
            return

        for code, data in self.languages.items():
            name = data.get("lang_name", code)
            ttk.Button(
                outer,
                text=name,
                style="Accent.TButton",
                width=20,
                command=lambda d=data: self._select_language(d),
            ).pack(pady=4)

    def _select_language(self, data):
        self.t = data
        self.title(self.t.get("app_title", "Installer"))
        self._show_welcome()

    # ──────────────────────────────────────────
    # Screen 1 — Welcome
    # ──────────────────────────────────────────
    def _show_welcome(self):
        self._clear()
        f = ttk.Frame(self, padding=28)
        f.pack(fill="both", expand=True)

        self._header(f, self.t.get("welcome_title", "Welcome"), 0)

        tk.Label(
            f,
            text=self.t.get("welcome_body", ""),
            bg=BG, fg=FG,
            font=(FONT_FAMILY, 11),
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=8)

        btn_row = ttk.Frame(f)
        btn_row.pack(side="bottom", fill="x", pady=(16, 0))
        ttk.Button(btn_row, text=self.t["btn_next"], style="Accent.TButton",
                   command=self._show_nas).pack(side="right")

    # ──────────────────────────────────────────
    # Screen 2 — NAS config
    # ──────────────────────────────────────────
    def _show_nas(self):
        self._clear()
        f = ttk.Frame(self, padding=28)
        f.pack(fill="both", expand=True)

        self._header(f, self.t.get("nas_title", "NAS Configuration"), 1)

        self._v_ip = self._field(f, self.t["nas_ip"], self.t["nas_ip_hint"],
                                 self.config_data.get("nas_ip", "192.168.0."))
        self._v_share1 = self._field(f, self.t["nas_share1"], self.t["nas_share1_hint"],
                                     self.config_data.get("share1", "/volume2/steamdeck"))
        self._v_mount1 = self._field(f, self.t["nas_mountpoint1"], self.t["nas_mountpoint1_hint"],
                                     self.config_data.get("mount1", "/var/mnt/nas"))
        self._v_share2 = self._field(f, self.t["nas_share2"], self.t["nas_share2_hint"],
                                     self.config_data.get("share2", ""))
        self._v_mount2 = self._field(f, self.t["nas_mountpoint2"], self.t["nas_mountpoint2_hint"],
                                     self.config_data.get("mount2", "/var/mnt/nas2"))
        self._v_roms = self._field(f, self.t["nas_roms_folder"], self.t["nas_roms_folder_hint"],
                                   self.config_data.get("roms_folder", ""))

        btn_row = ttk.Frame(f)
        btn_row.pack(side="bottom", fill="x", pady=(16, 0))
        ttk.Button(btn_row, text=self.t["btn_back"], style="Plain.TButton",
                   command=self._show_welcome).pack(side="left")
        ttk.Button(btn_row, text=self.t["btn_next"], style="Accent.TButton",
                   command=self._validate_nas).pack(side="right")

    def _validate_nas(self):
        ip = self._v_ip.get().strip()
        share1 = self._v_share1.get().strip()
        mount1 = self._v_mount1.get().strip()

        if not valid_ip(ip):
            messagebox.showerror("", self.t["error_ip"])
            return
        if not share1:
            messagebox.showerror("", self.t["error_share"])
            return
        if not mount1.startswith("/"):
            messagebox.showerror("", self.t["error_mount"])
            return

        self.config_data.update({
            "nas_ip": ip,
            "share1": share1,
            "mount1": mount1,
            "share2": self._v_share2.get().strip(),
            "mount2": self._v_mount2.get().strip(),
            "roms_folder": self._v_roms.get().strip(),
        })
        self._show_cache()

    # ──────────────────────────────────────────
    # Screen 3 — Cache settings
    # ──────────────────────────────────────────
    def _show_cache(self):
        self._clear()
        f = ttk.Frame(self, padding=28)
        f.pack(fill="both", expand=True)

        self._header(f, self.t.get("cache_title", "Cache Settings"), 2)

        self._v_csize = self._field(f, self.t["cache_size"], self.t["cache_size_hint"],
                                    self.config_data.get("cache_size", "1G"))
        self._v_cage = self._field(f, self.t["cache_age"], self.t["cache_age_hint"],
                                   self.config_data.get("cache_age", "720"))
        self._v_cdir = self._field(f, self.t["cache_dir"], self.t["cache_dir_hint"],
                                   self.config_data.get("cache_dir",
                                                        str(HOME / ".cache" / "rclone")),
                                   browse=True)
        self._v_cmount = self._field(f, self.t["cache_mount"], self.t["cache_mount_hint"],
                                     self.config_data.get("cache_mount",
                                                          str(HOME / "mnt" / "roms")),
                                     browse=True)

        tk.Label(f, text=self.t.get("cache_info", ""),
                 bg=BG, fg=YELLOW, font=(FONT_FAMILY, 10),
                 justify="left").pack(anchor="w", pady=(12, 0))

        btn_row = ttk.Frame(f)
        btn_row.pack(side="bottom", fill="x", pady=(16, 0))
        ttk.Button(btn_row, text=self.t["btn_back"], style="Plain.TButton",
                   command=self._show_nas).pack(side="left")
        ttk.Button(btn_row, text=self.t["btn_install"], style="Accent.TButton",
                   command=self._validate_cache).pack(side="right")

    def _validate_cache(self):
        size = self._v_csize.get().strip()
        age = self._v_cage.get().strip()

        if not valid_cache_size(size):
            messagebox.showerror("", self.t["error_cache_size"])
            return
        if not age.isdigit():
            messagebox.showerror("", self.t["error_cache_age"])
            return

        self.config_data.update({
            "cache_size": size.upper(),
            "cache_age": age,
            "cache_dir": self._v_cdir.get().strip(),
            "cache_mount": self._v_cmount.get().strip(),
            "t": self.t,
        })
        self._show_install()

    # ──────────────────────────────────────────
    # Screen 4 — Install
    # ──────────────────────────────────────────
    def _show_install(self):
        self._clear()
        f = ttk.Frame(self, padding=28)
        f.pack(fill="both", expand=True)

        self._header(f, self.t.get("install_title", "Installing..."), 3)

        self._progress = ttk.Progressbar(f, mode="indeterminate", length=400)
        self._progress.pack(pady=(0, 12))
        self._progress.start(12)

        log_frame = ttk.Frame(f)
        log_frame.pack(fill="both", expand=True)
        tk.Label(log_frame, text=self.t.get("log_title", "Log"),
                 bg=BG, fg=FG2, font=(FONT_FAMILY, 9)).pack(anchor="w")

        self._log_text = tk.Text(
            log_frame,
            bg=BG2, fg=FG,
            font=("Monospace", 9),
            relief="flat",
            state="disabled",
            height=14,
        )
        self._log_text.pack(fill="both", expand=True)
        self._log_text.tag_config("red", foreground=HIGHLIGHT)
        self._log_text.tag_config("green", foreground=GREEN)

        installer = Installer(self.config_data, self._append_log, self._install_done)
        threading.Thread(target=installer.run, daemon=True).start()

    def _append_log(self, msg, color=None):
        def _do():
            self._log_text.config(state="normal")
            self._log_text.insert("end", msg + "\n", color or "")
            self._log_text.see("end")
            self._log_text.config(state="disabled")
        self.after(0, _do)

    def _install_done(self, success):
        def _do():
            self._progress.stop()
            if success:
                self._show_done()
            else:
                messagebox.showerror("Installation failed",
                                     "Check the log above for details.")
        self.after(0, _do)

    # ──────────────────────────────────────────
    # Screen 5 — Done
    # ──────────────────────────────────────────
    def _show_done(self):
        self._clear()
        f = ttk.Frame(self, padding=28)
        f.pack(fill="both", expand=True)

        self._header(f, self.t.get("done_title", "Done!"), 4)

        mount_point = self.config_data.get("cache_mount", "~/mnt/roms")
        body = self.t.get("done_body", "").replace("{mount_point}", mount_point)

        tk.Label(f, text=body, bg=BG, fg=FG, font=(FONT_FAMILY, 11),
                 justify="left", anchor="w").pack(anchor="w")

        # Repair command box
        ttk.Separator(f).pack(fill="x", pady=12)
        tk.Label(f, text=self.t.get("done_repair_title", "After updates"),
                 bg=BG, fg=YELLOW, font=(FONT_FAMILY, 10, "bold")).pack(anchor="w")
        tk.Label(f, text=self.t.get("done_repair_body", ""),
                 bg=BG, fg=FG2, font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(2, 6))

        repair_cmd = f"bash {HOME}/repair-nas-cache.sh"
        cmd_frame = ttk.Frame(f)
        cmd_frame.pack(fill="x")
        cmd_entry = tk.Entry(cmd_frame, font=("Monospace", 10), bg=BG2, fg=GREEN,
                             relief="flat", readonlybackground=BG2)
        cmd_entry.insert(0, repair_cmd)
        cmd_entry.config(state="readonly")
        cmd_entry.pack(side="left", fill="x", expand=True, ipady=4)
        ttk.Button(cmd_frame, text=self.t["btn_copy"], style="Plain.TButton",
                   command=lambda: self._copy(repair_cmd)).pack(side="left", padx=(6, 0))

        btn_row = ttk.Frame(f)
        btn_row.pack(side="bottom", fill="x", pady=(16, 0))
        ttk.Button(btn_row, text=self.t["btn_close"], style="Accent.TButton",
                   command=self.destroy).pack(side="right")

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
