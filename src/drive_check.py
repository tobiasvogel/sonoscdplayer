"""Check whether an optical drive meets SonosCDPlayer's requirements.

Run through ``tools/check_drive.py`` with an audio CD in the drive. Each check
uses the same commands as the player (cdparanoia for the table of contents and
audio, cd-info for CD-TEXT, sg_start for spin-down and wake-up, the Linux eject
ioctl for the tray) and returns PASS, WARN, FAIL or SKIP:

* PASS: the drive does what the player needs.
* WARN: the player works, but a feature is unavailable or slower than ideal.
* FAIL: the player cannot work reliably with this drive.
* SKIP: the check could not run (missing tool, or not applicable).

The report names the drive model and the disc's track count and length. It
contains no serial numbers, CD titles, host names or user paths, so it can be
shared publicly.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
from pathlib import Path
import subprocess
import time

from cd_toc import (PCM_BYTES_PER_SECOND, SECTOR_BYTES, SECTORS_PER_SECOND,
                    DiscTOC, TOCError, parse_cdparanoia_query)
from disc_metadata import read_cdtext
from disc_startup import (CDS_DISC_OK, CDS_DRIVE_NOT_READY, CDS_NO_DISC,
                          CDS_TRAY_OPEN, drive_media_status)
from drive_identity import usb_identity
from safe_drive_eject import eject_tray

PASS, WARN, FAIL, SKIP = 'PASS', 'WARN', 'FAIL', 'SKIP'

# Reading must be clearly faster than playback: the player reads each track
# while Sonos already plays it, and the drive also has to seek and spin up.
MINIMUM_READ_SPEED = 1.1        # below: FAIL, playback would stall
# Slim drives read the start of a disc (inner tracks) at about 1.3x and
# reach about 4x towards the end; that is enough for playback.
RECOMMENDED_READ_SPEED = 1.25   # below: WARN, little reserve

# The player gives up starting playback after 30 seconds.
MAXIMUM_WAKE_SECONDS = 30.0     # at or above: FAIL
RECOMMENDED_WAKE_SECONDS = 15.0  # at or above: WARN

SPEED_TEST_SECONDS = 20
SEEK_TEST_SECONDS = 10
REPEAT_TEST_SECONDS = 5
WAKE_TEST_AUDIO_SECONDS = 2
SPIN_DOWN_PAUSE_SECONDS = 5
EJECT_WAIT_SECONDS = 10

MEDIA_STATUS_NAMES = {
    CDS_NO_DISC: 'no disc', CDS_TRAY_OPEN: 'tray open',
    CDS_DRIVE_NOT_READY: 'drive not ready', CDS_DISC_OK: 'disc present',
}


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one check, as shown in the report."""
    key: str
    title: str
    status: str
    summary: str


@dataclass
class DriveDescription:
    """What the report says about the drive; deliberately without serial."""
    device: str
    vendor: str | None = None
    model: str | None = None
    firmware: str | None = None
    usb_vendor_id: str | None = None
    usb_product_id: str | None = None
    usb_serial_present: bool = False


@dataclass
class DriveCheckReport:
    """All check results plus the drive and disc description."""
    software_version: str
    drive: DriveDescription
    disc: str | None = None
    results: list[CheckResult] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        statuses = {result.status for result in self.results}
        if FAIL in statuses:
            return 'NOT SUITABLE'
        if WARN in statuses:
            return 'SUITABLE WITH LIMITATIONS'
        return 'SUITABLE'

    @property
    def exit_code(self) -> int:
        return {'SUITABLE': 0, 'SUITABLE WITH LIMITATIONS': 1}.get(self.verdict, 2)

    def as_dict(self) -> dict:
        return {'software_version': self.software_version,
                'drive': asdict(self.drive), 'disc': self.disc,
                'verdict': self.verdict,
                'results': [asdict(result) for result in self.results]}


def read_sysfs_text(path: Path) -> str | None:
    """One stripped line from sysfs, or None if missing or unreadable."""
    try:
        text = path.read_text(encoding='utf-8', errors='replace').strip()
    except OSError:
        return None
    return text or None


