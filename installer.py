#!/usr/bin/env python3
"""
LazyDeck — NAS Cache Installer for Steam Deck
https://github.com/toaster-code/lazydeck
"""

import json, os, re, subprocess, sys, threading, urllib.request, zipfile
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    print("tkinter not found. Install: sudo pacman -S tk")
    sys.exit(1)

APP_NAME    = "LazyDeck"
APP_VERSION = "1.1.0"
GITHUB_URL  = "https://github.com/toaster-code/lazydeck"
RCLONE_VERSION = "v1.73.1"
RCLONE_URL  = f"https://github.com/rclone/rclone/releases/download/{RCLONE_VERSION}/rclone-{RCLONE_VERSION}-linux-amd64.zip"
HOME        = Path.home()
LANG_DIR    = Path(__file__).parent / "lang"

RCLONE_BIN     = HOME / ".local/bin/rclone"
RCLONE_CONF    = HOME / ".config/rclone/rclone.conf"
RCLONE_SERVICE = HOME / ".config/systemd/user/rclone-nas.service"
CACHE_DIR      = HOME / ".cache/rclone"
NFS_UNIT1      = Path("/etc/systemd/system/var-mnt-nas.mount")
NFS_UNIT2      = Path("/etc/systemd/system/var-mnt-nas2.mount")
REPAIR_SCRIPT  = HOME / "repair-lazydeck.sh"

BG="#1a1a2e"; BG2="#16213e"; ACCENT="#0f3460"; HIGHLIGHT="#e94560"
FG="#eaeaea"; FG2="#a0a0b0"; GREEN="#4caf50"; YELLOW="#ffc107"
FONT="DejaVu Sans"; MONO="Monospace"

def load_languages():
    langs = {}
    if not LANG_DIR.exists(): return langs
    for f in sorted(LANG_DIR.glob("*.json")):
        if f.stem == "TEMPLATE": continue
        try: langs[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except: pass
    return langs

class DetectionResult:
    def __init__(self):
        self.rclone_bin     = RCLONE_BIN.exists()
        self.rclone_conf    = RCLONE_CONF.exists()
        self.rclone_service = RCLONE_SERVICE.exists()
        self.nfs_unit1      = NFS_UNIT1.exists()
        self.cache_dir      = CACHE_DIR.exists()
        self.cache_size     = self._sz()
    def _sz(self):
        if not CACHE_DIR.exists(): return None
        r = subprocess.run(["du","-sh",str(CACHE_DIR)],capture_output=True,text=True)
        return r.stdout.split()[0] if r.returncode==0 else None
    @property
    def is_fresh(self): return not any([self.rclone_bin,self.rclone_conf,self.rclone_service,self.nfs_unit1])
    @property
    def is_complete(self): return all([self.rclone_bin,self.rclone_conf,self.rclone_service,self.nfs_unit1])
    @property
    def needs_repair(self): return not self.is_fresh and not self.is_complete

def read_existing_config():
    c = {}
    if RCLONE_SERVICE.exists():
        t = RCLONE_SERVICE.read_text()
        m = re.search(r"rclone mount \S+:(\S+)\s+(\S+)", t)
        if m: c["mount1"]=m.group(1); c["cache_mount"]=m.group(2)
        m = re.search(r"--vfs-cache-max-size\s+(\S+)", t)
        if m: c["cache_size"]=m.group(1)
        m = re.search(r"--vfs-cache-max-age\s+(\d+)h", t)
        if m: c["cache_age"]=m.group(1)
        m = re.search(r"--cache-dir\s+(\S+)", t)
        if m: c["cache_dir"]=m.group(1)
    if NFS_UNIT1.exists():
        t = NFS_UNIT1.read_text()
        m = re.search(r"What=(\S+):(\S+)", t)
        if m: c["nas_ip"]=m.group(1); c["share1"]=m.group(2)
        m = re.search(r"Where=(\S+)", t)
        if m: c["mount1"]=m.group(1)
    if NFS_UNIT2.exists():
        t = NFS_UNIT2.read_text()
        m = re.search(r"What=\S+:(\S+)", t)
        if m: c["share2"]=m.group(1)
        m = re.search(r"Where=(\S+)", t)
        if m: c["mount2"]=m.group(1)
    return c

def valid_ip(ip):
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip): return False
    return all(0<=int(p)<=255 for p in ip.split("."))

