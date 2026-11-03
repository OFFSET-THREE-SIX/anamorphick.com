#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_lut.py <file.cube> [--strict-size 33] [--quiet]

Checks a 3D .cube LUT the way it will be used (Resolve / ffmpeg lut3d, tetrahedral interpolation):
  * format: TITLE, LUT_3D_SIZE, DOMAIN_MIN/MAX 0..1, entry count, 3 values per line, 6 decimals
  * every output value inside [0,1]
  * per-channel strictly increasing along the neutral axis (lattice + 2049 interpolated steps)
  * black / white points, shadow separation  f(0.03) > 0.008
  * gray-ramp table: in, out RGB, R-B, HSV hue, luma, luma delta (+ warmth-only luma delta when the
    <stem>.params.json sidecar written by make_lut.py is present)
  * look targets (scaled by the sidecar "strength"; skipped for an identity LUT):
        0.41 gray -> R-B 0.04..0.06 (x strength), hue 40..50 deg
        0.90 gray -> R-B 0.06..0.08 (x strength)
        warmth luma change on grays within +-0.015
  * max / mean deviation from identity
  * saturated patches: 100% and 75% Rec.709 primaries/secondaries, ramps gray->primary
    (chroma must keep increasing, no plateau = no hard clip), share of values stuck at 0/1
  * skin-tone / ColorChecker-style patches: hue shift, saturation ratio, chroma gain vs luma
