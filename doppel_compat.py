#!/usr/bin/env python3
"""Moonshot compatibility patch for the third-party Doppelganger mod, 0.9.4.

Installs Doppelganger from its own release ZIP (verified against the ZIP's MANIFEST.txt), moves
aside files that an older Doppelganger release shipped and 0.9.4 no longer does, and applies the
compatibility layers: Moonshot's (sdk_mods/moonshot/__init__.py) and a map-change guard for
Doppelganger's Digi-Jack clones (sdk_mods/Doppelganger/digijack.py).

Standard library only; runs natively on Linux / Steam Deck (no Proton needed).

    python3 doppel_compat.py                      interactive: find the game folder and the ZIP
    python3 doppel_compat.py --bl2 DIR --zip ZIP  install / update
    python3 doppel_compat.py --bl2 DIR --compat-only
    python3 doppel_compat.py --bl2 DIR --status
    python3 doppel_compat.py --bl2 DIR --uninstall
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
PATCH_VERSION = "0.9.4-r10"
TESTED_VERSION = "0.9.4"
TESTED_MANIFEST_SHA1 = "b1ca44534a77630e74ebed1735fded8d0b4d665c"
RECORD = "moonshot_doppel_compat_manifest.json"      # same record file as the 0.9.2 Windows installer
STALE_DIR = "moonshot_doppel_compat_backup"
HIS_DIR = "sdk_mods/Doppelganger"
MOONSHOT_INIT = "sdk_mods/moonshot/__init__.py"


def log(msg=""):
    try:
        print(msg, flush=True)
    except (BrokenPipeError, OSError):     # output closed (piped into head, window gone): keep working
        pass


def sha1_bytes(b):
    return hashlib.sha1(b).hexdigest()


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def utc_stamp():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


# --- finding the game ---------------------------------------------------------------------------

def looks_like_bl2(root):
    return bool(root) and os.path.isdir(os.path.join(root, "WillowGame")) and os.path.isdir(os.path.join(root, "Binaries"))


def steam_libraries():
    home = os.path.expanduser("~")
    roots = [os.path.join(home, p) for p in (".steam/steam", ".local/share/Steam", ".steam/root",
                                             ".var/app/com.valvesoftware.Steam/.local/share/Steam")]
    libs, seen = [], set()
    for root in roots:
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        found = [root]
        try:
            with open(vdf, encoding="utf-8", errors="replace") as fh:
                found += re.findall(r'"path"\s+"([^"]+)"', fh.read())
        except OSError:
            pass
        for lib in found:
            lib = lib.replace("\\\\", "\\")
            key = os.path.realpath(lib)
            if key not in seen and os.path.isdir(os.path.join(lib, "steamapps")):
                seen.add(key)
                libs.append(lib)
    for media in glob.glob("/run/media/*/*") + glob.glob("/run/media/*"):
        key = os.path.realpath(media)
        if key not in seen and os.path.isdir(os.path.join(media, "steamapps")):
            seen.add(key)
            libs.append(media)
    return libs


def find_game_folders():
    """Every Borderlands 2 folder, Moonshot's separate "Project Moonshot" instance included."""
    cands = []
    for lib in steam_libraries():
        common = os.path.join(lib, "steamapps", "common")
        cands.append(os.path.join(common, "Borderlands 2"))
        try:
            for name in os.listdir(common):
                if os.path.isfile(os.path.join(common, name, "moonshot_instance.json")):
                    cands.append(os.path.join(common, name))
        except OSError:
            pass
    cands.append(os.path.join(os.path.expanduser("~"), "Games", "Project Moonshot"))
    out, seen = [], set()
    for c in cands:
        key = os.path.realpath(c)
        if key not in seen and looks_like_bl2(c):
            seen.add(key)
            out.append(c)
    return out


def find_zips():
    places = [HERE, os.path.dirname(HERE), os.path.join(os.path.expanduser("~"), "Downloads")]
    out = []
    for place in places:
        for z in sorted(glob.glob(os.path.join(place, "Doppelganger*.zip")) + glob.glob(os.path.join(place, "*", "Doppelganger*.zip"))):
            if os.path.realpath(z) not in [os.path.realpath(o) for o in out]:
                out.append(z)
    return out


