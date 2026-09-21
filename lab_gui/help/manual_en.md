# LabControl – User Manual

LabControl controls an electronic load (Korad KEL102) and a lab power
supply (HCS-34xx) over USB. This manual describes every part of the
interface: the dashboard, the three tabs "Control", "Testcase",
"History" and "Settings", and the status bar.

You can reopen this page at any time via the **Help** button in the
"Settings" tab.

---

## 1. Dashboard (always visible)

The dashboard sits at the top of the window, above the tabs, and shows
one tile per known device with its current readings (load:
voltage/current/power/mode; power supply: voltage/current/mode). The
display refreshes automatically about every 500 ms.

- **No device connected**: as long as no device has been detected at
  all, a gray placeholder tile is shown.
- **Disconnected device**: a device that has been seen once does not
  disappear from the dashboard when it disconnects — it turns gray and
  gets a "plug disconnected" icon, so it stays visible which devices
  belong to the session in general.
- **Toggle view**: the arrow button at the bottom right of the dashboard
  frame switches between the normal and a compact view (smaller tiles,
  for many devices visible at once).
- **Panel colors**: if the option is enabled in Settings (see section
  5), each device panel can be tinted individually, e.g. to tell "Load
  1" and "Load 2" apart at a glance. The color is picked in the
  "Control" tab and is applied to the dashboard automatically.

---

## 2. "Control" tab

Every connected device gets its own control section here. If two
identical devices are connected (e.g. two power supplies), they are
controlled independently.

### Preset bar

At the very top are 5 fixed, device-spanning preset slots. A preset
stores the setpoints and switch state for all currently visible device
panels together:

- Large button in the middle: **load preset** (writes to the hardware
  immediately).
