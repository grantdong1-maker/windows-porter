# Windows Porter

Translate Windows games to run natively on Ubuntu/Linux — **no emulation**.
Wine translates Win32 API calls to native Linux calls, DXVK translates
Direct3D 9/10/11 to Vulkan, and optional **Turbo mode** launches through
Proton (Valve's optimized Wine build) for maximum performance.

## Run it

```bash
./run.sh
```

If you get a `No module named 'tkinter'` error first:

```bash
sudo apt install python3-tk
```

## Steps in the app

1. **Detect system** — shows GPU (Intel / AMD / NVIDIA), Vulkan and Wine status.
2. **Install dependencies** — opens a terminal and installs Wine + Vulkan
   drivers + `python3-tk` for your distro (Ubuntu/Debian, Fedora, Arch,
   openSUSE). Enter your password there.
3. **Choose game folder…** — pick the folder containing the game's `.exe`.
4. **Create Wine prefix** — builds an isolated sandbox in `~/Games/pcbs2-prefix`.
5. **Install DXVK** — downloads the latest DXVK and places translated DLLs
   next to the game (each game folder needs this once).
6. **▶ Run game** — launches the exe directly. **▶ Run .bat** runs a batch
   launcher instead (optional). Unity games get `-force-d3d11` automatically.

## ⚡ Turbo mode (Proton)

Click **"Install umu (Turbo)"**, restart the app, then just launch games as
usual — they run through Proton (fsync/FUTEX2 thread sync, pre-cached shader
pipeline, Valve's per-game patches) into `~/Games/proton-prefix`. First
launch auto-downloads GE-Proton.

## Troubleshooting

- **"Vulkan NOT working"** after step 2 → log out/in once.
- **Game runs but textures are missing** → re-run **Install DXVK**; verify the
  game folder now contains large (`> 1 MB`) `d3d11.dll` / `dxgi.dll` files.
- **Still crashing at launch** → click **"5. Fix startup"** (sets Windows 10
  mode + installs `d3dcompiler_47`), then check **"View game log"**.
- Everything lives in `~/Games/` — deleting that folder removes the setup.
