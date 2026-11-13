# SonosCDPlayer

**Your CDs, straight to Sonos.**

SonosCDPlayer is an open-source DIY project for building a standalone audio CD player that streams music to Sonos speakers over a local network.

It brings together the familiar experience of a physical CD player and the convenience of network audio: insert a disc, select your Sonos speakers, and control playback using dedicated buttons and a compact display.

The project uses readily available components, including a single-board computer, an optical drive, and configurable hardware controls. The long-term goal is a reliable, easy-to-use CD player that works without a smartphone or separate app during everyday operation.

## Project status

**Functional prototype — active development**

SonosCDPlayer is no longer just a concept. A working Python implementation exists, and its core audio, playback, Sonos, and hardware-interface functionality has been tested on real equipment.

Current capabilities include:

- **Live CD playback:** Read audio CDs and stream digital audio to Sonos over the local network.
- **Playback control:** Play, pause, resume, stop, next, and previous, including hardware-tested physical buttons.
- **Sonos integration:** Discover and select speakers, with controlled playback on individual devices and temporary speaker groups.
- **Display interface:** Show playback status, navigate menus, and select Sonos speakers using physical controls.
- **RAM-only audio buffering:** Stream CD audio using bounded memory without creating persistent audio copies.
- **Disc management:** Detect disc changes, handle verified USB drive reconnection, and eject discs using a physical button on tested hardware.
- **System services:** Run the player and hardware interfaces through local services with configurable behaviour.

These foundations are implemented, but not every feature is enabled by default or validated across different hardware combinations. First-time setup, some device controls, broader hardware compatibility, and long-term reliability still require additional work.

**This is a working prototype, not yet a finished plug-and-play product or a complete beginner installation guide.**

## Design goals

- **Physical-first operation:** Dedicated controls and a display should make everyday playback feel like using a conventional CD player.
- **Local playback:** Play audio CDs through Sonos over the local network without requiring a cloud music service.
- **No ripping required:** Listen to physical CDs without first building a digital music library.
- **Respect existing Sonos playback:** Avoid unexpectedly interrupting other sources or changing unrelated speaker groups.
- **Accessible DIY hardware:** Use standard components and support different enclosure designs and hardware configurations.
- **Simple, maintainable software:** Prefer practical solutions, clear component boundaries, and predictable behaviour.

SonosCDPlayer is intended to be a dedicated CD player, not a general-purpose Sonos management system.

## How it works

The player reads digital audio from an inserted CD, buffers it in memory, and serves it as a WAV-compatible HTTP audio stream over the local network. The selected Sonos speaker or group retrieves this stream directly from the player.

```text
Audio CD -> Optical drive -> CD-DA reader -> Bounded RAM buffer
                                               |
                                               v
                                         Local HTTP server
                                               |
                                               v
                                         Sonos playback

Physical controls -> Player controller -> Display UI and menus
                              |
                              +-> Disc and drive management
                              |
                              +-> Sonos discovery and control
```

The player controller coordinates playback state, track navigation, disc handling, and Sonos control. Local interfaces allow hardware components to communicate with the player.

Audio is streamed as 44.1 kHz, 16-bit stereo PCM in a WAV-compatible format. The implementation includes buffering and HTTP range handling. Normal playback does not create persistent CD audio files.

## Hardware

SonosCDPlayer is designed around readily available components so builders can choose hardware that suits their needs and enclosure designs.

The software is developed and tested primarily on a Raspberry Pi 4 reference setup, while the architecture aims to accommodate different configurations.

A typical build consists of:

| Component | Purpose |
| --- | --- |
| Single-board computer (SBC), such as a Raspberry Pi | Runs the player software and network services |
| CD/DVD optical drive | Reads standard audio CDs |
| Sonos speaker or group | Receives and plays the CD audio stream |
| Display module | Shows playback information, device status, and menus |
| Physical controls (buttons and/or rotary encoder) | Control playback, navigate menus, and select options |
| Status LEDs (optional) | Provide additional visual feedback |
| Ambient light sensor (optional) | Enables automatic display and LED brightness adjustment |
| Power supply and enclosure | Complete the physical player |

### Hardware compatibility

The reference build combines physical buttons and a rotary encoder on a custom PCB, but alternative control layouts are a design goal.

Audio playback and drive handling have been tested with multiple USB optical drives, including the **ASUS SDRW-08U1MT** and **TSSTcorp CDDVDW TS-U633J**. Tested functionality may vary by setup; the examples are not an exhaustive compatibility list.