def valid_cache_size(s): return bool(re.match(r"^\d+[GgMm]$", s.strip()))

class Installer:
    def __init__(self, config, log_fn, done_fn, repair_only=False):
        self.c=config; self.log=log_fn; self.done=done_fn; self.repair_only=repair_only
    def run(self):
        try:
            self._repair() if self.repair_only else self._full()
            self.done(success=True)
        except Exception as e:
            self.log(f"\n✗ ERROR: {e}", color="red"); self.done(success=False)
    def _full(self):
        self._dirs(); self._rclone(); self._rclone_conf(); self._nfs(); self._service(); self._enable(); self._repair_script()
    def _repair(self):
        self.log("🔧 Repair mode...\n")
        det = DetectionResult()
        if not det.rclone_bin: self._rclone()
        else: self.log("  ✓ rclone binary OK")
        if not det.rclone_conf: self._rclone_conf()
        else: self.log("  ✓ rclone config OK")
        self._var_mnt()
        if not det.nfs_unit1: self._nfs()
        else:
            self.log("  ✓ NFS units OK — restarting")
            self._sudo(["systemctl","daemon-reload"])
            self._sudo(["systemctl","start","var-mnt-nas.mount"])
        if not det.rclone_service: self._service()
        else: self.log("  ✓ rclone service OK")
        subprocess.run(["systemctl","--user","daemon-reload"],check=False)
        subprocess.run(["systemctl","--user","restart","rclone-nas.service"],check=False)
        self.log("\n✓ Repair complete.", color="green")
    def _dirs(self):
        self.log(self.c.get("t",{}).get("install_step_dirs","Creating directories..."))
        for d in [Path(self.c["cache_dir"]),Path(self.c["cache_mount"]),
                  HOME/".local/bin",HOME/".config/systemd/user",HOME/".config/rclone"]:
            d.mkdir(parents=True,exist_ok=True); self.log(f"  mkdir {d}")
        self._var_mnt()
    def _var_mnt(self):
        for d in [self.c.get("mount1","/var/mnt/nas")] + ([self.c.get("mount2")] if self.c.get("share2") else []):
            self._sudo(["mkdir","-p",d]); self.log(f"  sudo mkdir {d}")
    def _rclone(self):
        if RCLONE_BIN.exists(): self.log("  rclone exists, skipping download."); return
        self.log(self.c.get("t",{}).get("install_step_rclone","Downloading rclone..."))
        tmp_zip=Path("/tmp/lazydeck_rclone.zip"); tmp_dir=Path("/tmp/lazydeck_rclone")
        self.log(f"  → {RCLONE_URL}")
        urllib.request.urlretrieve(RCLONE_URL,tmp_zip)
        if tmp_dir.exists():
            import shutil; shutil.rmtree(tmp_dir)
        tmp_dir.mkdir()
        with zipfile.ZipFile(tmp_zip,"r") as z: z.extractall(tmp_dir)
        found = list(tmp_dir.glob("rclone-*/rclone"))
        if not found: raise RuntimeError("rclone binary not found in zip")
        import shutil
        RCLONE_BIN.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(found[0],RCLONE_BIN); RCLONE_BIN.chmod(0o755)
        self.log(f"  Installed → {RCLONE_BIN}")
    def _rclone_conf(self):
        self.log(self.c.get("t",{}).get("install_step_rclone_config","Configuring rclone..."))
        RCLONE_CONF.parent.mkdir(parents=True,exist_ok=True)
        lines=["[nas]","type = local",f"path = {self.c.get('mount1','/var/mnt/nas')}",""]
        if self.c.get("share2"):
            lines+=["[nas2]","type = local",f"path = {self.c.get('mount2','/var/mnt/nas2')}",""]
        RCLONE_CONF.write_text("\n".join(lines),encoding="utf-8")
        self.log(f"  Written {RCLONE_CONF}")
    def _nfs(self):
        self.log(self.c.get("t",{}).get("install_step_systemd_nfs","Creating NFS units..."))
        self._nfs_unit(self.c.get("nas_ip",""),self.c.get("share1",""),self.c.get("mount1","/var/mnt/nas"),"var-mnt-nas","LazyDeck NFS Share")
        if self.c.get("share2"):
            self._nfs_unit(self.c.get("nas_ip",""),self.c.get("share2",""),self.c.get("mount2","/var/mnt/nas2"),"var-mnt-nas2","LazyDeck NFS Share 2")
    def _nfs_unit(self,ip,share,where,uname,desc):
        sd=Path("/etc/systemd/system"); uf=f"{uname}.mount"; od=sd/f"{uf}.d"
        self._sudo_write(sd/uf,
            f"[Unit]\nDescription={desc}\nAfter=network-online.target\n\n"
            f"[Mount]\nWhat={ip}:{share}\nWhere={where}\nType=nfs\n"
            f"Options=_netdev,timeo=5,retrans=1\nTimeoutSec=10\n")
        self._sudo(["mkdir","-p",str(od)])
        self._sudo_write(od/"override.conf",
            f"[Unit]\nDescription={desc}\nAfter=network-online.target\n"
            f"Wants=network-online.target\nConditionPathExists=!/etc/no-network.mount\n\n"
            f"[Mount]\nWhat={ip}:{share}\nWhere={where}\nType=nfs\n"
            f"Options=_netdev,noauto,x-systemd.automount,timeo=5,retrans=1,nofail\n"
            f"TimeoutSec=10\n\n[Install]\nWantedBy=multi-user.target\n")
        self.log(f"  Written {sd/uf}")
    def _service(self):
        self.log(self.c.get("t",{}).get("install_step_systemd_rclone","Creating rclone service..."))
        m1=self.c.get("mount1","/var/mnt/nas"); rs=self.c.get("roms_folder","").strip()
        nas=f"{m1}/{rs}".rstrip("/") if rs else m1
        cm=self.c.get("cache_mount",str(HOME/"mnt/roms")); cd=self.c.get("cache_dir",str(HOME/".cache/rclone"))
        cs=self.c.get("cache_size","1G"); ca=self.c.get("cache_age","720")
        RCLONE_SERVICE.parent.mkdir(parents=True,exist_ok=True)
        RCLONE_SERVICE.write_text(
            f"[Unit]\nDescription=LazyDeck rclone cache mount\n"
            f"After=var-mnt-nas.mount\nRequires=var-mnt-nas.mount\n\n"
            f"[Service]\nType=notify\n"
            f"ExecStart={RCLONE_BIN} mount nas:{nas} {cm} \\\n"
            f"  --vfs-cache-mode full \\\n  --vfs-cache-max-size {cs} \\\n"
            f"  --vfs-cache-max-age {ca}h \\\n  --cache-dir {cd} \\\n  --log-level INFO\n"
            f"ExecStop=/bin/fusermount -u {cm}\nRestart=on-failure\nRestartSec=10\n\n"
            f"[Install]\nWantedBy=default.target\n",encoding="utf-8")
        self.log(f"  Written {RCLONE_SERVICE}")
    def _enable(self):
        self.log(self.c.get("t",{}).get("install_step_enable","Enabling services..."))
        self._sudo(["systemctl","daemon-reload"])
        self._sudo(["systemctl","enable","--now","var-mnt-nas.mount"])
        if self.c.get("share2"): self._sudo(["systemctl","enable","--now","var-mnt-nas2.mount"])
        subprocess.run(["systemctl","--user","daemon-reload"],check=False)
        subprocess.run(["systemctl","--user","enable","--now","rclone-nas.service"],check=False)
        self.log("  Services enabled.")
    def _repair_script(self):
        m1=self.c.get("mount1","/var/mnt/nas"); m2=self.c.get("mount2","")
        lines=["#!/bin/bash",f"# LazyDeck repair — {GITHUB_URL}",f"sudo mkdir -p {m1}"]
        if m2: lines.append(f"sudo mkdir -p {m2}")
        lines+=["sudo systemctl daemon-reload","sudo systemctl start var-mnt-nas.mount",
                "systemctl --user restart rclone-nas.service",'echo "✓ LazyDeck restored."']
        REPAIR_SCRIPT.write_text("\n".join(lines)+"\n",encoding="utf-8"); REPAIR_SCRIPT.chmod(0o755)
        self.log(f"  Repair script → {REPAIR_SCRIPT}")
    def _sudo(self,cmd):
        r=subprocess.run(["sudo"]+cmd,capture_output=True,text=True)
        if r.returncode!=0: raise RuntimeError(f"sudo {' '.join(cmd)} failed:\n{r.stderr}")
        return r
    def _sudo_write(self,path,content):
        r=subprocess.run(["sudo","tee",str(path)],input=content,capture_output=True,text=True)
        if r.returncode!=0: raise RuntimeError(f"Failed to write {path}:\n{r.stderr}")

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.languages=load_languages(); self.t={}; self.config_data={}
        self.title(f"{APP_NAME} {APP_VERSION}"); self.configure(bg=BG)
        self.resizable(False,False); self._center(700,560); self._styles(); self._show_lang()
    def _center(self,w,h):
        self.update_idletasks()
        self.geometry(f"{w}x{h}+{(self.winfo_screenwidth()-w)//2}+{(self.winfo_screenheight()-h)//2}")
    def _styles(self):
        s=ttk.Style(self); s.theme_use("clam")
        s.configure(".",background=BG,foreground=FG,font=(FONT,11))
        s.configure("TFrame",background=BG); s.configure("TLabel",background=BG,foreground=FG,font=(FONT,11))
        s.configure("Hint.TLabel",background=BG,foreground=FG2,font=(FONT,9))
        s.configure("TEntry",fieldbackground=BG2,foreground=FG,insertcolor=FG)
        s.configure("TProgressbar",troughcolor=BG2,background=HIGHLIGHT,thickness=6)
        for n,bg,ac in [("Accent.TButton",HIGHLIGHT,"#c73652"),("Plain.TButton",ACCENT,"#1a4a80"),("Green.TButton","#2e7d32","#1b5e20")]:
            s.configure(n,background=bg,foreground=FG,font=(FONT,11,"bold"),padding=(14,8))
            s.map(n,background=[("active",ac)])
    def _clear(self):
        for w in self.winfo_children(): w.destroy()
    def _steps(self):
        return [self.t.get("step_welcome","Welcome"),self.t.get("step_nas","NAS"),
                self.t.get("step_cache","Cache"),self.t.get("step_install","Install"),
                self.t.get("step_done","Done")]
    def _stepbar(self,parent,cur):
        bar=ttk.Frame(parent); bar.pack(fill="x",pady=(0,10))
        for i,n in enumerate(self._steps()):
            c=HIGHLIGHT if i==cur else (GREEN if i<cur else FG2)
            ic="●" if i==cur else ("✓" if i<cur else "○")
            tk.Label(bar,text=f"{ic} {n}",bg=BG,fg=c,font=(FONT,9,"bold" if i==cur else "normal")).pack(side="left",padx=5)
            if i<len(self._steps())-1: tk.Label(bar,text="─",bg=BG,fg=FG2).pack(side="left")
    def _header(self,parent,title,step):
        tk.Label(parent,text=f"{APP_NAME} {APP_VERSION}  ·  {GITHUB_URL}",bg=BG,fg=FG2,font=(FONT,8)).pack(anchor="w")
        self._stepbar(parent,step)
        tk.Label(parent,text=title,bg=BG,fg=FG,font=(FONT,15,"bold")).pack(anchor="w",pady=(0,6))
        ttk.Separator(parent).pack(fill="x",pady=(0,12))
    def _field(self,parent,label,hint,default="",browse=False):
        row=ttk.Frame(parent); row.pack(fill="x",pady=3)
        ttk.Label(row,text=label).pack(anchor="w")
        sub=ttk.Frame(row); sub.pack(fill="x")
        var=tk.StringVar(value=default)
        ttk.Entry(sub,textvariable=var,font=(FONT,11)).pack(side="left",fill="x",expand=True)
        if browse:
            ttk.Button(sub,text=self.t.get("btn_browse","Browse"),style="Plain.TButton",
                       command=lambda:var.set(filedialog.askdirectory() or var.get())).pack(side="left",padx=(6,0))
        ttk.Label(row,text=hint,style="Hint.TLabel").pack(anchor="w")
        return var
    def _copy(self,text): self.clipboard_clear(); self.clipboard_append(text)

    # ── Language select ─────────────────────────
    def _show_lang(self):
        self._clear(); o=ttk.Frame(self); o.place(relx=.5,rely=.5,anchor="center")
        tk.Label(o,text="🦥",bg=BG,fg=FG,font=(FONT,48)).pack(pady=(0,4))
        tk.Label(o,text=APP_NAME,bg=BG,fg=FG,font=(FONT,22,"bold")).pack()
        tk.Label(o,text="Select language / Choisir la langue / Selecionar idioma",bg=BG,fg=FG2,font=(FONT,10)).pack(pady=(4,20))
        if not self.languages:
            tk.Label(o,text="No language files found in ./lang/",bg=BG,fg=HIGHLIGHT).pack(); return
        for code,data in self.languages.items():
            ttk.Button(o,text=data.get("lang_name",code),style="Accent.TButton",width=22,
                       command=lambda d=data:self._pick_lang(d)).pack(pady=4)
    def _pick_lang(self,data): self.t=data; self._show_detect()

    # ── Detection ───────────────────────────────
    def _show_detect(self):
        self._clear(); f=ttk.Frame(self,padding=28); f.pack(fill="both",expand=True)
        tk.Label(f,text=f"{APP_NAME} {APP_VERSION}  ·  {GITHUB_URL}",bg=BG,fg=FG2,font=(FONT,8)).pack(anchor="w")
        tk.Label(f,text=self.t.get("detect_title","Checking installation..."),bg=BG,fg=FG,font=(FONT,15,"bold")).pack(anchor="w",pady=(8,4))
        ttk.Separator(f).pack(fill="x",pady=(0,12))
        det=DetectionResult()
        for ok,label in [
            (det.rclone_bin,    self.t.get("detect_rclone_bin",    "rclone binary")),
            (det.rclone_conf,   self.t.get("detect_rclone_conf",   "rclone config")),
            (det.nfs_unit1,     self.t.get("detect_nfs_unit",      "NFS mount unit")),
            (det.rclone_service,self.t.get("detect_rclone_service","rclone systemd service")),
            (det.cache_dir,     self.t.get("detect_cache_dir",     "Cache directory")),
        ]:
            row=ttk.Frame(f); row.pack(fill="x",pady=2)
            tk.Label(row,text="✓" if ok else "✗",bg=BG,fg=GREEN if ok else HIGHLIGHT,font=(FONT,13,"bold"),width=2).pack(side="left")
            tk.Label(row,text=label,bg=BG,fg=FG,font=(FONT,11)).pack(side="left")
        if det.cache_size:
            tk.Label(f,text=f"   {self.t.get('detect_cache_used','Cache used')}: {det.cache_size}",bg=BG,fg=FG2,font=(FONT,9)).pack(anchor="w",pady=(2,0))
        ttk.Separator(f).pack(fill="x",pady=10)
        if det.is_fresh:     msg,col=self.t.get("detect_status_fresh","Not installed. Run the full installer."),FG2
        elif det.is_complete:msg,col=self.t.get("detect_status_ok","Installation complete and healthy. ✓"),GREEN
        else:                msg,col=self.t.get("detect_status_repair","Partial install — possibly after a SteamOS update."),YELLOW
        tk.Label(f,text=msg,bg=BG,fg=col,font=(FONT,10,"italic"),wraplength=600,justify="left").pack(anchor="w")
        btn_row=ttk.Frame(f); btn_row.pack(side="bottom",fill="x",pady=(16,0))
        ttk.Button(btn_row,text=self.t.get("btn_full_install","Full install →"),style="Accent.TButton",command=self._show_welcome).pack(side="right",padx=(6,0))
        if not det.is_fresh:
            ttk.Button(btn_row,text=self.t.get("btn_repair","🔧 Repair"),style="Green.TButton",
                       command=self._start_repair).pack(side="right")
    def _start_repair(self):
        c=read_existing_config(); c["t"]=self.t; self._show_progress(c,repair_only=True)

    # ── Welcome ─────────────────────────────────
    def _show_welcome(self):
        self._clear(); f=ttk.Frame(self,padding=28); f.pack(fill="both",expand=True)
        self._header(f,self.t.get("welcome_title","Welcome"),0)
        tk.Label(f,text=self.t.get("welcome_body",""),bg=BG,fg=FG,font=(FONT,11),justify="left").pack(anchor="w",pady=8)
        br=ttk.Frame(f); br.pack(side="bottom",fill="x",pady=(16,0))
        ttk.Button(br,text=self.t.get("btn_back","← Back"),style="Plain.TButton",command=self._show_detect).pack(side="left")
        ttk.Button(br,text=self.t.get("btn_next","Next →"),style="Accent.TButton",command=self._show_nas).pack(side="right")

    # ── NAS config ──────────────────────────────
    def _show_nas(self):
        self._clear(); f=ttk.Frame(self,padding=28); f.pack(fill="both",expand=True)
        self._header(f,self.t.get("nas_title","NAS Configuration"),1)
        cd=self.config_data
        self._v_ip    =self._field(f,self.t["nas_ip"],         self.t["nas_ip_hint"],         cd.get("nas_ip","192.168.0."))
        self._v_s1    =self._field(f,self.t["nas_share1"],     self.t["nas_share1_hint"],     cd.get("share1","/volume2/steamdeck"))
        self._v_m1    =self._field(f,self.t["nas_mountpoint1"],self.t["nas_mountpoint1_hint"],cd.get("mount1","/var/mnt/nas"))
        self._v_s2    =self._field(f,self.t["nas_share2"],     self.t["nas_share2_hint"],     cd.get("share2",""))
        self._v_m2    =self._field(f,self.t["nas_mountpoint2"],self.t["nas_mountpoint2_hint"],cd.get("mount2","/var/mnt/nas2"))
        self._v_roms  =self._field(f,self.t["nas_roms_folder"],self.t["nas_roms_folder_hint"],cd.get("roms_folder",""))
        br=ttk.Frame(f); br.pack(side="bottom",fill="x",pady=(16,0))
        ttk.Button(br,text=self.t["btn_back"],style="Plain.TButton",command=self._show_welcome).pack(side="left")
        ttk.Button(br,text=self.t["btn_next"],style="Accent.TButton",command=self._val_nas).pack(side="right")
    def _val_nas(self):
        ip=self._v_ip.get().strip(); s1=self._v_s1.get().strip(); m1=self._v_m1.get().strip()
        if not valid_ip(ip): messagebox.showerror("",self.t["error_ip"]); return
        if not s1: messagebox.showerror("",self.t["error_share"]); return
        if not m1.startswith("/"): messagebox.showerror("",self.t["error_mount"]); return
        self.config_data.update({"nas_ip":ip,"share1":s1,"mount1":m1,
            "share2":self._v_s2.get().strip(),"mount2":self._v_m2.get().strip(),"roms_folder":self._v_roms.get().strip()})
        self._show_cache()

    # ── Cache settings ──────────────────────────
    def _show_cache(self):
        self._clear(); f=ttk.Frame(self,padding=28); f.pack(fill="both",expand=True)
        self._header(f,self.t.get("cache_title","Cache Settings"),2)
        cd=self.config_data
        self._v_cs=self._field(f,self.t["cache_size"], self.t["cache_size_hint"], cd.get("cache_size","1G"))
        self._v_ca=self._field(f,self.t["cache_age"],  self.t["cache_age_hint"],  cd.get("cache_age","720"))
        self._v_cd=self._field(f,self.t["cache_dir"],  self.t["cache_dir_hint"],  cd.get("cache_dir",str(HOME/".cache/rclone")),browse=True)
        self._v_cm=self._field(f,self.t["cache_mount"],self.t["cache_mount_hint"],cd.get("cache_mount",str(HOME/"mnt/roms")),browse=True)
        tk.Label(f,text=self.t.get("cache_info",""),bg=BG,fg=YELLOW,font=(FONT,10),justify="left").pack(anchor="w",pady=(10,0))
        br=ttk.Frame(f); br.pack(side="bottom",fill="x",pady=(16,0))
        ttk.Button(br,text=self.t["btn_back"],style="Plain.TButton",command=self._show_nas).pack(side="left")
        ttk.Button(br,text=self.t["btn_install"],style="Accent.TButton",command=self._val_cache).pack(side="right")
    def _val_cache(self):
        size=self._v_cs.get().strip(); age=self._v_ca.get().strip()
        if not valid_cache_size(size): messagebox.showerror("",self.t["error_cache_size"]); return
        if not age.isdigit(): messagebox.showerror("",self.t["error_cache_age"]); return
        self.config_data.update({"cache_size":size.upper(),"cache_age":age,
            "cache_dir":self._v_cd.get().strip(),"cache_mount":self._v_cm.get().strip(),"t":self.t})
        self._show_progress(self.config_data)

    # ── Progress ────────────────────────────────
    def _show_progress(self,config,repair_only=False):
        self._clear(); f=ttk.Frame(self,padding=28); f.pack(fill="both",expand=True)
        title=self.t.get("repair_title","Repairing...") if repair_only else self.t.get("install_title","Installing...")
        self._header(f,title,3)
        self._prog=ttk.Progressbar(f,mode="indeterminate",length=420); self._prog.pack(pady=(0,10)); self._prog.start(12)
        tk.Label(f,text=self.t.get("log_title","Log"),bg=BG,fg=FG2,font=(FONT,9)).pack(anchor="w")
        self._log=tk.Text(f,bg=BG2,fg=FG,font=(MONO,9),relief="flat",state="disabled",height=16)
        self._log.pack(fill="both",expand=True)
        self._log.tag_config("red",foreground=HIGHLIGHT); self._log.tag_config("green",foreground=GREEN)
        threading.Thread(target=Installer(config,self._append,self._done,repair_only).run,daemon=True).start()
    def _append(self,msg,color=None):
        def _do():
            self._log.config(state="normal"); self._log.insert("end",msg+"\n",color or "")
            self._log.see("end"); self._log.config(state="disabled")
        self.after(0,_do)
    def _done(self,success):
        def _do():
            self._prog.stop()
            if success: self._show_done()
            else: messagebox.showerror("Failed","Check the log above for details.")
        self.after(0,_do)

    # ── Done ────────────────────────────────────
    def _show_done(self):
        self._clear(); f=ttk.Frame(self,padding=28); f.pack(fill="both",expand=True)
        self._header(f,self.t.get("done_title","Done!"),4)
        mp=self.config_data.get("cache_mount","~/mnt/roms")
        tk.Label(f,text=self.t.get("done_body","").replace("{mount_point}",mp),bg=BG,fg=FG,font=(FONT,11),justify="left").pack(anchor="w")
        ttk.Separator(f).pack(fill="x",pady=12)
        tk.Label(f,text=self.t.get("done_repair_title","After SteamOS updates"),bg=BG,fg=YELLOW,font=(FONT,10,"bold")).pack(anchor="w")
        tk.Label(f,text=self.t.get("done_repair_body",""),bg=BG,fg=FG2,font=(FONT,9)).pack(anchor="w",pady=(2,6))
        cmd=f"bash {REPAIR_SCRIPT}"; cf=ttk.Frame(f); cf.pack(fill="x")
        e=tk.Entry(cf,font=(MONO,10),bg=BG2,fg=GREEN,relief="flat",readonlybackground=BG2)
        e.insert(0,cmd); e.config(state="readonly"); e.pack(side="left",fill="x",expand=True,ipady=4)
        ttk.Button(cf,text=self.t.get("btn_copy","Copy"),style="Plain.TButton",command=lambda:self._copy(cmd)).pack(side="left",padx=(6,0))
        br=ttk.Frame(f); br.pack(side="bottom",fill="x",pady=(16,0))
        ttk.Button(br,text=self.t.get("btn_close","Close"),style="Accent.TButton",command=self.destroy).pack(side="right")

if __name__=="__main__":
    App().mainloop()