# --- his release --------------------------------------------------------------------------------

def safe_rel(rel):
    rel = rel.replace("\\", "/").strip()
    parts = rel.split("/")
    if not rel or rel.startswith("/") or ":" in rel or any(p in ("", ".", "..") for p in parts):
        raise ValueError("unsafe path in MANIFEST.txt: " + rel)
    return rel


def read_release(zip_path):
    """(version, manifest sha1, shipped rows [(rel, size, sha1)]) from the release ZIP."""
    with zipfile.ZipFile(zip_path) as zf:
        names = {n.lower(): n for n in zf.namelist()}
        if "manifest.txt" not in names:
            raise ValueError("this ZIP has no MANIFEST.txt - is it the Doppelganger release?")
        raw = zf.read(names["manifest.txt"])
    text = raw.decode("utf-8-sig", errors="replace")
    version = None
    m = re.search(r"#\s*Doppelganger\s+([0-9][0-9A-Za-z.\-]*)", text)
    if m:
        version = m.group(1).rstrip(".")
    rows, seen = [], set()
    for ln in text.splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        parts = ln.split(None, 3)
        if len(parts) != 4 or parts[0] not in ("new", "built"):
            raise ValueError("malformed MANIFEST.txt row: " + ln[:80])
        if parts[0] != "new":
            continue                           # "built" files are made by the mod on first launch
        rel = safe_rel(parts[3])
        if not re.fullmatch(r"[0-9a-fA-F]{40}", parts[2]) or not parts[1].isdigit():
            raise ValueError("bad size / SHA1 in MANIFEST.txt: " + rel)
        if rel.lower() in seen:
            raise ValueError("duplicate MANIFEST.txt path: " + rel)
        seen.add(rel.lower())
        if not rel.startswith(HIS_DIR + "/"):
            raise ValueError("MANIFEST.txt lists a file outside " + HIS_DIR + ": " + rel)
        rows.append((rel, int(parts[1]), parts[2].lower()))
    if not rows:
        raise ValueError("MANIFEST.txt lists no files")
    return version, sha1_bytes(raw), rows


def install_his_mod(bl2, zip_path, rows, record):
    created = set(record.get("created", []))
    written = kept = 0
    staged = []
    with zipfile.ZipFile(zip_path) as zf:
        index = {n.replace("\\", "/").lower(): n for n in zf.namelist()}
        for rel, size, digest in rows:                         # verify everything before writing anything
            src = index.get(rel.lower())
            if src is None:
                raise ValueError(rel + ": listed in MANIFEST.txt but not in the ZIP")
            data = zf.read(src)
            if len(data) != size or sha1_bytes(data) != digest:
                raise ValueError(rel + ": ZIP bytes do not match MANIFEST.txt")
            staged.append((rel, data))
    for rel, data in staged:
        dst = os.path.join(bl2, rel)
        if os.path.isfile(dst) and sha1_file(dst) == sha1_bytes(data):
            kept += 1
            continue
        existed = os.path.exists(dst)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".doppelcompat-tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dst)
        if not existed:
            created.add(rel)
        written += 1
    record["created"] = sorted(created)
    return written, kept


