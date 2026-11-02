# Project Notes (Keegan's Pronterface customization)

Running notes so any new Claude chat can pick up where we left off.

## Setup
- Fork: github.com/505isfound/printrun (forked from kliment/Printrun)
- Target platform: **Windows**
- Workflow: Claude edits and pushes to GitHub → GitHub Actions ("Build Windows Executable") builds Pronterface.exe → Keegan downloads it from the Actions tab and tests with the printer. GitHub Desktop "Fetch origin"/"Pull" keeps the local copy in sync.
- Printer: older, USB-only
- Testing on Claude's side: wxPython via apt (`python3-wxgtk4.0`, works with python3.12 only) + `pyglet<2`, run under `xvfb-run`. Pronterface redirects stdout into its log, so test scripts must write results to a file.

## Done (2026-10-31)
- **Closing never touches the heaters.** Closing asks first if a print is running (answering No now actually keeps the print going — before, heaters were turned off and the printer disconnected *before* the question). New setting "Turn heaters off when closing" (Settings → Printer), off by default.
- **Preheat buttons:** PLA / PETG / ABS set nozzle + bed together. PETG preset added (235/75). PLA default raised 185 → 200. All editable in Settings → Printer.
- **Quick-action buttons** (row under Preheat):
  - Stop print – pauses only; no heaters/motors/pause script touched. Toolbar button becomes Resume.
  - Heaters off, Motors off – ask first if a print is running.
  - Clear file – unloads the G-code (not allowed mid-print).
  - Clear all – cancels the print, heaters + fan + motors off, unloads file.
- **Big status panel** under the toolbar: nozzle/bed temps, progress bar, %, time left, height. Toggle: Settings → UI → "Display big status panel".
- **Dark mode:** Settings → Colors → Theme (Light/Dark). Switching rewrites the color settings, which can still be fine-tuned.
- **Fixed original bug:** saved color settings caused the UI to be built twice at startup, spamming "Gviz has been deleted" errors in the log.

## Known / notes
- `tests/test_printcore.py` socket tests fail on Python 3.12 here — same on the original code, not related to our changes.
- On Windows, some native controls (buttons, dropdown arrows) may stay light in dark mode; wxPython's Windows dark-mode support is limited.
- The toolbar's original "Off" button also sends M81 (power supply off). Left as-is.

## Direction (decided 2026-11-01)
- **Rebuilding Pronterface with a web-based interface** ("Pronterface Web", working name), iOS-like look.
  Runs locally; LAN access opt-in with PIN later. No public internet exposure planned.
- Goals in Keegan's words: keep as much of the original as possible, no new bugs, no lost
  functionality, no lost printer compatibility. Webcam support wanted.
- Engine = existing `pronsole` + `printcore` + `gcoder`, unchanged. New code goes in `web/`.
- Full plan + "nothing lost" list: **FEATURE_CHECKLIST.md** (89 items, Phases 0-8).
- Approved: redesigned temperature graph (mockup: thick actual line, dashed target, nozzle blue /
  bed orange, readouts on top; expanded view with 1/5/15 min, time labels, separate fan strip;
  drop the overlapping shaded fills).
- Testing rule: test G-code uses the printer's firmware settings, nothing overridden.

## Open questions / next steps
- Keegan to test the Windows build of the classic-Pronterface changes with the real printer.
- Decisions in FEATURE_CHECKLIST.md: niche Phase 8 tools, which printer(s) to test on, app name.
- Next: start Phase 0 (foundation).
