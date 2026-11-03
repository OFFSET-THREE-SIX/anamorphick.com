#!/usr/bin/env bash
# preview.sh -- desqueeze an anamorphic clip, grade it with a 3D LUT, and build a small review kit.
#
#   preview.sh <input_clip> [squeeze=1.5] [lut=<kit>/golden_709.cube] [outdir=./preview_<clipname>]
#
#   Arguments are positional (clip squeeze lut outdir) or key=value in any order, e.g.
#       ./preview.sh /Volumes/SD/DCIM/100_FUJI/DSCF0001.MOV
#       ./preview.sh clip.mov 1.33
#       ./preview.sh clip.mov squeeze=1.5 lut=golden_709_soft.cube outdir=~/Desktop/review
#   Environment: PREVIEW_WIDTH (1280)  PREVIEW_SECONDS (20)  PREVIEW_FONT (label font file)
#                FFMPEG / FFPROBE (binaries)  KEEP_WORK=1 (keep the .work folder)
#
# Writes into outdir:
#   info.txt                  ffprobe summary, all tags, geometry, colour pipeline, self-tests,
#                             log/flat-footage heuristic
#   preview_graded.mp4        desqueezed + graded, H.264 CRF 20 yuv420p, first <= 20 s, +faststart
#   preview_before_after.mp4  ungraded (top) over graded (bottom)
#   before_15|50|85.png, after_15|50|85.png, compare_15|50|85.jpg (before above after)
#   contact_sheet.jpg         9 graded frames, 3x3
#   ffmpeg_log.txt            ffmpeg warnings/errors
#
# Colour handling: the decoded YUV (matrix + range taken from the file; limited/BT.709 assumed when
# unflagged) is converted ONCE to 16-bit full-range RGB, the LUT is applied there (tetrahedral), and
# the result is re-encoded to BT.709 limited-range yuv420p and tagged bt709/bt709/bt709/tv.
# The "before" image takes the identical path minus the LUT, so before/after differ only by the LUT.
#
# Portability: bash 3.2 (macOS) to 5.x, BSD or GNU userland (no readlink -f, sed -i, date -d,
# GNU-only flags; arithmetic in awk). Needs ffmpeg/ffprobe >= 4.x with lut3d (Homebrew's plain
# "ffmpeg" formula has no drawtext -> labels are skipped automatically).

set -eu
set -f            # no globbing: "-map 0:a:0?" and friends are passed through literally
LC_ALL=C
export LC_ALL

FFMPEG=${FFMPEG:-ffmpeg}
FFPROBE=${FFPROBE:-ffprobe}
PREVIEW_WIDTH=${PREVIEW_WIDTH:-1280}
PREVIEW_SECONDS=${PREVIEW_SECONDS:-20}

die() { echo "preview.sh: error: $*" >&2; exit 1; }
say() { echo "preview.sh: $*"; }
usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }

# ----------------------------------------------------------------------------- arguments
IN=""; SQUEEZE=""; LUT=""; OUTDIR=""; npos=0
for a in "$@"; do
  case "$a" in
    -h|--help) usage; exit 0 ;;
    squeeze=*) SQUEEZE=${a#squeeze=} ;;
    lut=*)     LUT=${a#lut=} ;;
    outdir=*)  OUTDIR=${a#outdir=} ;;
    *) npos=$((npos + 1))
       case $npos in
         1) IN=$a ;; 2) SQUEEZE=$a ;; 3) LUT=$a ;; 4) OUTDIR=$a ;;
         *) die "too many arguments (see --help)" ;;
       esac ;;
  esac
done
[ -n "$IN" ] || { usage; exit 1; }

