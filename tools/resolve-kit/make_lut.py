#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_lut.py -- "Golden Rec.709" look for DaVinci Resolve, written as 33-point .cube 3D LUTs.

    python3 make_lut.py                    # writes the 4 standard LUTs next to this script
    python3 make_lut.py --outdir DIR       # ... somewhere else
    python3 make_lut.py --set contrast=1.2 --set warm_hue_deg=44 --name test_look --strength 1
    python3 make_lut.py --table            # print the neutral-axis table for every variant

Outputs (default: same folder as this script):
    golden_709.cube         medium look (default)            strength 1.0
    golden_709_soft.cube    ~0.6x the medium deltas          strength 0.6
    golden_709_strong.cube  ~1.5x the medium deltas          strength 1.5
    identity_33.cube        identity, for pipeline testing
    <name>.params.json      sidecar with every parameter (validate_lut.py reads it)

.cube format: optional '#' comment lines, TITLE, LUT_3D_SIZE 33, DOMAIN_MIN 0 0 0, DOMAIN_MAX 1 1 1,
then 33^3 = 35937 lines "R G B" with 6 decimals, RED index varying fastest, then green, then blue.

DOMAIN / WHERE TO USE IT
  Input and output are display-referred Rec.709 / Gamma 2.4 *encoded* RGB in full data levels 0..1,
  i.e. what a Resolve node sees on a Rec.709 Gamma 2.4 timeline (DaVinci YRGB, no colour management;
  or RCM with timeline = output = Rec.709 Gamma 2.4).  The LUT contains NO log, ACES or RCM transform.
  Log footage (F-Log / F-Log2) must first be converted to Rec.709 Gamma 2.4 (CST or the camera LUT)
  in an earlier node.  Put this LUT on a serial node AFTER exposure/white-balance balancing.

