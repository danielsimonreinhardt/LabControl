**LabControl 1.0.0 – first release.** One interface for hobbyist lab equipment, test sequences and an ECU under
test, on a Windows PC and on a Raspberry Pi with a touch display.

📖 Documentation (German for now): https://danielsimonreinhardt.github.io/LabControl/ – the user interface can be
switched to English (*Einstellungen → Allgemein → Sprache*).

### Download

`LabControl_v1.0.0.exe` below – a single file for Windows 10/11, no installation needed. To try it without any
hardware, enable *Einstellungen → Allgemein → Simulationsmodus* (a “SIMULATION” chip then shows at the top).
Raspberry Pi: see *Raspberry Pi und Verbund* in the documentation.

### What's in it

- **Devices** – MANSON HCS-3xxx power supplies, KORAD KEL102 load, JDS66xx function generator, PicoScope 2000,
  UNI-T MSO1000HD (SCPI over LAN), microHIL I/O box, J-Link, CAN interfaces (Vector, PCAN, SLCAN, SocketCAN), a
  self-built battery simulator. Dashboard, presets, recording to CSV/MF4.
- **Test sequences** – loops and conditions, checks with limits and requirement IDs, linked blocks, voltage
  profiles and ramps, timing checks, variants and parameter sets, debugging with breakpoints, HTML/PDF reports,
  JUnit and JSON results, result history with trends.
- **ECU under test** – UDS from ODX/PDX, XCP from A2L, flashing over CAN (canflash), rest-bus from DBC with AUTOSAR
  E2E profiles 1, 2 and 11.
- **Automation** – `labcontrol run|flash|history|coverage|timing` with exit codes for CI, REST API, MCP server for
  Claude, nightly runs on the Pi.
- **Safety** – limits and watchdog per device, ALL OFF across PC and Pi, interlock between power supply and load,
  remote control only when explicitly allowed.

### Known limitations

- Documentation is in German; an English version will follow.
- Flashing over CAN works with Vector interfaces; on the Raspberry Pi (SocketCAN) it needs an extension of canflash.
- Some features are verified in simulation only so far – the [CHANGELOG](https://github.com/danielsimonreinhardt/LabControl/blob/master/CHANGELOG.md)
  (German) records per change what was tested on real hardware.