def describe_drive(device: str, *, sys_root=Path('/sys/class/block'),
                   identity_reader=usb_identity) -> DriveDescription:
    """Vendor, model and firmware from sysfs; USB ids without the serial."""
    name = device.rsplit('/', 1)[-1]
    scsi_device = Path(sys_root) / name / 'device'
    description = DriveDescription(
        device=device,
        vendor=read_sysfs_text(scsi_device / 'vendor'),
        model=read_sysfs_text(scsi_device / 'model'),
        firmware=read_sysfs_text(scsi_device / 'rev'))
    identity = identity_reader(device, sys_root=str(sys_root))
    if identity is not None:
        description.usb_vendor_id = identity.vendor
        description.usb_product_id = identity.product
        description.usb_serial_present = bool(identity.serial)
    else:
        description.usb_vendor_id, description.usb_product_id = usb_ids_without_serial(
            scsi_device)
    return description


def usb_ids_without_serial(scsi_device: Path) -> tuple[str | None, str | None]:
    """USB vendor and product id of a drive that reports no serial number."""
    try:
        nodes = (scsi_device.resolve(), *scsi_device.resolve().parents)
    except OSError:
        return None, None
    for node in nodes:
        vendor = read_sysfs_text(node / 'idVendor')
        product = read_sysfs_text(node / 'idProduct')
        if vendor and product:
            return vendor.lower(), product.lower()
    return None, None


def sector_position(sectors: int) -> str:
    """cdparanoia position within a track: minutes:seconds.frames."""
    minutes, remaining = divmod(sectors, SECTORS_PER_SECOND * 60)
    seconds, frames = divmod(remaining, SECTORS_PER_SECOND)
    return f'{minutes}:{seconds:02d}.{frames:02d}'


def audio_span(track: int, first_sector: int, sector_count: int) -> str:
    """cdparanoia span for sector_count sectors from first_sector of track."""
    last_sector = first_sector + sector_count - 1
    return (f'{track}[{sector_position(first_sector)}]-'
            f'{track}[{sector_position(last_sector)}]')


def format_duration(seconds: float) -> str:
    minutes, seconds = divmod(int(round(seconds)), 60)
    return f'{minutes}:{seconds:02d}'


