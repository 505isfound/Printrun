# Project Layerline: Feature Checklist & Build Plan

The "nothing lost" list for rebuilding Pronterface with a modern web-based interface.
Every feature of the current Pronterface is listed here. A phase isn't done until every
item in it is checked off and tested on a real printer.

**Status boxes:** `[ ]` not started · `[~]` built and passing on the simulated printer, waiting for a real-printer test · `[x]` done (tested on the Ender 5 Pro)

**Legend**
- **Reused**: existing Pronterface code, used unchanged
- **Rebuilt**: same feature, new interface
- **New**: didn't exist before
- **Needs your call**: rarely used / niche, decide before that phase

---

## How it fits together

```
Printer ──USB── PC ┬─ Engine: Pronterface's own host code (pronsole + printcore + gcoder), unchanged
                   └─ Small local web server ── Interface (opens in its own app window, or a browser)
```

- **The engine is the existing Pronterface host (`pronsole`).** It runs without the old
  interface (verified: it starts with no screen at all) and already does connecting, temps,
  printing, SD card, macros, and the settings file. This is what keeps printer compatibility
  identical. It still borrows wxPython's settings classes; that's harmless and can be untangled later.
- **Same settings file.** Your settings, macros, and custom buttons carry over automatically.
- **Console from day one.** The command box runs the same commands as Pronterface's console,
  so every feature is reachable by typing a command even before its button exists.
- **Classic Pronterface stays installed** as a fallback the whole time.
- **Local network only.** By default only the PC itself can reach it. LAN access is an
  opt-in setting that requires a PIN (Phase 7).
- **Lives in the same GitHub fork** in a new `web/` folder, so engine fixes are shared and
  updates from the original Printrun project can still be merged in.

## Testing rules (apply to every phase)

- Test G-code uses the printer's firmware settings. Nothing gets overridden during testing
  (no steps/mm, acceleration, max-feedrate, or EEPROM commands like M92 / M201 / M203 / M500).
- Each feature is first tested against a **simulated printer** on Claude's side (automated),
  then by Keegan on the real printer before it's checked off.
- Every phase ships as a Windows .exe from GitHub's automatic build, same as now.

---

## Phase 0: Foundation

| | Feature | Status | How we test |
|---|---|---|---|
| [x] | App window: double-click .exe, opens like a normal app | New | Launches on Windows |
| [x] | Engine runs Pronterface's host code (`pronsole`/`printcore`) | Reused | Simulated printer connects |
| [x] | Port list + rescan | Rebuilt | Printer's COM port appears |
| [ ] | Baud rate picker (+ remembers last) | Rebuilt | Connects at your printer's baud |
| [x] | Auto-detect port/baud | New | Finds printer with no setup |
| [x] | Connect / Disconnect | Reused + Rebuilt | Real printer goes "online" |
| [~] | Reset printer | Reused + Rebuilt | Printer resets |
| [x] | DTR option (stops Arduino-based boards resetting on connect) | Reused | Setting respected |
| [~] | TCP/network printers (`host:port`) + TCP streaming mode | Reused | Simulated network printer |
| [x] | Command console: type G-code or Pronterface commands | Reused + Rebuilt | `M115` returns firmware info |
| [~] | All console commands work: connect, disconnect, load, print, pause, resume, reset, home, move, extrude, reverse, settemp, bedtemp, gettemp, tool, monitor, eta, ls, sdprint, upload, macro, set, gcodes, slice, run_script, run_gcode_script, shell, off, exit, help | Reused (help, settemp, bedtemp, set tested) |
| [~] | Commands that only existed in the classic window: button, setspeed, setflow | Rebuilt |
| [ ] | editgcode (classic-only) | Rebuilt in Phase 4 (Edit open file) | Each runs against simulated printer |
| [x] | Log view, Clear log, Don't-autoscroll | Rebuilt | |
| [~] | Debug communications (show raw traffic) | Reused + Rebuilt | |
| [~] | Command history (up/down arrows, saved between runs) | Reused + Rebuilt | |
| [ ] | Log to file (Log path setting) | Reused | File written |
| [~] | Launch options: `-a` autoconnect, `-c` config file, `-e` run command, open file | Reused | |
| [x] | Reads existing Pronterface settings file | Reused | Your settings show up |
| [ ] | Keep PC awake while printing | Reused | PC doesn't sleep mid-print |
| [~] | Custom port name pattern (Mac/Linux only) | Reused | |
| [~] | Local status server (RPC on port 7978, this PC only): status, temps, load, start/pause/resume, home, connect, send | Reused | |

