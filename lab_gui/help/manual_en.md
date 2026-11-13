# LabControl – User Manual

LabControl controls an electronic load (Korad KEL102) and a lab power
supply (HCS-34xx) over USB. This manual describes every part of the
interface: the dashboard, the pages "Control", "Testcase", "History" and
"Settings" (navigation on the left) and the bar at the top with the device
status and "ALL OFF".

You can reopen this page at any time via the question-mark button (**Help**)
in "Settings".

---

## 1. Dashboard (always visible)

The dashboard sits at the top of the window, above every page, and shows
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
  for many devices visible at once). Without a saved choice LabControl
  starts compact; the choice is saved.
- **Detach**: the button to its left opens the dashboard in a window of
  its own, e.g. for a second screen; the main window then shows only the
  tabs. Closing that window (or the same button) brings the dashboard
  back. The app remembers the window's position and size, and whether it
  was detached when you quit.
- **Panel colors**: if the option is enabled in Settings (see section
  5), each device panel can be tinted individually, e.g. to tell "Load
  1" and "Load 2" apart at a glance. The color is picked in the
  "Control" tab and is applied to the dashboard automatically.

---

## 2. "Control" tab

Every connected device gets its own control section here. If two
identical devices are connected (e.g. two power supplies), they are
controlled independently.

**Arranging**: grab a section by its title line and drag it into any cell
of the grid, including a gap next to a large section. While dragging you
already see where it will land. If a section is already there, only that one
moves to the next free place; all others stay put. The arrangement is
remembered; Escape cancels.

### Preset bar

At the very top, in their own "Presets" box, are 5 fixed, device-spanning preset slots. A preset
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
- **Output ON/OFF**: the output is really switched (command `SOUT`, the
  state is read back with `GOUT`). "OFF" leaves voltage and current
  unchanged; "ON" applies the entered voltage and current limit and then
  switches on. Voltage and current can also be set while "OFF" (set up first,
  then switch on). Without a load the output voltage falls slowly after "OFF"
  (discharging, about 10 s). If the output is switched from outside
  (e.g. via the remote-control connector on the back or another program; there
  is no output key on the front), the switch in the tile follows after about a
  second. When connecting, LabControl adopts the device's state (output,
  voltage, current) and changes nothing. "All off" switches the output off.
- **Manual control**: the device has no output key, and every command locks its
  knobs for about 5 s ("Rear control" LED; while polling it flashes at 5 Hz).
  Operating it by hand and from LabControl at the same time therefore does not
  work. The **Manual control** switch in the tile releases the device: voltage
  to the minimum (1 V; the device does not accept 0 V), current 0 A, **output
  ON**, knobs unlocked. After that LabControl no longer talks to the device (no
  polling, the LED stays off), the fields of the tile are locked, commands from
  remote control and test runs are rejected with "Netzteil in Handbedienung",
  and "All off" leaves the device alone. The dashboard shows the device as
  unavailable, the status bar "Manual control". When switching back, LabControl
  takes over the values set on the device. **When LabControl is closed, every
  power supply is released the same way** (1 V, 0 A, output ON), so that it is
  usable by hand afterwards.

### Function generator (JDS2915 / JDS66xx)

Two identical channel boxes (channel 1 and 2) side by side:

- **Shape, frequency, amplitude, offset, duty cycle**: each applied with
  its own check button. Amplitude is peak-to-peak into open circuit
  (0-20 V), offset is +-9.99 V.
- **Output ON/OFF** per channel. Unlike the load, the generator reports
  all settings back: the switches always show the state the device
  reports, and a change made on the device itself (knob, keys) shows up in
  the fields within two seconds. Input you are typing is not overwritten
  as long as the device value does not change.
- **Phase** (channel 2 relative to channel 1) exists only once.
- **ALL OFF** switches both outputs off; shape, frequency etc. stay set.
- The **test sequence** offers actions for frequency, amplitude, offset,
  duty cycle, phase, the shapes sine/square/pulse/triangle/DC and output
  ON/OFF; the channel is chosen in the row.
- The device is found through its CH340 USB-serial converter. Because that
  converter also sits in unrelated devices, a query checks that a function
  generator really answers. A powered-on device is therefore detected up
  to 30 seconds after switching it on.

