"""Drive check for community drives: every check against a simulated drive.

No real drive, cdparanoia or sg_start is used. The simulated drive answers
the same commands the check sends and advances a fake clock by the time a
drive of the given speed would need.
"""
import io
import json
import subprocess
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from sonoscdplayer.audio.table_of_contents import PCM_BYTES_PER_SECOND, SECTOR_BYTES
from sonoscdplayer.drive.disc_startup import CDS_DISC_OK, CDS_NO_DISC, CDS_TRAY_OPEN
from sonoscdplayer.drive.drive_check import (FAIL, PASS, SKIP, WARN, DriveChecker, audio_span,
                         format_report)
from sonoscdplayer.drive.usb_identity import UsbDriveId

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import check_drive  # noqa: E402

TOC_OUTPUT = """\
Table of contents (audio tracks only):
track        length               begin        copy pre ch
===========================================================
  1.    15000 [03:20.00]        0 [00:00.00]    no   no  2
  2.    22500 [05:00.00]    15000 [03:20.00]    no   no  2
TOTAL   37500 [08:20.00]    (audio only)
"""


class SimulatedDrive:
    """Answers cdparanoia, sg_start and the eject ioctl like a real drive."""

    def __init__(self, *, read_speed=8.0, wake_seconds=3.0, refuse_stop=False,
                 unstable_reads=False, ejects=True, toc_output=TOC_OUTPUT):
        self.now = 0.0
        self.read_speed = read_speed
        self.wake_seconds = wake_seconds
        self.refuse_stop = refuse_stop
        self.unstable_reads = unstable_reads
        self.ejects = ejects
        self.toc_output = toc_output
        self.media = CDS_DISC_OK
        self.spun_down = False
        self.reads = 0
        self.commands = []

    # injected functions
    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def media_status(self, device):
        return self.media

    def eject(self, device):
        if not self.ejects:
            raise OSError(5, 'Input/output error')
        self.media = CDS_TRAY_OPEN

    def run_command(self, command, **kwargs):
        self.commands.append(command)
        program = command[0]
        if program == 'cdparanoia' and '-Q' in command:
            return subprocess.CompletedProcess(command, 0, '', self.toc_output)
        if program == 'cdparanoia':
            return self.read(command)
        if program == 'sg_start' and '--stop' in command:
            if self.refuse_stop:
                return subprocess.CompletedProcess(command, 1, '', 'Illegal request')
            self.spun_down = True
            return subprocess.CompletedProcess(command, 0, '', '')
        if program == 'sg_start' and '--start' in command:
            if self.spun_down:
                self.now += self.wake_seconds
                self.spun_down = False
            return subprocess.CompletedProcess(command, 0, '', '')
        raise AssertionError(f'unexpected command {command}')

    def read(self, command):
        span = command[command.index('-r') + 1]
        start, end = span.split('-')
        sectors = sector_of(end) - sector_of(start) + 1
        self.reads += 1
        fill = bytes([self.reads % 256]) if self.unstable_reads else b'\x01'
        data = fill * (sectors * SECTOR_BYTES)
        self.now += len(data) / PCM_BYTES_PER_SECOND / self.read_speed
        return subprocess.CompletedProcess(command, 0, data, b'')


def sector_of(position):
    """'1[0:20.00]' -> sector index within the track."""
    inner = position[position.index('[') + 1:-1]
    minutes, rest = inner.split(':')
    seconds, frames = rest.split('.')
    return (int(minutes) * 60 + int(seconds)) * 75 + int(frames)


def make_checker(drive, *, cdtext=('available', {}, {1: {'TITLE': 'x'}, 2: {}}, None),
                 identity=UsbDriveId('152e', '2571', 'SERIAL'), discid=None, tmp_path=None):
    class FakeDiscId:
        @staticmethod
        def put(first, last, leadout, offsets):
            class Disc:
                id = 'abcdefghijklmnopqrstuvwxyz0-'
                toc = '1 2 37650 150 15150'
            return Disc()
    return DriveChecker(
        '/dev/sr0', run_command=drive.run_command, clock=drive.clock, sleep=drive.sleep,
        media_status=drive.media_status, eject=drive.eject,
        identity_reader=lambda device, sys_root=None: identity,
        cdtext_reader=lambda device: cdtext,
        discid_module=discid or FakeDiscId,
        sys_root=tmp_path or Path('/nonexistent'))


def results_by_key(report):
    return {result.key: result for result in report.results}