The [SonosCDPlayer website](https://sonoscdplayer.diy) is intended to collect tested components, hardware configurations, and community builds over time.

The goal is to support different builds, from simple functional prototypes to custom Hi-Fi components. Support for alternative SBCs, displays, and interfaces remains subject to implementation and testing.

## Playback and controls

SonosCDPlayer supports conventional CD playback controls through configurable physical input devices. The reference implementation combines dedicated buttons and a rotary encoder.

| Control | Behaviour |
| --- | --- |
| Play / Pause | Start, pause, or resume CD playback |
| Stop | Stop the player's CD playback |
| Next | Select or play the next track |
| Previous | Select or play the previous track |
| Eject | Open the drive tray when the required safety conditions are met |
| Rotary encoder | Navigate the display interface |
| Encoder press | Confirm menu selections |

Physical playback buttons and disc ejection have been validated on reference hardware.

A Power button implementation provides a display-based power menu and long-press shutdown request. These system actions require explicit configuration.

**Important:** Hardware controls may run in diagnostic or preview modes unless their execution paths are enabled. A fresh installation does not automatically activate every available control function.

See [Power button](docs/POWER_BUTTON.md) for details.

## Sonos integration

SonosCDPlayer discovers available Sonos devices on the local network and provides a display-based interface for choosing a playback target.

Users can select an individual speaker or a combination of supported speakers. The player can create a temporary group for CD playback when the target devices are in an appropriate state.

Speaker selection and playback remain separate operations:

- Selecting speakers does not automatically start playback.
- The player checks the target state before taking control.
- Playback commands are restricted to the player's own active stream where appropriate.
- Unrelated playback sessions and groups must not be changed without explicit authorisation.
- Temporary groups created by the player are handled through controlled playback and cleanup operations.

Individual-speaker playback and controlled group operation have been tested on real Sonos hardware. Compatibility with every Sonos model, firmware version, and network environment has not been established.

## Configuration

SonosCDPlayer uses **TOML configuration files** for software and hardware settings.

| File | Purpose |
| --- | --- |
| [`config/player.example.toml`](config/player.example.toml) | Audio streaming, optical drive management, and service settings |
| [`config/interface.example.toml`](config/interface.example.toml) | GPIO buttons, rotary encoder, and input behaviour |
| [`config/ui.example.toml`](config/ui.example.toml) | Display preferences and interface language |

The active configuration lives in **`/etc/sonoscdplayer/`** (`player.toml`, `interface.toml`, `ui.toml`); all services and tools read it from there. The repository files above are templates only. `tools/install_config.py` creates the files from the templates or migrates an existing configuration. See [Configuration files and location](docs/CONFIGURATION.md).

A configuration update utility offers dry-run checks, structural validation against an optional reference, backup creation, and rollback support. Perform service-affecting updates while the player is idle. See [Updating the player configuration](docs/CONFIG_UPDATE.md).

Guided first-time network setup, including a temporary Wi-Fi setup hotspot and browser-based configuration, is planned but is not yet a complete setup workflow.

## Display and languages

SonosCDPlayer provides a display-based interface for playback information, device status, and menu navigation.

The current reference implementation uses an OLED display. Support for other display types, resolutions, and visual themes is a future goal.

The implemented interface includes playback and disc status, interactive menus, Sonos target selection, and rotary encoder navigation. English and German are available, with an initial language set in TOML and session-only switching from the display menu. Adaptive brightness is supported on suitable reference hardware.

Richer album metadata, cover artwork, and further interface improvements are planned.

See [Display languages and translations](docs/I18N.md) for details.

## Development roadmap

The roadmap distinguishes working foundations from features still under development or awaiting broader validation.

### Working foundations

- [x] Detect audio CDs and read track information on reference hardware.
- [x] Stream live CD audio to a real Sonos device.
- [x] Implement bounded RAM-only audio buffering.
- [x] Implement playback state management and track navigation.
- [x] Provide a persistent player service and local control interface.
- [x] Implement Sonos discovery, target selection, and controlled temporary group playback.
- [x] Implement an interactive display interface with playback status and menus, validated on reference OLED hardware.
- [x] Implement and hardware-test physical playback buttons.
- [x] Detect disc changes and handle verified optical drive reconnection.
- [x] Implement and hardware-test physical disc ejection on reference hardware.

### Work in progress and planned features

- [ ] Complete hardware validation of remaining device controls and their integration.
- [ ] Validate optical drive idle spin-down and wake-up behaviour on reference and additional hardware.
- [ ] Complete boot, shutdown, restart, and long-term reliability testing.
- [ ] Develop guided first-time network setup and recovery.
- [ ] Add richer CD metadata, album artwork, and related display features.
- [ ] Expand and validate support for additional hardware configurations and interchangeable interfaces.
- [ ] Publish reproducible installation instructions and a hardware compatibility guide.
- [ ] Provide a complete bill of materials, wiring documentation, and a tested build guide.

Completed items represent implemented and tested foundations, not universal hardware compatibility or completion of every related enhancement.

## Documentation

Additional documentation is available in the [`docs/`](docs/) directory.

| Document | Description |
| --- | --- |
| [Configuration files and location](docs/CONFIGURATION.md) | Standard location, installation, migration, and systemd units |
| [Configuration updates](docs/CONFIG_UPDATE.md) | TOML configuration checks, replacement, and rollback |
| [Display languages and translations](docs/I18N.md) | Language selection and adding translations |
| [Power button](docs/POWER_BUTTON.md) | Power controls, permissions, and activation |
| [Optional OLED shutdown screen](docs/EXPERIMENTAL_SHUTDOWN.md) | Late-shutdown display and installation |

Some features are optional or hardware-specific. Consult each guide for its requirements and limitations.

## Contributing

Contributions are welcome, including software improvements, hardware compatibility reports, testing feedback, translations, wiring diagrams, documentation, and enclosure designs.

For substantial code changes, please open an issue describing the proposed improvement or problem before beginning work. The project favours maintainable implementations and incremental, hardware-backed testing.

Do not commit credentials, private network information, personal Sonos device identifiers, local configuration files containing private details, or copyrighted CD audio recordings.

## License

See [LICENSE](LICENSE) for the repository's GNU General Public License version 2 license text.

## Project links

**Website:** https://sonoscdplayer.diy  
**GitHub:** https://github.com/tobiasvogel/sonoscdplayer

## Disclaimer

SonosCDPlayer is an independent open-source community project and is not affiliated with, endorsed by, or sponsored by Sonos, Inc. Sonos is a trademark of its respective owner.

Hardware compatibility depends on the components, software versions, and network environment used. Not every configuration has been tested.

Product-style illustrations and renderings are conceptual unless explicitly identified as photographs of a tested build.