## Phase 1: Temperatures & safety

| | Feature | Status | How we test |
|---|---|---|---|
| [~] | Nozzle temp: set / off / dropdown of presets | Rebuilt | |
| [~] | Bed temp: set / off / presets | Rebuilt | |
| [~] | Preheat buttons: PLA / PETG / ABS (editable temps) | Rebuilt (added this week) | |
| [~] | Temperature monitoring (Monitor printer status setting) | Reused | Temps update every few sec |
| [~] | New temperature graph: small view + large view in its own window, 1/5/15 min | Rebuilt (approved mockup) | |
| [~] | Fan speed shown in expanded graph | Rebuilt | |
| [~] | Temperature gauges (optional) | Rebuilt | |
| [~] | Big status strip: temps, progress, time left, height | Rebuilt (added this week) | Temps done; progress/time/height fill in with Phase 2 |
| [~] | Heaters off / Motors off / Clear all buttons | Rebuilt (added this week) | |
| [~] | "Off": everything off incl. power supply (M81) | Reused, console `off` only | Button removed at Keegan's request (2026-11-01): the Ender 5 Pro can't power itself off, and Clear all covers the rest |
| [~] | Closing the app never turns heaters off (opt-in setting) | Rebuilt (added this week) | |
| [~] | Warning before closing mid-print | Rebuilt | |
| [~] | Interface lock (+ lock on print start) | Rebuilt | |
| [~] | Multiple extruders: count setting, tool select, per-tool temps | Reused + Rebuilt | Simulated 2-tool printer |

## Phase 2: Printing

| | Feature | Status | How we test |
|---|---|---|---|
| [ ] | Load G-code file (+ last folder & file-type filter remembered) | Rebuilt | |
| [ ] | Recent files | Rebuilt | |
| [ ] | Print / Restart | Reused + Rebuilt | Short test print |
| [ ] | Pause / Resume (with pause script) | Reused + Rebuilt | |
| [ ] | Stop print (pause only, nothing else touched) | Rebuilt (added this week) | Temps stay put |
| [ ] | Clear file | Rebuilt (added this week) | |
| [ ] | Progress %, line count, time remaining, current height | Reused + Rebuilt | Matches classic |
| [ ] | File info: size, filament used, estimated duration | Reused + Rebuilt | |
| [ ] | Show progress on printer's screen (M117) + update interval | Reused | |
| [ ] | End-of-print script | Reused | |
| [ ] | Run program on print start / finish / error (Start/Final/Error command) | Reused | |
| [ ] | Recover after disconnect (re-home XY, restore Z/E, continue) | Reused + Rebuilt | Simulated disconnect |
| [ ] | Filament-used total (for spool tracking) | Reused | |

## Phase 3: Movement & extrusion

| | Feature | Status | How we test |
|---|---|---|---|
| [ ] | Jog X/Y/Z with step sizes (0.1 / 1 / 10 / 100 mm) | Rebuilt (new pad replaces jog wheel) | |
| [ ] | Home all / X / Y / Z | Reused + Rebuilt | |
| [ ] | Manual feedrates for XY / Z / E (settings) | Reused | |
| [ ] | Clamp manual moves to build area | Reused | Can't jog past bed edge |
| [ ] | Build dimensions, origin offset, home position, circular bed | Reused + Rebuilt | |
| [ ] | Motors off (M84) | Reused + Rebuilt | |
| [ ] | Extrude / Reverse with length + speed | Reused + Rebuilt | |
| [ ] | Print speed % and flow % (M220 / M221), live during print | Reused + Rebuilt | |
| [ ] | Keyboard shortcuts: arrows / PgUp-PgDn jog, Space, focus keys | Rebuilt | |
| [ ] | Mini vs Standard controls mode | Rebuilt | |

