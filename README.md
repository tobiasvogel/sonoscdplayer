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
- **CD metadata and artwork:** Read CD-TEXT, retrieve MusicBrainz metadata, and store metadata and cover images in a local cache.
- **Wi-Fi setup:** Configure a network on the device or through a temporary setup hotspot.
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

The reference build combines physical buttons and a rotary encoder on a custom PCB, but alternative control layouts are a design goal. The repository also contains 3D-printable enclosure parts; a complete, tested assembly guide is still in preparation.

Reference optical drives are the **ASUS SDRW-08U1MT**, the **Panasonic/Matshita BD-MLT UJ265** and the **LG HL-DT-ST DVDRAM GTC0N**, each confirmed with the drive check below. Idle spin-down and wake-up were first verified with the ASUS drive connected through a powered USB hub. The **TSSTcorp CDDVDW TS-U633J** was also evaluated but proved unstable in the tested setup. These results are not an exhaustive compatibility list. To test another drive, run `python3 tools/check_drive.py` with an audio CD inserted; see [Checking a CD drive](docs/DRIVE_CHECK.md).

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
| Volume + / − | Change the Sonos volume in steps of 1 (the whole group when grouped); hold to repeat. Works during the player's own playback, or before playback on a selected speaker that is not playing anything |
| Rotary encoder | Navigate the display interface |
| Encoder press | Confirm menu selections |

Physical playback buttons and disc ejection have been validated on reference hardware.

Volume buttons have been tested on reference hardware.

A Power button implementation provides a display-based power menu and long-press shutdown request. These system actions require explicit configuration.

**Important:** Hardware controls may run in diagnostic or preview modes unless their execution paths are enabled. A fresh installation does not automatically activate every available control function for now.

See [Power button](docs/POWER_BUTTON.md) for details.

## Sonos integration

SonosCDPlayer discovers available Sonos devices on the local network and provides a display-based interface for choosing a playback target.

Users can select an individual speaker or a combination of supported speakers. The player can create a temporary group for CD playback when the target devices are in an appropriate state. A disc-wide track queue enables navigation from the Sonos app; targeted hardware validation of the latest start and transition handling remains open.

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

To join a Wi-Fi network, use the device menu to select a network and enter its password, or hold CONNECT for 10 seconds to open a temporary setup hotspot with a web page. Both methods have been tested on reference hardware; additional failure and timeout scenarios remain open. See [Wi-Fi setup](docs/NETWORK_SETUP.md).

## Display and languages

SonosCDPlayer provides a display-based interface for playback information, device status, and menu navigation.

The current reference implementation uses an OLED display. Drivers and test tools for a smaller monochrome OLED (SH1107) and a color LCD (ST7735S) are included and await their connection to the player; a four-color e-paper module was evaluated and found unsuitable. See [Displays](docs/DISPLAYS.md).

The implemented interface includes playback and disc status, interactive menus, Sonos target selection, and rotary encoder navigation. English and German are available; the selected language is retained across restarts in local application state. Adaptive brightness is supported on suitable reference hardware.

CD-TEXT, MusicBrainz metadata and album artwork on the reference OLED are implemented and have been tested on hardware. Further interface improvements and metadata edge-case testing remain open.

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
- [x] Verify optical-drive idle spin-down and wake-up using a powered USB hub on reference hardware.
- [x] Implement and hardware-test CD-TEXT, MusicBrainz lookup, and local metadata and cover caching.
- [x] Implement and hardware-test device-based Wi-Fi selection and setup hotspot.

### Work in progress and planned features

- [ ] Complete hardware validation of remaining device controls and their integration.
- [ ] Validate optical-drive idle spin-down and wake-up over extended operation and on additional hardware.
- [ ] Complete boot, shutdown, restart, and long-term reliability testing.
- [ ] Validate remaining Wi-Fi setup failure, timeout, and recovery scenarios.
- [ ] Complete hardware tests for disc-wide Sonos navigation and remaining metadata edge cases.
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
| [Wi-Fi setup](docs/NETWORK_SETUP.md) | Joining a new Wi-Fi network through the setup hotspot |
| [Power button](docs/POWER_BUTTON.md) | Power controls, permissions, and activation |
| [Shutdown and restart screens](docs/SHUTDOWN_SCREENS.md) | OLED screens while shutting down or restarting, installation, optional exitrd |
| [Status LEDs](docs/STATUS_LEDS.md) | Meaning of the power and connection (network) LEDs |
| [Checking a CD drive](docs/DRIVE_CHECK.md) | Test whether a USB CD drive suits the player and share the result |
| [Displays](docs/DISPLAYS.md) | Supported and evaluated display modules; [SH1107](docs/SH1107.md) and [ST7735S](docs/ST7735S.md) details |
| [Releases and updates](docs/RELEASE.md) | Version number, making a release, updating the device |
| [Changelog](CHANGELOG.md) | Changes per version |

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
