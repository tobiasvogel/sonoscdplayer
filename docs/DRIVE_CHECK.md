# Checking a CD drive

Not every USB CD drive suits the player. `tools/check_drive.py` tests a drive
with the same commands the player uses and says whether it is suitable. It
takes about two minutes and needs no Sonos speaker.

## Running the check

You need the packages from `scripts/packages.lst` (at least `cdparanoia`,
`libcdio-utils`, `sg3-utils`; `python3-libdiscid` for the MusicBrainz check)
and an **audio CD**, ideally one with CD-TEXT. A scratched CD can make a good
drive look bad.

1. Insert the audio CD and wait until the drive is quiet.
2. If SonosCDPlayer is installed, stop the player so that it does not use the
   drive at the same time:
   ```sh
   sudo systemctl stop sonoscdplayer-player.service
   ```
3. From the repository folder:
   ```sh
   python3 tools/check_drive.py
   ```
   Your user needs to be in the group `cdrom`
   (`sudo adduser $USER cdrom`, then log in again).
4. At the end the CD is ejected. Use `--no-eject` to keep it in; the eject
   check is then skipped. `--device /dev/sr1` picks another drive, `--json`
   prints the report as JSON.
5. Start the player again if you stopped it:
   ```sh
   sudo systemctl start sonoscdplayer-player.service
   ```

## Tested drives

| Drive | Read speed at disc start | Wake-up until audio | Result |
|---|---|---|---|
| ASUS SDRW-08U1MT (firmware A103) | 2.7× | 8.0 s | SUITABLE |
| Panasonic/Matshita BD-MLT UJ265 (firmware 1.01) | 1.4× | 8.6 s | SUITABLE |
| LG HL-DT-ST DVDRAM GTC0N (firmware 1.00) | 1.3× | 10.4 s | SUITABLE; cd-info needs up to about 20 s for CD-TEXT |

All three are reference drives of the project (tested with drive check
0.9.0; read speed limits as of today). The TSSTcorp CDDVDW TS-U633J was
unstable in an earlier setup.

## The checks

| Check | What it tests | Result |
|---|---|---|
| Disc in the drive | The drive reports an inserted disc | FAIL without a disc |
| Audio CD table of contents | cdparanoia reads the track list | FAIL if unreadable |
| Recognized again after a USB reconnect | USB vendor, product and serial number are readable | WARN without a serial number: after a USB reconnect the player waits for a restart instead of continuing |
| CD-TEXT | Titles stored on the CD can be read | SKIP if the CD has none; WARN if reading fails (titles then come only from MusicBrainz) |
| MusicBrainz disc ID | The disc ID can be computed from the track list | SKIP without `python3-libdiscid` |
| Audio read speed | 20 s of audio from the start of the disc, the slowest part | FAIL below 1.1× playback speed, WARN below 1.25×. Slim drives read the start of a disc at about 1.3×, which is enough |
| Jump to the end of the disc | The last 10 s of the last track | FAIL if not readable |
| Same audio on a second read | 5 s from the middle of the longest track, read twice | WARN if the reads differ |
| Spin down when idle, wake up for PLAY | `sg_start --stop`, wait, `sg_start --start`, then track list and audio again | WARN if the drive refuses to spin down; FAIL if waking with audio takes 30 s or more (the player gives up then), WARN from 15 s |
| Eject by software | The EJECT button's command opens the tray | FAIL if the drive does not eject |

The result is **SUITABLE** (all checks passed or skipped),
**SUITABLE WITH LIMITATIONS** (at least one WARN) or **NOT SUITABLE** (at least
one FAIL). The exit code is 0, 1 or 2 accordingly, and 3 if the check could
not run.

## Sharing the result

The report names the drive model, its firmware version, the USB id of the adapter (the
USB-to-SATA bridge, often the same for different drives) and the
test CD's track count and length. It contains no serial number, CD titles,
host name or user name. Please post it, together with how the drive is
powered (directly or through a powered USB hub), as an issue at
<https://github.com/tobiasvogel/sonoscdplayer/issues>. This builds up the list
of tested drives.

Example (illustrative values):

```
SonosCDPlayer drive check 0.9.0

Drive:       VENDOR MODEL-123 (firmware 1.00)
USB adapter: 1234:5678
Test CD:     12 audio tracks, 48:31

  PASS  Disc in the drive                          the drive reports a disc
  PASS  Audio CD table of contents                 12 audio tracks read with cdparanoia
  ...

Result: SUITABLE
```
