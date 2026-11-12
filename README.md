# PCBS2 Porter

A small app that **translates** Windows games (like PC Building Simulator 2) to run on Ubuntu — **no emulation**. Wine translates Win32 API calls to native Linux calls, and DXVK translates Direct3D 11 → Vulkan, so everything runs at native CPU/GPU speed.

## Run it

```bash
./run.sh
```

## Steps in the app

1. **Detect system** — checks your GPU (Intel Iris Xe ✓, AMD ✓, NVIDIA ✓), Vulkan and Wine status.
2. **Install dependencies** — opens a terminal and installs Wine + Vulkan drivers for your distro (Ubuntu/Debian, Fedora, Arch, openSUSE). Enter your password there if asked.
3. **Choose game folder** — pick the folder containing `PCBS2.exe` (or whatever your game folder is called).
4. **Create Wine prefix** — builds an isolated `~/Games/pcbs2-prefix` sandbox.
5. **Install DXVK** — downloads the latest DXVK release and places the translated DLLs next to the game.
6. **▶ Play** — launches the game with the right overrides (`-force-d3d11` is added automatically for Unity games like PCBS2).

## Performance notes

- First launch compiles shaders; the game may stutter briefly then run smooth afterwards.
- On Intel Iris Xe make sure `mesa-vulkan-drivers` is installed (step 1 handles it).
- Everything is isolated in `~/Games/` — deleting that folder removes it completely.

## Troubleshooting

- **"Vulkan NOT working"** after step 1 → log out/in once so the driver group permissions apply.
- **Black screen on launch** → run `wine winecfg` with `WINEPREFIX=~/Games/pcbs2-prefix` and enable a virtual desktop.
- **Game can't find saves** → saves live in `~/Games/pcbs2-prefix/drive_c/users/<you>/AppData/`.
