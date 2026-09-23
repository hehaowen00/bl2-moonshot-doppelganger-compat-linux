# bl2-moonshot-doppelganger-compat-linux

Compatibility patch (r10) that lets the third-party **Doppelganger 0.9.4** mod run next to
**Project Moonshot v0.1.2** in Borderlands 2, on Linux and Steam Deck. See `README.txt` for what
it changes and why.

## Install

### Requirements

- Borderlands 2 with **Project Moonshot v0.1.2** installed (Steam Deck / Linux installer), which
  also sets up the PythonSDK mod manager.
- The Mechromancer DLC (`DLC/Tulip`) and **The Pre-Sequel** installed (Doppelganger builds its game
  files from them on first launch).
- The original **Doppelganger 0.9.4** release zip,
  `Doppelganger 0.9.4 666 0.9.4 2026-09-22T23-25Z Frx46EFn.zip` (not included here).
- `python3` (ships with SteamOS; no extra packages needed).

### Steps

1. Close Borderlands 2. On a Steam Deck, switch to Desktop Mode.
2. Get this repo:
   ```sh
   git clone https://github.com/hehaowen00/bl2-moonshot-doppelganger-compat-linux.git
   cd bl2-moonshot-doppelganger-compat-linux
   ```
3. Put the Doppelganger 0.9.4 zip in this folder, the folder above it, or `~/Downloads`.
4. Run the installer and follow the prompts:
   ```sh
   ./install-doppelcompat.sh
   ```
   - Pick the Borderlands 2 folder marked `[Moonshot]`.
   - Pick the Doppelganger 0.9.4 zip.
5. Start Borderlands 2 from Steam. Doppelganger builds its game files on first launch and asks for
   a restart. Type `doppelsetup status` in the console to check the build.

You get two Jack tiles: **Jack** (Doppelganger) and **Jack (TPS)** (Moonshot).

### Without the prompts

```sh
./install-doppelcompat.sh --bl2 "<Borderlands 2 folder>" --zip "<Doppelganger 0.9.4 zip>"
```

The Steam default is usually `~/.local/share/Steam/steamapps/common/Borderlands 2`; run
`./install-doppelcompat.sh --list` to see the folders it finds.

### Update from an earlier revision

Pull the latest version, then re-apply the patch without reinstalling Doppelganger:

```sh
git pull
./install-doppelcompat.sh --bl2 "<Borderlands 2 folder>" --compat-only
```

### Check or remove

```sh
./install-doppelcompat.sh --bl2 "<Borderlands 2 folder>" --status
./install-doppelcompat.sh --bl2 "<Borderlands 2 folder>" --uninstall
```

`--uninstall` removes the files this patch installed (Doppelganger included, if the patch installed
it) and restores the Moonshot file it edited, plus Doppelganger's edited files if you had installed
Doppelganger yourself. Run `doppelsetup remove` in the game console first if you also want
Doppelganger's generated game files removed.
