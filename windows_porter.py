#!/usr/bin/env python3
"""
Windows Porter — translate Windows games to run natively on Ubuntu/Linux.

No emulation: Wine translates Win32 API calls to native Linux calls, and
DXVK translates Direct3D 9/10/11 to Vulkan. Every call happens at full
native speed on your CPU/GPU. Optional Turbo mode launches through Proton
(Valve's optimized Wine build via umu-launcher).

Supported GPUs: Intel (Iris Xe etc.), AMD, NVIDIA.
Supported distros: Ubuntu/Debian, Fedora, Arch/Manjaro/SteamOS-like, openSUSE.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.request import urlopen

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
except ImportError:
    print("=" * 60, file=sys.stderr)
    print("Windows Porter needs Python's tkinter (GUI toolkit).", file=sys.stderr)
    print("Install it by running this in a terminal:", file=sys.stderr)
    print("    sudo apt install python3-tk", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    sys.exit(1)

APP_TITLE = "Windows Porter"
PREFIX_DIR = Path.home() / "Games" / "pcbs2-prefix"
PROTON_PREFIX_DIR = Path.home() / "Games" / "proton-prefix"
DXVK_DIR = Path.home() / "Games" / "dxvk"
WINE_LOG = Path.home() / "Games" / "wine-game.log"

# PCI vendor IDs
VENDORS = {
    "10de": "NVIDIA",
    "8086": "Intel",
    "1002": "AMD",
}

# Wine DLLs that DXVK replaces
DXVK_DLLS = ["d3d9.dll", "d3d10core.dll", "d3d11.dll", "dxgi.dll"]

DISTRO_PKGS = {
    "debian": {
        "Intel/AMD": "mesa-vulkan-drivers mesa-utils libvulkan1 wine wine64 winetricks python3-tk",
        "NVIDIA": "libnvidia-gl-580 wine wine64 winetricks python3-tk",
        "base": "wine wine64 winetricks libvulkan1 mesa-vulkan-drivers mesa-utils python3-tk",
    },
    "fedora": {
        "Intel/AMD": "mesa-vulkan-drivers vulkan-loader wine winetricks python3-tkinter",
        "NVIDIA": "xorg-x11-drv-nvidia vulkan-loader wine winetricks python3-tkinter",
        "base": "wine winetricks vulkan-loader python3-tkinter",
    },
    "arch": {
        "Intel/AMD": "vulkan-intel vulkan-radeon vulkan-tools wine winetricks tk",
        "NVIDIA": "nvidia-utils wine winetricks tk",
        "base": "wine winetricks vulkan-icd-loader tk",
    },
    "suse": {
        "Intel/AMD": "Mesa-vulkan-drivers vulkan-loader wine winetricks python3-tk",
        "NVIDIA": "x11-video-nvidiaG06 vulkan-loader wine winetricks python3-tk",
        "base": "wine winetricks vulkan-loader python3-tk",
    },
}

INSTALL_CMDS = {
    "debian": ["sudo", "apt", "update"],
    "fedora": ["sudo", "dnf", "install", "-y"],
    "arch": ["sudo", "pacman", "-S", "--needed", "--noconfirm"],
    "suse": ["sudo", "zypper", "install", "-y"],
}


def run(cmd, **kw):
    """Run a command, returning CompletedProcess without raising."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kw)
    except FileNotFoundError:
        return None


def detect_distro():
    data = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k] = v.strip('"')
    except OSError:
        pass
    distro = (data.get("ID", "") + " " + data.get("ID_LIKE", "")).lower()
    if "ubuntu" in distro or "debian" in distro or "mint" in distro:
        return "debian", data.get("PRETTY_NAME", "Debian-like")
    if "fedora" in distro:
        return "fedora", data.get("PRETTY_NAME", "Fedora")
    if "arch" in distro or "manjaro" in distro or "steamos" in distro:
        return "arch", data.get("PRETTY_NAME", "Arch-like")
    if "suse" in distro:
        return "suse", data.get("PRETTY_NAME", "openSUSE")
    return None, data.get("PRETTY_NAME", "unknown")


