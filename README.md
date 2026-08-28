# LabControl

**LabControl** is a unified control platform for hobbyist lab equipment.

As an electronics hobbyist, I've accumulated a growing collection of lab gear — several power supplies, an electronic load, and more devices on the way. Most of this equipment can technically be remote-controlled over USB or Ethernet, but the software that ships with it is usually buggy, clunky, and barely usable — and controlling multiple devices together simply isn't supported at all.

LabControl aims to fix that by providing a single, reliable platform to control all your lab instruments — regardless of manufacturer — from one place, making it possible to coordinate multiple devices together instead of juggling a pile of incompatible, unreliable vendor tools.

## Features

- **Unified control** — manage power supplies, electronic loads, and other bench equipment through one consistent interface instead of a separate app per device.
- **Multi-device coordination** — drive several instruments together (e.g. sync a power supply and a load for automated test sequences), something vendor software typically can't do at all.
- **Vendor-agnostic** — designed to support equipment from different manufacturers side by side, not locked to a single brand's ecosystem.
- **Extensible** — new device types and protocols (USB, Ethernet, SCPI, …) can be added as drivers rather than requiring a rewrite.

## Status

LabControl is an early-stage hobby project, currently in active development. The core architecture and initial device drivers are being built out; expect rough edges and breaking changes.

## Installation

A new release is compiled after every master-push. Just download and run the .exe


## Supported Devices

- KORAD KEL102 electronic load
- MANSON HCS-3304 USB (all versions of the HCS family should work)

## Contributing

Contributions, ideas, and bug reports are welcome! If you own lab equipment that isn't supported yet, feel free to open an issue — especially if you're willing to help test a driver for it.