## Phase 4: Files, viewer & SD card

| | Feature | Status | How we test |
|---|---|---|---|
| [ ] | 2D layer viewer: layer up/down, zoom, pan, grid | Rebuilt | Same file looks the same |
| [ ] | 3D viewer: rotate/zoom, light mode, perspective, track current layer | Rebuilt | |
| [ ] | Highlight what's been printed / current layer | Rebuilt | |
| [ ] | Viewer colors & line width settings | Rebuilt | |
| [ ] | Update viewer while a file is still loading (setting) | Rebuilt | |
| [ ] | Inject G-code at a layer / edit a layer's G-code | Reused + Rebuilt | |
| [ ] | Edit open file | Rebuilt | |
| [ ] | Save file | Rebuilt | |
| [ ] | SD card: list files, print from SD, upload to SD, SD progress | Reused + Rebuilt | Needs a printer with SD |
| [ ] | Cutting moves shown as extrusion (laser/CNC users) | Reused | |

## Phase 5: Customization & settings

| | Feature | Status | How we test |
|---|---|---|---|
| [ ] | Settings page with every setting from the Options dialog | Rebuilt | Every setting listed in code is present |
| [ ] | Light / Dark theme | Rebuilt (added this week) | |
| [ ] | Macros: create / edit / delete / run (menu + console) | Reused + Rebuilt | Existing macros still work |
| [ ] | Custom buttons: add / edit / remove / reorder / color (up to 64) | Reused + Rebuilt | Existing buttons carry over |
| [ ] | Printer event plugins (printcore handlers) | Reused | Sample plugin loads |
| [ ] | Layout options (replaces Standard / Compact interface mode) | Rebuilt | |
| [ ] | Language / translations | Rebuilt | |
| [ ] | About page (credits, license) | Rebuilt | |

## Phase 6: Webcam (new)

| | Feature | Status | How we test |
|---|---|---|---|
| [ ] | Live view in the interface | New | |
| [ ] | Camera picker, resolution, frame rate, rotate/flip | New | |
| [ ] | Snapshot button | New | |
| [ ] | Timelapse: one frame per layer, saved as a video | New | Real print |

## Phase 7: Network access (opt-in)

| | Feature | Status | How we test |
|---|---|---|---|
| [ ] | "Allow other devices on my network" setting (off by default) | New | |
| [ ] | PIN / password required when network access is on | New | Phone can't control without PIN |
| [ ] | Phone-friendly layout | New | |

## Phase 8: Advanced tools

| | Feature | Status | How we test |
|---|---|---|---|
| [ ] | Excluder (skip parts of the bed) | Reused + Rebuilt | |
| [ ] | Spool manager (track filament per spool) | Reused + Rebuilt | |
| [ ] | G-code plater (combine G-code files onto one plate) | Reused + Rebuilt | |
| [ ] | Slice command (send an STL to your slicer from the app) + open slicer settings | Reused + Rebuilt | |
| — | STL plater (arrange 3D models) | Skipped: stays in classic Pronterface | |
| — | Old Slic3r integration (pick Slic3r profiles from a menu) | Skipped: stays in classic Pronterface | |
| — | Layer projector (for DLP resin printers) | Skipped: stays in classic Pronterface | |

---

## Decisions

1. **Phase 8 niche tools:** skipped (decided 2026-11-01). STL plater, old Slic3r integration and
   the resin projector stay available in classic Pronterface.
2. **Test printer:** Creality Ender 5 Pro (Keegan's only printer).
3. **Name:** "Layerline" (project: "Project Layerline"), decided 2026-11-01.