### Oscilloscope (PicoScope 2204A/2205A)

LabControl deliberately does not set channels, trigger or waveforms – that
is what the PicoScope 7 app or acquisitions through the MCP server are for.
The section shows:

- **Status** (free, busy, test run, connected via MCP) plus model and
  serial number.
- The **last acquisition** over network/MCP with the **Plot** button (chart
  icon, shows it as an image).
- **“Open PicoScope 7”** (PC only, not in touch mode) and – while LabControl
  holds the device for acquisitions – **Disconnect** (icon with a broken network
  link).
- As for the other devices: **rename** (pencil) and **panel colour** (colour
  wheel); both also apply to the dashboard tile.

### Network oscilloscope (UNI-T MSO1000HD, e.g. MSO1154HD-S)

The same section as for the PicoScope, just without the launch button – the
device is operated on its own screen. It is not found via USB but by its IP
address:

1. Give the oscilloscope a **fixed IP address** (or reserve one in the
   router). The address is also its identifier in LabControl – name, colour
   and sharing settings are tied to it.
2. **Settings → Devices → "Network oscilloscopes (SCPI)"**: enter the
   address, several separated by commas.
3. LabControl checks every 10 seconds whether the device answers; then the
   section appears.

After an acquisition through the MCP server the oscilloscope stays stopped
and shows that acquisition. *Tested with the simulator only so far.*

### Battery simulator (14 cells, over CAN)

The simulator has no USB connection of its own; it sits on a CAN interface
already set up in LabControl (Vector, PCAN or SLCAN). Setup:

1. **Settings → CAN interfaces**: add the interface the simulator is wired to
   (500 kbit/s) and pick "Battery simulator" in the column **"Device on
   bus"**. The interface can be shared with other nodes (e.g. the BCU board).
2. Its tile appears as soon as the simulator answers. If its status message
   is missing for one second, it counts as disconnected.

Operation:

- **Remote control ON** takes over the device (state "Fernsteuerung"); the
  output stays off. While remote control is off, LabControl sends nothing and
  the front panel works as usual.
- Set **setpoints** per cell or for **all cells**, switch cells **on/off**
  with the tick. A cell that is off is bridged at the output (0 V), the stack
  stays closed.
- **Output ON** applies the enabled cells. **ALL OFF** blocks the output and
  keeps remote control.
- **Limit per cell** (default 4.30 V, up to 5.0 V): higher setpoints are
  rejected, also from test sequences and remote control.
- **Measured** value per cell and the **stack voltage** come from the device.
- **New firmware (BattSimFW):** the "State" line also shows the **reason** of a FAIL (e.g. "Timeout 0x130",
  "Overload 48 V", "Current measurement invalid", "Overvoltage cell"), whether the **regulator** is working, the
  current of the 48 V supply and the firmware version; the dashboard tile shows FAIL reason and regulator. A cell
  with a setpoint below about 0.7 V shows "(OL)" (open load) after the measured value, see below – this is not a fault.
  With the old firmware these entries stay empty.
- **Channel status "OL" (open load):** With the new firmware a cell shows "(OL)" after its measured value (channel status 4;
  network/MCP: `cellN_status` = "OL") when its setpoint is above 0 V but below the **level floor** of the output stage
  (parameter `OutputFloor_V`, default 0.7 V). This means:
  - **Without a load** the output stage does not go below about 0.7 V. A setpoint below that cannot be reached, the output stays at
    about 0.7 V.
  - **With a load** (measured with 100 Ω and 1 kΩ) the voltage follows the setpoint down to about 0.3 V.
  - The channel stays **released**. It is **not a fault and not a FAIL**, only a hint.
  - In this range **the voltage regulator does not work** (without a load the setpoint cannot be reached). The overvoltage detection of
    the firmware then compares against the floor plus 0.2 V, not against the setpoint.
  - The firmware does not know the current of the individual channels. The "OL" hint therefore also appears **with a test object
    connected**, although the voltage is then mostly right; it does not show whether a load is really missing.
- **State "FAIL"**: if the link to LabControl is lost (crash, cable), the
  simulator switches off after 4.5–6 seconds. **"Reset FAIL"** brings it
  back; switch the output on again afterwards. On a normal exit LabControl
  hands over remote control in an orderly way first.