def detect_gpu():
    """Return list of (vendor_name, pci_address) for display adapters."""
    r = run(["lspci", "-nn"])
    gpus = []
    if r is None:
        for p in sorted(Path("/sys/class/drm").glob("card?-device/vendor")):
            gpus.append(VENDORS.get(p.read_text().strip()[2:].lower(), "Unknown"))
        return gpus
    for line in r.stdout.splitlines():
        low = line.lower()
        if ("vga compatible" in low or "3d controller" in low or "display controller" in low):
            for vid, name in VENDORS.items():
                if f"[{vid}:" in low:
                    gpus.append(name)
                    break
    return gpus or ["Unknown"]


def have(cmd):
    return shutil.which(cmd) is not None


def vulkan_ok():
    """True if vulkaninfo reports at least one working device."""
    if not have("vulkaninfo"):
        return False
    r = run(["vulkaninfo", "--summary"])
    return r is not None and "deviceName" in (r.stdout + r.stderr)


def term_cmd():
    """Find an available terminal emulator to run sudo commands in."""
    for t in ("x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
              "xterm", "kgx", "alacritty", "kitty"):
        if have(t):
            return t
    return None


class Porter:
    """Core logic — no GUI here so it stays testable."""

    def __init__(self, log):
        self.log = log

    def install_deps(self, distro, gpu_vendor):
        if distro is None:
            self.log("Unsupported distro — install wine, winetricks and Vulkan drivers manually.")
            return
        pkg = DISTRO_PKGS[distro]
        if gpu_vendor == "NVIDIA":
            packages = pkg["NVIDIA"]
        elif gpu_vendor in ("Intel", "AMD"):
            packages = pkg["Intel/AMD"]
        else:
            packages = pkg["base"]
        # Include drivers for all vendors if unsure? Keep it lean: user's GPU first.
        base = INSTALL_CMDS[distro]

        if distro == "debian":
            script = f"sudo apt update && sudo apt install -y {packages}"
        else:
            script = " ".join(base[:-1] + [f"{base[-1]} {packages}"]).replace("  ", " ")
            if distro == "fedora":
                script = f"sudo dnf install -y {packages}"
            elif distro == "arch":
                script = f"sudo pacman -S --needed --noconfirm {packages}"
            elif distro == "suse":
                script = f"sudo zypper install -y {packages}"

        self.log(f"$ {script}")
        t = term_cmd()
        if t is None:
            self.log("No terminal emulator found — run the command above manually in a terminal.")
            return
        # Run inside the user's terminal so they can type the sudo password.
        if Path(t).name in ("gnome-terminal", "kgx"):
            subprocess.Popen([t, "--", "bash", "-c", script + "; echo Press Enter; read"])
        elif Path(t).name == "konsole":
            subprocess.Popen([t, "-e", "bash", "-c", script + "; echo Press Enter; read"])
        elif Path(t).name == "kitty":
            subprocess.Popen([t, "bash", "-c", script + "; echo Press Enter; read"])
        elif Path(t).name == "x-terminal-emulator":
            subprocess.Popen([t, "-e", "bash", "-c", script + "; echo Press Enter; read"])
        else:
            subprocess.Popen([t, "-e", "bash", "-c", script + "; echo Press Enter; read"])
        self.log("Opened a terminal window — enter your password there if asked.")

    def create_prefix(self):
        PREFIX_DIR.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, WINEPREFIX=str(PREFIX_DIR), WINEARCH="win64")
        self.log(f"Creating Wine prefix at {PREFIX_DIR} ...")
        r = subprocess.run(["wine", "wineboot", "-u"], env=env, capture_output=True, text=True)
        self.log(r.stderr.strip() or "Prefix ready (exit code %d)." % r.returncode)

    def find_game_exe(self, folder: Path):
        """Best-guess main executable of a game folder."""
        exes = [p for p in folder.rglob("*.exe") if not p.name.lower().startswith(("unitycrashhandler", "crashhandler", "uninstaller", "setup", "dxsetup"))]
        if not exes:
            return None
        # Prefer the biggest exe (the actual game binary) over helpers/launchers.
        return max(exes, key=lambda p: p.stat().st_size)

    def install_dxvk(self, exe_path: Path):
        """Download latest DXVK and drop the translated DLLs next to the game exe."""
        exe_path = Path(exe_path)
        DXVK_DIR.mkdir(parents=True, exist_ok=True)
        marker = DXVK_DIR / "VERSION"
        url = "https://api.github.com/repos/doitsujin/dxvk/releases/latest"

        self.log("Fetching latest DXVK release info...")
        with urlopen(url, timeout=30) as resp:
            tag = json.load(resp)["tag_name"]
        if marker.exists() and marker.read_text().strip() == tag:
            self.log(f"DXVK {tag} already downloaded.")
        else:
            dl = f"https://github.com/doitsujin/dxvk/releases/download/{tag}/dxvk-{tag[1:]}.tar.gz"
            self.log(f"Downloading {dl}")
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                with urlopen(dl, timeout=300) as resp, tmp as out:
                    shutil.copyfileobj(resp, out)
                archive = tmp.name
            self.log(f"Extracting DXVK {tag} ...")
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(DXVK_DIR)
            (DXVK_DIR / "VERSION").write_text(tag)
            os.unlink(archive)

        src64 = next(DXVK_DIR.glob(f"dxvk-{tag[1:]}/x64"))
        src32 = next(DXVK_DIR.glob(f"dxvk-{tag[1:]}/x32"))
        n = 0
        for src in (src64, src32):
            for dll in src.glob("*.dll"):
                shutil.copy2(dll, exe_path.parent / dll.name)
                n += 1
        self.log(f"Placed {n} translated DLLs next to {exe_path.name}")
        self.log("Direct3D 11 calls will now be translated to Vulkan at native speed.")

    def _game_env(self, workdir: Path):
        return dict(
            os.environ,
            WINEPREFIX=str(PREFIX_DIR),
            WINEARCH="win64",
            WINEESYNC="1",                      # fast thread sync, no overhead
            DXVK_STATE_CACHE_PATH=str(workdir),
            # 'n,b' = use the native DXVK dll, fallback to builtin
            WINEDLLOVERRIDES="d3d11=n,b;d3d10core=n,b;d3d9=n,b;dxgi=n,b",
        )

    def launch(self, exe_path: Path):
        exe_path = Path(exe_path)
        env = self._game_env(exe_path.parent)
        self.log(f"Launching {exe_path.name} via Wine translation layer...")
        cmd = ["wine", str(exe_path)]
        if "unity" in exe_path.parent.name.lower() or exe_path.name.lower().startswith("pcbs"):
            # Unity games: force Direct3D 11 (DXVK's best path)
            cmd += ["-force-d3d11"]
        WINE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(WINE_LOG, "w") as logf:
            subprocess.Popen(cmd, env=env, cwd=str(exe_path.parent),
                             stdout=logf, stderr=subprocess.STDOUT)
        self.log(f"Game started. Game output is logged to {WINE_LOG}")
        self.log("First launch compiles shaders — the window may say 'Not Responding' for a couple of minutes. Click 'Wait'. That's normal and gets faster.")

    def launch_proton(self, exe_path: Path):
        """Launch via umu-run → Proton (Valve's optimized Wine: fsync, FUTEX2,
        better shader handling, per-game patches). umu auto-downloads the
        latest GE-Proton build on first run."""
        exe_path = Path(exe_path)
        env = dict(
            os.environ,
            WINEPREFIX=str(PROTON_PREFIX_DIR),
            GAMEID="umu-0",
            UMU_LOG="debug",
            DXVK_STATE_CACHE_PATH=str(exe_path.parent),
        )
        PROTON_PREFIX_DIR.mkdir(parents=True, exist_ok=True)
        self.log(f"Launching {exe_path.name} via Proton (⚡ Turbo mode)...")
        WINE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(WINE_LOG, "w") as logf:
            subprocess.Popen(["umu-run", str(exe_path)], env=env,
                             cwd=str(exe_path.parent),
                             stdout=logf, stderr=subprocess.STDOUT)
        self.log(f"Game started. Output logged to {WINE_LOG}")
        self.log("First Proton launch builds its prefix + shaders — may take a few minutes once.")

    def launch_batch(self, batch_path: Path):
        batch_path = Path(batch_path)
        env = self._game_env(batch_path.parent)
        self.log(f"Launching batch file {batch_path.name} via Wine ...")
        WINE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(WINE_LOG, "w") as logf:
            subprocess.Popen(["wine", "cmd", "/c", str(batch_path)], env=env,
                             cwd=str(batch_path.parent),
                             stdout=logf, stderr=subprocess.STDOUT)
        self.log(f"Batch file launched. Game output is logged to {WINE_LOG}")
        self.log("First launch compiles shaders — the window may say 'Not Responding' for a couple of minutes. Click 'Wait'. That's normal and gets faster.")

    def launch_batch_proton(self, batch_path: Path):
        """Proton can't run .bat directly — extract the exe + args the bat
        launches (works for game launchers like PCBS2's Start.bat)."""
        batch_path = Path(batch_path)
        exe, args = None, []
        for line in batch_path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith(("::", "REM", "@", "set ", "Set ")):
                continue
            parts = line.split()
            if parts and parts[0].lower().endswith(".exe"):
                exe = batch_path.parent / parts[0]
                args = parts[1:]
                break
            if ".exe" in line.lower():
                # quoted path form "C:\...\game.exe" args...
                tok = line.split('"')[1] if '"' in line else None
                if tok and tok.lower().endswith(".exe"):
                    exe = batch_path.parent / Path(tok.replace("\\", "/"))
                    args = line.split('"')[2].split()
                    break
        if exe is None or not exe.exists():
            self.log("Couldn't find the .exe this batch file launches — use '▶ Run game' instead.")
            return
        self.log(f"Batch launches {exe.name} — starting via Proton...")
        exe_path = exe
        env = dict(
            os.environ,
            WINEPREFIX=str(PROTON_PREFIX_DIR),
            GAMEID="umu-0",
            UMU_LOG="debug",
            DXVK_STATE_CACHE_PATH=str(exe_path.parent),
        )
        PROTON_PREFIX_DIR.mkdir(parents=True, exist_ok=True)
        WINE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(WINE_LOG, "w") as logf:
            subprocess.Popen(["umu-run", str(exe_path)] + args, env=env,
                             cwd=str(exe_path.parent),
                             stdout=logf, stderr=subprocess.STDOUT)
        self.log(f"Game started via Proton. Output logged to {WINE_LOG}")

    def install_umu(self):
        """Download umu-launcher for this distro and hand it to a terminal
        for sudo install. Afterwards Turbo mode (Proton) is available."""
        data = {}
        try:
            for line in Path("/etc/os-release").read_text().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    data[k] = v.strip('"')
        except OSError:
            pass
        id_like = (data.get("ID", "") + " " + data.get("ID_LIKE", "")).lower()
        codename = data.get("VERSION_CODENAME", "")
        arch = platform.machine()
        suffix = None
        if "ubuntu" in id_like:
            suffix = f"ubuntu-{codename}" if codename in ("noble", "resolute") else "ubuntu-noble"
        elif "debian" in id_like:
            major = data.get("VERSION_ID", "").split(".")[0]
            try:
                dver = Path("/etc/debian_version").read_text().split(".")[0].strip()
                major = dver or major
            except OSError:
                pass
            suffix = f"debian-{major if major in ('12', '13') else '13'}"
        if suffix is None:
            self.log("Can't auto-pick an umu package for this distro — download one manually from:")
            self.log("https://github.com/Open-Wine-Components/umu-launcher/releases")
            return

        self.log("Fetching latest umu-launcher release...")
        with urlopen("https://api.github.com/repos/Open-Wine-Components/umu-launcher/releases/latest",
                     timeout=30) as resp:
            rel = json.load(resp)
        asset_url = None
        asset_name = None
        for a in rel["assets"]:
            n = a["name"]
            if arch == "x86_64" and "amd64" not in n:
                continue
            if arch == "aarch64" and "arm64" not in n:
                continue
            if suffix in n and n.endswith(".deb"):
                asset_url, asset_name = a["browser_download_url"], n
                break
        if asset_url is None:
            self.log(f"No matching .deb found for {suffix}. Pick one manually from the releases page.")
            return

        dl_path = Path.home() / "Downloads" / asset_name
        self.log(f"Downloading {asset_name} ...")
        with urlopen(asset_url, timeout=300) as resp, open(dl_path, "wb") as out:
            shutil.copyfileobj(resp, out)
        self.log(f"Saved to {dl_path}")
        script = f"sudo apt install -y {dl_path}; echo Press Enter; read"
        t = term_cmd()
        if t is None:
            self.log("No terminal emulator found — run the command above manually.")
            return
        if Path(t).name in ("gnome-terminal", "kgx"):
            subprocess.Popen([t, "--", "bash", "-c", script])
        elif Path(t).name == "kitty":
            subprocess.Popen([t, "bash", "-c", script])
        else:
            subprocess.Popen([t, "-e", "bash", "-c", script])
        self.log("Opened a terminal to finish installing umu (enter your password).")
        self.log("After it installs, restart Windows Porter — ⚡ Turbo mode will switch on.")

    def fix_startup(self):
        """Apply the two most common crash fixes for Unity/DXVK games."""
        env = dict(os.environ, WINEPREFIX=str(PREFIX_DIR), WINEARCH="win64")
        self.log("Setting Windows version to Windows 10 ...")
        r = subprocess.run(["wine", "winecfg", "-v", "win10"], env=env,
                           capture_output=True, text=True)
        self.log(r.stderr.strip() or "done")
        if have("winetricks"):
            self.log("Installing d3dcompiler_47 (shader compiler many Unity games need)...")
            r = subprocess.run(["winetricks", "-q", "d3dcompiler_47"], env=env,
                               capture_output=True, text=True)
            self.log(r.stderr.strip()[-500:] or "done")
        else:
            self.log("winetricks not installed — run step 1 first, then this again.")
        self.log("Startup fixes applied. Try launching the game again.")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("760x600")
        self.minsize(680, 520)
        self.porter = Porter(self.log)

        pad = dict(padx=10, pady=6)
        header = ttk.Label(self, text="Windows Porter — translate Windows games to Linux (no emulation)",
                           font=("Sans", 13, "bold"))
        header.pack(**pad, anchor="w")

        # --- system info row
        info = ttk.Frame(self)
        info.pack(fill="x", **pad)
        self.info_lbl = ttk.Label(info, text="Click “Detect my system” to begin.", justify="left")
        self.info_lbl.pack(anchor="w")

        # --- game folder row
        row = ttk.Frame(self)
        row.pack(fill="x", **pad)
        ttk.Button(row, text="Choose game folder…", command=self.choose_folder).pack(side="left")
        self.folder_lbl = ttk.Label(row, text="(no folder selected)")
        self.folder_lbl.pack(side="left", padx=8)
        self.game_exe = None
        self.game_dir = None

        # --- batch launcher (optional) row
        brow = ttk.Frame(self)
        brow.pack(fill="x", **pad)
        ttk.Button(brow, text="Choose batch launcher (.bat)…", command=self.choose_batch).pack(side="left", padx=2)
        self.batch_lbl = ttk.Label(brow, text="(no batch file selected — optional)")
        self.batch_lbl.pack(side="left", padx=8)
        ttk.Button(brow, text="✕ clear", command=self.clear_batch).pack(side="left", padx=2)
        self.batch_path = None

        # --- turbo mode toggle
        trow = ttk.Frame(self)
        trow.pack(fill="x", **pad)
        self.turbo_var = tk.BooleanVar(value=have("umu-run"))
        ttk.Checkbutton(
            trow, variable=self.turbo_var,
            text="⚡ Turbo mode — launch via Proton (Valve's optimized Wine)",
            command=self.turbo_changed).pack(side="left")
        self.turbo_hint = ttk.Label(trow, text="", foreground="#a04400")
        self.turbo_hint.pack(side="left", padx=8)

        # --- action buttons
        actions = ttk.Frame(self)
        actions.pack(fill="x", **pad)
        ttk.Button(actions, text="Detect system", command=self.detect).pack(side="left", padx=2)
        ttk.Button(actions, text="1. Install dependencies", command=self.install).pack(side="left", padx=2)
        ttk.Button(actions, text="2. Create Wine prefix", command=self.prefix).pack(side="left", padx=2)
        ttk.Button(actions, text="3. Install DXVK", command=self.dxvk).pack(side="left", padx=2)
        ttk.Button(actions, text="4. ▶ Run game", command=self.play).pack(side="left", padx=2)
        ttk.Button(actions, text="▶ Run .bat", command=self.run_batch).pack(side="left", padx=2)
        ttk.Button(actions, text="Install umu (Turbo)", command=self.install_umu).pack(side="left", padx=2)
        ttk.Button(actions, text="5. Fix startup", command=self.fix_startup).pack(side="left", padx=2)
        ttk.Button(actions, text="View game log", command=self.view_log).pack(side="left", padx=2)

        # --- log
        ttk.Label(self, text="Log:").pack(anchor="w", padx=10)
        self.logtxt = scrolledtext.ScrolledText(self, height=18, state="disabled", font=("Monospace", 9))
        self.logtxt.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # status bar
        self.status = ttk.Label(self, text="", relief="sunken", anchor="w")
        self.status.pack(fill="x", side="bottom")
        self.detect(silent=True)

    def log(self, msg):
        self.logtxt.configure(state="normal")
        self.logtxt.insert("end", msg + "\n")
        self.logtxt.see("end")
        self.logtxt.configure(state="disabled")
        self.status.config(text=msg[:120])

    def choose_batch(self):
        b = filedialog.askopenfilename(
            title="Select the batch file (.bat) to run through Wine",
            filetypes=[("Batch files", "*.bat *.cmd"), ("All files", "*.*")])
        if not b:
            return
        self.batch_path = Path(b)
        self.batch_lbl.config(text=str(self.batch_path))
        self.log(f"Batch file selected: {self.batch_path}")

    def clear_batch(self):
        self.batch_path = None
        self.batch_lbl.config(text="(no batch file selected — optional)")
        self.log("Batch file cleared — '▶ Run game' will launch the selected game exe directly.")

    def turbo_changed(self):
        if self.turbo_var.get() and not have("umu-run"):
            self.log("umu-launcher not installed — click 'Install umu (Turbo)' first.")
            self.log("Once installed, restart the app and ⚡ Turbo mode switches on automatically.")
        elif self.turbo_var.get():
            self.log("⚡ Turbo mode ON — games will launch via Proton into ~/Games/proton-prefix.")
        else:
            self.log("Turbo mode OFF — games launch via plain Wine into ~/Games/pcbs2-prefix.")

    def _use_turbo(self):
        return self.turbo_var.get() and have("umu-run")

    def run_batch(self):
        if not self.batch_path:
            messagebox.showinfo(APP_TITLE, "No batch file selected — that's fine, use '▶ Run game' instead.")
            return
        if self._use_turbo():
            self.porter.launch_batch_proton(self.batch_path)
            return
        if not PREFIX_DIR.exists():
            messagebox.showinfo(APP_TITLE, "Create the Wine prefix first (step 2).")
            return
        if not have("wine"):
            messagebox.showwarning(APP_TITLE, "Wine is not installed yet — run step 1 first.")
            return
        self.porter.launch_batch(self.batch_path)

    # ---------- actions ----------

    def detect(self, silent=False):
        distro, pretty = detect_distro()
        gpus = detect_gpu()
        vulkan = vulkan_ok()
        wine = have("wine")
        wt = have("winetricks")
        arch = platform.machine()
        lines = [
            f"Distro:  {pretty}",
            f"GPU:     {', '.join(dict.fromkeys(gpus))}",
            f"Vulkan:  {'working' if vulkan else 'NOT working (install Vulkan drivers)'}",
            f"Wine:    {'installed' if wine else 'not installed'}    "
            f"winetricks: {'installed' if wt else 'not installed'}",
            f"Prefix:  {PREFIX_DIR} {'(exists)' if PREFIX_DIR.exists() else '(will be created)'}",
        ]
        text = "\n".join(lines)
        self.info_lbl.config(text=text)
        self.distro, self.gpus = distro, gpus
        if not silent:
            self.log(text)

    def choose_folder(self):
        d = filedialog.askdirectory(title="Select the PC Building Simulator 2 folder (the one with the .exe)")
        if not d:
            return
        folder = Path(d)
        exe = self.porter.find_game_exe(folder)
        if exe is None:
            messagebox.showerror(APP_TITLE, "No .exe found in that folder.")
            return
        self.game_exe, self.game_dir = exe, folder
        self.folder_lbl.config(text=f"{exe.relative_to(folder) or exe.name}  ({folder})")
        self.log(f"Game selected: {exe}")

    def install(self):
        self.detect(silent=True)
        self.porter.install_deps(self.distro, self.gpus[0] if self.gpus else "Unknown")

    def prefix(self):
        self._check_wine(self.porter.create_prefix)

    def dxvk(self):
        if not self.game_exe:
            messagebox.showinfo(APP_TITLE, "Choose the game folder first.")
            return
        self._check_wine(lambda: self.porter.install_dxvk(self.game_exe))

    def play(self):
        if not self.game_exe:
            messagebox.showinfo(APP_TITLE, "Choose the game folder first.")
            return
        if self._use_turbo():
            self.porter.launch_proton(self.game_exe)
            return
        if not PREFIX_DIR.exists():
            messagebox.showinfo(APP_TITLE, "Create the Wine prefix first (step 2).")
            return
        self.porter.launch(self.game_exe)

    def fix_startup(self):
        if not have("wine"):
            messagebox.showwarning(APP_TITLE, "Wine is not installed yet — run step 1 first.")
            return
        self.porter.fix_startup()

    def install_umu(self):
        self.porter.install_umu()

    def view_log(self):
        if not WINE_LOG.exists():
            messagebox.showinfo(APP_TITLE, "No game log yet — launch the game first.")
            return
        win = tk.Toplevel(self)
        win.title(f"Game log — {WINE_LOG}")
        txt = scrolledtext.ScrolledText(win, width=120, height=40, font=("Monospace", 9))
        txt.pack(fill="both", expand=True)
        try:
            tail = WINE_LOG.read_text(errors="replace")[-20000:]
        except OSError as e:
            tail = str(e)
        txt.insert("1.0", tail)

    def _check_wine(self, fn):
        if not have("wine"):
            messagebox.showwarning(APP_TITLE, "Wine is not installed yet — run step 1 first.")
            return
        fn()


if __name__ == "__main__":
    App().mainloop()
