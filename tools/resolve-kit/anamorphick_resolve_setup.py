#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
anamorphick_resolve_setup.py -- build a NEW DaVinci Resolve project for anamorphic clips
(default 1.5x) with the Anamorphick "golden Rec.709" LUT, and optionally render a tiny preview.

What it does, reading every value back from Resolve:
  1. creates a NEW project. If the name is taken it appends " (2)", " (3)" ... It never
     loads, changes, overwrites or deletes an existing project.
  2. on the still-empty project: color science DaVinci YRGB (not color managed), timeline
     and output color space Rec.709 Gamma 2.4, and the timeline frame rate of the clip.
  3. imports the clips. It sets the timeline to the desqueezed size (3840x2160 at 1.5x
     becomes 3840x1440) and puts Pixel Aspect Ratio 1.5 on every clip. If this Resolve
     build refuses that PAR, it falls back to timeline "stretch" input scaling, then to
     Inspector Zoom X/Y.
  4. copies the LUT into Resolve's LUT folder, in a subfolder "Anamorphick", and
     refreshes Resolve's LUT list.
  5. creates the timeline "<clip> - 1.5x Golden 709", puts the LUT on node 1 of every
     clip, and opens the Color page.
  6. with --render-preview: renders a 1280-wide H.264 .mp4 and prints its path.
  7. prints a SUMMARY table of the values read back from Resolve, plus manual fixes
     for any step that failed.

DaVinci Resolve must be open. There are three ways to run this script:
  A. From a terminal. This needs Resolve Studio. On 21.1+ you can also run it with
     Resolve's own ResolvePython.
        python3 anamorphick_resolve_setup.py --folder "/Volumes/SDCARD/DCIM" --render-preview
  B. Inside Resolve. This also works on the free version, up to 21.0.x. Open
     Workspace > Console, choose Py3, and paste ONE line. Fix the path first:
        import sys; sys.argv=["x","--folder","/Volumes/SDCARD/DCIM","--render-preview"]; p="/Users/me/resolve-kit/anamorphick_resolve_setup.py"; exec(open(p,encoding="utf-8").read(),{"__name__":"__main__","__file__":p,"resolve":globals().get("resolve"),"bmd":globals().get("bmd")})
  C. From Workspace > Scripts. Copy this file into ...Fusion/Scripts/Utility and edit
     CONFIG below (clips or folder, and the lut path). Restart Resolve, then run it
     from Workspace > Scripts.
Add --dry-run to print the plan without changing anything. Python 3.6+, stdlib only.
"""

# =====================================================================================
# CONFIG -- used when there are no command-line arguments (Workspace > Scripts, Console).
# Command-line flags override these values.
# =====================================================================================
CONFIG = {
    "clips": [],                 # e.g. ["/Volumes/SDCARD/DCIM/100_FUJI/DSCF0001.MOV"]
    "folder": "",                # e.g. "/Volumes/SDCARD/DCIM" ("" or "auto" with no clips:
                                 #      look for a DCIM folder on a mounted card)
    "squeeze": 1.5,              # anamorphic squeeze factor of the lens
    "lut": "",                   # "" = golden_709.cube next to this script
    "project": "",               # "" = "Anamorphick 2026-11-03 1.5x Golden"
    "timeline_width": 3840,      # never wider than (source width x squeeze)
    "render_preview": False,     # True = render a 1280-wide H.264 .mp4 preview
    "out": "",                   # "" = ~/Movies/Anamorphick Previews (macOS),
                                 #      ~/Videos/Anamorphick Previews (Windows/Linux)
    "dry_run": False,            # True = print the plan only
    # ---- advanced ----
    "desqueeze_method": "auto",  # auto (PAR, then fallbacks) | par | stretch | zoom
    "lut_dir": "",               # override Resolve's master LUT folder
    "render_timeout": 900,       # seconds to wait for the preview render
    "save_current_project_first": True,  # SaveProject() a NAMED current project before switching
    "max_auto_clips": 25,        # card auto-detect refuses to import more clips than this
}

import argparse
import glob
import hashlib
import json
import math
import os
import re
import shutil
import struct
import sys
import time

SCRIPT_VERSION = "1.0.0 (2026-11-03)"
DEFAULT_PROJECT_FMT = "Anamorphick 2026-11-03 {sq}x Golden"
TIMELINE_NAME_FMT = u"{stem} \u2014 {sq}x Golden 709"
LUT_SUBFOLDER = "Anamorphick"
DEFAULT_LUT_NAME = "golden_709.cube"
VIDEO_EXTS = (".mov", ".mp4", ".mxf", ".braw", ".r3d", ".mts", ".avi", ".mkv")
MIN_CLIP_BYTES = 100 * 1024
PREVIEW_WIDTH = 1280
FREE_MAX_W, FREE_MAX_H = 3840, 2160          # free Resolve timelines stop at UHD
MAC_BUNDLES = (
    "/Applications/DaVinci Resolve/DaVinci Resolve.app",   # Blackmagic installer
    "/Applications/DaVinci Resolve Studio.app",            # App Store Studio
    "/Applications/DaVinci Resolve.app",                   # App Store free
)
# Timeline frame rates offered by Resolve's Project Settings (label, exact value)
STD_RATES = (
    ("23.976", 24000.0 / 1001), ("24", 24.0), ("25", 25.0), ("29.97", 30000.0 / 1001),
    ("30", 30.0), ("47.952", 48000.0 / 1001), ("48", 48.0), ("50", 50.0),
    ("59.94", 60000.0 / 1001), ("60", 60.0), ("72", 72.0), ("95.904", 96000.0 / 1001),
    ("96", 96.0), ("100", 100.0), ("119.88", 120000.0 / 1001), ("120", 120.0),
)
# Clip Attributes PAR names seen in community scripts (value = horizontal stretch)
PAR_NAMES = {
    "square": 1.0, "cinemascope": 2.0, "dvcpro hd": 4.0 / 3.0, "1.3x anamorphic": 1.3,
    "16mm hd anamorphic": 1.2857, "35mm full aperture hd anamorphic": 1.35135,
    "super16 hd anamorphic": 1.0869, "ntsc": 1.111, "ntsc 16:9": 0.833, "ntsc dv": 1.125,
    "ntsc dv 16:9": 0.843, "pal": 0.937, "pal 16:9": 0.702,
}


# ------------------------------------------------------------------------------ output
def say(msg=""):
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(msg.encode(enc, "replace").decode(enc, "replace"))
    try:
        sys.stdout.flush()
    except Exception:
        pass


def banner(lines, ch="!"):
    width = max(len(x) for x in lines) + 6
    say(ch * width)
    for x in lines:
        say("%s%s %s %s%s" % (ch, ch, x, " " * (width - len(x) - 6), ch + ch))
    say(ch * width)


class Report(object):
    """Collects one row per step: status OK / WARN / FAILED / SKIPPED, read-back details, fix."""

    def __init__(self):
        self.rows = []

    def add(self, step, status, detail="", fix=None):
        self.rows.append({"step": step, "status": status, "detail": detail, "fix": fix})
        say("  -> [%s] %s: %s" % (status, step, detail))

    def count(self, status):
        return sum(1 for r in self.rows if r["status"] == status)

    def failed(self):
        return self.count("FAILED") > 0


def _wrap(text, width):
    words, lines, cur = str(text).split(" "), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w) if cur else w
    lines.append(cur)
    return lines


def print_summary(report, ctx):
    say("")
    say("=" * 100)
    say("SUMMARY  (values read back from DaVinci Resolve; nothing below is assumed)")
    say("-" * 100)
    say("%-3s %-24s %-8s %s" % ("#", "Step", "Status", "Read back / details"))
    for i, r in enumerate(report.rows, 1):
        lines = _wrap(r["detail"], 62)
        say("%-3s %-24s %-8s %s" % (i, r["step"][:24], r["status"], lines[0]))
        for extra in lines[1:]:
            say("%-3s %-24s %-8s %s" % ("", "", "", extra))
    fixes = [r for r in report.rows if r["fix"] and r["status"] in ("FAILED", "WARN")]
    if fixes:
        say("-" * 100)
        say("MANUAL FIXES (do these in Resolve):")
        for r in fixes:
            first = True
            for line in _wrap("%s: %s" % (r["step"], r["fix"]), 94):
                say((" * " if first else "   ") + line)
                first = False
    say("-" * 100)
    say("Result: %d OK, %d WARN, %d FAILED, %d SKIPPED" % (
        report.count("OK"), report.count("WARN"), report.count("FAILED"), report.count("SKIPPED")))
    if ctx.get("preview_path"):
        say("Preview: %s" % ctx["preview_path"])
    say("=" * 100)
    payload = {"version": SCRIPT_VERSION, "mode": ctx.get("mode"), "project": ctx.get("project_name"),
               "timeline": ctx.get("timeline_name"), "preview": ctx.get("preview_path"),
               "steps": [{"step": r["step"], "status": r["status"], "detail": r["detail"]}
                         for r in report.rows]}
    try:
        say("ANAMORPHICK_SUMMARY_JSON " + json.dumps(payload, ensure_ascii=True))
    except Exception:
        pass


# ------------------------------------------------------------------------------ small helpers
def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def fmt_sq(sq):
    return "%g" % sq


def even_round(x):
    return int(math.floor(x / 2.0 + 0.5)) * 2


def to_float(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group(0)) if m else None


def same_value(a, b, numeric=False, tol=0.011):
    if a is None:
        return False
    if numeric:
        fa, fb = to_float(a), to_float(b)
        return fa is not None and fb is not None and abs(fa - fb) <= tol
    return str(a).strip().lower() == str(b).strip().lower()


def parse_resolution(v):
    m = re.search(r"(\d+)\s*[xX\u00d7]\s*(\d+)", str(v or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def parse_par(v):
    """Horizontal pixel stretch from a Resolve PAR string ('Square', '1.5', 'Cinemascope'...)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower()
    if not s:
        return None
    if s in PAR_NAMES:
        return PAR_NAMES[s]
    m = re.match(r"^1\s*:\s*(\d+(?:\.\d+)?)$", s)          # manual: "the X in the 1:X ratio"
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", s)   # '2000/1000' style
    if m and float(m.group(2)) > 0:
        return float(m.group(1)) / float(m.group(2))
    return to_float(s)


def par_candidates(sq):
    out = []

    def add(c):
        if c not in out:
            out.append(c)
    for fmt in ("%.1f", "%g", "%.2f"):          # '1.5', '2.0' are the forms seen in the wild
        c = fmt % sq
        if abs(float(c) - sq) < 0.005:
            add(c)
    named = {2.0: ["Cinemascope"], 1.33: ["DVCPro HD"], 1.333: ["DVCPro HD"], 1.3: ["1.3x Anamorphic"]}
    for c in named.get(round(sq, 3), []):
        add(c)
    for c in ("%gx Anamorphic" % sq, "Anamorphic %gx" % sq, "%gx" % sq):
        add(c)
    return out


def fps_label(fps):
    if fps is None:
        return None
    best = min(STD_RATES, key=lambda r: abs(r[1] - fps))
    return best[0] if abs(best[1] - fps) <= 0.011 else None