- **Test sequences** offer actions for remote control, output, cell voltage
  (single/all), cell on/off and reading cell and stack voltage (with
  pass/fail check). The cell is chosen in the row.

*Tested against an emulated simulator only so far; the hardware check is
pending.*

### Fault box (microHIL)

If faults are set up in the settings (section 5), the **"Fault box"** tile
shows one button per fault; a tap sets the fault, a tap on the active one
clears it, as does **"Clear"**. At most one fault is active: while one is
set, the tile does not set another. The microHIL channels of the active
fault are locked in the microHIL tile (greyed out) and over the network.
ALL OFF clears the fault. During a test run the tile is locked.

### Device under test (UDS)

If a device under test is set up in the settings (section 5), it appears as
its own tile. At the top: session (with "unlocked") and the result of the
last operation. Below: choose and change the session, **unlock** (padlock),
choose a DID and **read** it (values with units), **read fault memory** (code,
status, text) and **clear** it, **reset**. Clearing and reset ask first.
While a test sequence runs, the tile is locked; the overview shows session,
number of DTCs and the last operation.

### J-Link (flashing and reset)

A SEGGER J-Link on the PC can be added as a device: in the settings under
**Devices → J-Link** tick "Use J-Link" and enter the **target device** (e.g.
`S32K144`), the interface (SWD/JTAG) and the speed; with several J-Links also
the serial number. The SEGGER J-Link software (J-Link Commander) is required;
LabControl finds it by itself.

The tile shows the target, the target voltage, the last operation with its
result and the last flashed file. Below are five icon buttons (name in the
tooltip): **Check** (plug) connects to the target and reads target voltage and
core (the core keeps running), **Reset** (circular arrow), **Halt** (pause),
**Run** (play) and **Flash** (download arrow: choose a file, then reset and run).
The file dialog starts at the last flashed file and shows HEX, SREC/S19/MOT and
ELF/AXF/OUT; `.bin` is rejected because it carries no address. Test
sequences offer the same operations; for flashing, choose the file (HEX, SREC
or ELF) in the value column. A failed step aborts the sequence.

LabControl opens the J-Link only for the duration of one operation and never
polls it in the background – the IDE debugger stays usable while nothing is
running. With the **EDU license** SEGGER shows a confirmation dialog once every
24 hours; until it is dismissed the operation waits (time limit 30 s, flashing
180 s) and then reports this reason. Every operation is logged, flashing with
file name and checksum (SHA-256).

---

## 3. "Testcase" tab

A row-based editor for automated test sequences. Every row is either an
**action step** (device + action + value + wait time) or a **flow
control element** (loop, condition, variable).

As long as no sequence is loaded and nothing has been entered, the hint
**"No test sequence yet"** is shown above the table, with the folder the
sequences are stored in. It disappears as soon as a sequence is loaded or a
step is edited. On the right of the toolbar are **New sequence** (sheet with a
plus: empties the table down to one start row and detaches it from the file,
asking first if something is already loaded or entered), **Load** and **Save**.

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
  file in the `testcases/` folder). If the opened file was changed elsewhere
  since it was opened (e.g. on the second computer in the shared folder),
  saving asks: **"Overwrite"**, **"Save as copy"** (creates
  "Name (copy).json") or **"Cancel"**.
- **Slider icon** ("Test sequence properties…"): description and requirement
  IDs of the whole sequence (e.g. `SSR-12, SSR-13`) and **"Power supply and
  load at the same time (source/sink)"** – lifts the power supply/load
  interlock (section 6) for this sequence, e.g. to emulate a battery. The
  report notes it.

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

### Fault box steps

With the device **"Fault box"** a sequence sets one of the configured faults
("Set fault", name in the value column), clears it or clears all. If another
fault is active, "Set fault" clears it first, waits the configured time and
then sets the new one. A step that switches a channel of the active fault
directly aborts the run. A fault set by the sequence is cleared at the end
of the run.

### UDS steps (device under test)

