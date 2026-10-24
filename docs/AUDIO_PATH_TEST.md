# SonosCDPlayer — Hardware and Audio Path Test Report

**Test date:** 2026-10-24  
**Status:** M0.1–M0.4 passed; M0.5 pending  
**Purpose:** Document verified baseline results for further development.

## Test environment

- **Host:** Raspberry Pi 4 Model B Rev 1.5, Debian GNU/Linux 13 (Trixie), 64-bit ARM.
- **Optical drive:** Samsung SE-S084 external USB DVD writer, USB ID `0e8d:1836`; drive reported as `CDDVDW TS-U633J`.
- **Linux device:** `usb-storage` driver, `/dev/sr0`.
- **Test disc:** CD-DA, 15 audio tracks. Lead-out at LSN 208313; all track start positions were readable, and ISRC values were reported.
- **HTTP test server:** Python `http.server`, TCP port `8765`, on the local network.
- **Sonos test target:** One idle speaker verified as the sole member and coordinator of its group (`STOPPED` before playback).
- **Sonos control:** SoCo in a dedicated Python virtual environment.

## Verified milestones

| ID | Test | Evidence | Result |
|---|---|---|---|
| **M0.1** | Detect USB optical drive and read audio CD TOC | `lsusb` detected `0e8d:1836`; `/dev/sr0` existed; `cd-info` identified CD-DA with 15 tracks. | **PASS** |
| **M0.2** | Extract CD audio | `cdparanoia -Q` returned a matching TOC; a roughly 10-second segment from track 1 (sectors 0–750) was extracted to WAV. `file` identified 16-bit stereo PCM at 44.1 kHz. Output size: 1,766,396 bytes. | **PASS** |
| **M0.3** | Serve the WAV file over HTTP | `curl -I` returned `HTTP/1.0 200 OK`, `Content-type: audio/x-wav`, and `Content-Length: 1766396`. | **PASS** (HTTP endpoint checked) |
| **M0.4** | Discover Sonos devices and play test audio | SoCo discovered six speakers; the selected target was independently confirmed to be a stopped, single-member group. `play_uri()` requested the test URL; the speaker issued `GET /track.wav` (HTTP 200), reported `PLAYING`, and audio was heard. | **PASS** |

**Example URL format:** `http://<PI_LAN_IP>:8765/track.wav` (use the actual local IP address; no fixed address is required).

## Observations and limitations

- During the WAV request, Python's basic HTTP server raised `BrokenPipeError: [Errno 32] Broken pipe`. Another `GET /track.wav` request followed, and playback was audible. **The cause of the disconnect has not been established.** A production server should handle early client disconnects without treating them as fatal application errors.
- Playback used **a WAV file extracted in advance**, not live audio streamed continuously from the CD drive.
- The temporary WAV file was stored under `/tmp`. Whether that path resided in RAM on the test installation was **not verified**. This test does **not** validate the planned RAM-only audio cache.
- Sustained reading, disc replacement, read-error recovery, buffer underruns, pause/seek/track transitions, metadata, and audio quality under sustained load remain **untested**.
- A Sonos transport state of `STOPPED` does not mean the previously selected media source is empty. Calling `play_uri()` replaced the test speaker's selected source. The test script did not regroup speakers or intentionally modify playback on other groups.
- M0.3 alone demonstrates that the Pi served an HTTP response; the later M0.4 test additionally confirms a Sonos client could fetch the file over the network.

## Open hardware issues

- **Delock 62626 USB-to-SATA adapter:** Not detected as a USB device on an initial Raspberry Pi 3 test system or on the Raspberry Pi 4 test host. Different USB cables and ports were tried. **The root cause is unknown; an adapter fault has not been established.** The direct USB DVD drive was used to unblock audio-path testing.
- **StarTech USB 3.0-to-SATA adapter:** Available as an alternative, but **not yet tested**.

## Next milestone: M0.5 — Live CD audio streaming

1. Implement a minimal HTTP test server that streams CD audio while reading it, **without first creating a complete WAV file**.
2. Use a bounded **RAM-only buffer**, measuring read rate, stream throughput, and buffer occupancy.
3. Handle client disconnects, repeated requests, and CD read failures predictably.
4. Start with one stopped, single-speaker Sonos group; do not reorganize groups or interrupt other playback.
5. Integrate with the main SonosCDPlayer architecture only after reproducible audible playback and sufficient diagnostics.

## Reproducing the baseline tests

With an audio CD inserted and appropriate tools installed:

```bash
lsusb
lsblk -o NAME,TYPE,SIZE,MODEL,TRAN
cd-info --no-analyze --no-device-info /dev/sr0
cdparanoia -d /dev/sr0 -Q
cdparanoia -d /dev/sr0 -w '1[0:00.00]-1[0:10.00]' /tmp/sonoscdplayer-test.wav
file /tmp/sonoscdplayer-test.wav
```

To serve the extracted file from a disposable local test directory:

```bash
mkdir -p /tmp/sonoscdplayer-http
cp /tmp/sonoscdplayer-test.wav /tmp/sonoscdplayer-http/track.wav
python3 -m http.server 8765 --bind 0.0.0.0 --directory /tmp/sonoscdplayer-http
```

In a second terminal:

```bash
curl -I http://127.0.0.1:8765/track.wav
```

> The built-in Python HTTP server is intended for development on a trusted local network, not for Internet exposure.

## Conclusion

The full **CD-DA → Raspberry Pi 4 → extracted WAV → HTTP → Sonos speaker** path has been demonstrated on physical hardware with audible output. **Direct, buffered live streaming from the optical drive is not yet demonstrated** and is the objective of M0.5.