def fps_candidates(fps):
    lab = fps_label(fps)
    if lab is None:
        return ["%g" % round(fps, 3)]
    extra = {"23.976": ["23.98"], "24": ["24.0"], "25": ["25.0"], "30": ["30.0"],
             "50": ["50.0"], "60": ["60.0"], "48": ["48.0"]}
    return [lab] + extra.get(lab, [])


def compute_timeline_size(src_w, src_h, sq, want_w, cap_free=False):
    """Width = min(want, src_w*sq), height keeps the desqueezed aspect; both even."""
    aspect = src_w * sq / float(src_h)
    w = min(int(want_w), int(math.floor(src_w * sq)))
    notes = []
    if w < int(want_w):
        notes.append("width capped at source width x squeeze = %d" % int(math.floor(src_w * sq)))
    if cap_free and w > FREE_MAX_W:
        w = FREE_MAX_W
        notes.append("free Resolve: width capped at %d" % FREE_MAX_W)
    w -= w % 2
    h = even_round(w / aspect)
    if cap_free and h > FREE_MAX_H:
        h = FREE_MAX_H
        w = even_round(h * aspect)
        notes.append("free Resolve: height capped at %d" % FREE_MAX_H)
    return w, h, aspect, notes


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def os_kind():
    p = sys.platform
    if p.startswith("darwin"):
        return "mac"
    if p.startswith("win") or p.startswith("cygwin"):
        return "win"
    return "linux"


def resolve_paths(kind):
    home = os.path.expanduser("~")
    if kind == "mac":
        sup = "/Library/Application Support/Blackmagic Design/DaVinci Resolve"
        usup = os.path.join(home, "Library", "Application Support", "Blackmagic Design", "DaVinci Resolve")
        return {
            "api": [os.path.join(sup, "Developer", "Scripting")] +
                   [b + "/Contents/Resources/Developer/Scripting" for b in MAC_BUNDLES],
            "lib": [b + "/Contents/Libraries/Fusion/fusionscript.so" for b in MAC_BUNDLES],
            "lut_master": os.path.join(sup, "LUT"),
            "lut_user": os.path.join(usup, "LUT"),
            "scripts_user": os.path.join(usup, "Fusion", "Scripts"),
            "scripts_all": os.path.join(sup, "Fusion", "Scripts"),
            "resolve_python": MAC_BUNDLES[0] + "/Contents/Applications/ResolvePython",
            "out": os.path.join(home, "Movies", "Anamorphick Previews"),
        }
    if kind == "win":
        pdata = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        adata = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
        pfiles = [os.environ.get(v) for v in ("PROGRAMFILES", "PROGRAMW6432")] + [r"C:\Program Files"]
        pfiles = [p for i, p in enumerate(pfiles) if p and p not in pfiles[:i]]
        bmd = os.path.join("Blackmagic Design", "DaVinci Resolve")
        return {
            "api": [os.path.join(pdata, bmd, "Support", "Developer", "Scripting")],
            "lib": [os.path.join(p, bmd, "fusionscript.dll") for p in pfiles],
            "lut_master": os.path.join(pdata, bmd, "Support", "LUT"),
            "lut_user": os.path.join(adata, bmd, "Support", "LUT"),
            "scripts_user": os.path.join(adata, bmd, "Support", "Fusion", "Scripts"),
            "scripts_all": os.path.join(pdata, bmd, "Fusion", "Scripts"),
            "resolve_python": os.path.join(pfiles[0], bmd, "ResolvePython", "ResolvePython.exe"),
            "out": os.path.join(home, "Videos", "Anamorphick Previews"),
        }
    roots = [r for r in ("/opt/resolve", "/home/resolve") if os.path.isdir(r)] or ["/opt/resolve"]
    r0 = roots[0]
    return {
        "api": [os.path.join(r, "Developer", "Scripting") for r in roots],
        "lib": [os.path.join(r, *p) for r in roots for p in
                (("libs", "Fusion", "fusionscript.so"), ("libs", "fusionscript.so"), ("bin", "fusionscript.so"))],
        "lut_master": os.path.join(r0, "LUT"),
        "lut_user": os.path.join(home, ".local", "share", "DaVinciResolve", "LUT"),
        "scripts_user": os.path.join(home, ".local", "share", "DaVinciResolve", "Fusion", "Scripts"),
        "scripts_all": os.path.join(r0, "Fusion", "Scripts"),
        "resolve_python": os.path.join(r0, "bin", "ResolvePython"),
        "out": os.path.join(home, "Videos", "Anamorphick Previews"),
    }


def script_path():
    p = globals().get("__file__")
    if p and os.path.isfile(p):
        return os.path.abspath(p)
    argv = getattr(sys, "argv", None) or [""]
    if argv[0] and argv[0].endswith(".py") and os.path.isfile(argv[0]):
        return os.path.abspath(argv[0])
    return None


# ------------------------------------------------------------------------------ clip discovery
def scan_folder(root):
    found, skipped = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted([d for d in dirnames if not d.startswith(".")], key=natural_key)
        for fn in sorted(filenames, key=natural_key):
            if os.path.splitext(fn)[1].lower() not in VIDEO_EXTS:
                continue
            full = os.path.join(dirpath, fn)
            if fn.startswith("._"):
                skipped.append((full, "AppleDouble '._' metadata file"))
                continue
            try:
                size = os.path.getsize(full)
            except OSError as e:
                skipped.append((full, str(e)))
                continue
            if size < MIN_CLIP_BYTES:
                skipped.append((full, "smaller than 100 KB (%d bytes)" % size))
                continue
            found.append(os.path.abspath(full))
    return found, skipped