With the device **"Device under test (UDS)"** a sequence talks UDS to the
ECU set up in the settings (section 5): change session, unlock, read/write
DID, read/clear fault memory, routine, reset, raw request. **"UDS
parameters…"** picks DID, element, session or routine from the ODX. Each step
has an **expectation**: positive response or a specific negative response
(NRC, e.g. 0x33 for "locked"). "Read fault memory" can require DTCs, forbid
them or require an empty memory. A mismatch counts as a violated check and
aborts the run. "Read DID" returns the value of the chosen element for a
range check and for a variable (conditions in "If"/"While").

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

- **Start** (play icon): begins sequential execution of all active rows. While
  running, the table is locked, the current step is highlighted and
  shown in the status line (including the current loop/while iteration,
  if applicable).
- **Stop** (square): aborts the run and immediately switches off all outputs
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
- **Touch operation**: for small touch displays such as on the Raspberry Pi
  (1024 × 600). Larger text and controls, larger buttons in the navigation
  on the left, the overview (dashboard) as its own page instead of a fixed
  strip, a swipeable device status at the top and the **Arrange** button (move cross).
  The full-screen button at the top exists on the PC too; on the Raspberry
  Pi LabControl starts in full screen. On Linux there is also a power button: after a
  confirmation it shuts the computer down or restarts it, LabControl
  releases the power supplies first. Number fields in the tiles have large
  −/+ keys on the left and right; tapping the value opens a keypad showing
  range and unit ("Set" applies and sends like the ✓ button, a value outside
  the range cannot be set). The CAN ID and payload get the same keypad with
  the keys A–F. The "Test sequence" tab shows a run view instead of the
  editor: the saved sequences on the left (from the network drive, folder
  `testcases/`, otherwise local), the steps, progress, status and large Start
  and Stop buttons on the right. Editing and saving is done on the PC; runs
  work exactly as in the editor (same checks, a step error is acknowledged
  with Stop). Lists, tables and pages can be swiped with a finger; tiles are
  moved only in **Arrange** mode (button with the move cross in the top bar; while
  arranging it shows a check mark, tapping it ends the mode).
  Buttons that only show an icon with a tooltip on the PC (timeline, CAN)
  carry a short text in touch mode. The overview wraps at the screen width
  and grows downwards instead of scrolling sideways; there it is always compact
  (slightly larger, no toggle). The battery simulator
  shows its cells as a 2 × 7 grid (1–7 left, 8–14 right); tapping a cell
  setpoint opens the keypad, cells that are off are shown in grey. In the
  timeline the time window is chosen with buttons (30 s … 30 min) and
  signals for a chart from a list you can swipe. Dialogs are never larger
  than the screen, file dialogs use all of it, and the plot of a capture
  has a "Close" button. The bar at the top shows a short chip per device
  (✓ connected, ✕ disconnected, Ⓗ manual control); tapping it lists all
  devices with their full names. As long as the checkbox has
  never been set, LabControl turns the profile on by itself on screens up to
  1024 × 600. Detaching the dashboard is not available in touch mode.
- **Desktop notification on run end/error**: see section 3.
- **Individual panel background colors**: globally enables/disables
  panel color selection in the "Control" tab (see sections 1/2). The
  first time it is enabled, every already-known device automatically
  gets its own color.
- **Language**: switches the interface language immediately, without
  restarting.
- **Network drive (log and data)**: if a folder is entered (e.g.
  `\\nas\labor` on the PC or `/mnt/labor` on the Raspberry Pi), LabControl
  stores everything it produces during operation there, for later analysis:

  | Location on the network drive | Content |
  |---|---|
  | `<computer name>/labdash.log`, `share_access.log` | Log and access log |
  | `<computer name>/captures/` | Oscilloscope captures (CSV) |
  | `<computer name>/reports/` | Run reports (HTML, PDF) |
  | `<computer name>/recordings/` | Timeline recordings (CSV, MF4) |
  | `testcases/`, `blocks/` | Test sequences and blocks, shared by all computers |

  Captures and reports are first written locally and moved about a minute
  later, so a hanging network drive never blocks operation. If the drive
  cannot be reached, everything stays local and is moved over once it is
  back (including older files from before the setting). At most 200 MB stay
  local; beyond that the oldest files not yet moved are deleted (noted in
  the log). The log keeps writing locally to `labdash.log` and retries every
  minute. Save dialogs and the folders for test sequences and blocks point
  to the network drive when it is reachable. The lines below the field show
  where data currently goes and how many files are waiting. On the
  Raspberry Pi a folder on the SD card does not count as a network drive
  (nor does an empty, unmounted mount point). The environment variable
  `LABCONTROL_NETWORK_DIR` takes precedence over this field. Without an
  entry everything stays local as before.