class DriveChecker:
    """Runs the checks against one drive; every outside effect is injectable.

    ``run_command`` has the signature of ``subprocess.run``; ``clock`` and
    ``sleep`` those of ``time.monotonic`` and ``time.sleep``.
    """

    def __init__(self, device: str, *, run_command=subprocess.run,
                 clock=time.monotonic, sleep=time.sleep,
                 media_status=drive_media_status, identity_reader=usb_identity,
                 eject=eject_tray, cdtext_reader=read_cdtext,
                 discid_module=None, sys_root=Path('/sys/class/block')):
        self.device = device
        self.run_command = run_command
        self.clock = clock
        self.sleep = sleep
        self.media_status = media_status
        self.identity_reader = identity_reader
        self.eject = eject
        self.cdtext_reader = cdtext_reader
        self.discid_module = discid_module
        self.sys_root = Path(sys_root)
        self.toc: DiscTOC | None = None

    # ---- running -------------------------------------------------------

    def run(self, *, software_version: str, include_eject: bool = True,
            progress=lambda title: None) -> DriveCheckReport:
        """Run all checks in order; stop early if no audio CD can be read."""
        report = DriveCheckReport(
            software_version=software_version,
            drive=describe_drive(self.device, sys_root=self.sys_root,
                                 identity_reader=self.identity_reader))
        for check in (self.check_audio_cd_present, self.check_table_of_contents):
            progress(check.__doc__.splitlines()[0])
            result = check()
            report.results.append(result)
            if result.status == FAIL:
                return report
        report.disc = (f'{len(self.toc.tracks)} audio tracks, '
                       f'{format_duration(self.toc.leadout_lsn / SECTORS_PER_SECOND)}')
        checks = [self.check_usb_identity, self.check_cd_text, self.check_disc_id,
                  self.check_read_speed, self.check_seek_to_disc_end,
                  self.check_repeatable_read, self.check_spin_down_and_wake]
        if include_eject:
            checks.append(self.check_eject)
        for check in checks:
            progress(check.__doc__.splitlines()[0])
            report.results.append(check())
        return report

    # ---- helpers -------------------------------------------------------

    def read_audio(self, track: int, first_sector: int, sector_count: int,
                   *, timeout: float = 120) -> tuple[bytes, float]:
        """Read raw PCM like the player does; return (data, elapsed seconds)."""
        command = ['cdparanoia', '-q', '-d', self.device, '-r',
                   audio_span(track, first_sector, sector_count), '-']
        started = self.clock()
        result = self.run_command(command, capture_output=True, timeout=timeout,
                                  check=False)
        elapsed = self.clock() - started
        if result.returncode != 0:
            raise RuntimeError(f'cdparanoia exited with code {result.returncode}')
        return result.stdout, elapsed

    def read_table_of_contents(self, *, timeout: float = 60) -> DiscTOC:
        result = self.run_command(['cdparanoia', '-d', self.device, '-Q'],
                                  capture_output=True, text=True,
                                  timeout=timeout, check=False)
        if result.returncode != 0:
            raise TOCError(f'cdparanoia -Q exited with code {result.returncode}')
        return parse_cdparanoia_query(result.stdout + '\n' + result.stderr)

    def first_audio_track(self):
        """Track 1 for speed tests: the inner, slowest part of the disc."""
        return self.toc.tracks[0]

    @staticmethod
    def complete(data: bytes, sector_count: int) -> bool:
        """cdparanoia returned the requested sectors (one sector tolerance)."""
        return abs(len(data) - sector_count * SECTOR_BYTES) <= SECTOR_BYTES

    # ---- checks --------------------------------------------------------

    def check_audio_cd_present(self) -> CheckResult:
        """Drive reports an inserted disc"""
        title = 'Disc in the drive'
        try:
            status = self.media_status(self.device)
        except PermissionError:
            return CheckResult('disc_present', title, FAIL,
                               f'no permission to use {self.device}; add your user to the '
                               'group "cdrom" (sudo adduser $USER cdrom, then log in again)')
        except OSError as exc:
            return CheckResult('disc_present', title, FAIL,
                               f'cannot open {self.device}: {exc.strerror or exc}')
        if status == CDS_DISC_OK:
            return CheckResult('disc_present', title, PASS, 'the drive reports a disc')
        name = MEDIA_STATUS_NAMES.get(status, f'status {status}')
        return CheckResult('disc_present', title, FAIL,
                           f'the drive reports "{name}"; insert an audio CD and run again')

    def check_table_of_contents(self) -> CheckResult:
        """Read the table of contents"""
        title = 'Audio CD table of contents'
        try:
            self.toc = self.read_table_of_contents()
        except FileNotFoundError:
            return CheckResult('table_of_contents', title, FAIL,
                               'cdparanoia is not installed (package cdparanoia)')
        except (TOCError, OSError, subprocess.TimeoutExpired) as exc:
            return CheckResult('table_of_contents', title, FAIL,
                               f'no readable audio CD: {exc}')
        return CheckResult('table_of_contents', title, PASS,
                           f'{len(self.toc.tracks)} audio tracks read with cdparanoia')

    def check_usb_identity(self) -> CheckResult:
        """Identify the drive for USB reconnects"""
        title = 'Recognized again after a USB reconnect'
        identity = self.identity_reader(self.device, sys_root=str(self.sys_root))
        if identity is not None:
            return CheckResult('usb_identity', title, PASS,
                               'USB vendor, product and serial number are readable')
        vendor, product = usb_ids_without_serial(self.sys_root / self.device.rsplit('/', 1)[-1] / 'device')
        if vendor:
            return CheckResult('usb_identity', title, WARN,
                               'the drive reports no USB serial number; after a USB '
                               'reconnect the player cannot be sure it is the same drive '
                               'and waits for a restart')
        return CheckResult('usb_identity', title, SKIP, 'not a USB drive')

    def check_cd_text(self) -> CheckResult:
        """Read CD-TEXT"""
        title = 'CD-TEXT (titles stored on the CD)'
        status, disc, tracks, error = self.cdtext_reader(self.device)
        if status == 'available':
            named = sum(1 for fields in tracks.values() if fields.get('TITLE'))
            return CheckResult('cd_text', title, PASS,
                               f'CD-TEXT read (track titles: {named})')
        if status == 'absent':
            return CheckResult('cd_text', title, SKIP,
                               'this CD has no CD-TEXT; try a CD that has it to check '
                               'the drive (titles also come from MusicBrainz)')
        if status == 'unavailable':
            return CheckResult('cd_text', title, SKIP, error or 'cd-info is not installed')
        return CheckResult('cd_text', title, WARN,
                           f'CD-TEXT could not be read ({error}); titles come from '
                           'MusicBrainz only')

    def check_disc_id(self) -> CheckResult:
        """Compute the MusicBrainz disc ID"""
        title = 'MusicBrainz disc ID'
        from disc_musicbrainz import DiscIdentificationError, identity_from_toc
        try:
            identity_from_toc(self.toc, libdiscid_module=self.discid_module)
        except DiscIdentificationError as exc:
            if 'unavailable' in str(exc):
                return CheckResult('disc_id', title, SKIP,
                                   'python3-libdiscid is not installed')
            return CheckResult('disc_id', title, WARN, f'not computable: {exc}')
        return CheckResult('disc_id', title, PASS,
                           'computed from the table of contents')

    def check_read_speed(self) -> CheckResult:
        """Measure the audio read speed"""
        title = 'Audio read speed at the start of the disc'
        track = self.first_audio_track()
        sectors = min(track.sectors, SPEED_TEST_SECONDS * SECTORS_PER_SECOND)
        try:
            # A short read first, so that spin-up is not counted as speed.
            self.read_audio(track.number, 0, SECTORS_PER_SECOND)
            data, elapsed = self.read_audio(track.number, 0, sectors)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            return CheckResult('read_speed', title, FAIL, f'audio could not be read: {exc}')
        if not self.complete(data, sectors):
            return CheckResult('read_speed', title, FAIL,
                               f'incomplete read: {len(data)} of {sectors * SECTOR_BYTES} bytes')
        speed = (len(data) / PCM_BYTES_PER_SECOND) / max(elapsed, 1e-6)
        summary = (f'{speed:.1f}x playback speed '
                   f'({len(data) / PCM_BYTES_PER_SECOND:.0f} s of audio in {elapsed:.1f} s)')
        if speed < MINIMUM_READ_SPEED:
            return CheckResult('read_speed', title, FAIL,
                               summary + f'; at least {MINIMUM_READ_SPEED}x is needed')
        if speed < RECOMMENDED_READ_SPEED:
            return CheckResult('read_speed', title, WARN,
                               summary + f'; {RECOMMENDED_READ_SPEED}x or more recommended')
        return CheckResult('read_speed', title, PASS, summary)

    def check_seek_to_disc_end(self) -> CheckResult:
        """Read the end of the last track"""
        title = 'Jump to the end of the disc'
        track = self.toc.tracks[-1]
        sectors = min(track.sectors, SEEK_TEST_SECONDS * SECTORS_PER_SECOND)
        first = track.sectors - sectors
        try:
            data, elapsed = self.read_audio(track.number, first, sectors)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            return CheckResult('seek', title, FAIL, f'the end of the disc could not be read: {exc}')
        if not self.complete(data, sectors):
            return CheckResult('seek', title, FAIL,
                               f'incomplete read: {len(data)} of {sectors * SECTOR_BYTES} bytes')
        return CheckResult('seek', title, PASS,
                           f'last {sectors // SECTORS_PER_SECOND} s of track {track.number} '
                           f'read in {elapsed:.1f} s')

    def check_repeatable_read(self) -> CheckResult:
        """Read the same audio twice"""
        title = 'Same audio on a second read'
        track = max(self.toc.tracks, key=lambda item: item.sectors)
        sectors = min(track.sectors, REPEAT_TEST_SECONDS * SECTORS_PER_SECOND)
        first = (track.sectors - sectors) // 2
        try:
            data_one, _ = self.read_audio(track.number, first, sectors)
            data_two, _ = self.read_audio(track.number, first, sectors)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            return CheckResult('repeatable_read', title, FAIL, f'audio could not be read: {exc}')
        if hashlib.sha256(data_one).digest() == hashlib.sha256(data_two).digest():
            return CheckResult('repeatable_read', title, PASS,
                               f'{sectors // SECTORS_PER_SECOND} s from the middle of '
                               f'track {track.number} identical on both reads')
        return CheckResult('repeatable_read', title, WARN,
                           'the two reads differ; the disc may be scratched, or the '
                           'drive reads inexactly (try another CD)')

    def check_spin_down_and_wake(self) -> CheckResult:
        """Spin the drive down and wake it up"""
        title = 'Spin down when idle, wake up for PLAY'
        try:
            stop = self.run_command(['sg_start', '--stop', self.device],
                                    capture_output=True, text=True, timeout=15, check=False)
        except FileNotFoundError:
            return CheckResult('spin_down', title, SKIP,
                               'sg_start is not installed (package sg3-utils)')
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CheckResult('spin_down', title, WARN,
                               f'spin-down command failed ({exc}); the drive keeps spinning '
                               'while the player is idle')
        if stop.returncode != 0:
            return CheckResult('spin_down', title, WARN,
                               'the drive refuses the spin-down command; it keeps spinning '
                               'while the player is idle; remove idle_spin_down_seconds '
                               'from [drive_power] in player.toml')
        self.sleep(SPIN_DOWN_PAUSE_SECONDS)
        started = self.clock()
        try:
            start = self.run_command(['sg_start', '--start', self.device],
                                     capture_output=True, text=True, timeout=60, check=False)
            if start.returncode != 0:
                raise RuntimeError(f'sg_start --start exited with code {start.returncode}')
            woken_toc = self.read_table_of_contents()
            track = self.first_audio_track()
            sectors = min(track.sectors, WAKE_TEST_AUDIO_SECONDS * SECTORS_PER_SECOND)
            data, _ = self.read_audio(track.number, 0, sectors)
        except (RuntimeError, OSError, TOCError, subprocess.TimeoutExpired) as exc:
            return CheckResult('spin_down', title, FAIL,
                               f'the drive did not wake up properly: {exc}')
        wake_seconds = self.clock() - started
        if woken_toc != self.toc:
            return CheckResult('spin_down', title, FAIL,
                               'after waking, the drive reports a different table of contents')
        if not self.complete(data, sectors):
            return CheckResult('spin_down', title, FAIL, 'after waking, audio was incomplete')
        summary = f'awake with audio after {wake_seconds:.1f} s'
        if wake_seconds >= MAXIMUM_WAKE_SECONDS:
            return CheckResult('spin_down', title, FAIL,
                               summary + f'; the player gives up after {MAXIMUM_WAKE_SECONDS:.0f} s')
        if wake_seconds >= RECOMMENDED_WAKE_SECONDS:
            return CheckResult('spin_down', title, WARN,
                               summary + f'; under {RECOMMENDED_WAKE_SECONDS:.0f} s recommended')
        return CheckResult('spin_down', title, PASS, summary)

    def check_eject(self) -> CheckResult:
        """Eject the disc"""
        title = 'Eject by software (EJECT button)'
        try:
            self.eject(self.device)
        except OSError as exc:
            return CheckResult('eject', title, FAIL,
                               f'the eject command was refused: {exc.strerror or exc}')
        deadline = self.clock() + EJECT_WAIT_SECONDS
        status = None
        while self.clock() < deadline:
            try:
                status = self.media_status(self.device)
            except OSError:
                status = None
            if status in (CDS_TRAY_OPEN, CDS_NO_DISC):
                return CheckResult('eject', title, PASS,
                                   f'disc ejected ({MEDIA_STATUS_NAMES[status]})')
            self.sleep(0.5)
        name = MEDIA_STATUS_NAMES.get(status, 'unknown')
        return CheckResult('eject', title, FAIL,
                           f'the drive still reports "{name}" {EJECT_WAIT_SECONDS} s after eject')


def format_report(report: DriveCheckReport) -> str:
    """Plain-text report to read in the terminal or paste into an issue."""
    drive = report.drive
    name = ' '.join(part for part in (drive.vendor, drive.model) if part) or 'unknown drive'
    lines = [f'SonosCDPlayer drive check {report.software_version}', '',
             f'Drive:       {name}' + (f' (firmware {drive.firmware})' if drive.firmware else '')]
    if drive.usb_vendor_id:
        # The id belongs to the USB-to-SATA bridge, not to the drive itself.
        lines.append(f'USB adapter: {drive.usb_vendor_id}:{drive.usb_product_id}')
    if report.disc:
        lines.append(f'Test CD:     {report.disc}')
    lines.append('')
    width = max((len(result.title) for result in report.results), default=0)
    for result in report.results:
        lines.append(f'  {result.status:<4}  {result.title:<{width}}  {result.summary}')
    lines += ['', f'Result: {report.verdict}']
    return '\n'.join(lines)
