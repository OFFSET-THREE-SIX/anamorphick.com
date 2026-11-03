# Anamorphick Resolve kit

`anamorphick_resolve_setup.py` (Python 3.6+, stdlib only) builds a **new** DaVinci Resolve project from your clip(s):

- color science DaVinci YRGB (not color managed); timeline and output color space Rec.709 Gamma 2.4
- timeline frame rate taken from the clip; timeline sized to the desqueezed frame (3840x2160 at 1.5x gives 3840x1440)
- Pixel Aspect Ratio 1.5 on every clip. If Resolve refuses it, the fallback is timeline "Stretch frame to all corners", then Inspector Zoom X/Y. The fallback is announced loudly.
- `golden_709.cube` copied into Resolve's LUT folder under `Anamorphick/` (a different file with the same name gets `_v2`), then applied to node 1 of every clip. The script finishes on the Color page.
- `--render-preview`: a 1280-wide H.264 `.mp4` in `~/Movies/Anamorphick Previews` (macOS) or `%USERPROFILE%\Videos\Anamorphick Previews` (Windows)
- it ends with a SUMMARY table of the values Resolve reports back, plus manual fixes for anything that failed

It never loads, changes or deletes an existing project. A taken name gets " (2)", " (3)" and so on. If a *named* project is open, the script saves it before switching (`--no-save-current` turns that off). Add `--dry-run` to print the plan only.

## macOS

1. Copy this folder to the Mac (e.g. `~/Downloads/resolve-kit`), insert the SD card, open Resolve and wait for the Project Manager.
2. **Resolve Studio** in Terminal. First set Preferences > System > General > *External scripting using* = Local.
   ```
   python3 ~/Downloads/resolve-kit/anamorphick_resolve_setup.py --folder "/Volumes/CARD/DCIM" --render-preview
   ```
   On 21.1+ you can replace `python3` with Resolve's own `"/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolvePython"`.
   **Free Resolve** blocks scripts run from Terminal. Use Workspace > Console, click **Py3**, and paste one line (fix both paths):
   ```
   import sys; sys.argv=["x","--folder","/Volumes/CARD/DCIM","--render-preview"]; p="/Users/YOU/Downloads/resolve-kit/anamorphick_resolve_setup.py"; exec(open(p,encoding="utf-8").read(),{"__name__":"__main__","__file__":p,"resolve":globals().get("resolve"),"bmd":globals().get("bmd")})
   ```
   A failed Terminal run prints this line with your paths already filled in.
3. Read the SUMMARY and check `~/Movies/Anamorphick Previews`.

## Windows

1. Copy the folder (e.g. `%USERPROFILE%\Downloads\resolve-kit`), insert the card, open Resolve.
2. **Studio** (External scripting = Local): `py -3 "%USERPROFILE%\Downloads\resolve-kit\anamorphick_resolve_setup.py" --folder E:\DCIM --render-preview`. On 21.1+ you can use `"C:\Program Files\Blackmagic Design\DaVinci Resolve\ResolvePython\ResolvePython.exe"` instead of `py -3`.
   **Free**: Workspace > Console > Py3, paste:
   ```
   import sys; sys.argv=["x","--folder",r"E:\DCIM","--render-preview"]; p=r"C:\Users\YOU\Downloads\resolve-kit\anamorphick_resolve_setup.py"; exec(open(p,encoding="utf-8").read(),{"__name__":"__main__","__file__":p,"resolve":globals().get("resolve"),"bmd":globals().get("bmd")})
   ```
3. Read the SUMMARY and check `%USERPROFILE%\Videos\Anamorphick Previews`.

**Workspace > Scripts route (no typing).** Copy the `.py` and `golden_709.cube` into `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility` (macOS) or `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility` (Windows). Create the folder if needed. Then restart Resolve and run Workspace > Scripts > anamorphick_resolve_setup. With `CONFIG` left empty, the script uses the one mounted card's `DCIM`. Output appears in Workspace > Console.

**Free vs Studio.** Studio supports all three routes. Free up to 21.0.x supports only the Console and the Scripts menu. Before 21.1, those need a 64-bit Python 3.6+ installed for all users. Community reports say free 21.1 moved Python scripting to Studio: `.py` files are no longer listed under Scripts, and whether the Console still runs Python is unconfirmed. In that case, do the manual fixes the SUMMARY prints, or use `preview.sh`.

**Check the desqueeze.** Blackmagic does not document the PAR string for 1.5x. The script tries `'1.5'`, `'1.50'`, ... and reads each one back. Circles must look round in the preview. If they don't, re-run with `--desqueeze-method stretch`.

Other flags: `--clip A.MOV B.MOV`, `--squeeze 1.33`, `--lut golden_709_soft.cube`, `--project NAME`, `--timeline-width 2880`, `--out DIR`, `--lut-dir DIR`, `--render-timeout SEC`.

## preview.sh (no Resolve needed; bash + ffmpeg/ffprobe, e.g. `brew install ffmpeg`)

```
./preview.sh /Volumes/CARD/DCIM/100_FUJI/DSCF0001.MOV                     # 1.5x, golden_709.cube
./preview.sh clip.mov squeeze=1.5 lut=golden_709_soft.cube outdir=~/Desktop/review
```
It writes `preview_graded.mp4` (1280 wide, first 20 s; set PREVIEW_WIDTH / PREVIEW_SECONDS to change), `preview_before_after.mp4`, before/after stills at 15/50/85 %, `contact_sheet.jpg` and `info.txt`. On Windows, run it from Git Bash or WSL.

## LUT strengths

All are 33-point `.cube` files: Rec.709 Gamma 2.4 in and out, display-referred, full data levels, with no log/ACES/RCM transform. The warm offsets scale with strength.

| file | strength | contrast | saturation |
|---|---|---|---|
| `golden_709_soft.cube` | 0.6 | 1.108 | 1.132 |
| `golden_709.cube` (default) | 1.0 | 1.18 | 1.22 |
| `golden_709_strong.cube` | 1.5 | 1.27 | 1.33 |
| `identity_33.cube` | test only, no change | | |
