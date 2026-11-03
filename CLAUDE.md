# Anamorphick: context for Claude sessions

## Video work
- Noah edits and grades in **DaVinci Resolve Studio** (version not recorded yet). Terminal scripting needs Preferences > System > General > External scripting using: Local.
- Lens: **1.5x Nanomorph** anamorphic. A 16:9 recording desqueezes to 2.67:1; 3:2 open gate to 2.25:1.
- Default look: Rec.709 Gamma 2.4 (no log or ACES transform), a light golden warmth, more contrast, more color intensity. That is `tools/resolve-kit/golden_709.cube`; `golden_709_soft.cube` and `golden_709_strong.cube` are the lighter and heavier versions.
- Footage lives on the camera's SD card or in ~/Downloads on Noah's MacBook. Cloud sessions cannot reach it, and on 2026-11-03 the Mac's Remote Control refused cloud sessions for every folder. Do video work in a Claude Code session running on the Mac.
- `tools/resolve-kit/anamorphick_resolve_setup.py --clip <file> --render-preview` builds a new Resolve project (1.5x desqueeze, timeline sized to the desqueezed frame, golden LUT on node 1) and renders a small H.264 preview. `tools/resolve-kit/preview.sh <file>` makes an ffmpeg before/after without Resolve. See `tools/resolve-kit/README.md`.