def find_card_dcim(kind):
    cands = []
    if kind == "mac":
        if os.path.isdir("/Volumes"):
            cands = [os.path.join("/Volumes", v, "DCIM") for v in sorted(os.listdir("/Volumes"))]
    elif kind == "win":
        letters = "DEFGHIJKLMNOPQRSTUVWXYZ"
        try:
            import ctypes
            ctypes.windll.kernel32.SetErrorMode(1)            # no "insert disk" dialogs
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            letters = [c for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ") if mask >> i & 1 and c > "C"]
        except Exception:
            pass
        cands = ["%s:\\DCIM" % c for c in letters]
    else:
        for pat in ("/media/*/DCIM", "/media/*/*/DCIM", "/run/media/*/*/DCIM", "/mnt/*/DCIM"):
            cands += sorted(glob.glob(pat))
    return [c for c in cands if os.path.isdir(c)]


def collect_clips(opts, kind):
    """Returns (clips, skipped, source_description, error)."""
    if opts.get("clips"):
        clips, skipped = [], []
        for c in opts["clips"]:
            p = os.path.abspath(os.path.expanduser(str(c)))
            if not os.path.isfile(p):
                hint = " (that is a folder: use --folder)" if os.path.isdir(p) else ""
                return [], skipped, "--clip", "clip not found: %s%s" % (p, hint)
            name = os.path.basename(p)
            if name.startswith("._"):
                skipped.append((p, "AppleDouble '._' metadata file"))
            elif os.path.getsize(p) < MIN_CLIP_BYTES:
                skipped.append((p, "smaller than 100 KB"))
            else:
                clips.append(p)
        return clips, skipped, "--clip", None
    folder = str(opts.get("folder") or "auto")
    if folder.lower() == "auto":
        roots = find_card_dcim(kind)
        if not roots:
            return [], [], "auto", ("no clip given and no camera card found (looked for a DCIM folder on "
                                    "mounted volumes). Use --clip PATH or --folder PATH, or fill CONFIG.")
        if len(roots) > 1:
            return [], [], "auto", "several cards found (%s); pick one with --folder" % ", ".join(roots)
        clips, skipped = scan_folder(roots[0])
        if len(clips) > int(opts.get("max_auto_clips") or 25):
            return [], skipped, roots[0], ("%d clips on the card -- too many for auto mode; pass the clip(s) "
                                           "with --clip, or the folder with --folder" % len(clips))
        return clips, skipped, "auto: %s" % roots[0], None
    root = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(root):
        return [], [], root, "folder not found: %s" % root
    clips, skipped = scan_folder(root)
    return clips, skipped, root, None


# ------------------------------------------------------------------------------ QuickTime / MP4 header sniffing
def _boxes(buf, start, end):
    pos = start
    while pos + 8 <= end:
        size, typ = struct.unpack(">I4s", buf[pos:pos + 8])
        hdr = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack(">Q", buf[pos + 8:pos + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr or pos + size > end:
            return
        yield typ, pos + hdr, pos + size
        pos += size


def _parse_stsd(buf, a, b, t):
    if a + 8 > b:
        return
    count = struct.unpack(">I", buf[a + 4:a + 8])[0]
    p = a + 8
    if count < 1 or p + 8 > b:
        return
    esize, fmt = struct.unpack(">I4s", buf[p:p + 8])
    t["codec"] = fmt.decode("latin-1", "replace")
    if p + 36 <= b:
        t["width"], t["height"] = struct.unpack(">HH", buf[p + 32:p + 36])
    for tc, ac, bc in _boxes(buf, p + 86, min(p + esize, b)):
        if tc == b"pasp" and ac + 8 <= bc:
            t["pasp"] = struct.unpack(">II", buf[ac:ac + 8])
        elif tc == b"colr" and ac + 10 <= bc and buf[ac:ac + 4] in (b"nclc", b"nclx"):
            t["colr"] = struct.unpack(">HHH", buf[ac + 4:ac + 10])


def _parse_stts(buf, a, b, t):
    if a + 8 > b:
        return
    n = struct.unpack(">I", buf[a + 4:a + 8])[0]
    p, best, total = a + 8, None, 0
    for _ in range(min(n, 200000)):
        if p + 8 > b:
            break
        cnt, delta = struct.unpack(">II", buf[p:p + 8])
        p += 8
        total += cnt
        if best is None or cnt > best[0]:
            best = (cnt, delta)
    t["stts"], t["frames"] = best, total


def _parse_trak(buf, s, e):
    t = {}
    for typ, a, b in _boxes(buf, s, e):
        if typ != b"mdia":
            continue
        for t2, a2, b2 in _boxes(buf, a, b):
            if t2 == b"hdlr" and a2 + 12 <= b2:
                t["handler"] = buf[a2 + 8:a2 + 12].decode("latin-1", "replace")
            elif t2 == b"mdhd" and a2 + 20 <= b2:
                if buf[a2] == 1 and a2 + 32 <= b2:
                    t["timescale"], t["duration"] = struct.unpack(">IQ", buf[a2 + 20:a2 + 32])
                else:
                    t["timescale"], t["duration"] = struct.unpack(">II", buf[a2 + 12:a2 + 20])
            elif t2 == b"minf":
                for t3, a3, b3 in _boxes(buf, a2, b2):
                    if t3 != b"stbl":
                        continue
                    for t4, a4, b4 in _boxes(buf, a3, b3):
                        if t4 == b"stsd":
                            _parse_stsd(buf, a4, b4, t)
                        elif t4 == b"stts":
                            _parse_stts(buf, a4, b4, t)
    return t


def sniff_media(path):
    """Reads the first video track of a QuickTime/MP4 file header: size, fps, codec, pasp, colr.
    Returns None for other containers or unreadable files. Reads only the 'moov' box."""
    if os.path.splitext(path)[1].lower() not in (".mov", ".mp4", ".m4v"):
        return None
    try:
        fsize = os.path.getsize(path)
        with open(path, "rb") as f:
            pos, moov, brand = 0, None, None
            while pos + 8 <= fsize:
                f.seek(pos)
                hdr = f.read(16)
                if len(hdr) < 8:
                    break
                size, typ = struct.unpack(">I4s", hdr[:8])
                hl = 8
                if size == 1:
                    if len(hdr) < 16:
                        break
                    size, hl = struct.unpack(">Q", hdr[8:16])[0], 16
                elif size == 0:
                    size = fsize - pos
                if size < hl:
                    break
                if typ == b"ftyp" and size >= hl + 4:
                    f.seek(pos + hl)
                    brand = f.read(4).decode("latin-1", "replace")
                if typ == b"moov":
                    moov = (pos + hl, min(pos + size, fsize))
                    break
                pos += size
            if not moov or moov[1] - moov[0] > 64 * 1024 * 1024:
                return None
            f.seek(moov[0])
            buf = f.read(moov[1] - moov[0])
    except (OSError, IOError, struct.error):
        return None
    try:
        for typ, a, b in _boxes(buf, 0, len(buf)):
            if typ != b"trak":
                continue
            t = _parse_trak(buf, a, b)
            if t.get("handler") == "vide" and t.get("width"):
                info = {"width": t["width"], "height": t["height"], "codec": t.get("codec"),
                        "brand": brand, "pasp": t.get("pasp"), "colr": t.get("colr"), "fps": None,
                        "frames": t.get("frames")}
                ts, st = t.get("timescale"), t.get("stts")
                if ts and st and st[1]:
                    info["fps"] = round(ts / float(st[1]), 3)
                if ts and t.get("duration"):
                    info["seconds"] = round(t["duration"] / float(ts), 2)
                return info
    except (struct.error, IndexError):
        return None
    return None


def describe_sniff(info):
    if not info:
        return "header not readable here (Resolve will report it after import)"
    s = "%dx%d" % (info["width"], info["height"])
    if info.get("fps"):
        s += " %s fps" % fps_label(info["fps"]) if fps_label(info["fps"]) else " %g fps" % info["fps"]
    if info.get("codec"):
        s += " %s" % info["codec"].strip()
    if info.get("seconds"):
        s += " %gs" % info["seconds"]
    if info.get("pasp") and info["pasp"][0] != info["pasp"][1]:
        s += " pasp %d:%d" % tuple(info["pasp"])
    if info.get("colr"):
        s += " colr %d/%d/%d" % tuple(info["colr"])
    return s


# ------------------------------------------------------------------------------ Resolve connection
def _method(obj, name):
    if obj is None:
        return None
    try:
        f = getattr(obj, name, None)
    except Exception:
        return None
    return f if callable(f) else None


def _ok(r):
    if isinstance(r, bool):
        return r
    if isinstance(r, (tuple, list)) and r and isinstance(r[0], bool):
        return r[0]
    return bool(r) if r is not None else False


def _load_fusionscript(path):
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.ExtensionFileLoader("fusionscript", path)
    spec = importlib.util.spec_from_file_location("fusionscript", path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def find_inapp_resolve():
    """Inside Resolve (Console / Workspace > Scripts) 'resolve' and 'bmd' are predefined globals."""
    g = globals()
    r = g.get("resolve")
    if r is not None and _method(r, "GetProjectManager"):
        return r, "inside Resolve (global 'resolve')"
    b = g.get("bmd")
    if b is not None and _method(b, "scriptapp"):
        try:
            r = b.scriptapp("Resolve")
            if r is not None:
                return r, "inside Resolve (bmd.scriptapp)"
        except Exception:
            pass
    return None, None


def connect_external(kind, diag):
    paths = resolve_paths(kind)
    mod = None
    try:
        import DaVinciResolveScript as mod  # noqa: N813
        diag.append("import DaVinciResolveScript: OK (%s)" % getattr(mod, "__file__", "built in"))
    except Exception as e:
        diag.append("import DaVinciResolveScript failed: %s: %s" % (type(e).__name__, e))
        api = os.environ.get("RESOLVE_SCRIPT_API")
        if not (api and os.path.isdir(api)):
            api = next((p for p in paths["api"] if os.path.isdir(p)), None)
            if api:
                os.environ["RESOLVE_SCRIPT_API"] = api
        lib = os.environ.get("RESOLVE_SCRIPT_LIB")
        if not (lib and os.path.isfile(lib)):
            lib = next((p for p in paths["lib"] if os.path.isfile(p)), None)
            if lib:
                os.environ["RESOLVE_SCRIPT_LIB"] = lib
        diag.append("RESOLVE_SCRIPT_API=%s  RESOLVE_SCRIPT_LIB=%s" % (api or "(not found)", lib or "(not found)"))
        if api:
            mods = os.path.join(api, "Modules")
            if os.path.isdir(mods) and mods not in sys.path:
                sys.path.append(mods)
            try:
                sys.modules.pop("DaVinciResolveScript", None)
                import DaVinciResolveScript as mod  # noqa: N813
                diag.append("import DaVinciResolveScript from %s: OK" % mods)
            except Exception as e2:
                mod = None
                diag.append("import DaVinciResolveScript from %s failed: %s: %s" % (mods, type(e2).__name__, e2))
        if mod is None and lib:
            try:
                mod = _load_fusionscript(lib)
                diag.append("loaded fusionscript directly from %s" % lib)
            except Exception as e3:
                diag.append("loading %s failed: %s: %s" % (lib, type(e3).__name__, e3))
    if mod is None:
        return None
    app = None
    try:
        app = mod.scriptapp("Resolve")
        diag.append("scriptapp('Resolve') -> %s" % ("connected" if app is not None else "None"))
    except Exception as e:
        diag.append("scriptapp('Resolve') raised %s: %s" % (type(e).__name__, e))
    if app is None:
        try:
            app = mod.scriptapp("Resolve", "127.0.0.1")
            diag.append("scriptapp('Resolve', '127.0.0.1') -> %s" % ("connected" if app is not None else "None"))
        except Exception as e:
            diag.append("scriptapp('Resolve', '127.0.0.1') not usable: %s" % type(e).__name__)
    return app


_PATH_OPTS = ("--clip", "--folder", "--lut", "--out", "--lut-dir")


def _abs_path_args(argv):
    """Absolute paths for the Console line: Resolve's working directory is not this terminal's."""
    out, cur = [], None
    for a in argv:
        a = str(a)
        if a.startswith("--"):
            key, eq, val = a.partition("=")
            cur = key if (key in _PATH_OPTS and not eq) else None
            if eq and key in _PATH_OPTS and val:
                a = key + "=" + _abs_if_meant(key, val)
            out.append(a)
            continue
        if cur is not None:
            a = _abs_if_meant(cur, a)
            if cur != "--clip":                     # only --clip takes several values
                cur = None
        out.append(a)
    return out


def _abs_if_meant(opt, val):
    v = os.path.expanduser(val)
    if os.path.isabs(v) or (opt == "--folder" and v.lower() == "auto"):
        return v
    if opt in ("--out", "--lut-dir") or os.path.exists(v):
        return os.path.abspath(v)
    return val                                      # e.g. a LUT name the script finds next to itself


def console_one_liner(argv):
    sp = script_path() or "/path/to/anamorphick_resolve_setup.py"
    args = ["anamorphick_resolve_setup.py"] + _abs_path_args(argv)
    return ('import sys; sys.argv = %r; p = %r; exec(open(p, encoding="utf-8").read(), '
            '{"__name__": "__main__", "__file__": p, "resolve": globals().get("resolve"), '
            '"bmd": globals().get("bmd")})' % (args, sp))


def explain_connection_failure(kind, diag, argv):
    paths = resolve_paths(kind)
    say("")
    banner(["Could not connect to DaVinci Resolve from this terminal."])
    say("What was tried:")
    for d in diag:
        say("   - " + d)
    say("")
    say("Most likely causes, in order:")
    say(" 1. Resolve is not running, or is still starting. Open it, wait for the Project Manager, re-run.")
    say(" 2. External scripting is off: DaVinci Resolve > Preferences > System > General >")
    say("    'External scripting using' = Local, then restart Resolve.")
    say(" 3. FREE DaVinci Resolve: Blackmagic allows terminal (external) scripting only in Studio.")
    say("    Run the script INSIDE Resolve instead -- Workspace > Console, click 'Py3', paste this ONE line:")
    say("")
    say("      " + console_one_liner(argv))
    say("")
    say("    or copy this file into %s" % os.path.join(paths["scripts_user"], "Utility"))
    say("    (create the folder if needed), edit CONFIG at the top, restart Resolve, then use")
    say("    Workspace > Scripts > anamorphick_resolve_setup.")
    say("    Community reports say free Resolve 21.1 no longer runs .py files from the Scripts menu; if the")
    say("    script is not listed, use the Console line above, or Resolve Studio.")
    say(" 4. Python cannot find Resolve's scripting module. Set, then re-run:")
    if kind == "win":
        say('      set RESOLVE_SCRIPT_API=%PROGRAMDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Developer\\Scripting')
        say('      set RESOLVE_SCRIPT_LIB=C:\\Program Files\\Blackmagic Design\\DaVinci Resolve\\fusionscript.dll')
        say('      set PYTHONPATH=%PYTHONPATH%;%RESOLVE_SCRIPT_API%\\Modules\\')
    elif kind == "mac":
        say('      export RESOLVE_SCRIPT_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"')
        say('      export RESOLVE_SCRIPT_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"')
        say('      export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"')
    else:
        say('      export RESOLVE_SCRIPT_API="/opt/resolve/Developer/Scripting"')
        say('      export RESOLVE_SCRIPT_LIB="/opt/resolve/libs/Fusion/fusionscript.so"')
        say('      export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"')
    say("    On Resolve 21.1+ you can skip that and run Resolve's own Python instead:")
    words = [script_path() or "anamorphick_resolve_setup.py"] + list(argv)
    say('      "%s" %s' % (paths["resolve_python"], " ".join('"%s"' % w if " " in w else w for w in words)))


# ------------------------------------------------------------------------------ version-tolerant API layer
class Api(object):
    """Calls the canonical 21.1 forms (SetSettings, SetProperties, dict ImportMedia...) on 21.1+
    and the classic forms before that, falling back to the other form if the first is refused."""

    def __init__(self, app):
        self.app = app
        self.product = str(self.safe(app, "GetProductName") or "DaVinci Resolve (unknown edition)")
        vs = self.safe(app, "GetVersionString")
        vl = self.safe(app, "GetVersion")
        nums = []
        if isinstance(vl, (list, tuple)):
            nums = [int(x) for x in vl if isinstance(x, int) or (isinstance(x, str) and x.isdigit())]
        if not nums and vs:
            nums = [int(x) for x in re.findall(r"\d+", str(vs))]
        self.version = tuple(nums[:4]) if nums else (0,)
        self.version_str = str(vs) if vs else ".".join(str(n) for n in self.version)
        self.studio = "studio" in self.product.lower()
        self.new_api = self.version[:2] >= (21, 1)

    @staticmethod
    def safe(obj, name, *args):
        f = _method(obj, name)
        if f is None:
            return None
        try:
            return f(*args)
        except Exception:
            return None

    # ---- project / timeline settings
    def get_setting(self, obj, key):
        order = ("GetSettings", "GetSetting") if self.new_api else ("GetSetting", "GetSettings")
        for name in order:
            f = _method(obj, name)
            if f is None:
                continue
            try:
                if name == "GetSettings":
                    d = f()
                    if isinstance(d, dict) and key in d:
                        return d[key]
                else:
                    v = f(key)
                    if v is not None:
                        return v
            except Exception:
                continue
        return None

    def set_setting(self, obj, key, value):
        value = value if isinstance(value, str) else str(value)
        order = ("SetSettings", "SetSetting") if self.new_api else ("SetSetting", "SetSettings")
        for name in order:
            f = _method(obj, name)
            if f is None:
                continue
            try:
                r = f({key: value}) if name == "SetSettings" else f(key, value)
            except Exception:
                continue
            if _ok(r):
                return True
        return False

    def set_verify(self, obj, key, value, numeric=False):
        ret = self.set_setting(obj, key, value)
        rb = self.get_setting(obj, key)
        return same_value(rb, value, numeric), rb, ret

    # ---- timeline item properties
    def get_item_prop(self, item, key):
        order = ("GetProperties", "GetProperty") if self.new_api else ("GetProperty", "GetProperties")
        for name in order:
            f = _method(item, name)
            if f is None:
                continue
            try:
                if name == "GetProperties":
                    d = f()
                    if isinstance(d, dict) and key in d:
                        return d[key]
                else:
                    v = f(key)
                    if v is not None:
                        return v
            except Exception:
                continue
        return None

    def set_item_prop(self, item, key, value):
        order = ("SetProperties", "SetProperty") if self.new_api else ("SetProperty", "SetProperties")
        for name in order:
            f = _method(item, name)
            if f is None:
                continue
            try:
                r = f({key: value}) if name == "SetProperties" else f(key, value)
            except Exception:
                continue
            if _ok(r):
                return True
        return False

    # ---- clips
    def clip_props(self, mpi):
        d = self.safe(mpi, "GetClipProperty")
        return d if isinstance(d, dict) else {}

    def clip_prop(self, mpi, key):
        d = self.clip_props(mpi)
        if key in d:
            return d[key]
        return self.safe(mpi, "GetClipProperty", key)

    @staticmethod
    def _as_list(r):
        if isinstance(r, dict):
            r = [r[k] for k in sorted(r)]
        if isinstance(r, (list, tuple)):
            return [x for x in r if x is not None]
        return []

    def folder_clips(self, folder):
        return self._as_list(self.safe(folder, "GetClipList"))

    def import_media(self, mp, paths):
        folder = self.safe(mp, "GetCurrentFolder")
        before = len(self.folder_clips(folder))
        dict_form = [{"FilePath": p} for p in paths]
        forms = [("[{FilePath: ...}]", dict_form), ("[paths]", list(paths))]
        if not self.new_api:
            forms.reverse()
        for label, arg in forms:
            f = _method(mp, "ImportMedia")
            if f is None:
                break
            try:
                items = self._as_list(f(arg))
            except Exception:
                items = []
            if items:
                return items, "MediaPool.ImportMedia(%s)" % label
            now = self.folder_clips(folder)
            if len(now) > before:
                return now[before:], "MediaPool.ImportMedia(%s)" % label
        ms = self.safe(self.app, "GetMediaStorage")
        forms = [("[{media: ...}]", [{"media": p} for p in paths]), ("[paths]", list(paths))]
        if not self.new_api:
            forms.reverse()
        for label, arg in forms:
            f = _method(ms, "AddItemListToMediaPool")
            if f is None:
                break
            try:
                items = self._as_list(f(arg))
            except Exception:
                items = []
            if items:
                return items, "MediaStorage.AddItemListToMediaPool(%s)" % label
            now = self.folder_clips(folder)
            if len(now) > before:
                return now[before:], "MediaStorage.AddItemListToMediaPool(%s)" % label
        return [], "nothing imported"

    # ---- timelines
    def find_timeline(self, proj, name):
        n = self.safe(proj, "GetTimelineCount") or 0
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 0
        for i in range(1, n + 1):
            t = self.safe(proj, "GetTimelineByIndex", i)
            if t is not None and self.safe(t, "GetName") == name:
                return t
        return None

    def video_items(self, tl):
        return self._as_list(self.safe(tl, "GetItemListInTrack", "video", 1))

    def create_timeline_from_clips(self, mp, proj, name, items):
        forms = [("[{mediaPoolItem}]", [{"mediaPoolItem": c} for c in items]), ("[clips]", list(items))]
        if not self.new_api:
            forms.reverse()
        for label, arg in forms:
            f = _method(mp, "CreateTimelineFromClips")
            if f is None:
                break
            try:
                tl = f(name, arg)
            except Exception:
                tl = None
            if tl is None:
                tl = self.find_timeline(proj, name)
            if tl is not None:
                return tl, "MediaPool.CreateTimelineFromClips(name, %s)" % label
        return None, None

    def append(self, mp, tl, items):
        before = len(self.video_items(tl))
        forms = [("[{mediaPoolItem}]", [{"mediaPoolItem": c} for c in items]), ("[clips]", list(items))]
        if not self.new_api:
            forms.reverse()
        for label, arg in forms:
            f = _method(mp, "AppendToTimeline")
            if f is None:
                break
            try:
                f(arg)
            except Exception:
                pass
            if len(self.video_items(tl)) > before:
                return True, "MediaPool.AppendToTimeline(%s)" % label
        return False, None

    # ---- grading
    def num_nodes(self, item, graph):
        for obj in (graph, item):
            v = self.safe(obj, "GetNumNodes")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return int(v)
        return None

    def set_lut(self, item, graph, idx, path):
        for obj, label in ((graph, "Graph.SetLUT"), (item, "TimelineItem.SetLUT")):
            f = _method(obj, "SetLUT")
            if f is None:
                continue
            try:
                if _ok(f(idx, path)):
                    return label
            except Exception:
                continue
        return None

    def get_lut(self, item, graph, idx):
        for obj in (graph, item):
            v = self.safe(obj, "GetLUT", idx)
            if v:
                return str(v)
        return ""


# ------------------------------------------------------------------------------ steps
def lut_matches(readback, rel, dest):
    rb = str(readback or "").replace("\\", "/").strip().lower()
    if not rb:
        return False
    return (rb.endswith(rel.replace("\\", "/").lower()) or rb == dest.replace("\\", "/").lower()
            or os.path.basename(rb) == os.path.basename(dest).lower())


def install_lut(src, targets):
    """Copy src into <target>/Anamorphick/. Same name + same content = reuse; same name +
    different content = name_v2.cube, _v3 ... Returns dict or None, plus notes."""
    notes = []
    src_hash = sha256_of(src)
    stem, ext = os.path.splitext(os.path.basename(src))
    for label, base in targets:
        dest_dir = os.path.join(base, LUT_SUBFOLDER)
        try:
            if not os.path.isdir(dest_dir):
                os.makedirs(dest_dir)
        except OSError as e:
            notes.append("%s LUT folder not writable (%s: %s)" % (label, type(e).__name__, e))
            continue
        n, dest, reused = 1, None, False
        while n < 100:
            name = stem + ext if n == 1 else "%s_v%d%s" % (stem, n, ext)
            cand = os.path.join(dest_dir, name)
            if not os.path.exists(cand):
                dest = cand
                break
            try:
                if os.path.isfile(cand) and sha256_of(cand) == src_hash:
                    dest, reused = cand, True
                    break
            except OSError:
                pass
            n += 1
        if dest is None:
            notes.append("%s: no free file name in %s" % (label, dest_dir))
            continue
        try:
            if not reused:
                shutil.copyfile(src, dest)
            if sha256_of(dest) != src_hash:
                notes.append("%s: copy verification failed for %s" % (label, dest))
                continue
        except (OSError, IOError) as e:
            notes.append("%s: copy failed (%s: %s)" % (label, type(e).__name__, e))
            continue
        rel = LUT_SUBFOLDER + "/" + os.path.basename(dest)
        return {"where": label, "base": base, "dest": dest, "rel": rel, "reused": reused,
                "versioned": n > 1, "sha256": src_hash}, notes
    return None, notes


def zoom_for(mode, src_w, src_h, tl_w, tl_h, sq):
    """Inspector ZoomX/ZoomY that make a square-pixel clip look desqueezed by sq and fill the
    timeline. Assumes Zoom 1.0 = the clip after input scaling ('fitted' image)."""
    if mode == "stretch":
        return 1.0, 1.0
    if mode == "scaleToCrop":
        s = max(tl_w / float(src_w), tl_h / float(src_h))
    elif mode == "centerCrop":
        s = 1.0
    else:
        s = min(tl_w / float(src_w), tl_h / float(src_h))
    k = max(tl_w / (src_w * sq), tl_h / float(src_h))
    return round(sq * k / s, 4), round(k / s, 4)


def unique_basename(folder, base, exts=(".mp4", ".mov")):
    name, n = base, 2
    while any(os.path.exists(os.path.join(folder, name + e)) for e in exts):
        name = "%s_%d" % (base, n)
        n += 1
    return name


def pick_h264(codecs):
    if not isinstance(codecs, dict) or not codecs:
        return None
    vals = [str(v) for v in codecs.values()]
    if "H264" in vals:
        return "H264"
    for v in vals:
        if v.upper().startswith("H264"):
            return v
    for desc, v in codecs.items():
        if "264" in str(desc):
            return str(v)
    return None


def job_info(api, proj, job):
    for j in api._as_list(api.safe(proj, "GetRenderJobList")):
        if isinstance(j, dict) and str(j.get("JobId")) == str(job):
            return j
    return {}


def render_started(api, proj, job, wait):
    end = time.time() + wait
    while True:
        st = api.safe(proj, "GetRenderJobStatus", job) or {}
        pct = to_float(st.get("CompletionPercentage")) if isinstance(st, dict) else None
        js = str(st.get("JobStatus", "")).lower() if isinstance(st, dict) else ""
        if (pct or 0) > 0 or js in ("rendering", "complete") or _ok(api.safe(proj, "IsRenderingInProgress")):
            return True
        if time.time() >= end:
            return False
        time.sleep(0.5)


def start_render(api, proj, job):
    f = _method(proj, "StartRendering")
    if f is None:
        return None
    attempts = (("StartRendering([job])", lambda: f([job])),
                ("StartRendering([job], isInteractiveMode=False)", lambda: f([job], isInteractiveMode=False)),
                ("StartRendering([job], False)", lambda: f([job], False)),
                ("StartRendering(job)", lambda: f(job)))
    for n, (label, call) in enumerate(attempts):
        try:
            r = call()
        except Exception:
            continue
        if render_started(api, proj, job, 8.0 if n == 0 else 4.0):
            return label
        if r is True:                       # accepted but no progress yet: do not start it twice
            return label + " (returned True, no progress yet)"
    return None


def build_plan(ctx):
    o = ctx["opts"]
    say("")
    say("=" * 100)
    say("ANAMORPHICK RESOLVE SETUP %s   mode: %s%s" % (SCRIPT_VERSION, ctx["mode"],
                                                     "   (DRY RUN - nothing will be changed)" if o["dry_run"] else ""))
    say("=" * 100)
    say("Clips (%d, from %s):" % (len(ctx["clips"]), ctx["clip_source"]))
    for i, c in enumerate(ctx["clips"], 1):
        say("  %d. %s" % (i, c))
        say("     %s" % describe_sniff(ctx["sniffs"].get(c)))
    for p, why in ctx["skipped"]:
        say("  skipped: %s (%s)" % (p, why))
    sq = ctx["squeeze"]
    first = ctx["sniffs"].get(ctx["clips"][0]) if ctx["clips"] else None
    if first:
        w, h, aspect, notes = compute_timeline_size(first["width"], first["height"], sq, o["timeline_width"])
        say("Desqueeze: %gx -> %dx%d source shows as %.4f:1 -> timeline %d x %d%s" % (
            sq, first["width"], first["height"], aspect, w, h, (" (" + "; ".join(notes) + ")") if notes else ""))
        if first.get("fps"):
            say("Frame rate: %s (from the file header; re-checked against Resolve after import)"
                % (fps_label(first["fps"]) or first["fps"]))
    else:
        say("Desqueeze: %gx; timeline size and frame rate come from Resolve after import" % sq)
    say("Project:  \"%s\"  (new; ' (2)', ' (3)' ... appended if the name exists)" % ctx["project_base"])
    say("Color:    DaVinci YRGB (not color managed); timeline + output color space Rec.709 Gamma 2.4")
    say("LUT:      %s" % (ctx["lut_src"] or "NOT FOUND"))
    if ctx["lut_src"]:
        tg = [os.path.join(t[1], LUT_SUBFOLDER) for t in ctx["lut_targets"]]
        say("          -> %s%s" % (tg[0], ("  (if not writable: %s)" % tg[1]) if len(tg) > 1 else ""))
    say("Timeline: \"%s\"; LUT on node 1 of every clip; then the Color page" % ctx["timeline_name"])
    say("Desqueeze method: %s" % {"auto": "clip Pixel Aspect Ratio, fallback timeline 'stretch', then Zoom X/Y",
                                  "par": "clip Pixel Aspect Ratio only", "stretch": "timeline input scaling 'stretch'",
                                  "zoom": "Inspector Zoom X/Y"}[o["desqueeze_method"]])
    if o["render_preview"]:
        say("Preview:  H.264 .mp4, %d px wide, into %s" % (PREVIEW_WIDTH, ctx["out_dir"]))
    else:
        say("Preview:  not requested (add --render-preview)")
    say("-" * 100)


def run_steps(ctx, api, report):
    o, sq, kind = ctx["opts"], ctx["squeeze"], ctx["kind"]
    app = api.app
    pm = api.safe(app, "GetProjectManager")
    if pm is None:
        report.add("Project manager", "FAILED", "GetProjectManager() returned nothing",
                   "Make sure Resolve has finished starting and a project database is open.")
        return
    if _method(pm, "GetCurrentDatabase") and api.safe(pm, "GetCurrentDatabase") is None:
        report.add("Database", "WARN", "GetCurrentDatabase() -> None (Resolve may not have a database attached)",
                   "If project creation fails: quit and restart Resolve.")

    # ---- 1. project
    say("\n[1] New project")
    cur = api.safe(pm, "GetCurrentProject")
    cur_name = str(api.safe(cur, "GetName") or "") if cur is not None else ""
    if cur_name and cur_name.lower() != "untitled project" and o["save_current_project_first"]:
        r = api.safe(pm, "SaveProject")
        say("    saved your open project \"%s\" before switching (SaveProject -> %s)" % (cur_name, r))
    existing = [str(x) for x in api._as_list(api.safe(pm, "GetProjectListInCurrentFolder"))]
    lower = set(x.lower() for x in existing)
    base = ctx["project_base"]
    name, n = base, 2
    while name.lower() in lower:
        name = "%s (%d)" % (base, n)
        n += 1
    proj = api.safe(pm, "CreateProject", name)
    if proj is None:
        # None does not always mean "name taken" (a blocked project switch also returns None):
        # re-read the list, move to a still-free name, save a named open project, try once more.
        low2 = set(str(x).lower() for x in api._as_list(api.safe(pm, "GetProjectListInCurrentFolder")))
        while name.lower() in low2:
            name = "%s (%d)" % (base, n)
            n += 1
        if cur_name and cur_name.lower() != "untitled project" and o["save_current_project_first"]:
            api.safe(pm, "SaveProject")
        proj = api.safe(pm, "CreateProject", name)
    if proj is None:
        report.add("New project", "FAILED", "CreateProject(\"%s\") returned None (current project: \"%s\")"
                   % (name, cur_name or "?"),
                   "Close any open Resolve dialog, save the open project (File > Save Project), then re-run. "
                   "If it still fails, open any other project from the Project Manager (no changes needed) and re-run.")
        return
    rb_name = api.safe(api.safe(pm, "GetCurrentProject"), "GetName") or api.safe(proj, "GetName")
    ctx["project_name"] = rb_name or name
    taken = "" if name == base else " (\"%s\" already existed)" % base
    folder = api.safe(pm, "GetCurrentFolder")
    report.add("New project", "OK" if rb_name == name else "WARN",
               "created \"%s\"%s; current project reads back \"%s\"; project folder \"%s\""
               % (name, taken, rb_name, folder if folder else "root"),
               None if rb_name == name else "Check File > Project Manager for the new project.")

    # ---- 2. color management on the empty project
    say("\n[2] Color management (empty project)")
    ok_sci, rb_sci, _ = api.set_verify(proj, "colorScienceMode", "davinciYRGB")
    cs = {}
    for which in ("Timeline", "Output"):
        key, gkey = "colorSpace" + which, "colorSpace" + which + "Gamma"
        done, how = False, ""
        if str(api.get_setting(proj, "separateColorSpaceAndGamma") or "0") != "1":
            ok, rb, _ = api.set_verify(proj, key, "Rec.709 Gamma 2.4")
            done, how = ok, "combined"
        if not done:
            if str(api.get_setting(proj, "separateColorSpaceAndGamma") or "0") != "1":
                api.set_verify(proj, "separateColorSpaceAndGamma", "1")
            ok1, rb1, _ = api.set_verify(proj, key, "Rec.709")
            ok2, rb2, _ = api.set_verify(proj, gkey, "Gamma 2.4")
            done, how = ok1 and ok2, "separate"
        cs[which] = (done, how)
    sep = api.get_setting(proj, "separateColorSpaceAndGamma")
    rbs = {k: api.get_setting(proj, k) for k in ("colorScienceMode", "colorSpaceTimeline", "colorSpaceTimelineGamma",
                                                  "colorSpaceOutput", "colorSpaceOutputGamma", "isAutoColorManage")}
    det = "colorScienceMode=%s; timeline=%s%s; output=%s%s; separateColorSpaceAndGamma=%s; isAutoColorManage=%s" % (
        rbs["colorScienceMode"], rbs["colorSpaceTimeline"],
        (" + " + str(rbs["colorSpaceTimelineGamma"])) if str(sep) == "1" else "",
        rbs["colorSpaceOutput"], (" + " + str(rbs["colorSpaceOutputGamma"])) if str(sep) == "1" else "",
        sep, rbs["isAutoColorManage"])
    all_ok = ok_sci and cs["Timeline"][0] and cs["Output"][0]
    report.add("Color management", "OK" if all_ok else ("WARN" if ok_sci else "FAILED"), det,
               None if all_ok else "File > Project Settings > Color Management: Color science = DaVinci YRGB; "
                                   "Timeline color space = Rec.709 Gamma 2.4; Output color space = Rec.709 Gamma 2.4; Save.")

    # ---- 3. frame rate before import (from the file header)
    say("\n[3] Timeline frame rate (before import)")
    first_sniff = ctx["sniffs"].get(ctx["clips"][0])
    fps_set = None
    if first_sniff and first_sniff.get("fps"):
        for lab in fps_candidates(first_sniff["fps"]):
            ok, rb, _ = api.set_verify(proj, "timelineFrameRate", lab, numeric=True)
            if ok:
                fps_set = (lab, rb)
                break
        if fps_set:
            say("    timelineFrameRate set to %s from the file header (reads back %s)" % fps_set)
        else:
            say("    could not set timelineFrameRate from the header value %s; will retry after import"
                % first_sniff["fps"])
    else:
        say("    header frame rate unknown; will set it from Resolve's clip FPS after import")

    # ---- 4. import
    say("\n[4] Import")
    mp = api.safe(proj, "GetMediaPool")
    root = api.safe(mp, "GetRootFolder")
    if root is not None:
        api.safe(mp, "SetCurrentFolder", root)
    items, how = api.import_media(mp, ctx["clips"])
    by_path = {}
    for it in items:
        p = api.clip_prop(it, "File Path")
        if p:
            by_path[os.path.normcase(os.path.abspath(str(p)))] = it
    ordered, missing = [], []
    used = set()
    for c in ctx["clips"]:
        it = by_path.get(os.path.normcase(os.path.abspath(c)))
        if it is None:                               # e.g. a resolved /Volumes path: match by file name
            for cand in items:
                if id(cand) not in used and str(api.clip_prop(cand, "File Name") or "").lower() == os.path.basename(c).lower():
                    it = cand
                    break
        if it is not None:
            used.add(id(it))
            ordered.append(it)
        else:
            missing.append(c)
    if not ordered and items:
        ordered = list(items)                        # paths not reported: keep Resolve's order
        missing = []
    if not ordered:
        report.add("Import", "FAILED", "%s; no media pool items" % how,
                   "Media page: drag the clip into the Media Pool. If Resolve shows it as unsupported/offline, "
                   "the codec may need Resolve Studio or a transcode.")
        return
    props = [api.clip_props(it) for it in ordered]
    first_p = props[0]
    det = "%d of %d clip(s) via %s; first: %s, Resolution %s, FPS %s, PAR %s, codec %s" % (
        len(ordered), len(ctx["clips"]), how, first_p.get("File Name") or first_p.get("Clip Name"),
        first_p.get("Resolution"), first_p.get("FPS"), first_p.get("PAR"), first_p.get("Video Codec"))
    report.add("Import", "OK" if not missing else "WARN", det + ("; NOT imported: %s" % ", ".join(missing) if missing else ""),
               None if not missing else "Import the missing clip(s) on the Media page and check their codec.")
    for i, p in enumerate(props[1:], 2):
        if p.get("Resolution") != first_p.get("Resolution") or not same_value(p.get("FPS"), first_p.get("FPS"), True):
            say("    note: clip %d is %s @ %s fps (first clip %s @ %s) -- the timeline follows the first clip"
                % (i, p.get("Resolution"), p.get("FPS"), first_p.get("Resolution"), first_p.get("FPS")))

    # ---- 5. frame rate check against Resolve's clip FPS
    say("\n[5] Timeline frame rate (check against Resolve)")
    clip_fps = to_float(first_p.get("FPS"))
    if clip_fps is None and first_sniff:
        clip_fps = first_sniff.get("fps")
    proj_fps = api.get_setting(proj, "timelineFrameRate")
    ctx["timeline_fps"] = None
    if clip_fps is None:
        report.add("Frame rate", "WARN", "clip FPS unknown; project timelineFrameRate reads back %s" % proj_fps,
                   "Check File > Project Settings > Master Settings > Timeline frame rate against the clip.")
    elif same_value(proj_fps, clip_fps, True):
        report.add("Frame rate", "OK", "clip FPS %s; project timelineFrameRate reads back %s%s" % (
            first_p.get("FPS"), proj_fps, " (set before import)" if fps_set else ""))
    else:
        fixed = None
        for lab in fps_candidates(clip_fps):
            ok, rb, _ = api.set_verify(proj, "timelineFrameRate", lab, numeric=True)
            if ok:
                fixed = rb
                break
        if fixed is not None:
            report.add("Frame rate", "OK", "clip FPS %s; project timelineFrameRate set after import, reads back %s"
                       % (first_p.get("FPS"), fixed))
        else:
            ctx["timeline_fps"] = fps_candidates(clip_fps)
            say("    project frame rate is locked at %s; the timeline will get its own frame rate" % proj_fps)
            ctx["fps_pending"] = (first_p.get("FPS"), proj_fps)

    # ---- 6. timeline format
    say("\n[6] Timeline format")
    src_w, src_h = parse_resolution(first_p.get("Resolution"))
    if not src_w and first_sniff:
        src_w, src_h = first_sniff["width"], first_sniff["height"]
    if not src_w:
        report.add("Timeline format", "FAILED", "clip resolution unknown (Resolution=%r)" % first_p.get("Resolution"),
                   "Project Settings > Master Settings: set Timeline resolution to source width x %g : height." % sq)
        return
    tw, th, aspect, notes = compute_timeline_size(src_w, src_h, sq, o["timeline_width"], cap_free=not api.studio)
    ctx["src_wh"], ctx["aspect"] = (src_w, src_h), aspect
    method = o["desqueeze_method"]
    mismatch = {"zoom": "scaleToFit"}.get(method, "scaleToCrop")
    want = (("timelineResolutionWidth", str(tw), True), ("timelineResolutionHeight", str(th), True),
            ("timelinePixelAspectRatio", "square", False), ("timelineOutputResMatchTimelineRes", "1", False),
            ("timelineInputResMismatchBehavior", mismatch, False))
    bad = []
    for key, val, num in want:
        ok, rb, _ = api.set_verify(proj, key, val, numeric=num)
        if not ok:
            bad.append("%s=%s (read back %r)" % (key, val, rb))
    rw = api.get_setting(proj, "timelineResolutionWidth")
    rh = api.get_setting(proj, "timelineResolutionHeight")
    rmis = api.get_setting(proj, "timelineInputResMismatchBehavior")
    ctx["tl_wh"] = (int(to_float(rw) or tw), int(to_float(rh) or th))
    ctx["mismatch"] = str(rmis or mismatch)
    det = "source %dx%d x %g = %.4f:1 -> timeline reads back %sx%s, PAR %s, output matches timeline %s, input scaling %s%s" % (
        src_w, src_h, sq, aspect, rw, rh, api.get_setting(proj, "timelinePixelAspectRatio"),
        api.get_setting(proj, "timelineOutputResMatchTimelineRes"), rmis, ("; " + "; ".join(notes)) if notes else "")
    report.add("Timeline format", "OK" if not bad else "FAILED", det + ("; refused: " + ", ".join(bad) if bad else ""),
               None if not bad else "File > Project Settings > Master Settings: Timeline resolution Custom %d x %d, "
                                    "Pixel aspect ratio Square; Image Scaling > Input Scaling: Scale full frame with crop."
                                    % (tw, th))

    # ---- 7. pixel aspect ratio on the clips
    say("\n[7] Desqueeze: clip Pixel Aspect Ratio %g" % sq)
    ctx["par_ok"] = [False] * len(ordered)
    if method in ("auto", "par"):
        cands = par_candidates(sq)
        winner = None
        lines = []
        for i, it in enumerate(ordered):
            orig = api.clip_prop(it, "PAR")
            if parse_par(orig) is not None and abs(parse_par(orig) - sq) < 0.02:
                ctx["par_ok"][i] = True
                lines.append("clip %d already PAR %r" % (i + 1, orig))
                continue
            order = ([winner] + [c for c in cands if c != winner]) if winner else cands
            tried = []
            for c in order:
                ret = api.safe(it, "SetClipProperty", "PAR", c)
                rb = api.clip_prop(it, "PAR")
                tried.append("%r->%s/%r" % (c, ret, rb))
                v = parse_par(rb)
                if v is not None and abs(v - sq) < 0.02:
                    ctx["par_ok"][i], winner = True, c
                    lines.append("clip %d: SetClipProperty('PAR', %r) -> reads back %r" % (i + 1, c, rb))
                    break
            if not ctx["par_ok"][i]:
                if str(api.clip_prop(it, "PAR")) != str(orig) and orig is not None:
                    api.safe(it, "SetClipProperty", "PAR", str(orig))
                lines.append("clip %d: no PAR value stuck (tried %s); restored %r" % (i + 1, ", ".join(tried), orig))
        nok = sum(ctx["par_ok"])
        if nok == len(ordered):
            report.add("Desqueeze (clip PAR)", "OK", "; ".join(lines))
        else:
            report.add("Desqueeze (clip PAR)", "FAILED" if method == "par" else "WARN",
                       "%d of %d clip(s) took PAR %g: %s" % (nok, len(ordered), sq, "; ".join(lines)),
                       "Media Pool: select the clip(s) > right-click > Clip Attributes > Video > Pixel Aspect Ratio > "
                       "Custom, enter %g (or pick a %g preset) > OK." % (sq, sq))
            if method == "auto":
                banner(["PIXEL ASPECT RATIO %g WAS NOT ACCEPTED ON %d CLIP(S)." % (sq, len(ordered) - nok),
                        "Using the fallback: timeline input scaling 'Stretch frame to all corners'",
                        "(then Inspector Zoom X/Y if that is refused). Check the result on the Color page."])
    else:
        report.add("Desqueeze (clip PAR)", "SKIPPED", "--desqueeze-method %s" % method)

    # ---- 8. LUT into Resolve's LUT folder
    say("\n[8] LUT install")
    lut = None
    if ctx["lut_src"]:
        lut, notes = install_lut(ctx["lut_src"], ctx["lut_targets"])
        for n_ in notes:
            say("    " + n_)
        if lut:
            rr = api.safe(proj, "RefreshLUTList")
            det = "%s %s (%s LUT folder%s); RefreshLUTList -> %s" % (
                "reused identical" if lut["reused"] else "copied to", lut["dest"], lut["where"],
                ", versioned: a different file had that name" if lut["versioned"] else "", rr)
            if lut["where"] == "user":
                report.add("LUT install", "WARN", det + "; master folder not writable: " + "; ".join(notes),
                           "Project Settings > Color Management > Lookup Tables > Open LUT Folder: copy %s into an "
                           "'Anamorphick' subfolder there, click Update Lists." % os.path.basename(ctx["lut_src"]))
            else:
                report.add("LUT install", "OK", det)
        else:
            report.add("LUT install", "FAILED", "; ".join(notes) or "copy failed",
                       "Project Settings > Color Management > Lookup Tables > Open LUT Folder: copy %s into an "
                       "'Anamorphick' subfolder there, click Update Lists." % os.path.basename(ctx["lut_src"]))
    else:
        report.add("LUT install", "FAILED", "LUT file not found (looked in: %s)" % ", ".join(ctx["lut_looked"]),
                   "Re-run with --lut /path/to/golden_709.cube (or set CONFIG['lut']).")

    # ---- 9. timeline
    say("\n[9] Timeline")
    tl_name = ctx["timeline_name"]
    tl, how = None, None
    if ctx.get("fps_pending"):
        tl = api.safe(mp, "CreateEmptyTimeline", tl_name)
        if tl is not None:
            api.safe(proj, "SetCurrentTimeline", tl)
            api.set_verify(tl, "useCustomSettings", "1")
            got = None
            for lab in ctx["timeline_fps"]:
                ok, rb, _ = api.set_verify(tl, "timelineFrameRate", lab, numeric=True)
                if ok:
                    got = rb
                    break
            for key, val, num in want[:3] + (("timelineInputResMismatchBehavior", ctx["mismatch"], False),):
                api.set_verify(tl, key, val, numeric=num)
            okk, how = api.append(mp, tl, ordered)
            how = "CreateEmptyTimeline + custom timeline frame rate + %s" % how
            if got is not None:
                report.add("Frame rate", "OK", "project rate locked at %s; timeline \"Use Project Settings\" off, "
                           "timeline frame rate reads back %s (clip FPS %s)" % (ctx["fps_pending"][1], got, ctx["fps_pending"][0]))
            else:
                report.add("Frame rate", "FAILED", "project timelineFrameRate locked at %s, timeline-level rate refused "
                           "(clip FPS %s)" % (ctx["fps_pending"][1], ctx["fps_pending"][0]),
                           "Media Pool: right-click the clip > Create New Timeline Using Selected Clips; untick 'Use "
                           "Project Settings', Format tab: Timeline Frame Rate = %s." % ctx["fps_pending"][0])
    else:
        tl, how = api.create_timeline_from_clips(mp, proj, tl_name, ordered)
        if tl is None:
            tl = api.safe(mp, "CreateEmptyTimeline", tl_name)
            if tl is not None:
                api.safe(proj, "SetCurrentTimeline", tl)
                okk, how2 = api.append(mp, tl, ordered)
                how = "CreateEmptyTimeline + %s" % how2
    if tl is None:
        report.add("Timeline", "FAILED", "could not create \"%s\"" % tl_name,
                   "Media Pool: select the clip(s) > right-click > Create New Timeline Using Selected Clips.")
        return
    api.safe(proj, "SetCurrentTimeline", tl)
    cur_tl = api.safe(proj, "GetCurrentTimeline")
    vitems = api.video_items(tl)
    ctx["timeline_name"] = api.safe(tl, "GetName") or tl_name
    det = "\"%s\" via %s; current timeline \"%s\"; %d video item(s) on V1; timeline reads %sx%s @ %s fps" % (
        ctx["timeline_name"], how, api.safe(cur_tl, "GetName"), len(vitems), api.get_setting(tl, "timelineResolutionWidth"),
        api.get_setting(tl, "timelineResolutionHeight"), api.get_setting(tl, "timelineFrameRate"))
    report.add("Timeline", "OK" if len(vitems) >= len(ordered) else "WARN", det,
               None if len(vitems) >= len(ordered) else "Drag the missing clip(s) from the Media Pool onto V1.")

    # ---- 10. desqueeze fallbacks
    need = [i for i, ok in enumerate(ctx["par_ok"]) if not ok]
    tl_w, tl_h = ctx["tl_wh"]
    if method == "par" or not need:
        if method in ("auto", "par"):
            report.add("Desqueeze fallback", "SKIPPED", "not needed" if not need else "--desqueeze-method par")
    else:
        say("\n[10] Desqueeze fallback")
        done = False
        if method in ("auto", "stretch"):
            ok1, rb1, _ = api.set_verify(tl, "useCustomSettings", "1")
            okw, rbw, _ = api.set_verify(tl, "timelineResolutionWidth", str(tl_w), numeric=True)
            okh, rbh, _ = api.set_verify(tl, "timelineResolutionHeight", str(tl_h), numeric=True)
            ok3, rb3, _ = api.set_verify(tl, "timelineInputResMismatchBehavior", "stretch")
            if ok1 and ok3 and okw and okh:
                done = True
                report.add("Desqueeze fallback", "WARN",
                           "timeline \"%s\": useCustomSettings=%s, %sx%s, timelineInputResMismatchBehavior=%s -> the "
                           "%.4f:1 frame is stretched to all corners = %gx horizontal desqueeze (this timeline only; "
                           "clips of another shape will be distorted)" % (ctx["timeline_name"], rb1, rbw, rbh, rb3, aspect, sq),
                           "Optional, cleaner: set Clip Attributes > Pixel Aspect Ratio = %g on the clip, then Timeline "
                           "Settings > Input Scaling back to 'Scale full frame with crop'." % sq)
            else:
                say("    timeline stretch refused (useCustomSettings %r, input scaling %r); trying Zoom X/Y" % (rb1, rb3))
                if str(rb1) == "1":
                    api.set_verify(tl, "useCustomSettings", "0")
        if not done:
            mode_rb = str(api.get_setting(tl, "timelineInputResMismatchBehavior") or ctx["mismatch"])
            zx, zy = zoom_for(mode_rb, src_w, src_h, tl_w, tl_h, sq)
            res, allok = [], True
            for i in need:
                if i >= len(vitems):
                    allok = False
                    continue
                it = vitems[i]
                api.set_item_prop(it, "ZoomGang", False)
                api.set_item_prop(it, "ZoomX", zx)
                api.set_item_prop(it, "ZoomY", zy)
                g, x, y = (api.get_item_prop(it, k) for k in ("ZoomGang", "ZoomX", "ZoomY"))
                ok = same_value(x, zx, True, 0.001) and same_value(y, zy, True, 0.001)
                allok = allok and ok
                res.append("item %d ZoomGang=%s ZoomX=%s ZoomY=%s" % (i + 1, g, x, y))
            report.add("Desqueeze fallback", "WARN" if allok else "FAILED",
                       "Inspector zoom (input scaling %s): %s; assumes Zoom 1.0 = the fitted clip, check the "
                       "picture" % (mode_rb, "; ".join(res) or "no items"),
                       "Edit page: select the clip > Inspector > Transform: unlink Zoom, Zoom X = %g, Zoom Y = %g "
                       "(or Clip Attributes > Pixel Aspect Ratio = %g)." % (zx, zy, sq))

    # ---- 11. Color page
    say("\n[11] Color page")
    api.safe(app, "OpenPage", "color")
    page = api.safe(app, "GetCurrentPage")
    report.add("Color page", "OK" if page == "color" else "WARN", "GetCurrentPage() -> %r" % page,
               None if page == "color" else "Click the Color page tab at the bottom of the Resolve window.")

    # ---- 12. LUT on node 1
    say("\n[12] LUT on node 1")
    if not lut:
        report.add("LUT on node 1", "SKIPPED", "no LUT installed",
                   "Color page: right-click node 01 > LUT > Anamorphick > golden_709 (after installing the LUT).")
    else:
        forms = [lut["rel"], lut["dest"]]
        if os.sep != "/":
            forms.append(lut["rel"].replace("/", os.sep))
        res, nok, winner = [], 0, None
        for attempt in (1, 2):
            res, nok = [], 0
            for i, it in enumerate(vitems):
                graph = api.safe(it, "GetNodeGraph")
                nn = api.num_nodes(it, graph)
                used = None
                for form in ([winner] if winner else []) + [f for f in forms if f != winner]:
                    via = api.set_lut(it, graph, 1, form)
                    if via:
                        used, winner = (form, via), form
                        break
                rb = api.get_lut(it, graph, 1)
                good = bool(used) and lut_matches(rb, lut["rel"], lut["dest"])
                nok += 1 if good else 0
                res.append("item %d: nodes=%s, %s, GetLUT(1)=%r" % (
                    i + 1, nn, ("%s(1, %r)" % (used[1], used[0])) if used else "SetLUT refused", rb))
            if nok == len(vitems) or attempt == 2:
                break
            say("    SetLUT refused; RefreshLUTList again and retry in 1 s")
            api.safe(proj, "RefreshLUTList")
            time.sleep(1.0)
        st = "OK" if vitems and nok == len(vitems) else "FAILED"
        report.add("LUT on node 1", st, "; ".join(res) or "no timeline items",
                   None if st == "OK" else "Color page: for each clip right-click node 01 > LUT > %s > %s "
                   "(if it is not listed: Project Settings > Color Management > Update Lists)."
                   % (LUT_SUBFOLDER, os.path.splitext(os.path.basename(lut["dest"]))[0]))

    # ---- 13. preview render
    if not o["render_preview"]:
        report.add("Preview render", "SKIPPED", "not requested (--render-preview)")
    else:
        say("\n[13] Preview render")
        render_preview(ctx, api, proj, tl, report)
        api.safe(app, "OpenPage", "color")
        page = api.safe(app, "GetCurrentPage")
        say("    back to the Color page: GetCurrentPage() -> %r" % page)

    # ---- 14. save
    r = api.safe(pm, "SaveProject")
    report.add("Save project", "OK" if _ok(r) else "WARN", "SaveProject() -> %r for \"%s\"" % (r, ctx["project_name"]),
               None if _ok(r) else "File > Save Project.")


def render_preview(ctx, api, proj, tl, report):
    o, sq = ctx["opts"], ctx["squeeze"]
    out_dir = ctx["out_dir"]
    try:
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
    except OSError as e:
        report.add("Preview render", "FAILED", "cannot create %s (%s)" % (out_dir, e), "Re-run with --out <writable folder>.")
        return
    tw, th = ctx["tl_wh"]
    pw = min(PREVIEW_WIDTH, tw - tw % 2)
    ph = max(2, even_round(pw * th / float(tw)))
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.splitext(os.path.basename(ctx["clips"][0]))[0])
    name = unique_basename(out_dir, "%s_%sx_golden709_preview" % (stem, fmt_sq(sq)))
    api.safe(proj, "SetCurrentTimeline", tl)
    presets = [str(p) for p in api._as_list(api.safe(proj, "GetRenderPresetList"))]
    preset = "H.264 Master" if "H.264 Master" in presets else None
    preset_ok = _ok(api.safe(proj, "LoadRenderPreset", preset)) if preset else False
    fmts = api.safe(proj, "GetRenderFormats")
    exts = [str(v).lower() for v in fmts.values()] if isinstance(fmts, dict) else []
    chosen = None
    for ext in ("mp4", "mov"):
        if exts and ext not in exts:
            continue
        codecs = api.safe(proj, "GetRenderCodecs", ext)
        if not codecs and isinstance(fmts, dict):
            key = next((k for k, v in fmts.items() if str(v).lower() == ext), None)
            codecs = api.safe(proj, "GetRenderCodecs", key) if key else None
        codec = pick_h264(codecs)
        if codec and _ok(api.safe(proj, "SetCurrentRenderFormatAndCodec", ext, codec)):
            chosen = (ext, codec)
            break
    cur = api.safe(proj, "GetCurrentRenderFormatAndCodec")
    if not chosen:
        report.add("Preview render", "FAILED", "no H.264 format/codec accepted (formats %s; current %r)" % (exts, cur),
                   "Deliver page: Format MP4, Codec H.264, Resolution Custom %d x %d, Location %s, Add to Render Queue, "
                   "Render All." % (pw, ph, out_dir))
        return
    if _method(proj, "SetCurrentRenderMode"):
        api.safe(proj, "SetCurrentRenderMode", 1)               # 1 = Single clip -> one preview file
    mode_rb = api.safe(proj, "GetCurrentRenderMode")
    groups = [("target", {"SelectAllFrames": True, "TargetDir": out_dir, "CustomName": name}),
              ("streams", {"ExportVideo": True, "ExportAudio": True}),
              ("size", {"FormatWidth": pw, "FormatHeight": ph, "PixelAspectRatio": "square"})]
    applied = []
    for label, payload in groups:
        r = _ok(api.safe(proj, "SetRenderSettings", payload))
        if not r and label == "size":
            r = _ok(api.safe(proj, "SetRenderSettings", {"FormatWidth": pw, "FormatHeight": ph}))
            label = "size(no PAR)"
        applied.append("%s=%s" % (label, r))
    _ok(api.safe(proj, "SetRenderSettings", {"VideoQuality": "High"}))
    job = api.safe(proj, "AddRenderJob")
    if not job:
        report.add("Preview render", "FAILED", "AddRenderJob() returned %r (settings: %s)" % (job, ", ".join(applied)),
                   "Deliver page: Format MP4, Codec H.264, Resolution Custom %d x %d, Location %s, Add to Render "
                   "Queue, Render All." % (pw, ph, out_dir))
        return
    info = job_info(api, proj, job)
    say("    job %s: %s/%s %sx%s -> %s" % (job, info.get("VideoFormat", chosen[0]), info.get("VideoCodec", chosen[1]),
                                          info.get("FormatWidth"), info.get("FormatHeight"),
                                          os.path.join(str(info.get("TargetDir", out_dir)), str(info.get("OutputFilename", name)))))
    how = start_render(api, proj, job)
    if not how and " " in out_dir:
        alt = os.path.join(os.path.dirname(out_dir), os.path.basename(out_dir).replace(" ", "_"))
        if " " in alt:
            import tempfile
            alt = os.path.join(tempfile.gettempdir(), "Anamorphick_Previews")
        say("    render did not start; retrying once with a folder without spaces: %s" % alt)
        try:
            if not os.path.isdir(alt):
                os.makedirs(alt)
            api.safe(proj, "DeleteRenderJob", job)
            api.safe(proj, "SetRenderSettings", {"TargetDir": alt})
            job = api.safe(proj, "AddRenderJob")
            info = job_info(api, proj, job) if job else {}
            how = start_render(api, proj, job) if job else None
            out_dir = alt
        except OSError:
            pass
    if not how:
        report.add("Preview render", "FAILED", "job %s queued but did not start (render settings: %s)" % (job, ", ".join(applied)),
                   "Deliver page: the job is in the Render Queue -- click Render All.")
        return
    timeout = float(o["render_timeout"])
    stall_limit = min(60.0, timeout)          # no progress at all for this long: stop waiting
    say("    started with %s; waiting up to %ss" % (how, int(timeout)))
    t0, last, st, moving, stalled = time.time(), None, {}, False, False
    while time.time() < t0 + timeout:
        st = api.safe(proj, "GetRenderJobStatus", job) or {}
        pct = to_float(st.get("CompletionPercentage")) if isinstance(st, dict) else None
        js = str(st.get("JobStatus", "")) if isinstance(st, dict) else ""
        if (pct, js) != last:
            say("    %s %s%%" % (js, "?" if pct is None else int(pct)))
            last = (pct, js)
        moving = moving or (pct or 0) > 0 or js.lower() in ("rendering", "complete") or \
            _ok(api.safe(proj, "IsRenderingInProgress"))
        if isinstance(st, dict) and (st.get("Error") or js.lower() in ("failed", "cancelled")):
            break
        if pct is not None and pct >= 100 and not _ok(api.safe(proj, "IsRenderingInProgress")):
            break
        if not moving and time.time() - t0 > stall_limit:
            break
        time.sleep(1.0)
    stalled = not moving
    info = job_info(api, proj, job) or info
    target = str(info.get("TargetDir") or out_dir)
    fname = info.get("OutputFilename")
    out_path = os.path.join(target, str(fname)) if fname else None
    if not (out_path and os.path.isfile(out_path)):
        hits = sorted(glob.glob(os.path.join(glob.escape(target), glob.escape(name) + "*")), key=os.path.getmtime)
        out_path = hits[-1] if hits else out_path
    size = os.path.getsize(out_path) if out_path and os.path.isfile(out_path) else 0
    pct = to_float(st.get("CompletionPercentage")) if isinstance(st, dict) else None
    done = size > 0 and pct is not None and pct >= 100 and not (isinstance(st, dict) and st.get("Error"))
    ctx["preview_path"] = out_path if size else None
    det = "preset %s loaded %s; format/codec read back %s; job %s %s; render mode %s; status %s %s%%%s; job reads %s %sx%s; %s" % (
        preset, preset_ok, cur, job, how, {0: "individual clips", 1: "single clip"}.get(mode_rb, mode_rb),
        st.get("JobStatus") if isinstance(st, dict) else st, pct,
        (" error: %s" % st.get("Error")) if isinstance(st, dict) and st.get("Error") else "",
        "%s/%s" % (info.get("VideoFormat", chosen[0]), info.get("VideoCodec", chosen[1])),
        info.get("FormatWidth"), info.get("FormatHeight"),
        ("file %s (%d bytes)" % (out_path, size)) if size else "no output file yet (expected %s)" % out_path)
    if stalled and not done:
        report.add("Preview render", "WARN", det + "; no progress after %ds, stopped waiting" % int(stall_limit),
                   "Deliver page: the job is in the Render Queue -- if it is not rendering, click Render All; the "
                   "file will be %s." % os.path.join(target, str(fname or name + "." + chosen[0])))
        return
    report.add("Preview render", "OK" if done else "FAILED", det,
               None if done else "Deliver page: check the Render Queue; re-render the job (it is queued), or render "
                                 "MP4/H.264 %dx%d manually into %s." % (pw, ph, out_dir))


# ------------------------------------------------------------------------------ main
def parse_options(argv, inapp):
    opts = dict(CONFIG)
    p = argparse.ArgumentParser(prog="anamorphick_resolve_setup.py",
                                description="Create a NEW DaVinci Resolve project for anamorphic clips with the "
                                            "Anamorphick golden Rec.709 LUT (see the docstring for the 3 ways to run it).")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--clip", nargs="+", metavar="PATH", help="clip file(s), in timeline order")
    g.add_argument("--folder", metavar="PATH", help="folder to scan recursively (e.g. the card's DCIM), or 'auto'")
    p.add_argument("--squeeze", type=float, help="anamorphic squeeze (default 1.5)")
    p.add_argument("--lut", help="LUT file (default golden_709.cube next to this script)")
    p.add_argument("--project", help='project name (default "Anamorphick 2026-11-03 1.5x Golden")')
    p.add_argument("--timeline-width", type=int, help="timeline width (default 3840, never above source width x squeeze)")
    p.add_argument("--render-preview", action="store_true", default=None, help="render a 1280-wide H.264 .mp4")
    p.add_argument("--out", help="preview folder (default ~/Movies/Anamorphick Previews or ~/Videos/...)")
    p.add_argument("--dry-run", action="store_true", default=None, help="print the plan only")
    p.add_argument("--desqueeze-method", choices=("auto", "par", "stretch", "zoom"), help="default auto")
    p.add_argument("--lut-dir", help="use this folder instead of Resolve's master LUT folder")
    p.add_argument("--render-timeout", type=int, help="seconds to wait for the render (default 900)")
    p.add_argument("--no-save-current", action="store_true", default=None,
                   help="do not SaveProject() the open project before switching")
    if inapp:
        a, unknown = p.parse_known_args(argv)
        if unknown:
            say("ignoring unknown arguments: %s" % " ".join(unknown))
    else:
        a = p.parse_args(argv)
    m = {"clip": "clips", "folder": "folder", "squeeze": "squeeze", "lut": "lut", "project": "project",
         "timeline_width": "timeline_width", "render_preview": "render_preview", "out": "out",
         "dry_run": "dry_run", "desqueeze_method": "desqueeze_method", "lut_dir": "lut_dir",
         "render_timeout": "render_timeout"}
    for k, dest in m.items():
        v = getattr(a, k)
        if v is not None:
            opts[dest] = v
    if a.clip:
        opts["folder"] = ""
    if a.folder:
        opts["clips"] = []
    if a.no_save_current:
        opts["save_current_project_first"] = False
    return opts


def find_lut(opts, kind):
    looked = []
    sp = script_path()
    kit = os.path.dirname(sp) if sp else None
    if opts.get("lut"):
        p = os.path.expanduser(str(opts["lut"]))
        for cand in [p] + ([os.path.join(kit, p)] if kit and not os.path.isabs(p) else []):
            looked.append(cand)
            if os.path.isfile(cand):
                return os.path.abspath(cand), looked
        return None, looked
    dirs = [kit] if kit else []
    paths = resolve_paths(kind)
    dirs += [os.path.join(paths["scripts_user"], s) for s in ("Utility", "Color", "")]
    dirs += [os.path.join(paths["scripts_all"], "Utility"), os.getcwd()]
    for d in dirs:
        cand = os.path.join(d, DEFAULT_LUT_NAME)
        looked.append(cand)
        if os.path.isfile(cand):
            return os.path.abspath(cand), looked
    return None, looked


def main(argv=None):
    app, how = find_inapp_resolve()
    inapp = app is not None
    if argv is None:
        argv = list((getattr(sys, "argv", None) or [""])[1:])
    try:
        opts = parse_options(argv, inapp)
    except SystemExit as e:
        if inapp:
            return 2
        raise
    kind = os_kind()
    report = Report()
    ctx = {"opts": opts, "kind": kind, "mode": how or "terminal (DaVinciResolveScript)"}
    try:
        sq = float(opts["squeeze"])
        if not 0.5 <= sq <= 4.0:
            raise ValueError
    except (TypeError, ValueError):
        say("error: squeeze must be a number between 0.5 and 4 (got %r)" % opts.get("squeeze"))
        return 2
    ctx["squeeze"] = sq
    if opts["desqueeze_method"] not in ("auto", "par", "stretch", "zoom"):
        opts["desqueeze_method"] = "auto"

    clips, skipped, source, err = collect_clips(opts, kind)
    ctx.update(clips=clips, skipped=skipped, clip_source=source)
    if err or not clips:
        say("error: %s" % (err or "no usable clips found in %s (extensions: %s; files under 100 KB and '._' files are "
                                  "skipped)" % (source, " ".join(VIDEO_EXTS))))
        for p_, why in skipped:
            say("  skipped: %s (%s)" % (p_, why))
        return 2
    ctx["sniffs"] = dict((c, sniff_media(c)) for c in clips)
    ctx["lut_src"], ctx["lut_looked"] = find_lut(opts, kind)
    paths = resolve_paths(kind)
    if opts.get("lut_dir"):
        ctx["lut_targets"] = [("custom", os.path.abspath(os.path.expanduser(str(opts["lut_dir"]))))]
    else:
        ctx["lut_targets"] = [("master", paths["lut_master"]), ("user", paths["lut_user"])]
    ctx["project_base"] = opts["project"] or DEFAULT_PROJECT_FMT.format(sq=fmt_sq(sq))
    ctx["timeline_name"] = TIMELINE_NAME_FMT.format(stem=os.path.splitext(os.path.basename(clips[0]))[0], sq=fmt_sq(sq))
    ctx["out_dir"] = os.path.abspath(os.path.expanduser(str(opts["out"]))) if opts.get("out") else paths["out"]
    build_plan(ctx)

    diag = []
    if not inapp:
        app = connect_external(kind, diag)
    if app is None:
        if opts["dry_run"]:
            say("Resolve: not reachable from here -- the plan above comes from the file headers only.")
            explain_connection_failure(kind, diag, argv)
            return 0
        explain_connection_failure(kind, diag, argv)
        return 3
    api = Api(app)
    say("Connected: %s %s -- %s" % (api.product, api.version_str, ctx["mode"]))
    report.add("Connect", "OK", "%s %s, %s" % (api.product, api.version_str, ctx["mode"]))
    if not api.studio:
        say("    free edition: no Studio-only calls are used; timeline capped at UHD")
    report.add("Clips", "OK", "%d clip(s): %s%s" % (
        len(clips), ", ".join(os.path.basename(c) for c in clips),
        ("; skipped %d: %s" % (len(skipped), ", ".join("%s (%s)" % (os.path.basename(p_), w) for p_, w in skipped))) if skipped else ""))

    if opts["dry_run"]:
        pm = api.safe(app, "GetProjectManager")
        existing = set(str(x).lower() for x in api._as_list(api.safe(pm, "GetProjectListInCurrentFolder")))
        name, n = ctx["project_base"], 2
        while name.lower() in existing:
            name = "%s (%d)" % (ctx["project_base"], n)
            n += 1
        say("Dry run: the new project would be named \"%s\" (%d project(s) in the current Project Manager folder)."
            % (name, len(existing)))
        report.add("Dry run", "SKIPPED", "nothing changed in Resolve; project would be \"%s\"" % name)
        print_summary(report, ctx)
        return 0
    try:
        run_steps(ctx, api, report)
    except Exception as e:
        import traceback
        traceback.print_exc()
        report.add("Unexpected error", "FAILED", "%s: %s" % (type(e).__name__, e),
                   "Copy the output above and send it back; nothing existing was deleted.")
    print_summary(report, ctx)
    return 1 if report.failed() else 0


if __name__ == "__main__" or find_inapp_resolve()[0] is not None:
    _rc = main()
    if find_inapp_resolve()[0] is None:
        sys.exit(_rc)