- Small icon top right on the slot: **save preset** (overwrites the
  slot's content with the current state of all panels).
- Small icon bottom right: **rename preset**.

### Electronic load (KEL102)

- **Mode**: constant current (CC), constant voltage (CV), constant
  resistance (CR), constant power (CW), or short circuit (SHORT).
- **Setpoint**: numeric value for the chosen mode, applied with the
  checkmark button. No setpoint is needed for "short circuit".
- **Input ON/OFF**: switches the load input. The last active state is
  highlighted in color (green = on, red = off). Right after connecting,
  the state is unknown until the first hardware feedback arrives — both
  buttons stay neutral until then.
- The pencil icon at the top of the section **renames** the device, the
  color-circle icon next to it picks the **panel color**.

### Lab power supply (HCS-34xx)

- **Voltage** and **current**: setpoints, each applied with its own
  checkmark button.
- **OVP/OCP**: over-voltage/over-current protection threshold of the
  device. If the voltage or current setpoint is higher than the
  matching OVP/OCP threshold, a warning appears — the device would
  otherwise silently reject such a value.
- **Output workaround**: the HCS-34xx has no real software output-off.
  "OFF" internally sets the current to 0 A; "ON" applies the entered
  voltage and raises the current to at least 0.1 A, without overwriting
  an already higher configured current. **While "OFF" is active, the
  apply buttons for voltage/current are locked** — this prevents a
  setpoint click from silently undoing the emulated off state. Only
  clicking "ON" unlocks the setpoint buttons again.

---

## 3. "Testcase" tab

A row-based editor for automated test sequences. Every row is either an
**action step** (device + action + value + wait time) or a **flow
control element** (loop, condition, variable).

### Toolbar

- **"+" button**: the arrow next to it opens a menu with every insertable
  row type (see below). Clicking the button itself inserts a new action
  step directly.
- **"–" button**: removes the selected row.
- **Up/down arrows**: move the selected row.
- **Puzzle piece with plus** ("Save block…"): saves a selected,
  contiguous range of rows as a reusable **block** in its own file
  (folder `blocks/`). The dialog checks that the chosen range is
  structurally self-contained (e.g. a loop including its "End").
- **Puzzle piece** ("Insert block…"): inserts a previously saved block at
  the current position — handy for recurring sections such as a
  charge/discharge profile.
- **Folder icon** / **floppy-disk icon**: load/save a testcase (JSON
  file in the `testcases/` folder).

### Editing a row (action step)

- **Device**: a specific device or "automatic" (at runtime, the only
  connected device of that kind is used — if several matching devices
  are connected, the step aborts with an error).
- **Action**: depends on the device, e.g. set setpoint, switch output
  on/off, choose mode. The value column automatically adapts unit and
  minimum/maximum to the selected action.
- **Arbitrary signal**: for actions that allow a changing signal instead
  of a fixed value, the number field is replaced by a "Define signal…"
  button. The dialog lets you choose between sine, square (with
  adjustable duty cycle), triangle and sawtooth, plus amplitude, offset,
  frequency and update interval — a live preview shows the resulting
  waveform.
- **Value / Duration (s)**: setpoint resp. wait time after the step,
  before the next one begins. The wait time must cover the device's
  physical settling time (see note below).
- **Check**: optional pass/fail limits (see below).
- **Active**: checkbox to temporarily disable a row without deleting it.

### Flow control

The "+" menu additionally offers:

- **Loop (n×) … End**: repeats the body a fixed number of times (e.g.
  for charge/discharge cycles in battery tests).
- **While … End**: repeats the body while a condition holds (see
  "Conditions" below). An adjustable "Max. iterations" cap prevents an
  infinite loop if the condition never turns false (0 = unlimited, use
  with care).
- **If … End** (with an optional **Else**): only runs the body if a
  condition holds.
- **Set variable / Increase variable**: creates a run variable resp.
  changes its value — useful as a counter or to query conditions later
  in the sequence.

A block start and its "End" are always inserted as a pair. The table
shows nesting as indentation; if the structure is unbalanced (e.g. a
loop without its "End"), the Start button stays disabled until the
structure is fixed.

### Conditions (While/If)

The button with the question-mark-diamond icon in the condition row
opens a dialog with three sources:

- **Measurement**: compares voltage/current/power of a specific or
  automatically chosen device against a value.
- **Time**: compares the time elapsed since the block or the testcase
  started against a value.
- **Variable**: compares a run variable previously created with "Set
  variable" against a value.

If a current measurement is missing for "Measurement" (device just
disconnected, or data stale), the condition counts as not met, instead
of computing with a stale value.

### Pass/fail checks

The checkmark-circle button in the "Check" column lets you set an
expected [Min, Max] range for voltage, current or power of the step's
device, per action step. After the wait time elapses (or after an
arbitrary signal ends), the testcase evaluates the first measurement
that arrives afterwards:

- The row is permanently colored **green** (passed) or **red** (failed);
  the actual measured value is shown as a tooltip on the check cell.
- Inside loops, a violation is "sticky": once a row turns red, it stays
  red even if later iterations pass.
- Selectable per check: either a violation aborts the whole run
  immediately (like a device error), or the test keeps running and the
  status line reports "PASSED"/"NOT passed" at the end, with a counter
  of all checks.
- If the expected measurement never arrives (device disconnected), the
  step fails.

**Important note on wait time:** on the real HCS-34xx, without a load
connected, the output voltage only drops slowly toward a lower setpoint
via the internal bleeder resistor (measured: 2 seconds after "set 5 V",
still 8.7 V). For downward steps without a load, choose generous wait
times — otherwise a check can fail incorrectly.

### Start, Stop, Report

- **Start**: begins sequential execution of all active rows. While
  running, the table is locked, the current step is highlighted and
  shown in the status line (including the current loop/while iteration,
  if applicable).
- **Stop**: aborts the run and immediately switches off all outputs
  (safety shutdown).
- **Report button** (enabled after every run): opens a post-run report
  as an HTML page in the browser, or exports it as PDF. The report
  contains the sequence, all check results, and a chart of the recorded
  measurements.
- With desktop notifications enabled (see Settings), a system
  notification reports the run finishing or failing, even if the window
  is not in the foreground.

---

## 4. "History" tab

A continuous oscilloscope view of all device readings, in one or more
charts.

- **Add chart**: creates a new, empty chart.
- Per chart: **add signal** chooses which device readings are shown
  (shared Y axis per unit); **set Y axis…** switches between automatic
  and fixed scaling (left/right separately); **rename chart** and
  **remove chart** are also available.
- **Time window**: how many seconds of history are visible.
- **Pause/Resume**: freezes the live display without stopping the
  recording.
- **Reset view**: clears only the visible chart buffers, independent of
  an ongoing recording.

### Recording

Above the charts is the recording of a measurement log across all known
devices (timestamp, device, channel, value) — independent of the
time-window-limited ring buffer of the live charts.

- Start/stop via one shared button (blinking red = active).
- Only signals currently assigned to at least one chart are recorded.
- **Reset**: clears the recording so far.
- **Export as CSV…**: long format, one row per measurement.
- **Export as MF4…**: ASAM MDF4 file (one signal per device+channel),
  requires the additional Python package `asammdf`. Both exports also
  work while a recording is running.

---

## 5. "Settings" tab

- **Simulation mode**: shows a virtual power supply and a virtual load,
  to test the interface without connected hardware. This option is not
  present in the built `.exe`, for safety reasons.
- **Dark mode**: switches between the light "Modern Light" and the dark
  "Amber Industrial" color scheme, without restarting.
- **Desktop notification on run end/error**: see section 3.
- **Individual panel background colors**: globally enables/disables
  panel color selection in the "Control" tab (see sections 1/2). The
  first time it is enabled, every already-known device automatically
  gets its own color.
- **Language**: switches the interface language immediately, without
  restarting.
- **Clear device assignment**: resets stored device names, safety limits,
  panel colors and network sharing for all devices to their defaults
  (asks for confirmation, cannot be undone).
- **Safety limits (watchdog)**: see section 6.
- **Network sharing and remote control**: see below.
- **Help**: opens this user manual.

### “Network” sub-tab: sharing tiles on the local network

This makes **individual dashboard tiles** visible to other devices on the
local network — for example a small ESP32 display in the lab, or a browser
on your phone. For **display**, the “Read” share is enough; **remote
control** additionally needs “Control” and the master switch (see “Remote
control” below).

Everything is off by default: the server is not running, no tile is
shared, and an access token is required.

Setup in four steps:

1. Tick **Enable sharing on the local network**.
2. Under **Reachable by**, choose “All devices on the local network” (the
   default “This PC only” is meant for trying things out — an ESP32 cannot
   reach the app that way). The first time, Windows Firewall will ask for
   permission, which must be granted.
3. In the device table below, tick **Read** for each tile you want to
   share. Without that tick a tile is invisible from outside, even if its
   device ID is known.
4. Copy the **Address** shown into your ESP32 sketch or into a browser.

Useful addresses (`ADDRESS` is the base address shown in the field):

| Address | Result |
| --- | --- |
| `ADDRESS/` | overview of all shared tiles with ready-made links |
| `ADDRESS/display?tile=psu:0001` | compact display page, refreshes itself |
| `ADDRESS/display?tile=psu:0001&refresh=2` | the same, every 2 seconds |
| `ADDRESS/display?tile=psu:0001&fmt=text` | plain `name=value` lines |
| `ADDRESS/api/v1/tiles` | list of shared tiles as JSON |
| `ADDRESS/api/v1/tiles/psu:0001` | current readings of one tile as JSON |

The display page needs no JavaScript and is about one kilobyte — it is
deliberately built so an ESP32 can render it without trouble. A stale
value or a disconnected device is shown in red and labelled accordingly.
A running test sequence or a tripped watchdog is shown on the page too.

**Token.** It is generated automatically when sharing is enabled. Unless “Allow read access without a token” is ticked, every
request must carry the token — either as a header
(`Authorization: Bearer …`) or simply appended to the address
(`…/api/v1/tiles?token=…`), which is the easier route for simple displays.
“Generate a new token” invalidates the previous one immediately and is
therefore the way to revoke a device's access.

**Security note.** The connection is **unencrypted**, and the token is
transmitted in clear text and stored in clear text in `settings.json`. It
is a local-network access key, **not a password**. This feature is meant
for a trusted home or lab network: the port must **never** be made
reachable from the internet via port forwarding or UPnP.

**Log requests** writes every request to a separate file
`share_access.log` next to the program. Deliberately separate from
`labdash.log`, so that frequent polling does not overwrite the main log.
Off by default.

#### Remote control

The same interface can also **control** devices: set setpoints, switch outputs
and relays, switch everything off. It is meant for an AI assistant (see MCP
server below) or your own app. Because real hardware is moved, **all** of the
following must hold:

1. **Token**: write requests always require the token — even if reading is
   allowed without one — and only as a header (`Authorization: Bearer …`),
   never in the address.
2. **Control** is ticked for the device in the table (requires “Read”). This
   is possible for load, power supply and microHIL; CAN and oscilloscope cannot
   be remote-controlled.
3. The master switch **“Remote control active”** is on (not needed for access from
   the same computer, see “Exception for this PC”). It is off after every
   program start and switches itself off after the configured time limit
   (default 60 minutes). While it is on, an orange notice with the remaining
   time is shown in the status bar. On expiry the devices stay in their state
   — they are not switched off.
4. **No test sequence** is running and the **safety trip** has not fired.
   Otherwise every control command is rejected (reading stays possible).
5. Action, value and channel are valid — the value ranges are those of the
   test editor — and the setpoint is **not above an active safety limit** of
   this device (section 6).

**Exception for this PC.** Programs on the same computer as LabControl — such as
the MCP server — do not need the master switch as long as “Access from this PC
does not need the master switch” is ticked (default). There is then no time
window; the token, “Control” for the device, value ranges, limits and the locks
during a test run and after a safety trip still apply. Unticking it restores the
time window for local access too. “Local” is detected from the connection
(loopback or the computer's own address), not from anything the caller states.

**Emergency stop.** The action “All outputs off” is the only exception: it
requires only the token and always works — even with the master switch off,
during a test sequence and after a safety trip. It acts like the “ALL OFF”
button.

In addition, at most two commands are in flight at once, and fast command
sequences are throttled so that a backlog cannot delay the emergency stop.
Every control command **and every rejection** is written to `labdash.log` with
the caller's address.

On the HCS-34xx power supply, “output OFF” is merely a current of 0 A; a
current above 0 A switches the output back on. The ON/OFF switch on the
“Control” tab follows a remote action; the setpoint fields there stay
unchanged, the dashboard shows the actual values.

Commands are `POST` requests with a JSON body, e.g. (insert `TOKEN` and
`ADDRESS`; the allowed actions per device are listed at
`ADDRESS/api/v1/tiles/<ID>`):

```
curl -X POST -H "Authorization: Bearer TOKEN" ^
     -d "{\"action\":\"PSU_VOLT\",\"value\":5.0}" ^
     ADDRESS/api/v1/tiles/psu:0001/actions
curl -X POST -H "Authorization: Bearer TOKEN" ADDRESS/api/v1/all-off
```

The response says whether the device confirmed the command (`"ok":true`) or why
it was rejected (`"error":"…"`).

**MCP server for AI assistants.** The `labcontrol_mcp` folder contains a small
server that exposes this interface as tools for Claude Code and similar
(read status, list devices, read values, perform an action, emergency stop).
Setup and security model are in `labcontrol_mcp/README.md`. It runs as a
separate process and can do no more than the interface above allows — in
particular it cannot switch the master switch on by itself.

---

## 6. Safety: software watchdog

For every known device, the Settings tab offers its own section of
limits (max. voltage/current, plus max. power for the load). If a live
measurement exceeds an enabled limit **of that specific device**, the
software immediately switches off **all** outputs — regardless of
whether a testcase is currently running.

- A triggered alarm "latches": it stays active until confirmed via the
  **Acknowledge** button in the red banner at the top of the window —
  so an unattended run doesn't silently keep sitting in the "triggered"
  state.
- The safety status in the status bar shows **OFF** (no limits active),
  **ARMED** (monitored, everything within range), or **TRIGGERED**
  (limit exceeded, outputs switched off).
- During a testcase run, the watchdog additionally monitors the
  connection of the involved devices — if the connection drops or a
  device fails to deliver a fresh measurement for too long, it also
  shuts down immediately.
- The **"ALL OFF" button** at the far right of the status bar manually
  switches off all outputs immediately at any time, independent of the
  watchdog state.
- Remote-control commands over the network (section 5) are rejected while
  the alarm is tripped or a test sequence is running, and a setpoint above
  an active limit is not applied at all. “All outputs off” remains possible.

---

## 7. Status bar

At the bottom of the window, one label per known device shows its
connection status (green = connected, red = disconnected). The
connection is retried automatically every 3 seconds as long as a device
is unreachable. Next to it are the safety status (see section 6) and,
at the far right, the "ALL OFF" button.

---

## 8. Testcase files, blocks and presets on disk

- Testcases: folder `testcases/` next to the `.exe` resp. next to
  `lab_gui/`.
- Reusable blocks: folder `blocks/`.
- Post-run reports (HTML/PDF): a dedicated reports folder, selectable
  through the export dialog.
- Presets, device names, panel colors and safety limits are saved
  automatically between program runs and do not need to be exported
  manually.
