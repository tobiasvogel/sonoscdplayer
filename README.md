# SonosCDPlayer

**Your CDs, straight to Sonos.**

SonosCDPlayer is an open-source DIY project for building a standalone audio CD player that streams to Sonos speakers over a local network. The goal is to combine a familiar, physical CD-player experience with network playback: insert a disc, use dedicated playback buttons and a compact display, and listen through your Sonos system without needing a smartphone or separate app for everyday operation.

The design is intended to use a Raspberry Pi (or a comparable single-board computer), a standard optical drive, and readily available off-the-shelf parts. Ethernet and Wi-Fi are both potential network options; a wired connection may be preferable where available.

> **Project status:** Early-stage design and development. This repository currently documents the concept and planned software. Do not treat the features below as an existing, tested release or a complete build guide.

## The idea

- **Build it yourself:** This is a community-driven DIY project, not a commercial CD-player product.
- **Physical operation:** Playback buttons, a display, and optionally a rotary push encoder allow normal operation without an external app.
- **Local-network audio:** A player running on the single-board computer reads an audio CD and makes its audio available for playback on a selected Sonos speaker or group.
- **Off-the-shelf hardware:** Reuse ordinary optical drives, boards, and controls wherever practical.
- **Adaptable builds:** Encourage different enclosures, 3D-printed designs, and sheet-metal constructions. GPIO button polarity/pinout, rotary controls, display drivers and resolutions are intended to be configurable without code edits.
- **Built on open-source work:** Reuse suitable existing libraries and acknowledge their authors and licenses.

## Intended user experience

1. Power on the player and connect it to the local network. On a supported unconfigured device, a temporary Wi-Fi setup hotspot and browser portal are planned for first-time provisioning.
2. Choose a Sonos playback target using the device's controls (after initial configuration, if required).
3. Insert an audio CD.
4. Press Play, Pause, Next, or Previous on the player; see playback information on the display.
5. Listen through the selected Sonos speaker or group.

The exact discovery, playback, buffering, and track-change behavior must be validated against real Sonos devices before these steps can be documented as supported functionality.

## Proposed hardware

| Component | Role |
| --- | --- |
| Raspberry Pi or compatible SBC | Runs the player and network services |
| Compatible CD/DVD optical drive | Reads Red Book audio CDs |
| Small OLED or eInk display | Shows disc/track, state, and selected Sonos target |
| Physical buttons | Play/pause, stop, next, previous |
| Rotary encoder with push action | Menu navigation and selection |
| Optional power and connect buttons | Device-level controls |
| Enclosure and power supply | Physical assembly |

This is a **conceptual parts list**, not a verified compatibility list. Power requirements, drive access, display wiring, and exact GPIO mappings remain to be specified and tested.

## How it works (planned)

```text
Audio CD -> Optical drive -> Disc reader / buffering
                                    |
                                    v
                          Local HTTP audio endpoint
                                    |
                                    v
                      Sonos playback target or group

Buttons / rotary encoder -> Player controller -> Display
                                     |
                                     +-> Sonos control / discovery
```

Sonos playback requires a suitable streaming and control integration. The specific protocols, supported formats, and group behavior are **design decisions requiring a hardware-backed proof of concept**, not yet a promise of compatibility with every Sonos product.

## Development roadmap

- [x] Prove reliable digital audio-CD reading on a target SBC and drive.
- [x] Prove playback on a real Sonos speaker using a locally hosted, Sonos-compatible stream.
- [x] Define buffering, timing, end-of-track handling, and error recovery.
- [x] Implement a playback state machine and physical controls.
- [ ] Add Sonos discovery, target selection, and group-aware behavior.
- [ ] Add a compact display and settings menu.
- [ ] Validate boot-time operation, unplug/reinsert events, and network interruptions.
- [ ] Implement secure temporary Wi-Fi hotspot provisioning, network handoff and recovery, with encoder entry as fallback.
- [ ] Publish a reproducible bill of materials and a first tested build guide.

The planned configuration options include an editable YAML file, a **local browser-based setup interface**, and on-device settings for everyday use. For first-time Wi-Fi setup, a **temporary, secured device-specific Wi-Fi hotspot** and local provisioning page are planned on supported hardware. Wi-Fi credentials can also be entered with the rotary encoder as a fallback. These are planned features, not currently available services.

## Contributing

Contributions are welcome: software, compatible hardware reports, wiring diagrams, enclosure models, build write-ups, and constructive testing feedback. For code, start with an issue describing the problem and proposed approach.

Project website: **https://sonoscdplayer.diy**  
GitHub: **https://github.com/tobiasvogel/sonoscdplayer**

## Disclaimer

SonosCDPlayer is an independent community project and is not affiliated with, endorsed by, or sponsored by Sonos, Inc. Sonos is a trademark of its respective owner.

Product-style images are **concept renderings**, not photographs of a verified reference build.