- **Delete disconnected devices**: forgets only the devices that are
  currently not connected (greyed-out tiles), including names, safety
  limits, panel colors and network sharing. Connected devices stay
  unchanged. A forgotten device that is plugged in again later shows up
  with a new default name.
- **Delete all devices**: resets stored device names, safety limits,
  panel colors and network sharing for all devices to their defaults;
  connected devices get new default names, disconnected ones disappear.
  Both buttons ask for confirmation and cannot be undone.
- **Requirements register**: files with requirements, several separated by
  `;` – StrictDoc (`.sdoc`, e.g. neXtBSW's `ssr.sdoc`) or a CSV/Markdown
  table (columns ID and title). The "Requirements" field of the check dialog
  completes from it; the report shows the titles and a verdict per
  requirement: passed only if at least one check ran and all passed;
  without a check that ran, "not tested". `python lab_gui/main.py coverage
  --csv coverage.csv` summarises all runs (per requirement the verdict of the
  latest run).
- **Fault box** ("Devices" tab): faults as a table with name, switching
  sequence, settle time and description. The sequence is a shorthand:
  `R1=1` relay 1 on, `O3=1` digital output 3 on, `A1=1500` analog output 1 at
  1500 mV, several separated by commas. Setting follows this order, clearing
  the reverse (relays/outputs off, analog output 0 mV). The settle time applies
  between clearing and setting when a sequence switches faults. Check button:
  check and save (`faultbox.json` in the shared folder).
- **Device under test (UDS)** ("Devices" tab): the ECU LabControl talks to
  over UDS. **ODX/PDX** is the neXtBSW export
  (`python -m tools.diaggen pdx <project> -o <file>.pdx`); it provides CAN IDs,
  DIDs with units, fault texts and routines. **CAN interface** "automatic"
  takes the only connected one. **Key** is the name of the file
  `<shared folder>/keys/<name>.key` with the master key (64 hex digits) for
  unlocking. In simulation mode without an entry, LabControl talks to a
  simulated ECU on the simulated bus.
- **Measuring instruments** ("Devices" tab): per device the type, serial
  number and firmware as reported when connecting (the HCS power supply has
  no firmware query), plus inventory number, calibration date and due date to
  fill in (date as YYYY-MM-DD). Stored in `messmittel.json` in the shared
  folder, so PC and Pi see the same data. A run's report lists the
  instruments used and warns if a calibration has expired; the run still
  counts as passed.
- **Safety limits (watchdog)**: see section 6.
- **Network sharing and remote control**: see below.
- **Help** (question mark): opens this user manual.

### “Network” sub-tab: sharing tiles on the local network

This makes **individual dashboard tiles** visible to other devices on the
local network — for example a small ESP32 display in the lab, or a browser
on your phone. For **display**, the “Read” share is enough; **remote
control** additionally needs “Control”, and the caller must be allowed to control
(see “Remote control” below).

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
`share_access.log` next to the program, or `<computer name>/share_access.log`
on the network drive if one is set. Deliberately separate from
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
   is possible for load, power supply, microHIL, function generator and battery
   simulator; CAN and
   oscilloscope cannot be remote-controlled (for the oscilloscope see “Measure” below).
3. The caller **may control**. There are exactly three:
   - the paired link partner (see “Link”),
   - programs on the same computer, as long as “Claude (MCP) may control” is ticked,
   - **paired programs** with their own key (see below).

   Anyone with just the general token (e.g. an ESP32 display) may read but not
   control. The former master switch with a time window no longer exists since
   0.57.0.
4. **No test sequence** is running and the **safety trip** has not fired.
   Otherwise every control command is rejected (reading stays possible).
5. Action, value and channel are valid — the value ranges are those of the
   test editor — and the setpoint is **not above an active safety limit** of
   this device (section 6).

**Claude (MCP) may control.** Programs on the same computer as LabControl —
such as the MCP server — may control as long as **“Claude (MCP) may control”**
is ticked (default, directly below “Enable sharing on the local network”). The
token, “Control” for the device, value ranges, limits and the locks during a test
run and after a safety trip still apply. Without the tick Claude may only read.
The **“MCP”** chip in the bar at the top shows the state: green = Claude may
control, grey = read only. “Local” is detected from the connection (loopback or
the computer's own address), not from anything the caller states.

**Pairing programs.** A script or an MCP server on another computer pairs like the
link partner: `python tools/labcontrol_pair.py ADDRESS "Program name"` shows a
six-digit code, LabControl asks “Program “Name” (address) wants to pair and
control” with the same code, and if the codes match you tap **“Codes match –
pair”**. The program then receives its **own key** once; it uses it like the token
(header `Authorization: Bearer …` only), LabControl stores only a fingerprint. With
the key the program may read and control; the share list (“Read”, “Control”) and
all locks still apply, and every command is logged with the program name.
**Settings → Network → “Paired programs”** lists all programs with address and
date; the bin button removes a pairing, the key stops working immediately.

**Emergency stop.** The action “All outputs off” is the only exception: it
requires only the token and always works — even for callers that may not control,
during a test sequence and after a safety trip. It acts like the “ALL OFF”
button.

In addition, at most two commands are in flight at once, and fast command
sequences are throttled so that a backlog cannot delay the emergency stop.
Every control command **and every rejection** is written to `labdash.log` with
the caller's address.

Every operation on the devices themselves is logged there too, one line per
operation on power supply, load, function generator, microHIL and battery
simulator: device, action, value, channel, source (`Kachel` = tile, `Preset`
or `Testablauf` = test sequence) and result, e.g.
`Netzteil psu:…: PSU_VOLT (wert=12.0, kanal=0) per Kachel -> ok`. On `FEHLER`
(error) the reason follows in square brackets. Pure read actions of a test
sequence (inputs, cell voltages) are not logged.

On the HCS-34xx power supply, `PSU_OUT_ON`/`PSU_OUT_OFF` really switch the
output; voltage and current limit stay unchanged. `PSU_CURR` only sets the
current limit and switches nothing. The ON/OFF switch on the
“Control” tab and (since 0.56.0) its setpoint fields follow a remote action,
except a field someone is typing in.

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
(read status, list devices, read values, perform an action, emergency stop, and
measure with the oscilloscope). Setup and security model are in
`labcontrol_mcp/README.md`. It runs as a separate process and can do no more than
the interface above allows — in particular it cannot grant itself control. If it
runs on another computer, pair it as a program (see above) and use its key as the
token.

**Measuring with the oscilloscope over the network.** With **“Measure”** ticked
for the oscilloscope, an assistant may set up and evaluate acquisitions through
the MCP server (channels, range, timebase, trigger; readings such as frequency,
duty cycle, rise time). Since nothing is switched, “Measure” needs no control
permission, but it does need the token and “Read”. While an acquisition runs
or ran recently, LabControl keeps the oscilloscope open — the tile then shows
**“Connected (MCP)”**. It disconnects by itself 60 seconds after the last
acquisition; **“Disconnect”** on the tile or **“Open PicoScope 7”** release it
immediately. After an acquisition the tile shows its time and readings;
**“Plot”** opens it as an image — so you can see what the assistant measured.
A test run with oscilloscope steps takes precedence: on start, a
connection for acquisitions is closed, and no new ones are accepted until the run
ends. Every acquisition is also saved as a CSV file in the `captures` folder next
to the program.

**Link with a second LabControl (e.g. Raspberry Pi and PC).** At the bottom of
the "Network" sub-tab, under **"Linked setup"**, enter the partner address
(e.g. `192.168.178.181:8420`) and the partner token and tick **"Enable link"**.
On both computers the share must run for **all devices on the local network**.
The link can be operated equally from both computers: the paired partner sees
and operates **all** devices of the other computer, regardless of its share
list. The share list still applies to all other access and to Claude (MCP):
Claude only gets partner devices that are shared there for reading or control.
The partner's devices then appear in the
**overview and control view** with the same tiles as your own devices and the
name they have at the partner; the only difference is the network icon behind
the tile title (tooltip: computer name). What does not work over the link
(manual mode, OVP/OCP and memory slots of the power supply, PWM and 12 V outputs
of the microHIL, the battery simulator's limit, sending CAN frames) is missing
from the partner tile; the partner's CAN bus is shown in the overview only.
**Both computers show the same:** a tile's fields (voltage setpoint, current
limit, load mode and setpoint, function generator, cell values) follow the
device, no matter on which computer, via Claude or in a test sequence it was
operated; a field someone is typing in is left alone. Name and tile colour belong
to the computer the device is plugged into. Renaming or recolouring a partner
tile changes them there, and both computers show the result. Each computer keeps
its own tile arrangement.
The paired partner may always control there: pairing, with the same code
confirmed on both computers, is the permission. It is recognised by the link
header **and** the configured partner address. Operating works as long as no
test run or safety shutdown is active at the partner; otherwise the tile
is locked (greyed out), the link chip at the top turns yellow and its tooltip
gives the reason. If the partner rejects an operation, the reason briefly
appears at the tile. Every device belongs to the computer it is plugged into:
limits, manual control and the safety shutdown are always checked there.

**Pair instead of typing tokens.** The easiest way is to enter only the other
computer's address and press **"Pair…"**. Both computers then show the same
six-digit code; the other computer shows "Pairing request" – if the code
matches, tap **"Pair"** there. Both then exchange their tokens, enter each other
as partner and switch the link on; nothing has to be typed on the Pi. The
request is valid for 60 seconds. "Decline" or "Cancel" discard it and nothing
changes. Requirement: the share for all devices on the local network runs on the
computer that is asked. So that the partner can also reach the asking computer
(ALL OFF in both directions), it needs the network share as well – otherwise a
notice reminds you. Which devices may be read and controlled is still set by
each computer in its own share list.

**In a link, ALL OFF acts on both computers**: the button, ALL OFF via MCP or
the network and a safety shutdown also switch everything off at the partner.
Stopping a test sequence or a failed step only affects the own computer. The
bar at the top shows the link as a chip "⇄ Pi" (connected) or "✕ Pi"
(disconnected). Closing the app switches nothing off at the partner. ALL OFF
also aborts a running test sequence, so that its next step does not switch the
outputs back on.

**Shared test sequences.** In the test sequence editor the partner's devices
appear in the device selection ("Pi · Power supply 1 (PSU)") next to the own
ones; conditions and checks can use their readings (PSU, load, microHIL; about
once per second). The computer that starts the sequence runs it (control
station). On start it **reserves** the partner devices the sequence operates:
at the partner their tiles show "reserved by <computer> · <sequence>" and are
locked (manual control, ALL OFF and the safety shutdown stay available), and
operations from anywhere else are rejected. The control station reports every
second; **if that stops for 3 seconds** (network gone, crash), the partner
switches the reserved devices off and the control station aborts the sequence.
After the sequence ends the reservation is lifted. Connect Pi and PC by cable,
not via Wi-Fi. The link carries the operations of the partner tile (setpoints,
outputs, channels), but not arbitrary waveforms, read steps or CAN messages –
such sequences do not start and say why. Files are saved with computer names
("@raspberrypi/psu:0001"), so the same file means the same devices on both
computers.

**Recording in a link.** The control station also records the partner's device
values (PSU, load, microHIL): they can be selected in the timeline and end up
in the recording (CSV/MF4) and the report, about once per second with the time
of arrival. Both clocks should run on NTP (Pi: systemd-timesyncd, PC: Windows
time service); if they differ by more than a second, the link chip shows "⚠"
and a hint. Fast events belong to the oscilloscope.

**Claude (MCP) in a link.** The MCP server stays pointed at the PC. The PC's
share additionally shows Claude the partner devices as `@raspberrypi/psu:0001`
and forwards commands for them to the partner. This needs “Claude (MCP) may
control” ticked at the PC; the partner needs nothing else.
Before the first command the PC reserves the device at the partner ("reserved
by PC · Claude (MCP)") and reports every second; if that stops for 3 seconds,
the partner switches the device off. The reservation ends after 10 minutes
without a command from Claude, on ALL OFF, a safety shutdown, the start of a
test sequence or when the link is changed; nothing is switched off then. Only
callers on the same computer get the partner devices.

**Battery simulator: one sender only.** If Pi and PC share a CAN bus, only one
of them may control the battery simulator (message 0x130, normally the Pi).
LabControl listens for 0x130: if another computer is already sending it,
"Remote control ON" is rejected and the tile shows "another computer is sending
0x130". Sending 0x130 by hand or from a CAN step is blocked while a battery
simulator is attached to the bus or the link partner runs one.

**LAN oscilloscope.** A network oscilloscope belongs to one computer. If the
same address is configured on both, the link chip and the settings point it
out – remove it on one of them.

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
- The safety status in the bar at the top (chip with shield icon) shows **OFF** (no limits active),
  **ARMED** (monitored, everything within range), or **TRIGGERED**
  (limit exceeded, outputs switched off).
- During a testcase run, the watchdog additionally monitors the
  connection of the involved devices — if the connection drops or a
  device fails to deliver a fresh measurement for too long, it also
  shuts down immediately.
- The **"ALL OFF" button** at the far right of the bar at the top manually
  switches off all outputs immediately at any time, independent of the
  watchdog state.
- Remote-control commands over the network (section 5) are rejected while
  the alarm is tripped or a test sequence is running, and a setpoint above
  an active limit is not applied at all. “All outputs off” remains possible.
- **Power supply/load interlock** (Settings → Safety): pairs of a power
  supply and a load connected to the same point. In a test sequence,
  "Output ON" is rejected while the other device of the pair is on or its
  state is unknown; the run then ends with an error and ALL OFF. A sequence
  can lift this in "Test sequence properties…" (source/sink). Tiles, presets
  and the network are not interlocked. Only devices of this computer count.

---

## 7. Bar at the top

In the bar at the top of the window, the safety status comes first as a
chip with a shield icon (see section 6), then the link chip and the "MCP"
chip (section 5), followed by one chip per known
device with icon and short name: ✓ connected (green), ✕ disconnected
(red), Ⓗ manual control (orange), e.g. "✓ PSU1". The full name is in the
tooltip; clicking the chips opens "Device status" with all devices. The
connection is retried automatically every 3 seconds as long as a device
is unreachable. The full-screen button and the "ALL OFF" button follow on
the right. App messages appear briefly as a notice at the bottom of the
window.

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

---

## 9. Command line (without a window)

For CI and nightly runs, LabControl runs a test sequence without a window:

```
python lab_gui/main.py run sequence.json --report report.html --junit result.xml --json run-result.json
LabControl.exe run sequence.json --junit result.xml
python lab_gui/main.py devices
```

- `--var name=value` presets run variables (repeatable), `--simulation`
  uses simulated devices, `--wait-devices S` waits up to S seconds for the
  sequence's devices (default 15), `--timeout S` aborts the run after S
  seconds.
- **Exit code:** 0 passed, 1 failed, 2 aborted, error or safety shutdown,
  3 configuration or devices missing, 130 interrupted (Ctrl+C).
- At the end LabControl always switches ALL OFF and releases the power
  supplies to manual control at 1 V / 0 A, as when closing the window.
- If LabControl is already running (e.g. the kiosk on the Pi), the command
  line starts the run through the running instance (otherwise two programs
  would open the same devices). This needs network sharing and "Claude (MCP)
  may control" switched on there, and no unsaved changes in its editor; the
  sequence then appears in the instance's editor.
- Other programs and Claude start sequences through the network interface
  (`POST /api/v1/runs` with the name of a sequence in the shared folder,
  state at `/api/v1/runs/<id>`, results at `…/result`, `…/junit`,
  `…/report`; MCP: `run_testcase`, `get_run`, `stop_run`). Every run, also
  from the window, stores `run_<id>.json/.xml/.html` in the reports folder.
- Nightly run on the Pi: `tools/pi/nightly_pi.sh` (see `tools/pi/README.md`).
- Not yet without a window: devices of a linked partner and J-Link steps.
- The `.exe` is a windowed program: it prints nothing to the console, and
  `cmd` only waits for it with `start /wait LabControl.exe run …`. The result
  is in the exit code and the files.