def test_a_good_drive_is_suitable():
    drive = SimulatedDrive()
    report = make_checker(drive).run(software_version='0.9.0')
    statuses = {key: result.status for key, result in results_by_key(report).items()}
    assert statuses == {'disc_present': PASS, 'table_of_contents': PASS,
                        'usb_identity': PASS, 'cd_text': PASS, 'disc_id': PASS,
                        'read_speed': PASS, 'seek': PASS, 'repeatable_read': PASS,
                        'spin_down': PASS, 'eject': PASS}
    assert report.verdict == 'SUITABLE'
    assert report.exit_code == 0
    assert report.disc == '2 audio tracks, 8:20'
    assert '8.0x playback speed' in results_by_key(report)['read_speed'].summary


def test_reads_use_the_players_cdparanoia_invocation():
    drive = SimulatedDrive()
    make_checker(drive).run(software_version='0.9.0')
    reads = [command for command in drive.commands
             if command[0] == 'cdparanoia' and '-Q' not in command]
    assert all(command[:5] == ['cdparanoia', '-q', '-d', '/dev/sr0', '-r']
               and command[-1] == '-' for command in reads)


def test_audio_span_covers_exactly_the_requested_sectors():
    assert audio_span(1, 0, 75) == '1[0:00.00]-1[0:00.74]'
    assert audio_span(2, 4500, 1500) == '2[1:00.00]-2[1:19.74]'


def test_no_disc_stops_the_check_early():
    drive = SimulatedDrive()
    drive.media = CDS_NO_DISC
    report = make_checker(drive).run(software_version='0.9.0')
    assert [result.key for result in report.results] == ['disc_present']
    assert 'insert an audio CD' in report.results[0].summary
    assert report.verdict == 'NOT SUITABLE'
    assert drive.commands == []


def test_a_data_disc_fails_on_the_table_of_contents():
    drive = SimulatedDrive(toc_output='no audio tracks here')
    report = make_checker(drive).run(software_version='0.9.0')
    assert report.results[-1].key == 'table_of_contents'
    assert report.results[-1].status == FAIL


def test_missing_permission_names_the_cdrom_group():
    drive = SimulatedDrive()
    def denied(device):
        raise PermissionError(13, 'Permission denied')
    drive.media_status = denied
    report = make_checker(drive).run(software_version='0.9.0')
    assert 'cdrom' in report.results[0].summary


@pytest.mark.parametrize('speed, status', [(1.0, FAIL), (1.2, WARN), (1.3, PASS)])
def test_read_speed_thresholds(speed, status):
    # 1.3x is what slim drives reach at the start of a disc; playback works.
    drive = SimulatedDrive(read_speed=speed)
    report = make_checker(drive).run(software_version='0.9.0', include_eject=False)
    assert results_by_key(report)['read_speed'].status == status


def test_spin_up_before_the_speed_measurement_is_not_counted():
    drive = SimulatedDrive(read_speed=4.0)
    checker = make_checker(drive)
    checker.check_audio_cd_present()
    checker.check_table_of_contents()
    original_read = drive.read
    def slow_first_read(command):
        if drive.reads == 0:
            drive.now += 10  # spin-up on the first read only
        return original_read(command)
    drive.read = slow_first_read
    assert '4.0x' in checker.check_read_speed().summary


@pytest.mark.parametrize('wake_seconds, status', [(5, PASS), (20, WARN), (31, FAIL)])
def test_wake_up_time_thresholds(wake_seconds, status):
    drive = SimulatedDrive(wake_seconds=wake_seconds)
    report = make_checker(drive).run(software_version='0.9.0', include_eject=False)
    assert results_by_key(report)['spin_down'].status == status


def test_a_drive_that_refuses_to_spin_down_is_a_limitation():
    drive = SimulatedDrive(refuse_stop=True)
    report = make_checker(drive).run(software_version='0.9.0')
    assert results_by_key(report)['spin_down'].status == WARN
    assert report.verdict == 'SUITABLE WITH LIMITATIONS'
    assert report.exit_code == 1


def test_missing_sg_start_skips_the_spin_down_check():
    drive = SimulatedDrive()
    original = drive.run_command
    def without_sg3_utils(command, **kwargs):
        if command[0] == 'sg_start':
            raise FileNotFoundError('sg_start')
        return original(command, **kwargs)
    drive.run_command = without_sg3_utils
    checker = make_checker(drive)
    checker.run_command = without_sg3_utils
    report = checker.run(software_version='0.9.0')
    result = results_by_key(report)['spin_down']
    assert result.status == SKIP
    assert 'sg3-utils' in result.summary