Exit code 0 = all hard checks passed, 1 = something failed, 2 = unreadable file.
"""
import json
import math
import os
import re
import sys

import numpy as np

LUMA = np.array([0.2126, 0.7152, 0.0722])
NUM6 = re.compile(r"^-?\d+\.\d{6}$")

# Approximate, commonly cited sRGB 8-bit values of the 24 ColorChecker Classic patches (test colours
# only - not a measurement reference) plus three generic skin samples.
PATCHES = [
    ("dark skin", (115, 82, 68)), ("light skin", (194, 150, 130)), ("blue sky", (98, 122, 157)),
    ("foliage", (87, 108, 67)), ("blue flower", (133, 128, 177)), ("bluish green", (103, 189, 170)),
    ("orange", (214, 126, 44)), ("purplish blue", (80, 91, 166)), ("moderate red", (193, 90, 99)),
    ("purple", (94, 60, 108)), ("yellow green", (157, 188, 64)), ("orange yellow", (224, 163, 46)),
    ("blue", (56, 61, 150)), ("green", (70, 148, 73)), ("red", (175, 54, 60)),
    ("yellow", (231, 199, 31)), ("magenta", (187, 86, 149)), ("cyan", (8, 133, 161)),
    ("white 9.5", (243, 243, 242)), ("neutral 8", (200, 200, 200)), ("neutral 6.5", (160, 160, 160)),
    ("neutral 5", (122, 122, 121)), ("neutral 3.5", (85, 85, 85)), ("black 2", (52, 52, 52)),
    ("skin deep (generic)", (92, 60, 45)), ("skin medium (generic)", (165, 118, 92)),
    ("skin fair (generic)", (232, 192, 172)),
]
SKIN = ("dark skin", "light skin", "skin deep (generic)", "skin medium (generic)", "skin fair (generic)")


class Report:
    def __init__(self):
        self.fails = 0
        self.lines = []

    def check(self, ok, name, detail):
        if ok is None:
            tag = "INFO"
        elif ok:
            tag = "PASS"
        else:
            tag = "FAIL"
            self.fails += 1
        self.out("[%s] %-34s %s" % (tag, name, detail))

    def out(self, s=""):
        self.lines.append(s)
        print(s)


def read_cube(path):
    hdr = {"TITLE": None, "LUT_3D_SIZE": None, "DOMAIN_MIN": None, "DOMAIN_MAX": None}
    data, bad_tokens, bad_lines, kw_after_data, comments = [], 0, [], [], 0
    with open(path, "r") as f:
        for ln, line in enumerate(f, 1):
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                comments += 1
                continue
            tok = s.split()
            if re.match(r"^[A-Z_]+", tok[0]) and not re.match(r"^[-+0-9.]", tok[0]):
                key = tok[0]
                if data:
                    kw_after_data.append((ln, key))
                if key == "TITLE":
                    hdr["TITLE"] = s[5:].strip()
                elif key == "LUT_3D_SIZE":
                    hdr["LUT_3D_SIZE"] = int(tok[1])
                elif key in ("DOMAIN_MIN", "DOMAIN_MAX"):
                    hdr[key] = [float(t) for t in tok[1:4]]
                else:
                    hdr[key] = " ".join(tok[1:])
                continue
            if len(tok) != 3:
                bad_lines.append(ln)
                continue
            for t in tok:
                if not NUM6.match(t):
                    bad_tokens += 1
            data.append([float(t) for t in tok])
    return hdr, np.array(data, float), bad_tokens, bad_lines, kw_after_data, comments


def tetra(L, pts):
    """Tetrahedral interpolation; L indexed [r, g, b, channel]."""
    n = L.shape[0]
    p = np.clip(np.asarray(pts, float), 0, 1) * (n - 1)
    i0 = np.minimum(np.floor(p).astype(int), n - 2)
    f = p - i0
    r, g, b = i0[:, 0], i0[:, 1], i0[:, 2]
    fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]

    def c(dr, dg, db):
        return L[r + dr, g + dg, b + db]

    c000, c100, c010, c001 = c(0, 0, 0), c(1, 0, 0), c(0, 1, 0), c(0, 0, 1)
    c110, c101, c011, c111 = c(1, 1, 0), c(1, 0, 1), c(0, 1, 1), c(1, 1, 1)
    R, G, B = fr[:, 0], fg[:, 0], fb[:, 0]
    conds = [(R >= G) & (G >= B), (R >= B) & (B >= G), (B >= R) & (R >= G),
             (G >= R) & (R >= B), (G >= B) & (B >= R)]
    vals = [c000 + fr * (c100 - c000) + fg * (c110 - c100) + fb * (c111 - c110),
            c000 + fr * (c100 - c000) + fb * (c101 - c100) + fg * (c111 - c101),
            c000 + fb * (c001 - c000) + fr * (c101 - c001) + fg * (c111 - c101),
            c000 + fg * (c010 - c000) + fr * (c110 - c010) + fb * (c111 - c110),
            c000 + fg * (c010 - c000) + fb * (c011 - c010) + fr * (c111 - c011)]
    last = c000 + fb * (c001 - c000) + fg * (c011 - c001) + fr * (c111 - c011)
    out = last.copy()
    done = np.zeros(len(R), bool)
    for cnd, v in zip(conds, vals):
        m = cnd & ~done
        out[m] = v[m]
        done |= m
    return out


def hue_sat(rgb):
    rgb = np.atleast_2d(rgb)
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    mx, mn = rgb.max(1), rgb.min(1)
    c = mx - mn
    cs = np.where(c > 1e-9, c, 1)
    h = np.where(mx == r, np.mod((g - b) / cs, 6), np.where(mx == g, (b - r) / cs + 2, (r - g) / cs + 4)) * 60
    h = np.where(c > 1e-6, h, np.nan)
    s = np.where(mx > 1e-9, c / np.maximum(mx, 1e-9), 0)
    return h, s


def hue_diff(a, b):
    return (a - b + 180.0) % 360.0 - 180.0


def load_sidecar(cube_path):
    side = os.path.splitext(cube_path)[0] + ".params.json"
    if not os.path.exists(side):
        return None, None
    with open(side) as f:
        P = json.load(f)
    curve = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import make_lut  # noqa: E402
        curve = lambda x: make_lut.neutral_curve(x, P)  # noqa: E731
    except Exception as exc:  # pragma: no cover
        print("note: could not import make_lut.py (%s); warmth-only luma delta skipped" % exc)
    return P, curve


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    path = args[0]
    want_size = 33
    if "--strict-size" in argv:
        want_size = int(argv[argv.index("--strict-size") + 1])
    rep = Report()
    rep.out("=" * 100)
    rep.out("validate_lut.py  %s" % os.path.abspath(path))
    rep.out("=" * 100)
    try:
        hdr, data, bad_tokens, bad_lines, kw_after, ncom = read_cube(path)
    except Exception as exc:
        print("cannot read: %s" % exc)
        return 2
    n = hdr["LUT_3D_SIZE"]

    # ---------------------------------------------------------------- format
    rep.out("-- format")
    rep.check(hdr["TITLE"] is not None, "TITLE", repr(hdr["TITLE"]))
    rep.check(n == want_size, "LUT_3D_SIZE", "%s (expected %d)" % (n, want_size))
    rep.check(hdr["DOMAIN_MIN"] == [0.0, 0.0, 0.0], "DOMAIN_MIN", hdr["DOMAIN_MIN"])
    rep.check(hdr["DOMAIN_MAX"] == [1.0, 1.0, 1.0], "DOMAIN_MAX", hdr["DOMAIN_MAX"])
    expected = (n or 0) ** 3
    rep.check(len(data) == expected and not bad_lines, "entry count",
              "%d data lines (expected %d), malformed lines: %d" % (len(data), expected, len(bad_lines)))
    rep.check(bad_tokens == 0, "6-decimal formatting", "%d tokens not in d.dddddd form" % bad_tokens)
    rep.check(not kw_after, "no keywords after data", kw_after[:3] if kw_after else "ok")
    rep.check(None, "comment lines", "%d '#' lines (allowed by the Adobe/IRIDAS cube format)" % ncom)
    rep.check(bool(np.all(np.isfinite(data))) if len(data) else False, "finite values", "no NaN/Inf")
    if len(data) != expected or n is None:
        rep.out("cannot continue: wrong entry count")
        return 1
    # red fastest:  index = r + n*g + n*n*b  ->  reshape [b][g][r] then transpose to [r][g][b]
    L = data.reshape(n, n, n, 3).transpose(2, 1, 0, 3)
    v = np.linspace(0, 1, n)

    # ---------------------------------------------------------------- range / identity deviation
    rep.out("-- range")
    lo, hi = data.min(), data.max()
    rep.check(lo >= 0.0 and hi <= 1.0, "all values in [0,1]", "min %.6f  max %.6f" % (lo, hi))
    grid = np.stack(np.meshgrid(v, v, v, indexing="ij"), -1)      # [r][g][b] -> (r,g,b)
    dev = np.abs(L - grid)
    k = np.unravel_index(np.argmax(dev.max(-1)), dev.shape[:3])
    rep.check(None, "max deviation from identity",
              "%.4f at in (%.3f,%.3f,%.3f) -> out (%.4f,%.4f,%.4f); mean %.4f"
              % (dev.max(), v[k[0]], v[k[1]], v[k[2]], *L[k], dev.mean()))
    is_identity = dev.max() < 2e-6
    in01 = (grid <= 1e-9) | (grid >= 1 - 1e-9)
    out01 = (L <= 1e-7) | (L >= 1 - 1e-7)
    rep.check(None, "values exactly 0 or 1",
              "%.2f%% of output values (identity has %.2f%%)" % (100 * out01.mean(), 100 * in01.mean()))
    # each output channel vs its own input axis, whole cube
    viol = 0
    viol += int(np.sum(np.diff(L[..., 0], axis=0) < -1e-6))
    viol += int(np.sum(np.diff(L[..., 1], axis=1) < -1e-6))
    viol += int(np.sum(np.diff(L[..., 2], axis=2) < -1e-6))
    rep.check(viol == 0, "own-axis monotonic (whole cube)", "%d decreasing steps of %d" % (viol, 3 * n * n * (n - 1)))

    # ---------------------------------------------------------------- neutral axis
    rep.out("-- neutral axis")
    diag = L[np.arange(n), np.arange(n), np.arange(n)]
    dsteps = np.diff(diag, axis=0)
    rep.check(bool(np.all(dsteps > 0)), "lattice diagonal strictly incr.",
              "min step per channel R %.5f G %.5f B %.5f" % tuple(dsteps.min(0)))
    x = np.linspace(0, 1, 2049)
    ramp = tetra(L, np.stack([x, x, x], -1))
    st = np.diff(ramp, axis=0)
    rep.check(bool(np.all(st > 0)), "2049-step gray ramp strictly incr.",
              "min step %.2e (R) %.2e (G) %.2e (B)" % tuple(st.min(0)))
    P, curve = load_sidecar(path)
    strength = 0.0 if is_identity else float(P.get("strength", 1.0)) if P else 1.0

    def at(val):
        return tetra(L, np.array([[val, val, val]]))[0]

    blk, wht, s03 = at(0.0), at(1.0), at(0.03)
    rep.check(bool(np.all(np.abs(blk) <= 0.005)), "black point |out(0)| <= 0.005",
              "(%.5f, %.5f, %.5f)" % tuple(blk))
    rep.check(bool(np.all(s03 > 0.008)), "shadow sep. out(0.03) > 0.008",
              "(%.5f, %.5f, %.5f)  luma %.5f" % (*s03, s03 @ LUMA))
    rep.check(bool(wht.max() >= 0.985 and wht.min() >= 0.90 and wht @ LUMA >= 0.97), "white point",
              "(%.4f, %.4f, %.4f) luma %.4f  8-bit (%d,%d,%d)"
              % (*wht, wht @ LUMA, *np.round(wht * 255).astype(int)))

    steps = [0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.18, 0.25, 0.3, 0.35, 0.41, 0.435, 0.5,
             0.6, 0.7, 0.8, 0.9, 0.95, 0.98, 1.0]
    rep.out("")
    rep.out("   in   |   R_out    G_out    B_out  |   R-B    hue  | luma_out  dLuma  dLuma_warmth | 8-bit out")
    worst_wl = 0.0
    tab = {}
    for s in steps:
        o = at(s)
        y = float(o @ LUMA)
        h, _ = hue_sat(o)
        wl = (y - float(curve(s))) if curve else float("nan")
        if curve and not math.isnan(wl):
            worst_wl = max(worst_wl, abs(wl))
        tab[s] = (o, h[0])
        rep.out("  %.3f | %.5f  %.5f  %.5f | %+.4f %5s | %.5f  %+.4f   %+.4f      | %3d %3d %3d"
                % (s, o[0], o[1], o[2], o[0] - o[2], "  -  " if np.isnan(h[0]) else "%5.1f" % h[0],
                   y, y - s, wl, *np.round(o * 255).astype(int)))
    rep.out("  (dLuma = luma_out - in, includes the contrast curve; dLuma_warmth = luma_out - contrast"
            " curve(in), from the params sidecar)")
    rep.out("")
    if is_identity:
        rep.check(None, "look targets", "identity LUT - warmth/contrast targets not applicable")
    else:
        o, h = tab[0.41]
        rb = o[0] - o[2]
        lo_, hi_ = 0.04 * strength, 0.06 * strength
        rep.check(lo_ <= rb <= hi_ and 40 <= h <= 50, "0.41 gray warmth",
                  "R-B %+.4f (target %.3f..%.3f), hue %.1f (target 40..50)" % (rb, lo_, hi_, h))
        o, h = tab[0.9]
        rb = o[0] - o[2]
        lo_, hi_ = 0.06 * strength, 0.08 * strength
        rep.check(lo_ <= rb <= hi_, "0.90 gray warmth",
                  "R-B %+.4f (target %.3f..%.3f), hue %.1f" % (rb, lo_, hi_, h))
        if curve:
            rep.check(worst_wl <= 0.015, "warmth luma change on grays", "max |dLuma_warmth| %.4f (<= 0.015)" % worst_wl)
        else:
            rep.check(None, "warmth luma change on grays", "no sidecar - not separable from contrast")
        hues = [tab[s][1] for s in steps if s >= 0.1]
        rep.check(None, "warm hue across ramp 0.1..1", "%.1f .. %.1f deg" % (np.nanmin(hues), np.nanmax(hues)))

    # ---------------------------------------------------------------- saturated patches
    rep.out("-- saturated patches (Rec.709 primaries / secondaries)")
    rep.out("   patch        in              out                 hue in->out   sat in->out  e_out  stuck-0/1")
    names = ["red", "green", "blue", "cyan", "magenta", "yellow"]
    base = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 1, 1], [1, 0, 1], [1, 1, 0]], float)
    for lev in (1.0, 0.75):
        pts = base * lev
        outp = tetra(L, pts)
        hi_, si_ = hue_sat(pts)
        ho_, so_ = hue_sat(outp)
        for nm, pi, po, a, b, c, d in zip(names, pts, outp, hi_, ho_, si_, so_):
            Y = po @ LUMA
            Cv = po - Y
            e = max(np.max(np.where(Cv > 0, Cv / max(1 - Y, 1e-6), 0)), np.max(np.where(Cv < 0, -Cv / max(Y, 1e-6), 0)))
            stuck = int(np.sum((po <= 1e-7) | (po >= 1 - 1e-7)))
            rep.out("   %-7s %3d%%  (%.2f,%.2f,%.2f) -> (%.3f,%.3f,%.3f)   %5.1f->%5.1f   %.3f->%.3f   %.3f   %d"
                    % (nm, lev * 100, *pi, *po, a, b, c, d, e, stuck))
    # ramps gray -> primary: the chroma the LUT adds on top of the (warm) gray start must keep growing
    # (no fold-back), and no channel may sit at 0/1 before the ramp reaches the primary (no plateau).
    t = np.linspace(0, 1, 129)
    worst_plateau, worst_dip, reversals = 0, 0.0, 0
    for prim in base:
        for start in (0.25, 0.5, 0.75):
            pts = (1 - t)[:, None] * start + t[:, None] * prim
            o = tetra(L, pts)
            Y = o @ LUMA
            rel = (o - Y[:, None]) - (o[0] - Y[0])
            chroma = np.linalg.norm(rel, axis=1)
            dip = np.maximum.accumulate(chroma) - chroma
            worst_dip = max(worst_dip, float(dip.max()))
            reversals += int(np.sum(dip > 0.002))
            stuck = np.any((o <= 1e-6) | (o >= 1 - 1e-6), axis=1)
            run = best = 0
            for sflag in stuck[:-1]:
                run = run + 1 if sflag else 0
                best = max(best, run)
            worst_plateau = max(worst_plateau, best)
    rep.check(reversals == 0, "gray->primary ramps: no fold-back",
              "max chroma dip %.4f (%.2f 8-bit codes; tolerance 0.002) over 18 ramps x 129 steps"
              % (worst_dip, worst_dip * 255))
    rep.check(worst_plateau <= 3 or is_identity, "no clip plateau on ramps",
              "longest run of ramp steps with a channel stuck at 0/1 before the end: %d" % worst_plateau)

    # ---------------------------------------------------------------- skin + ColorChecker-ish
    rep.out("-- ColorChecker-style patches (approx. sRGB values) and skin samples")
    rep.out("   patch                  in (8-bit)       out (8-bit)      hue in->out (d)     sat in->out  chroma/luma gain  dLuma")
    gains = []
    for nm, rgb8 in PATCHES:
        pi = np.array(rgb8, float) / 255
        po = tetra(L, pi[None])[0]
        hi_, si_ = hue_sat(pi)
        ho_, so_ = hue_sat(po)
        yi, yo = pi @ LUMA, po @ LUMA
        ci = np.linalg.norm(pi - yi) / max(yi, 1e-6)
        co = np.linalg.norm(po - yo) / max(yo, 1e-6)
        g = co / ci if ci > 0.02 else float("nan")
        if not math.isnan(g) and nm not in SKIN:
            gains.append(g)
        dh = hue_diff(ho_[0], hi_[0]) if not (np.isnan(hi_[0]) or np.isnan(ho_[0])) else float("nan")
        rep.out("   %-22s (%3d,%3d,%3d) -> (%3d,%3d,%3d)   %5.1f->%5.1f (%+5.1f)   %.3f->%.3f    %5s          %+.3f"
                % (nm, *rgb8, *np.round(po * 255).astype(int), hi_[0], ho_[0], dh, si_[0], so_[0],
                   "  -  " if math.isnan(g) else "%.3f" % g, yo - yi))
    if not is_identity:
        sk = []
        for nm, rgb8 in PATCHES:
            if nm in SKIN:
                pi = np.array(rgb8, float) / 255
                po = tetra(L, pi[None])[0]
                sk.append(hue_diff(hue_sat(po)[0][0], hue_sat(pi)[0][0]))
        rep.check(max(abs(s) for s in sk) <= 10.0, "skin hue shift <= 10 deg",
                  "shifts: " + ", ".join("%+.1f" % s for s in sk) + " deg (positive = toward yellow)")
        rep.check(None, "chroma/luma gain, non-skin patches",
                  "median %.3f  (min %.3f, max %.3f)" % (np.median(gains), min(gains), max(gains)))
    rep.out("")
    rep.out("RESULT: %s  (%d failed checks)" % ("OK" if rep.fails == 0 else "FAILED", rep.fails))
    return 0 if rep.fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