OPERATION ORDER (chosen to keep skin natural) -- all maths on the 0..1 encoded values:
  1. CONTRAST  S-curve around pivot 0.435 (slope 1.18 at the pivot for medium) built from two
     power segments: smooth toe and shoulder, f(0)=0, f(1)=1, strictly increasing, never clips.
     It is driven by max(R,G,B) and applied as ONE common gain to R, G and B ("max-RGB norm"):
     neutrals follow the curve exactly; for colours hue and HSV saturation are unchanged and no
     channel can leave 0..1, so the contrast stage itself can't skew skin toward orange/red the way
     separate per-channel curves do.  (contrast_rgb_mix > 0 blends in classic per-channel curves.)
  2. COLOUR INTENSITY  chroma relative to Rec.709 luma Y = 0.2126 R + 0.7152 G + 0.0722 B is scaled
     by 1 + b, Y untouched, b = (sat-1) * (1 - vibrance*s) * (1 - s^q) * shadow/white roll-offs *
     (1 - skin_protect * skin weight), s = HSV saturation.  Vibrance-style: muted colours get ~the
     full boost, already-saturated ones much less, fully saturated ones none; less around the
     skin-tone hue (~22 deg HSV) and on foliage greens (~100 deg, hue_protect) because the warm
     stage already adds chroma there, a little more on sky blue (~215 deg, hue_boost) because the warm
     stage takes chroma away there; rolled off in deep shadows (Y < ~0.15) and near white (Y > ~0.8).
  3. SOFT GAMUT COMPRESSION toward luma.  The (1 - s^q) term already keeps every channel >= 0.
     Toward the top of the cube the excursion e0 = (max - Y)/(1 - Y) (1.0 = brightest channel at
     1.0) would become e0*(1+b); instead it is squeezed into the room that is left:
         e_final = e0 + (1-e0) * S(u),  u = e0*b/(1-e0),  S(u) = u / (1+u^p)^(1/p)
     S(u) ~ u for small u (colours far from the boundary keep the full boost), S -> 1 for large u, so
     a colour approaches the cube boundary smoothly but never crosses it, and never ends up less
     saturated than it started.  Only the chroma is scaled (toward luma): hue and luma are kept,
     no per-channel hard clip anywhere.
  4. GOLDEN WARMTH, lift/gamma/gain style, as per-channel 1D offsets (like Resolve's wheels):
     on the neutral axis the offset is an amber vector of HSV hue 38 deg (shadows) -> 41 (mids) ->
     45.5 (highlights) -> 47 (white) whose direction is ~luma-neutral (R up, B down, G a hair up;
     luma moves by warm_luma_tilt * (R-B) <= +0.002); its size A(y) = R-B follows a
     smooth monotone-cubic curve through warm_amount_anchors: 0 at black (clean blacks, "lift" = 0),
     ~0.05 in the mids ("gamma"), ~0.07 in the highlights ("gain"), tapering to ~0.04 at white so
     white stays a warm white.  Where red would exceed 1.0 near white, all three channels are
     lowered together by a softplus "pin" (keeps the amber hue instead of drifting to yellow).
     Warmth is applied LAST so the saturation stage never amplifies the tint, and it is reduced by
     skin_warm_protect on skin-hue pixels (weight 0 on neutrals, so the gray targets are unchanged)
     so skin turns golden instead of orange.
  5. Final clamp to 0..1 (a safety no-op; the report counts how many values it touched).
"""
import argparse
import datetime
import json
import os
import sys

import numpy as np

VERSION = "1.0"
LUMA = np.array([0.2126, 0.7152, 0.0722])  # Rec.709 luma weights

# ---------------------------------------------------------------- parameters (medium = strength 1)
MEDIUM = {
    "pivot": 0.435,
    "contrast": 1.18,             # slope at the pivot
    "contrast_rgb_mix": 0.0,      # 0 = max-RGB norm (hue preserving), 1 = per-channel curves
    "sat": 1.22,                  # chroma gain for muted midtone colours
    "vibrance": 0.40,             # boost x (1 - vibrance*s), s = HSV saturation
    "sat_limit_power": 3.0,       # boost x (1 - s^q): fades to 0 for fully saturated colours
    "skin_protect": 0.65,         # saturation boost reduced by this fraction at the skin hue
    "skin_hue_deg": 22.0,         # HSV hue of typical skin in Rec.709 RGB
    "skin_hue_sigma_deg": 12.0,
    "skin_sat_window": [0.06, 0.18, 0.60, 0.85],   # HSV-sat ramp-in / ramp-out of skin weight
    "hue_protect": [[100.0, 32.0, 0.45]],          # [hue, sigma, fraction]: less boost on foliage greens
    "hue_boost": [[215.0, 22.0, 0.45]],            # [hue, sigma, fraction]: a bit more on sky blue, which
                                                   # the warm stage would otherwise grey out
    "sat_shadow_rolloff": [0.02, 0.15],            # boost ramps in between these luma values
    "sat_white_rolloff": [0.80, 1.00],             # boost ramps out between these luma values
    "gamut_power": 2.0,           # knee sharpness of the soft gamut compression
    "skin_warm_protect": 0.50,    # warmth reduced by this fraction on skin-hue pixels (0 on neutrals)
    # HSV hue of the warm shift on neutrals vs. tone: a little more amber in the shadows/mids (a dark,
    # low-saturation yellow reads olive/khaki), more golden-yellow toward the highlights.
    "warm_hue_anchors": [[0.00, 38.0], [0.20, 39.0], [0.41, 41.0], [0.70, 43.5], [0.90, 45.5], [1.00, 47.0]],
    "warm_luma_tilt": 0.03,       # warm offset luma = tilt * (R-B): ~0 (+0.002 at most), lets G rise a hair
    # (y, R-B) pairs on the neutral axis, y = value after the contrast stage
    "warm_amount_anchors": [[0.00, 0.000], [0.05, 0.003], [0.15, 0.016], [0.41, 0.050],
                            [0.70, 0.064], [0.93, 0.070], [1.00, 0.040]],
    "warm_pin_softness": 0.008,
    "white_luma_drop_max": 0.0145,  # cap on the luma cost of warming pure white (see scaled_params)
}

VARIANTS = [  # (file stem, strength, title)
    ("golden_709", 1.0, "Anamorphick Golden 709 medium"),
    ("golden_709_soft", 0.6, "Anamorphick Golden 709 soft"),
    ("golden_709_strong", 1.5, "Anamorphick Golden 709 strong"),
]


def scaled_params(strength, base=None):
    """Scale every *delta* of the medium look by `strength` (0.6 soft, 1.5 strong)."""
    base = MEDIUM if base is None else base
    p = json.loads(json.dumps(base))
    p["strength"] = float(strength)
    p["contrast"] = 1.0 + strength * (base["contrast"] - 1.0)
    p["sat"] = 1.0 + strength * (base["sat"] - 1.0)
    p["warm_amount_anchors"] = [[x, y * strength] for x, y in base["warm_amount_anchors"]]
    # The white end is pinned (red cannot exceed 1.0), so its warmth costs luma: the pin lowers all
    # three channels by ~pin(1).  Cap the white anchor so that cost stays <= white_luma_drop_max.
    eps = base["warm_pin_softness"]
    kr = float(warm_coeffs(base["warm_hue_anchors"][-1][1], base["warm_luma_tilt"])[0])
    z = np.log(np.expm1(base["white_luma_drop_max"] / eps))      # inverse softplus
    a_cap = float(z * eps / kr)
    if p["warm_amount_anchors"][-1][0] >= 1.0 and p["warm_amount_anchors"][-1][1] > a_cap:
        p["warm_amount_anchors"][-1][1] = a_cap
        p["white_anchor_capped"] = True
    return p


# ---------------------------------------------------------------- helpers
def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def pchip(xk, yk, x):
    """Monotone piecewise-cubic Hermite interpolation (Fritsch-Carlson / scipy PCHIP rules)."""
    xk = np.asarray(xk, float)
    yk = np.asarray(yk, float)
    n = len(xk)
    h = np.diff(xk)
    delta = np.diff(yk) / h
    d = np.zeros(n)
    for k in range(1, n - 1):
        if delta[k - 1] * delta[k] > 0:
            w1 = 2 * h[k] + h[k - 1]
            w2 = h[k] + 2 * h[k - 1]
            d[k] = (w1 + w2) / (w1 / delta[k - 1] + w2 / delta[k])

    def end(h0, h1, d0, d1):
        dd = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        if np.sign(dd) != np.sign(d0):
            dd = 0.0
        elif np.sign(d0) != np.sign(d1) and abs(dd) > abs(3 * d0):
            dd = 3 * d0
        return dd

    d[0] = end(h[0], h[1], delta[0], delta[1])
    d[-1] = end(h[-1], h[-2], delta[-1], delta[-2])
    x = np.clip(np.asarray(x, float), xk[0], xk[-1])
    i = np.clip(np.searchsorted(xk, x, side="right") - 1, 0, n - 2)
    t = (x - xk[i]) / h[i]
    t2, t3 = t * t, t * t * t
    return ((2 * t3 - 3 * t2 + 1) * yk[i] + (t3 - 2 * t2 + t) * h[i] * d[i]
            + (-2 * t3 + 3 * t2) * yk[i + 1] + (t3 - t2) * h[i] * d[i + 1])


def contrast_curve(x, pivot, k):
    """Pivoted S-curve: slope k at the pivot, f(0)=0, f(1)=1, smooth power toe and shoulder."""
    x = np.clip(np.asarray(x, float), 0.0, 1.0)
    lo = pivot * np.power(x / pivot, k)
    hi = 1.0 - (1.0 - pivot) * np.power((1.0 - x) / (1.0 - pivot), k)
    return np.where(x < pivot, lo, hi)


def hsv_hue_sat(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = rgb.max(-1)
    mn = rgb.min(-1)
    c = mx - mn
    cs = np.where(c > 1e-9, c, 1.0)
    h = np.where(mx == r, np.mod((g - b) / cs, 6.0),
                 np.where(mx == g, (b - r) / cs + 2.0, (r - g) / cs + 4.0)) * 60.0
    h = np.where(c > 1e-9, h, 0.0)
    s = np.where(mx > 1e-9, c / np.maximum(mx, 1e-9), 0.0)
    return h, s


def warm_coeffs(hue_deg, tilt=0.0):
    """Per-channel weights (R,G,B) of an offset with R-B = 1, HSV hue `hue_deg` and luma `tilt`.

    Solves kR-kB = 1, kG-kB = hue/60, 0.2126 kR + 0.7152 kG + 0.0722 kB = tilt.  Works on arrays
    (returns [..., 3])."""
    t = np.asarray(hue_deg, float) / 60.0  # (G-B)/(R-B) for an R>=G>=B colour
    kb = tilt - (LUMA[0] + LUMA[1] * t)
    return np.stack([kb + 1.0, kb + t, kb + 0.0 * t], axis=-1)


def warm_hue(x, P):
    xs = [a[0] for a in P["warm_hue_anchors"]]
    ys = [a[1] for a in P["warm_hue_anchors"]]
    return pchip(xs, ys, x)


def warm_amount(x, P):
    xs = [a[0] for a in P["warm_amount_anchors"]]
    ys = [a[1] for a in P["warm_amount_anchors"]]
    return pchip(xs, ys, x)


def gamut_excursion(rgb):
    """1.0 = on the RGB cube boundary along the line from (Y,Y,Y) through the colour."""
    Y = np.clip(rgb @ LUMA, 0.0, 1.0)
    C = rgb - Y[..., None]
    up = np.where(C > 0, C / np.maximum(1.0 - Y, 1e-6)[..., None], 0.0).max(-1)
    dn = np.where(C < 0, -C / np.maximum(Y, 1e-6)[..., None], 0.0).max(-1)
    return np.maximum(up, dn), Y, C


def soft_room(u, pw):
    """S(u) = u / (1 + u^p)^(1/p): ~u for small u, -> 1 for large u, smooth, increasing."""
    u = np.maximum(u, 0.0)
    return u / np.power(1.0 + np.power(u, pw), 1.0 / pw)


def skin_weight(rgb, P):
    hue, s = hsv_hue_sat(rgb)
    dh = np.abs(np.mod(hue - P["skin_hue_deg"] + 180.0, 360.0) - 180.0)
    a0, a1, b0, b1 = P["skin_sat_window"]
    return (np.exp(-0.5 * (dh / P["skin_hue_sigma_deg"]) ** 2)
            * smoothstep(a0, a1, s) * (1.0 - smoothstep(b0, b1, s)))


# ---------------------------------------------------------------- the look
def stage_contrast(rgb, P):
    mx = rgb.max(-1)
    fm = contrast_curve(mx, P["pivot"], P["contrast"])
    ratio = np.where(mx > 1e-9, fm / np.maximum(mx, 1e-9), 0.0)
    out = rgb * ratio[..., None]
    m = P.get("contrast_rgb_mix", 0.0)
    if m > 0:
        out = (1 - m) * out + m * contrast_curve(rgb, P["pivot"], P["contrast"])
    return out


def stage_saturation(rgb, P):
    """Returns the boosted pixel and the boost factor b (chroma scale = 1 + b, luma kept)."""
    Y = rgb @ LUMA
    C = rgb - Y[..., None]
    hue, s = hsv_hue_sat(rgb)
    w_hue = np.ones_like(Y)
    for center, sigma, amount in P.get("hue_protect", []):
        dh = np.abs(np.mod(hue - center + 180.0, 360.0) - 180.0)
        w_hue = w_hue * (1.0 - amount * np.exp(-0.5 * (dh / sigma) ** 2) * smoothstep(0.03, 0.10, s))
    for center, sigma, amount in P.get("hue_boost", []):
        dh = np.abs(np.mod(hue - center + 180.0, 360.0) - 180.0)
        w_hue = w_hue * (1.0 + amount * np.exp(-0.5 * (dh / sigma) ** 2) * smoothstep(0.03, 0.10, s))
    w_sh = smoothstep(P["sat_shadow_rolloff"][0], P["sat_shadow_rolloff"][1], Y)
    w_wh = 1.0 - smoothstep(P["sat_white_rolloff"][0], P["sat_white_rolloff"][1], Y)
    boost = ((P["sat"] - 1.0) * (1.0 - P["vibrance"] * s) * (1.0 - np.power(s, P["sat_limit_power"]))
             * w_sh * w_wh * w_hue * (1.0 - P["skin_protect"] * skin_weight(rgb, P)))
    return Y[..., None] + (1.0 + boost)[..., None] * C, boost


def stage_gamut(rgb_pre, boost, P):
    """Soft gamut compression of the boosted chroma toward luma (see header, step 3).

    Bottom of the cube (a channel going below 0): impossible by construction -- the boost fades as
    (1 - s^q) and stays below (1-s)/s for every HSV saturation s, so no channel can cross 0.
    Top of the cube (a channel going above 1): the boosted excursion toward the top face,
    e_top = (max - Y)/(1 - Y), is squeezed into the room that is left (smooth, never crosses 1).
    """
    Y = rgb_pre @ LUMA
    C = rgb_pre - Y[..., None]
    mx = rgb_pre.max(-1)
    e0 = np.clip((mx - Y) / np.maximum(1.0 - Y, 1e-9), 0.0, 1.0)
    room = np.maximum(1.0 - e0, 1e-12)
    b = np.maximum(boost, 0.0)
    u = e0 * b / room
    e_fin = e0 + room * soft_room(u, P["gamut_power"])
    scale = np.where(e0 > 1e-9, e_fin / np.maximum(e0, 1e-9), 1.0 + b)
    return Y[..., None] + C * scale[..., None]


def stage_warmth(rgb, P):
    # per-channel 1D curves: channel c at value x moves by k_c(hue(x)) * A(x)
    K = warm_coeffs(warm_hue(rgb, P), P["warm_luma_tilt"])     # [..., channel, (kR,kG,kB)]
    k_own = np.stack([K[..., 0, 0], K[..., 1, 1], K[..., 2, 2]], axis=-1)
    k_red = K[..., 0]                                          # red weight at each channel's value
    A = warm_amount(rgb, P)
    g = 1.0 - P.get("skin_warm_protect", 0.0) * skin_weight(rgb, P)
    A = A * g[..., None]
    eps = P["warm_pin_softness"]
    over = rgb - 1.0 + k_red * A                  # how far red would go past 1.0 at this value
    pin = eps * np.logaddexp(0.0, over / eps)     # smooth max(0, over)
    return rgb + k_own * A - pin


def apply_look(rgb, P, return_stages=False):
    x = np.clip(np.asarray(rgb, float), 0.0, 1.0)
    s1 = stage_contrast(x, P)
    s2, boost = stage_saturation(s1, P)
    s3 = stage_gamut(s1, boost, P)
    s4 = stage_warmth(s3, P)
    out = np.clip(s4, 0.0, 1.0)
    if return_stages:
        return out, {"contrast": s1, "saturation": s2, "gamut": s3, "warmth": s4}
    return out


def neutral_curve(x, P):
    """Neutral-axis result of the contrast stage alone (validate_lut.py uses it)."""
    return contrast_curve(x, P["pivot"], P["contrast"])


# ---------------------------------------------------------------- .cube writer
def lattice(n):
    v = np.linspace(0.0, 1.0, n)
    b, g, r = np.meshgrid(v, v, v, indexing="ij")     # last axis (red) varies fastest
    return np.stack([r.ravel(), g.ravel(), b.ravel()], axis=-1)


def write_cube(path, data, n, title, comments=()):
    data = np.clip(np.asarray(data, float), 0.0, 1.0) + 0.0   # +0.0 turns -0.0 into 0.0
    assert data.shape == (n ** 3, 3)
    with open(path, "w", newline="\n") as f:
        for c in comments:
            f.write("# %s\n" % c)
        f.write('TITLE "%s"\n' % title)
        f.write("LUT_3D_SIZE %d\n" % n)
        f.write("DOMAIN_MIN 0 0 0\n")
        f.write("DOMAIN_MAX 1 1 1\n")
        f.write("\n".join("%.6f %.6f %.6f" % (r, g, b) for r, g, b in data))
        f.write("\n")


def build(stem, strength, title, outdir, n=33, params=None):
    P = scaled_params(strength) if params is None else params
    grid = lattice(n)
    out, st = apply_look(grid, P, return_stages=True)
    clamped = int(np.sum((st["warmth"] < -1e-7) | (st["warmth"] > 1 + 1e-7)))
    e_pre, _, _ = gamut_excursion(st["saturation"])
    e_fin, _, _ = gamut_excursion(st["gamut"])
    comments = [
        "%s - display-referred Rec.709 Gamma 2.4 in/out, full data levels, no log/ACES/RCM" % title,
        "made by make_lut.py v%s %s, strength %.2f (see %s.params.json)"
        % (VERSION, datetime.date.today().isoformat(), strength, stem),
    ]
    path = os.path.join(outdir, stem + ".cube")
    write_cube(path, out, n, title, comments)
    meta = dict(P)
    meta.update({"name": stem, "title": title, "lut_size": n, "generator": "make_lut.py v" + VERSION,
                 "domain": "Rec.709 Gamma 2.4 display-referred, full data levels 0..1",
                 "operation_order": ["contrast(max-RGB norm)", "vibrance/saturation vs 709 luma",
                                     "soft gamut compression toward luma",
                                     "golden warmth (per-channel lift/gamma/gain-style offsets)",
                                     "safety clamp"],
                 "stats": {"values_touched_by_final_clamp": clamped,
                           "max_gamut_excursion_before_compression": float(e_pre.max()),
                           "max_gamut_excursion_after_compression": float(e_fin.max())}})
    with open(os.path.join(outdir, stem + ".params.json"), "w") as f:
        json.dump(meta, f, indent=2)
    return path, meta


def neutral_table(P, steps=(0, 0.03, 0.1, 0.18, 0.41, 0.5, 0.7, 0.9, 1.0)):
    x = np.array(steps, float)
    out = apply_look(np.stack([x, x, x], -1), P)
    rows = []
    for xi, o in zip(x, out):
        y = float(o @ LUMA)
        rb = o[0] - o[2]
        hue = hsv_hue_sat(o[None])[0][0] if rb > 1e-6 else float("nan")
        rows.append("  in %.3f -> R %.4f G %.4f B %.4f | R-B %+.4f hue %5.1f | luma %.4f (curve %.4f)"
                    % (xi, o[0], o[1], o[2], rb, hue, y, float(neutral_curve(xi, P))))
    return "\n".join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outdir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--size", type=int, default=33)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a MEDIUM parameter (JSON value), e.g. --set sat=1.25")
    ap.add_argument("--name", help="write ONE custom LUT with this stem instead of the standard set")
    ap.add_argument("--strength", type=float, default=1.0, help="strength for --name")
    ap.add_argument("--table", action="store_true", help="print neutral-axis tables")
    a = ap.parse_args(argv)

    base = json.loads(json.dumps(MEDIUM))
    for kv in a.set:
        k, v = kv.split("=", 1)
        if k not in base:
            sys.exit("unknown parameter %s" % k)
        base[k] = json.loads(v)
    os.makedirs(a.outdir, exist_ok=True)

    jobs = ([(a.name, a.strength, a.name)] if a.name else VARIANTS)
    for stem, strength, title in jobs:
        P = scaled_params(strength, base)
        path, meta = build(stem, strength, title, a.outdir, a.size, P)
        print("wrote %s  (strength %.2f, contrast %.3f, sat %.3f, clamp-touched %d, max e %.3f)"
              % (path, strength, P["contrast"], P["sat"], meta["stats"]["values_touched_by_final_clamp"],
                 meta["stats"]["max_gamut_excursion_before_compression"]))
        if a.table:
            print(neutral_table(P))
    if not a.name:
        grid = lattice(a.size)
        ipath = os.path.join(a.outdir, "identity_%d.cube" % a.size)
        write_cube(ipath, grid, a.size, "Identity %d" % a.size,
                   ["identity LUT for pipeline round-trip tests (made by make_lut.py)"])
        print("wrote %s" % ipath)


if __name__ == "__main__":
    main()