def retire_stale(bl2, rows, record, dry_run=False):
    """Move aside files an older release shipped (byte-identical) that this release dropped."""
    shipped_now = {rel.lower() for rel, _, _ in rows}
    old = load_json(os.path.join(DATA, "previous_releases.json"), {}).get("releases", {})
    known = {}
    for ver, entries in old.items():
        for e in entries:
            known.setdefault(e["path"].lower(), []).append((e["sha1"], ver, e["path"]))
    stamp = record.get("stale_stamp") or utc_stamp()
    moved, left = [], []
    his = os.path.join(bl2, HIS_DIR)
    if not os.path.isdir(his):
        return moved, left
    for dirpath, dirnames, filenames in os.walk(his):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, bl2).replace(os.sep, "/")
            if rel.lower() in shipped_now or ".pre-doppelcompat-" in name:    # ours: a layer's backup
                continue
            match = [k for k in known.get(rel.lower(), []) if k[0] == sha1_file(full)]
            if not match:
                left.append(rel)
                continue
            moved.append((rel, match[0][1]))
            if dry_run:
                continue
            dst = os.path.join(bl2, STALE_DIR, stamp, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(full, dst)
            if rel.endswith(".py"):                       # its compiled copy would still be importable
                stem = os.path.splitext(name)[0]
                for pyc in glob.glob(os.path.join(dirpath, "__pycache__", stem + ".*.pyc")):
                    os.remove(pyc)
    if moved and not dry_run:
        record["stale_stamp"] = stamp
        record.setdefault("stale_moved", [])
        record["stale_moved"] = sorted(set(record["stale_moved"]) | {r for r, _ in moved})
    return moved, left


# --- the compatibility layers ---------------------------------------------------------------------
# Each layer is a list of text edits for one file: Moonshot's __init__.py, and Doppelganger's
# digijack.py (the map-change guard). The record keys keep the names the first release used.
LAYERS = (
    {"file": "compat_layer.json", "label": "Moonshot", "backup": "compat_backup", "written": "compat_written_sha1"},
    {"file": "doppelganger_layer.json", "label": "Doppelganger", "backup": "his_patch_backup", "written": "his_patch_written_sha1"},
    {"file": "doppelganger_init_layer.json", "label": "Doppelganger hooks", "backup": "his_init_backup", "written": "his_init_written_sha1"},
)


def load_layer(spec):
    layer = load_json(os.path.join(DATA, spec["file"]))
    if not layer or layer.get("schema") != 1 or not layer.get("target"):
        raise SystemExit("data/%s is missing or damaged - extract the whole patch folder" % spec["file"])
    return layer


def plan_layer(text, layer):
    """(patched text, edits applied, already in, misfits). Edits are tried in order, each against
    the text the earlier ones produced; an "optional" edit whose anchor is absent is skipped."""
    applied, done, misfit = 0, 0, []
    for h in layer["hunks"]:
        if h["witness"] in text:
            done += 1
            continue
        if h["kind"] == "replace":
            n = text.count(h["anchor"])
        else:
            n = sum(1 for ln in text.split("\n") if h["anchor"] in ln)
        if n == 0 and h.get("optional"):
            continue
        if n != 1:
            misfit.append((h["witness"][:60], n))
            continue
        text = apply_hunk(text, h)
        applied += 1
    return text, applied, done, misfit


def apply_hunk(text, h):
    if h["kind"] == "replace":
        return text.replace(h["anchor"], h["new"], 1)
    lines = text.split("\n")
    i = next(k for k, ln in enumerate(lines) if h["anchor"] in ln)
    block = h["new"].rstrip("\n").split("\n")
    if h["kind"] == "after":
        lines[i + 1:i + 1] = block
    else:
        lines[i:i] = block
    return "\n".join(lines)


def add_tags(text, tags):
    head = "__version__ = '"
    i = text.find(head)
    if i < 0:
        return text
    for tag in tags:
        j = text.find("'", i + len(head))
        if tag not in text[i + len(head):j]:
            text = text[:i + len(head)] + tag + text[i + len(head):]
    return text


def read_text(path):
    raw = open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8") if bom else raw.decode("utf-8")
    crlf = "\r\n" in text
    return text.replace("\r\n", "\n"), bom, crlf


def layer_state(bl2, spec):
    layer = load_layer(spec)
    path = os.path.join(bl2, layer["target"])
    if not os.path.isfile(path):
        return None
    text, _, _ = read_text(path)
    _, applied, done, misfit = plan_layer(text, layer)
    return applied, done, misfit


def apply_layer(bl2, record, spec):
    layer = load_layer(spec)
    label = spec["label"]
    path = os.path.join(bl2, layer["target"])
    if not os.path.isfile(path):
        log("  %s is not installed in this folder (no %s): its layer was skipped" % (label, layer["target"]))
        return "absent"
    text, bom, crlf = read_text(path)
    new, applied, done, misfit = plan_layer(text, layer)
    if misfit:
        log("  This %s build does not match the patch; %s was not changed." % (label, layer["target"]))
        for w, n in misfit:
            log("    no single place for: %r (found %d)" % (w, n))
        return "misfit"
    if not applied:
        log("  %s layer already in place (%d edits present)" % (label, done))
        return "present"
    new = add_tags(new, layer.get("version_tags", []))
    try:
        compile(new, path, "exec")
    except SyntaxError as exc:
        log("  patched %s would not compile (%s); nothing was changed" % (label, exc))
        return "error"
    # One backup to restore on uninstall: the file as it was before this patch touched it. A file
    # that changed since the patch last wrote it (a Moonshot update, a fresh Doppelganger copy) is
    # backed up again, so uninstall never puts back an out-of-date version.
    # A file this patch installed itself needs none: uninstall removes it, a reinstall restores it.
    prev = record.get(spec["backup"])
    prev_ok = bool(prev) and os.path.isfile(os.path.join(bl2, prev))
    ours = layer["target"] in record.get("created", [])
    if not ours and (not prev_ok or sha1_file(path) != record.get(spec["written"])):
        backup = path + layer["backup_prefix"] + utc_stamp()
        shutil.copy2(path, backup)
        if prev_ok and record.get(spec["written"]) is None:
            pass                           # a backup made by an earlier release: keep the oldest
        else:
            record[spec["backup"]] = os.path.relpath(backup, bl2).replace(os.sep, "/")
        log("  backup: " + os.path.relpath(backup, bl2).replace(os.sep, "/"))
    out = new.replace("\n", "\r\n") if crlf else new
    tmp = path + ".doppelcompat-tmp"
    with open(tmp, "wb") as fh:
        fh.write((b"\xef\xbb\xbf" if bom else b"") + out.encode("utf-8"))
    os.replace(tmp, path)
    stem = os.path.splitext(os.path.basename(path))[0]
    for pyc in glob.glob(os.path.join(os.path.dirname(path), "__pycache__", stem + ".*.pyc")):
        os.remove(pyc)
    record[spec["written"]] = sha1_file(path)
    log("  %s layer applied: %d edit(s) added, %d already present" % (label, applied, done))
    return "applied"


def restore_layer(bl2, record, spec):
    layer = load_layer(spec)
    target = layer["target"]
    backup = record.get(spec["backup"])
    path = os.path.join(bl2, target)
    if not backup or not os.path.isfile(os.path.join(bl2, backup)) or not os.path.isfile(path):
        return
    written = record.get(spec["written"])
    if written and sha1_file(path) != written:
        log("%s changed since the patch (updated?); backup NOT restored: %s" % (target, backup))
        return
    shutil.copy2(os.path.join(bl2, backup), path)
    os.remove(os.path.join(bl2, backup))
    stem = os.path.splitext(os.path.basename(path))[0]
    for pyc in glob.glob(os.path.join(os.path.dirname(path), "__pycache__", stem + ".*.pyc")):
        os.remove(pyc)
    log("restored %s from %s" % (target, backup))


# --- status / uninstall ---------------------------------------------------------------------------

def his_version(bl2):
    try:
        with open(os.path.join(bl2, HIS_DIR, "__init__.py"), encoding="utf-8") as fh:
            m = re.search(r'^__version__\s*=\s*[\'"]([^\'"]+)[\'"]', fh.read(), re.M)
        return m.group(1) if m else "unknown"
    except OSError:
        return None


def status(bl2):
    record = load_json(os.path.join(bl2, RECORD), {})
    log("Game folder:   " + bl2)
    log("Doppelganger:  " + (his_version(bl2) or "not installed"))
    for spec in LAYERS:
        st = layer_state(bl2, spec)
        name = (spec["label"] + " layer:").ljust(20)
        if st is None:
            log(name + " target not installed here")
            continue
        applied, done, misfit = st
        state = ("does NOT fit this build" if misfit else
                 "applied" if not applied else "not applied (%d edit(s) missing)" % applied)
        log(name + " " + state)
    if record:
        log("Patch record:  %s (patch %s, stamp %s)" % (RECORD, record.get("patch", "0.9.2 Windows installer"), record.get("stamp", "?")))
    if record.get("shipped"):
        rows = [(rel, 0, "") for rel in record["shipped"]]
        stale, left = retire_stale(bl2, rows, {}, dry_run=True)
        if stale:
            log("Older-release files still present: %s" % ", ".join(r for r, _ in stale))
        if left:
            log("Unrecognised files in %s (left alone): %s" % (HIS_DIR, ", ".join(left)))
    return 0


def uninstall(bl2):
    rec_path = os.path.join(bl2, RECORD)
    record = load_json(rec_path)
    if not record:
        log("No patch record in this folder; nothing to remove.")
        return 1
    created = set(record.get("created", []))
    for spec in LAYERS:                       # a file this patch created is simply removed below
        if load_layer(spec)["target"] not in created:
            restore_layer(bl2, record, spec)
    removed = 0
    for rel in created:
        p = os.path.join(bl2, safe_rel(rel))
        if os.path.isfile(p):
            os.remove(p)
            removed += 1
    his = os.path.join(bl2, HIS_DIR)
    for dirpath, _, _ in sorted(os.walk(his), key=lambda t: -len(t[0])) if os.path.isdir(his) else []:
        pyc = os.path.join(dirpath, "__pycache__")
        if os.path.isdir(pyc) and all(f.endswith(".pyc") for f in os.listdir(pyc)):
            shutil.rmtree(pyc)
        try:
            os.rmdir(dirpath)
        except OSError:
            pass
    log("removed %d file(s) this patch created; files that were already there are kept" % removed)
    stamp = record.get("stale_stamp")
    if stamp:
        log("older-release files moved aside earlier are still in %s/%s (delete it when you like)" % (STALE_DIR, stamp))
    os.remove(rec_path)
    log("Run `doppelsetup remove` in-game first if you also want Doppelganger's generated assets removed.")
    return 0


# --- install -------------------------------------------------------------------------------------

def install(bl2, zip_path, compat_only=False, any_version=False):
    if not looks_like_bl2(bl2):
        raise SystemExit("%s does not look like a Borderlands 2 folder (no WillowGame / Binaries)" % bl2)
    if not os.path.isfile(os.path.join(bl2, "Binaries", "Win32", "Plugins", "unrealsdk.dll")):
        log("WARNING: the PythonSDK plugin is missing from this folder; no mod will load until it is installed.")
    if not os.path.isdir(os.path.join(bl2, "DLC", "Tulip")):
        log("WARNING: the Mechromancer pack (DLC/Tulip) is missing; Doppelganger needs it.")
    rec_path = os.path.join(bl2, RECORD)
    record = load_json(rec_path, {}) or {}
    if not compat_only:
        version, manifest_sha1, rows = read_release(zip_path)
        tested = manifest_sha1 == TESTED_MANIFEST_SHA1
        log("Doppelganger release: %s (%d shipped files)%s" % (version or "unknown", len(rows), "" if tested else " - not the tested 0.9.4 build"))
        if version != TESTED_VERSION and not any_version:
            raise SystemExit("This patch is for Doppelganger %s. Pass --any-version to install %s anyway." % (TESTED_VERSION, version))
        written, kept = install_his_mod(bl2, zip_path, rows, record)
        log("  Doppelganger: %d file(s) written, %d already correct" % (written, kept))
        moved, left = retire_stale(bl2, rows, record)
        for rel, ver in moved:
            log("  moved aside %s (shipped by %s, dropped in this release)" % (rel, ver))
        if moved:
            log("  (kept in %s/%s)" % (STALE_DIR, record["stale_stamp"]))
        for rel in left:
            log("  left alone, not from a known release: " + rel)
        record["shipped"] = [r for r, _, _ in rows]
        record["doppelganger_version"] = version
    results = [apply_layer(bl2, record, spec) for spec in LAYERS]
    record["patch"] = PATCH_VERSION
    record["stamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write_json(rec_path, record)
    log("")
    if "misfit" in results or "error" in results:
        return 2
    log("Done. Start Borderlands 2 (with The Pre-Sequel installed). Doppelganger rebuilds any of its")
    log("generated assets that changed in this release, then asks for a restart. `doppelsetup status`")
    log("in the console shows the build. Moonshot's Jack appears as \"Jack (TPS)\".")
    return 0


def choose(prompt, options):
    for i, o in enumerate(options, 1):
        log("  %d) %s" % (i, o))
    while True:
        ans = input(prompt).strip().strip('"').strip("'")
        if ans.isdigit() and 1 <= int(ans) <= len(options):
            return options[int(ans) - 1]
        if ans and os.path.exists(os.path.expanduser(ans)):
            return os.path.expanduser(ans)
        log("  type a number from the list, or paste a path")


def interactive():
    log("Moonshot x Doppelganger compatibility patch " + PATCH_VERSION)
    log("")
    games = find_game_folders()
    moon = [g for g in games if os.path.isfile(os.path.join(g, MOONSHOT_INIT))]
    games = moon + [g for g in games if g not in moon]
    if not games:
        log("No Borderlands 2 folder found automatically.")
        bl2 = os.path.expanduser(input("Paste the Borderlands 2 / Project Moonshot folder: ").strip().strip('"').strip("'"))
    else:
        log("Borderlands 2 folders found (Moonshot ones first):")
        bl2 = choose("Which folder? ", [g + ("   [Moonshot]" if g in moon else "") for g in games]).replace("   [Moonshot]", "")
    log("")
    zips = find_zips()
    zips.sort(key=lambda z: (TESTED_VERSION not in os.path.basename(z), z))
    log("Doppelganger release ZIP:")
    opts = zips + ["(skip - Doppelganger 0.9.4 is already installed, just apply the Moonshot layer)"]
    z = choose("Which ZIP? ", opts)
    log("")
    if z == opts[-1]:
        return install(bl2, None, compat_only=True)
    return install(bl2, z)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Moonshot compatibility patch for Doppelganger " + TESTED_VERSION)
    ap.add_argument("--bl2", help="Borderlands 2 folder Moonshot is installed in")
    ap.add_argument("--zip", help="the Doppelganger release ZIP")
    ap.add_argument("--compat-only", action="store_true", help="only apply Moonshot's compatibility layer")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--any-version", action="store_true", help="allow a Doppelganger release other than 0.9.4")
    ap.add_argument("--list", action="store_true", help="list the Borderlands 2 folders found")
    a = ap.parse_args(argv)
    if a.list:
        for g in find_game_folders():
            log(g + ("   [Moonshot]" if os.path.isfile(os.path.join(g, MOONSHOT_INIT)) else ""))
        return 0
    if not (a.bl2 or a.zip or a.compat_only or a.status or a.uninstall):
        return interactive()
    bl2 = a.bl2
    if not bl2:
        moon = [g for g in find_game_folders() if os.path.isfile(os.path.join(g, MOONSHOT_INIT))]
        if len(moon) != 1:
            raise SystemExit("pass --bl2 <folder> (%d Moonshot folders found)" % len(moon))
        bl2 = moon[0]
    bl2 = os.path.abspath(os.path.expanduser(bl2))
    try:
        if a.status:
            return status(bl2)
        if a.uninstall:
            return uninstall(bl2)
        if a.compat_only:
            return install(bl2, None, compat_only=True)
        if not a.zip:
            raise SystemExit("give --zip, --compat-only, --status or --uninstall")
        return install(bl2, os.path.abspath(os.path.expanduser(a.zip)), any_version=a.any_version)
    except (ValueError, zipfile.BadZipFile, OSError) as exc:
        log("ERROR: %s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