def test_differing_reads_are_reported():
    drive = SimulatedDrive(unstable_reads=True)
    report = make_checker(drive).run(software_version='0.9.0', include_eject=False)
    assert results_by_key(report)['repeatable_read'].status == WARN


def test_a_drive_that_cannot_eject_fails():
    drive = SimulatedDrive(ejects=False)
    report = make_checker(drive).run(software_version='0.9.0')
    assert results_by_key(report)['eject'].status == FAIL


def test_eject_is_left_out_on_request():
    drive = SimulatedDrive()
    report = make_checker(drive).run(software_version='0.9.0', include_eject=False)
    assert 'eject' not in results_by_key(report)
    assert drive.media == CDS_DISC_OK


def test_a_drive_without_usb_serial_is_a_limitation(tmp_path):
    usb = tmp_path / 'usb'
    usb.mkdir()
    (usb / 'idVendor').write_text('0bda\n')
    (usb / 'idProduct').write_text('0001\n')
    scsi = usb / 'scsi'
    scsi.mkdir()
    (tmp_path / 'block' / 'sr0').mkdir(parents=True)
    (tmp_path / 'block' / 'sr0' / 'device').symlink_to(scsi)
    drive = SimulatedDrive()
    checker = make_checker(drive, identity=None, tmp_path=tmp_path / 'block')
    report = checker.run(software_version='0.9.0', include_eject=False)
    assert results_by_key(report)['usb_identity'].status == WARN
    assert report.drive.usb_vendor_id == '0bda'


def test_cd_without_cd_text_is_skipped_not_failed():
    drive = SimulatedDrive()
    checker = make_checker(drive, cdtext=('absent', {}, {}, None))
    report = checker.run(software_version='0.9.0', include_eject=False)
    assert results_by_key(report)['cd_text'].status == SKIP
    assert report.verdict == 'SUITABLE'


def test_report_contains_no_serial_number_or_titles(tmp_path):
    (tmp_path / 'sr0' / 'device').mkdir(parents=True)
    (tmp_path / 'sr0' / 'device' / 'vendor').write_text('HL-DT-ST\n')
    (tmp_path / 'sr0' / 'device' / 'model').write_text('DVDRAM GP57EB40\n')
    (tmp_path / 'sr0' / 'device' / 'rev').write_text('PF00\n')
    drive = SimulatedDrive()
    checker = make_checker(drive, cdtext=('available', {'TITLE': 'Secret Album'},
                                          {1: {'TITLE': 'Secret Song'}}, None),
                           tmp_path=tmp_path)
    report = checker.run(software_version='0.9.0')
    text = format_report(report) + json.dumps(report.as_dict())
    assert 'HL-DT-ST DVDRAM GP57EB40 (firmware PF00)' in text
    assert 'USB adapter: 152e:2571' in text
    for private in ('SERIAL', 'Secret'):
        assert private not in text


# ---- command line --------------------------------------------------------

@contextmanager
def free_lock(device, blocking=False):
    yield True


def run_tool(argv, drive, *, service_active=False, devices=('/dev/sr0',)):
    def run_command(command, **kwargs):
        if command[0] == 'systemctl':
            return subprocess.CompletedProcess(command, 0 if service_active else 3)
        return drive.run_command(command, **kwargs)
    def factory(device, run_command):
        return make_checker(drive)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = check_drive.main(argv, run_command=run_command,
                                find_devices=lambda: devices,
                                checker_factory=factory, lock=free_lock)
    return code, out.getvalue(), err.getvalue()


def test_tool_prints_the_report_and_returns_the_verdict():
    code, out, err = run_tool([], SimulatedDrive())
    assert code == 0
    assert 'Result: SUITABLE' in out
    assert 'github.com/tobiasvogel/sonoscdplayer/issues' in out


def test_tool_prints_json_on_request():
    code, out, err = run_tool(['--json', '--no-eject'], SimulatedDrive())
    data = json.loads(out)
    assert data['verdict'] == 'SUITABLE'
    assert 'eject' not in [result['key'] for result in data['results']]


def test_tool_refuses_while_the_player_uses_the_drive():
    drive = SimulatedDrive()
    code, out, err = run_tool([], drive, service_active=True)
    assert code == check_drive.COULD_NOT_RUN
    assert 'systemctl stop sonoscdplayer-player.service' in err
    assert drive.commands == []


def test_tool_without_drive_says_so():
    code, out, err = run_tool([], SimulatedDrive(), devices=())
    assert code == check_drive.COULD_NOT_RUN
    assert 'No CD drive found' in err