KIT_DIR=$(cd "$(dirname "$0")" && pwd)
[ -n "$SQUEEZE" ] || SQUEEZE=1.5
[ -n "$LUT" ] || LUT="$KIT_DIR/golden_709.cube"
case "$OUTDIR" in \~/*) OUTDIR="$HOME/${OUTDIR#??}" ;; esac
case "$LUT" in \~/*) LUT="$HOME/${LUT#??}" ;; esac
case "$IN" in \~/*) IN="$HOME/${IN#??}" ;; esac
# a bare LUT file name is also looked up in the kit folder
if [ ! -f "$LUT" ] && [ -f "$KIT_DIR/$LUT" ]; then LUT="$KIT_DIR/$LUT"; fi

awk -v s="$SQUEEZE" 'BEGIN { exit !(s + 0 >= 0.5 && s + 0 <= 4) }' \
  || die "squeeze must be a number between 0.5 and 4 (got '$SQUEEZE')"
[ -f "$IN" ] || die "input clip not found: $IN"
[ -f "$LUT" ] || die "LUT not found: $LUT"
command -v "$FFMPEG" >/dev/null 2>&1 || die "ffmpeg not found (macOS: brew install ffmpeg)"
command -v "$FFPROBE" >/dev/null 2>&1 || die "ffprobe not found (it ships with ffmpeg)"

abspath() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *)  printf '%s/%s\n' "$(cd "$(dirname "$1")" && pwd)" "$(basename "$1")" ;;
  esac
}
IN=$(abspath "$IN")
LUT=$(abspath "$LUT")
# binaries given as relative paths must survive the cd into the work folder below
case "$FFMPEG" in */*) FFMPEG=$(abspath "$FFMPEG") ;; esac
case "$FFPROBE" in */*) FFPROBE=$(abspath "$FFPROBE") ;; esac
BASE=$(basename "$IN")
CLIPNAME=${BASE%.*}
[ -n "$OUTDIR" ] || OUTDIR="./preview_${CLIPNAME}"
mkdir -p "$OUTDIR" || die "cannot create $OUTDIR"
OUTDIR=$(cd "$OUTDIR" && pwd)
WORK="$OUTDIR/.work"
mkdir -p "$WORK"
LOG="$OUTDIR/ffmpeg_log.txt"
: > "$LOG"
INFO="$OUTDIR/info.txt"
WARN="$WORK/warnings.txt"
: > "$WARN"
warn() { echo "WARNING: $*" >> "$WARN"; echo "preview.sh: WARNING: $*" >&2; }

# ffmpeg runs inside $WORK so the LUT and font can be referenced by plain relative names
# (no filtergraph escaping problems with spaces, colons or quotes in user paths).
cp "$LUT" "$WORK/look.cube"
cd "$WORK"

ff() {
  echo "+ ffmpeg $*" >> "$LOG"
  "$FFMPEG" -hide_banner -nostdin -y -v error "$@" 2>> "$LOG" \
    || { echo "--- last ffmpeg messages:" >&2; tail -n 15 "$LOG" >&2; die "ffmpeg failed (full log: $LOG)"; }
}
calc() { awk "BEGIN { printf \"$1\", $2 }"; }

FILTERS=$("$FFMPEG" -hide_banner -filters 2>/dev/null || true)
ENCODERS=$("$FFMPEG" -hide_banner -encoders 2>/dev/null || true)
has_filter() { printf '%s\n' "$FILTERS" | grep -q " $1 "; }
has_encoder() { printf '%s\n' "$ENCODERS" | grep -q " $1 "; }
for f in lut3d scale format split vstack setsar tile blend signalstats metadata null; do
  has_filter "$f" || die "this ffmpeg build lacks the '$f' filter"
done
FFVER=$("$FFMPEG" -hide_banner -version 2>/dev/null | head -n 1)

# ----------------------------------------------------------------------------- probe
"$FFPROBE" -v error -select_streams v:0 -show_entries \
  stream=codec_name,codec_long_name,profile,codec_tag_string,width,height,coded_width,coded_height,sample_aspect_ratio,display_aspect_ratio,r_frame_rate,avg_frame_rate,pix_fmt,bits_per_raw_sample,color_range,color_space,color_transfer,color_primaries,chroma_location,field_order,nb_frames,duration,bit_rate \
  -of default=noprint_wrappers=1 "$IN" > probe_v.txt 2>> "$LOG" || true
"$FFPROBE" -v error -show_entries format=format_name,format_long_name,duration,size,bit_rate,nb_streams \
  -of default=noprint_wrappers=1 "$IN" > probe_f.txt 2>> "$LOG" || true
getv() { awk -F= -v k="$1" '$1 == k { print substr($0, length(k) + 2); exit }' probe_v.txt; }
getf() { awk -F= -v k="$1" '$1 == k { print substr($0, length(k) + 2); exit }' probe_f.txt; }

W=$(getv width); H=$(getv height)
[ -n "$W" ] && [ -n "$H" ] || die "no video stream found in $IN"
CODEC=$(getv codec_name); PROFILE=$(getv profile); PIX=$(getv pix_fmt)
SAR=$(getv sample_aspect_ratio); DAR=$(getv display_aspect_ratio)
RFR=$(getv r_frame_rate); AFR=$(getv avg_frame_rate)
CRANGE=$(getv color_range); CSPACE=$(getv color_space)
CTRC=$(getv color_transfer); CPRIM=$(getv color_primaries)
BPRS=$(getv bits_per_raw_sample); NBF=$(getv nb_frames); SDUR=$(getv duration)
FDUR=$(getf duration)

FPS=$(awk -v r="$AFR" -v q="$RFR" 'BEGIN {
  n = split(r, a, "/"); v = (n == 2 && a[2] + 0 > 0) ? a[1] / a[2] : r + 0
  if (v <= 0) { n = split(q, b, "/"); v = (n == 2 && b[2] + 0 > 0) ? b[1] / b[2] : q + 0 }
  printf "%.3f", v }')
DUR=$(awk -v f="$FDUR" -v s="$SDUR" -v n="$NBF" -v fps="$FPS" 'BEGIN {
  d = f + 0; if (d <= 0) d = s + 0; if (d <= 0 && n + 0 > 0 && fps + 0 > 0) d = n / fps
  printf "%.3f", d }')
if awk -v d="$DUR" 'BEGIN { exit !(d <= 0) }'; then
  warn "duration unknown - using the first frame only for stills"
  DUR=0
fi

case "$PIX" in
  *p10*|*10le|*10be|p010*|y210*|v210) BITDEPTH=10 ;;
  *p12*|*12le|*12be) BITDEPTH=12 ;;
  *p16*|*16le|*16be|rgb48*|bgr48*|rgba64*|bgra64*|p016*) BITDEPTH=16 ;;
  *) BITDEPTH=8 ;;
esac
case "$BPRS" in ''|N/A|0) ;; *) BITDEPTH=$BPRS ;; esac

IS_RGB=0
case "$PIX" in rgb*|bgr*|gbr*|argb|abgr|0rgb|0bgr|x2rgb*|x2bgr*) IS_RGB=1 ;; esac
case "$CRANGE" in
  pc|full|jpeg) IN_RANGE=pc ;;
  tv|limited|mpeg) IN_RANGE=tv ;;
  *) case "$PIX" in
       yuvj*) IN_RANGE=pc ;;
       *) IN_RANGE=tv; [ "$IS_RGB" = 1 ] || warn "colour range not flagged - assuming limited (video) range" ;;
     esac ;;
esac
[ "$IS_RGB" = 1 ] && IN_RANGE=pc
case "$CSPACE" in
  bt709) MATRIX=bt709 ;;
  smpte170m|bt470bg|bt601|bt470) MATRIX=bt601 ;;
  bt2020nc|bt2020c|bt2020) MATRIX=bt2020 ;;
  smpte240m) MATRIX=smpte240m ;;
  fcc) MATRIX=fcc ;;
  *) if [ "$H" -ge 720 ]; then MATRIX=bt709; else MATRIX=bt601; fi
     [ "$IS_RGB" = 1 ] || warn "YUV matrix not flagged - assuming $MATRIX" ;;
esac
case "$CTRC" in
  arib-std-b67) warn "transfer is HLG (arib-std-b67): this look expects Rec.709 Gamma 2.4 - convert HLG -> 709 first" ;;
  smpte2084)    warn "transfer is PQ (smpte2084, HDR): convert to Rec.709 Gamma 2.4 before this look" ;;
esac
case "$CPRIM" in bt2020) warn "primaries are BT.2020: the LUT expects Rec.709 primaries (colours will be off)" ;; esac
ROT=$("$FFPROBE" -v error -select_streams v:0 -show_entries stream_side_data=rotation \
      -of default=noprint_wrappers=1:nokey=1 "$IN" 2>/dev/null | head -n 1 || true)
case "$ROT" in ''|0|-0|N/A) ;; *) warn "rotation metadata ($ROT deg) ignored: frames are used as stored (-noautorotate)" ;; esac

# ----------------------------------------------------------------------------- geometry
SAR_N=$(printf '%s\n' "${SAR:-1:1}" | awk -F: '{ print ($1 + 0 > 0 && $2 + 0 > 0) ? $1 : 1 }')
SAR_D=$(printf '%s\n' "${SAR:-1:1}" | awk -F: '{ print ($1 + 0 > 0 && $2 + 0 > 0) ? $2 : 1 }')
DISP_AR=$(awk -v w="$W" -v h="$H" -v n="$SAR_N" -v d="$SAR_D" -v s="$SQUEEZE" \
  'BEGIN { printf "%.6f", (w * n / d * s) / h }')
OUT_W=$PREVIEW_WIDTH
OUT_H=$(awk -v ar="$DISP_AR" -v w="$OUT_W" 'BEGIN { h = int(w / ar / 2 + 0.5) * 2; if (h < 2) h = 2; print h }')
DISP_W=$(awk -v w="$W" -v n="$SAR_N" -v d="$SAR_D" -v s="$SQUEEZE" 'BEGIN { printf "%.1f", w * n / d * s }')

# ----------------------------------------------------------------------------- filter chains
SWS="flags=lanczos+accurate_rnd+full_chroma_int"
SRC="in_color_matrix=${MATRIX}:in_range=${IN_RANGE}"
TO_RGB16="scale=w=${OUT_W}:h=${OUT_H}:${SWS}:${SRC},format=gbrp16le,setsar=1"
GRADE="lut3d=file=look.cube:interp=tetrahedral"
TO_YUV709="scale=${SWS}:out_color_matrix=bt709:out_range=tv,format=yuv420p"
TO_PNG="scale=flags=accurate_rnd+full_chroma_int,format=rgb24"
TAGS="-colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv"
if has_encoder libx264; then
  VENC="-c:v libx264 -preset medium -crf 20 -profile:v high -pix_fmt yuv420p"
elif has_encoder h264_videotoolbox; then
  VENC="-c:v h264_videotoolbox -b:v 12M -pix_fmt yuv420p"; warn "libx264 missing - using h264_videotoolbox at 12 Mb/s instead of CRF 20"
else
  die "no H.264 encoder (libx264) in this ffmpeg build"
fi
HAS_AUDIO=$("$FFPROBE" -v error -select_streams a:0 -show_entries stream=codec_type -of csv=p=0 "$IN" 2>/dev/null | head -n 1 || true)
if [ -n "$HAS_AUDIO" ] && has_encoder aac; then AMAP="-map 0:a:0? -c:a aac -b:a 160k"; else AMAP="-an"; fi

# labels (drawtext needs libfreetype; Homebrew's plain ffmpeg formula does not have it)
FONT=""
LBL_B=""; LBL_A=""
if has_filter drawtext; then
  for f in "${PREVIEW_FONT:-}" \
           /System/Library/Fonts/Supplemental/Arial.ttf /Library/Fonts/Arial.ttf \
           /System/Library/Fonts/Supplemental/Verdana.ttf /System/Library/Fonts/Helvetica.ttc \
           /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf /usr/share/fonts/dejavu/DejaVuSans.ttf \
           /usr/share/fonts/TTF/DejaVuSans.ttf /usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf \
           /c/Windows/Fonts/arial.ttf C:/Windows/Fonts/arial.ttf "$KIT_DIR/fonts/label.ttf"; do
    if [ -n "$f" ] && [ -f "$f" ]; then FONT=$f; break; fi
  done
fi
if [ -n "$FONT" ]; then
  FEXT=${FONT##*.}
  cp "$FONT" "label.$FEXT"
  FS=$(awk -v h="$OUT_H" 'BEGIN { s = int(h / 20); if (s < 12) s = 12; print s }')
  LUT_TXT=$(printf '%s' "$(basename "$LUT")" | tr -c 'A-Za-z0-9._-' '_')
  SQ_TXT=$(printf '%s' "$SQUEEZE" | tr -c '0-9.' '_')
  DT="fontfile=label.$FEXT:x=12:y=10:fontsize=${FS}:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=6"
  LBL_B=",drawtext=${DT}:text='BEFORE  ungraded  desqueezed ${SQ_TXT}x'"
  LBL_A=",drawtext=${DT}:text='AFTER  ${LUT_TXT}'"
  LABELS="yes ($(basename "$FONT"))"
else
  if has_filter drawtext; then LABELS="no (no font file found; set PREVIEW_FONT=/path/font.ttf)"
  else LABELS="no (this ffmpeg has no drawtext filter; e.g. brew install ffmpeg-full)"; fi
fi

say "input   $IN"
say "probe   ${CODEC} ${W}x${H} SAR ${SAR:-?} ${FPS} fps ${PIX} (${BITDEPTH}-bit) range=${CRANGE:-?} matrix=${CSPACE:-?} dur ${DUR}s"
say "output  ${OUT_W}x${OUT_H} (desqueeze ${SQUEEZE}x -> display ${DISP_W}x${H}, aspect ${DISP_AR}:1) -> $OUTDIR"

# ----------------------------------------------------------------------------- self-tests
# identity LUT generated here (independent of the kit files)
awk -v n=33 'BEGIN { print "TITLE \"identity\""; print "LUT_3D_SIZE 33"; print "DOMAIN_MIN 0 0 0"; print "DOMAIN_MAX 1 1 1"
  for (b = 0; b < n; b++) for (g = 0; g < n; g++) for (r = 0; r < n; r++)
    printf "%.6f %.6f %.6f\n", r / (n - 1), g / (n - 1), b / (n - 1) }' > identity.cube
mae_report() {   # $1 = metadata file from blend=difference,signalstats  ->  "Y U V all max"
  awk -F= '/signalstats.YAVG=/ { y += $2; n++ } /signalstats.UAVG=/ { u += $2 } /signalstats.VAVG=/ { v += $2 }
       END { if (n == 0) { print "NA NA NA NA NA"; exit }
             y /= n; u /= n; v /= n; a = (4 * y + u + v) / 6
             m = y; if (u > m) m = u; if (v > m) m = v
             printf "%.3f %.3f %.3f %.3f %.3f\n", y, u, v, a, m }' "$1"
}
st_verdict() { printf '%s\n' "$1" | awk '{ if ($5 == "NA") print "NOT RUN"; else if ($5 + 0 < 1.0) print "PASS"; else print "FAIL" }'; }
DIFF_STATS="blend=all_mode=difference,signalstats,metadata=mode=print"
T_MID=$(calc "%.3f" "$DUR * 0.5")
# (1) this clip: decode -> 16-bit RGB -> identity LUT -> same matrix/range YUV, vs. the plain decode
ff -noautorotate -ss "$T_MID" -i "$IN" -frames:v 3 -filter_complex \
  "[0:v]split=2[a][b];[a]scale=${SWS}:${SRC}:out_color_matrix=${MATRIX}:out_range=${IN_RANGE},format=yuv420p[ref];[b]scale=${SWS}:${SRC},format=gbrp16le,lut3d=file=identity.cube:interp=tetrahedral,scale=${SWS}:out_color_matrix=${MATRIX}:out_range=${IN_RANGE},format=yuv420p[rt];[rt][ref]${DIFF_STATS}:file=selftest_clip.txt" \
  -f null -
ST_CLIP=$(mae_report selftest_clip.txt)
# (2) the exact output path on a synthetic chart (testsrc2 rendered in RGB, encoded to BT.709 limited
#     4:2:0 first so every YUV sample is legal): YUV -> 16-bit RGB -> identity LUT -> BT.709 limited
ff -f lavfi -i "testsrc2=size=1280x720:rate=24:duration=0.125" -filter_complex \
  "[0:v]format=rgb24,${TO_YUV709},split=2[a][b];[b]scale=${SWS}:in_color_matrix=bt709:in_range=tv,format=gbrp16le,lut3d=file=identity.cube:interp=tetrahedral,${TO_YUV709}[rt];[rt][a]${DIFF_STATS}:file=selftest_chart.txt" \
  -f null -
ST_BARS=$(mae_report selftest_chart.txt)
V_CLIP=$(st_verdict "$ST_CLIP"); V_BARS=$(st_verdict "$ST_BARS")
[ "$V_CLIP" = PASS ] || warn "identity round trip on this clip: mean abs error $ST_CLIP (8-bit codes). Either colour handling is off or the clip holds super-white / sub-black / out-of-gamut YUV values, which any RGB pipeline clips"
[ "$V_BARS" = PASS ] || warn "identity round trip on the synthetic chart: mean abs error $ST_BARS (8-bit codes) - colour handling of this ffmpeg build is suspect"

# ----------------------------------------------------------------------------- log / flat heuristic
: > sigstats.txt
for p in 10 30 50 70 90; do
  t=$(calc "%.3f" "$DUR * $p / 100")
  ff -noautorotate -ss "$t" -i "$IN" -frames:v 1 \
    -vf "scale=w=480:h=-2:flags=area+accurate_rnd:${SRC}:out_color_matrix=bt709:out_range=tv,format=yuv420p,signalstats,metadata=mode=print:file=sig_$p.txt" \
    -f null -
  awk -F= -v p="$p" '
    /signalstats.YMIN=/ { ymin = $2 } /signalstats.YLOW=/ { ylow = $2 } /signalstats.YAVG=/ { yavg = $2 }
    /signalstats.YHIGH=/ { yhigh = $2 } /signalstats.YMAX=/ { ymax = $2 } /signalstats.SATAVG=/ { sat = $2 }
    END { if (ymax != "") printf "%s %s %s %s %s %s %s\n", p, ymin, ylow, yavg, yhigh, ymax, sat }' "sig_$p.txt" >> sigstats.txt
done
HEUR=$(awk '
  { n++; p[n] = $1; ymin[n] = $2; ylow[n] = $3; yavg[n] = $4; yhigh[n] = $5; ymax[n] = $6; sat[n] = $7 }
  function med(a, k,   i, j, t, b) { for (i = 1; i <= k; i++) b[i] = a[i]
      for (i = 1; i <= k; i++) for (j = i + 1; j <= k; j++) if (b[j] < b[i]) { t = b[i]; b[i] = b[j]; b[j] = t }
      return (k % 2) ? b[(k + 1) / 2] : (b[k / 2] + b[k / 2 + 1]) / 2 }
  END {
    if (n == 0) { print "VERDICT: not measured (no frames decoded)"; exit }
    printf "  sample  YMIN  YLOW(10%%)  YAVG  YHIGH(90%%)  YMAX  SATAVG   (8-bit, limited range: black 16, white 235)\n"
    mn = 999; mx = -1; ss = 0
    for (i = 1; i <= n; i++) {
      printf "  %3d%%   %4d  %6d     %6.1f  %6d     %4d  %6.1f\n", p[i], ymin[i], ylow[i], yavg[i], yhigh[i], ymax[i], sat[i]
      if (ymin[i] < mn) mn = ymin[i]; if (ymax[i] > mx) mx = ymax[i]; ss += sat[i]; spread[i] = yhigh[i] - ylow[i] }
    ml = med(ylow, n); mh = med(yhigh, n); msp = med(spread, n); ms = ss / n
    printf "  summary: darkest YMIN %d, brightest YMAX %d, median YLOW %.0f, median YHIGH %.0f, median spread %.0f, mean SATAVG %.1f\n", mn, mx, ml, mh, msp, ms
    # core log/flat signature = lifted blacks; the other four only count together with it
    lift = (mn >= 26); sc = 0; why = ""
    if (lift)      why = why "blacks never reach legal black (darkest code " mn " >= 26); "
    if (ml >= 36)  { sc++; why = why "10th-percentile luma is high (" ml " >= 36); " }
    if (mx <= 215) { sc++; why = why "nothing reaches near white (brightest " mx " <= 215); " }
    if (msp <= 110){ sc++; why = why "low tonal spread (YHIGH-YLOW " msp " <= 110); " }
    if (ms <= 12)  { sc++; why = why "low saturation (SATAVG " ms " <= 12; also true for B&W); " }
    printf "  lifted blacks: %s; other flat indicators %d/4: %s\n", (lift ? "YES" : "no"), sc, (why == "" ? "none" : why)
    if (mx > 235 || mn < 16) printf "  note: values outside 16-235 present (min %d, max %d): super-white/sub-black or scaler overshoot\n", mn, mx
    if (lift && sc >= 2)
      print "VERDICT: LOOKS LIKE F-Log/F-Log2/flat - it needs a log->709 transform (Resolve CST F-Log/F-Log2 F-Gamut -> Rec.709 Gamma 2.4, or the camera LUT) BEFORE this look; the preview below is NOT representative."
    else if ((lift && sc == 1) || (!lift && sc >= 3))
      print "VERDICT: possibly flat/low-contrast (log, haze, or a flat picture profile) - check before grading."
    else
      print "VERDICT: looks like display-referred Rec.709 - OK to apply the look directly." }' sigstats.txt)
case "$HEUR" in *"LOOKS LIKE F-Log"*) warn "footage looks LOG/FLAT - apply a log->Rec.709 transform before the look" ;; esac
TAGLOG=$("$FFPROBE" -v error -show_entries format_tags:stream_tags -of default=noprint_wrappers=1 "$IN" 2>/dev/null \
         | grep -i -E 'log|hlg|gamma|profile|film|simulation' || true)

# ----------------------------------------------------------------------------- info.txt
write_info() {
{
  echo "preview.sh report - $(date '+%Y-%m-%d %H:%M:%S')"
  echo "input:        $IN"
  echo "size bytes:   $(wc -c < "$IN" | tr -d ' ')"
  echo "ffmpeg:       $FFVER"
  echo
  echo "== ffprobe summary =="
  echo "container:    $(getf format_name)  ($(getf format_long_name))  bit_rate $(getf bit_rate)"
  echo "video codec:  ${CODEC} ($(getv codec_long_name)) profile ${PROFILE:-?} tag $(getv codec_tag_string)"
  echo "frame size:   ${W}x${H} (coded $(getv coded_width)x$(getv coded_height))"
  echo "SAR / DAR:    ${SAR:-N/A} / ${DAR:-N/A}"
  echo "frame rate:   r=${RFR} avg=${AFR} (${FPS} fps), field_order $(getv field_order)"
  echo "duration:     ${DUR} s (format $(getf duration), stream ${SDUR:-N/A}, frames ${NBF:-N/A})"
  echo "pixel format: ${PIX}, bit depth ${BITDEPTH} (bits_per_raw_sample ${BPRS:-N/A}), chroma_location $(getv chroma_location)"
  echo "colour:       primaries=${CPRIM:-unknown} transfer=${CTRC:-unknown} space=${CSPACE:-unknown} range=${CRANGE:-unknown}"
  echo "audio:        ${HAS_AUDIO:-none}"
  echo
  echo "== tags (format) =="
  "$FFPROBE" -v error -show_entries format_tags -of default=noprint_wrappers=1 "$IN" 2>/dev/null || true
  echo
  echo "== tags (streams) =="
  "$FFPROBE" -v error -show_entries stream=index,codec_type:stream_tags -of default=noprint_wrappers=1 "$IN" 2>/dev/null || true
  echo
  echo "== geometry =="
  echo "squeeze:      ${SQUEEZE}x horizontal, stored SAR ${SAR_N}:${SAR_D}"
  echo "display:      ${DISP_W}x${H}  aspect ${DISP_AR}:1"
  echo "preview:      ${OUT_W}x${OUT_H}, square pixels (setsar=1)"
  echo
  echo "== colour pipeline =="
  echo "decode:       YUV matrix ${MATRIX}, range ${IN_RANGE} -> 16-bit full-range RGB (swscale ${SWS})"
  echo "grade:        lut3d tetrahedral, LUT $LUT ($(wc -c < "$LUT" | tr -d ' ') bytes)"
  echo "encode:       BT.709 limited-range yuv420p, tagged bt709/bt709/bt709/tv; video: $VENC"
  echo "labels:       $LABELS"
  echo "self-test 1:  identity LUT round trip on this clip (3 frames @ ${T_MID}s, native size)"
  echo "              mean abs error Y/U/V/all-samples/worst-plane = $ST_CLIP (8-bit codes) -> $V_CLIP (limit 1.0)"
  echo "              (super-white/sub-black or out-of-gamut YUV in the clip also raise it: an RGB pipeline clips them)"
  echo "self-test 2:  identity LUT round trip, synthetic BT.709 limited chart through the exact output path"
  echo "              mean abs error Y/U/V/all-samples/worst-plane = $ST_BARS (8-bit codes) -> $V_BARS (limit 1.0)"
  echo
  echo "== log / flat-footage heuristic (signalstats on 5 frames) =="
  echo "$HEUR"
  [ -n "$TAGLOG" ] && { echo "  tags mentioning log/hlg/gamma/profile/film:"; printf '%s\n' "$TAGLOG" | sed 's/^/    /'; }
  echo
  echo "== warnings =="
  if [ -s "$WARN" ]; then cat "$WARN"; else echo "none"; fi
  echo
  echo "== outputs =="
  for f in preview_graded.mp4 preview_before_after.mp4 before_15.png after_15.png compare_15.jpg \
           before_50.png after_50.png compare_50.jpg before_85.png after_85.png compare_85.jpg contact_sheet.jpg; do
    if [ -f "$OUTDIR/$f" ]; then echo "  $f  ($(wc -c < "$OUTDIR/$f" | tr -d ' ') bytes)"; else echo "  $f  MISSING"; fi
  done
  echo
  echo "== full ffprobe dump =="
  "$FFPROBE" -v error -show_format -show_streams "$IN" 2>/dev/null || true
} > "$INFO"
}

write_info   # written now so it exists even if a render below fails; rewritten at the end

# ----------------------------------------------------------------------------- renders
say "rendering preview_graded.mp4"
ff -noautorotate -i "$IN" -t "$PREVIEW_SECONDS" -map 0:v:0 $AMAP \
  -vf "${TO_RGB16},${GRADE},${TO_YUV709}" $VENC $TAGS -movflags +faststart "$OUTDIR/preview_graded.mp4"
say "rendering preview_before_after.mp4"
ff -noautorotate -i "$IN" -t "$PREVIEW_SECONDS" -filter_complex \
  "[0:v]${TO_RGB16},split=2[a][b];[a]${TO_YUV709}${LBL_B}[top];[b]${GRADE},${TO_YUV709}${LBL_A}[bot];[top][bot]vstack=inputs=2[v]" \
  -map "[v]" $AMAP $VENC $TAGS -movflags +faststart "$OUTDIR/preview_before_after.mp4"

say "stills + compares"
for p in 15 50 85; do
  t=$(calc "%.3f" "$DUR * $p / 100")
  ff -noautorotate -ss "$t" -i "$IN" -frames:v 1 -update 1 -vf "${TO_RGB16},${TO_PNG}" "$OUTDIR/before_$p.png"
  ff -noautorotate -ss "$t" -i "$IN" -frames:v 1 -update 1 -vf "${TO_RGB16},${GRADE},${TO_PNG}" "$OUTDIR/after_$p.png"
  ff -i "$OUTDIR/before_$p.png" -i "$OUTDIR/after_$p.png" -filter_complex \
    "[0:v]null${LBL_B}[t];[1:v]null${LBL_A}[b];[t][b]vstack=inputs=2[v]" -map "[v]" -frames:v 1 -update 1 -q:v 2 "$OUTDIR/compare_$p.jpg"
done

say "contact sheet"
i=1
while [ $i -le 9 ]; do
  t=$(calc "%.3f" "$DUR * ($i - 0.5) / 9")
  ff -noautorotate -ss "$t" -i "$IN" -frames:v 1 -update 1 -vf "${TO_RGB16},${GRADE},${TO_PNG}" "$(printf 'cs_%02d.png' $i)"
  i=$((i + 1))
done
ff -i cs_%02d.png -vf "scale=w=640:h=-2:flags=lanczos,tile=3x3:padding=6:margin=6" -frames:v 1 -update 1 -q:v 2 "$OUTDIR/contact_sheet.jpg"

write_info
cd "$OUTDIR"
[ "${KEEP_WORK:-0}" = 1 ] || rm -rf "${WORK:?}"
say "self-tests (identity LUT, mean abs error Y U V all worst): clip $V_CLIP ($ST_CLIP), chart $V_BARS ($ST_BARS)"
printf '%s\n' "$HEUR" | grep VERDICT | sed 's/^/preview.sh: /'
say "done -> $OUTDIR"
