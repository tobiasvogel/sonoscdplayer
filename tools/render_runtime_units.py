#!/usr/bin/env python3
"""Render standalone SonosCDPlayer units for a local deployment.

The versioned files under systemd/ are templates with @SONOSCDPLAYER_*@
placeholders; they contain no installation paths or account names. All units
read their configuration from the standard location (src/config_paths.py,
/etc/sonoscdplayer by default).

POWER actions (with the polkit rule), drive-activity wake events and CONNECT
gestures are enabled by default; --no-power-actions, --no-drive-activity-events
and --no-connect-gestures switch them off.

Preparation only: never installs, removes drop-ins, calls systemctl, or restarts.
All deployment-specific paths must be passed explicitly and remain local.
"""
import argparse
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import config_paths  # noqa: E402


UNITS = (
    'sonoscdplayer-player.service',
    'sonoscdplayer-led.service',
    'sonoscdplayer-oled.service',
    'sonoscdplayer-buttons.service',
)
EARLY_UNIT = 'sonoscdplayer-early-power-led.service'
POLKIT_RULE = '50-sonoscdplayer-power.rules'
PLACEHOLDER = re.compile(r'@SONOSCDPLAYER_[A-Z_]+@')
USER_NAME = re.compile(r'[a-z_][a-z0-9_-]{0,31}')


def absolute_path(value):
    path = Path(value)
    if not path.is_absolute() or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError('Path must be absolute and contain no whitespace/control characters')
    return str(path)


def service_user(value):
    if not USER_NAME.fullmatch(value) or value == 'root':
        raise ValueError('Service user must be a plain, non-root account name')
    return value


def strip_template_comments(text):
    return '\n'.join(line for line in text.split('\n')
                     if not line.startswith('#@'))


def require_rendered(name, text):
    leftover = sorted(set(PLACEHOLDER.findall(text)))
    if leftover:
        raise ValueError(f'{name}: unrendered placeholder(s) {", ".join(leftover)}')
    return text


def replace_directive(text, name, value, *, expected=1):
    expr = re.compile(r'^' + re.escape(name) + r'=.*$', re.MULTILINE)
    result, count = expr.subn(lambda match: name + '=' + value, text)
    if count != expected:
        raise ValueError(f'Expected {expected} {name}= directive(s), found {count}')
    return result


def add_after(text, requirement):
    match = re.search(r'^After=(.*)$', text, re.MULTILINE)
    if match is None:
        raise ValueError('Missing After= directive')
    existing = match.group(1).split()
    if requirement not in existing:
        existing.append(requirement)
    return replace_directive(text, 'After', ' '.join(existing))


def render(source_dir, *, checkout, python_venv, user, power_actions=True,
           drive_activity_events=True, connect_gestures=True):
    checkout, python_venv = (absolute_path(str(v)) for v in (checkout, python_venv))
    player_config, interface_config, ui_config = (absolute_path(str(p)) for p in (
        config_paths.player_config(), config_paths.interface_config(),
        config_paths.ui_config()))
    user = service_user(user)
    root = Path(source_dir)
    rendered = {}
    oled_power = ' --ui-power-actions' if power_actions else ''
    buttons_extra = ''.join(flag for flag, enabled in (
        (' --power-actions', power_actions),
        (' --drive-activity-events', drive_activity_events),
        (' --connect-gestures', connect_gestures),
    ) if enabled)
    commands = {
        UNITS[0]: (
            f'{python_venv} -u player_service.py --config {player_config} '
            '--socket /run/sonoscdplayer/control.sock'
        ),
        UNITS[1]: (
            f'/usr/bin/python3 -u -m integrations.outputs.led_pwm '
            f'--config {player_config} --socket /run/sonoscdplayer/control.sock '
            '--gpio --execute --seconds 0'
        ),
        UNITS[2]: (
            f'{python_venv} -u -m integrations.outputs.oled_status '
            f'--socket /run/sonoscdplayer/control.sock '
            f'--display-config {player_config} --gpio --execute --seconds 0 '
            f'--ambient-auto --ui-modern --ui-config {ui_config} '
            '--cover auto --ui-events-socket /run/sonoscdplayer-ui/events.sock '
            f'--oled-recover-on-error --ui-target-rpc{oled_power}'
        ),
        UNITS[3]: (
            f'{python_venv} -u -m integrations.interfaces.interface_input '
            f'--config {interface_config} --gpio --commands '
            '--socket /run/sonoscdplayer/control.sock '
            '--ui-events-socket /run/sonoscdplayer-ui/events.sock '
            f'--execute --selected-target-execute{buttons_extra}'
        ),
    }
    for name in UNITS:
        text = strip_template_comments((root / name).read_text(encoding='utf-8'))
        text = replace_directive(text, 'User', user)
        text = replace_directive(text, 'WorkingDirectory', checkout)
        text = replace_directive(text, 'ExecStart', commands[name])
        if name == UNITS[2]:
            if 'RuntimeDirectory=' not in text:
                text = text.replace('[Service]\n', (
                    '[Service]\nRuntimeDirectory=sonoscdplayer-ui\n'
                    'RuntimeDirectoryMode=0700\n'
                ), 1)
            else:
                text = replace_directive(text, 'RuntimeDirectory', 'sonoscdplayer-ui')
        if name == UNITS[3]:
            text = add_after(text, 'sonoscdplayer-oled.service')
            text = replace_directive(
                text, 'Description', 'SonosCDPlayer GPIO buttons')
        # Remove comments describing intentionally superseded preview/default setup.
        text = '\n'.join(line for line in text.split('\n') if not (
            line.lstrip().startswith('#') and (
                'preview' in line.lower() or
                'm016b-config.conf' in line or
                'config path to player.toml' in line
            )
        ))
        rendered[name] = require_rendered(name, text)
    rendered[EARLY_UNIT] = require_rendered(EARLY_UNIT, render_early_unit(root, player_config))
    if power_actions:
        rule = (root / 'polkit' / POLKIT_RULE).read_text(encoding='utf-8')
        rendered[POLKIT_RULE] = require_rendered(
            POLKIT_RULE, rule.replace('@SONOSCDPLAYER_USER@', user))
    return rendered


def render_early_unit(root, player_config):
    """Provide the configured power LED pin mapping to the early boot service."""
    text = strip_template_comments((Path(root) / EARLY_UNIT).read_text(encoding='utf-8'))
    match = re.search(r'^ExecStart=(.*)$', text, re.MULTILINE)
    if match is None or '--config' in match.group(1):
        raise ValueError('Early LED unit must have one ExecStart= without --config')
    return replace_directive(text, 'ExecStart', f'{match.group(1)} --config {player_config}')


def check_checkout(checkout):
    """Refuse a --checkout that is not the src directory of a checkout.

    Services start with WorkingDirectory=<checkout> and `python -m
    integrations...`; a wrong directory only fails at runtime otherwise.
    """
    root = Path(checkout)
    missing = [name for name in ('player_service.py', 'integrations')
               if not (root / name).exists()]
    if missing:
        hint = ' (did you mean its src/ subdirectory?)' if (root / 'src' / 'player_service.py').exists() else ''
        raise ValueError(f'--checkout {checkout} lacks {", ".join(missing)}{hint}')


def existing_dropins(systemd_dir, names):
    """Read-only inventory of units with .conf drop-ins."""
    found = []
    for name in names:
        directory = Path(systemd_dir) / f'{name}.d'
        if directory.is_dir() and any(directory.glob('*.conf')):
            found.append(name)
    return found


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo-root', type=Path,
                   default=Path(__file__).resolve().parents[1])
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--checkout', required=True,
                   help='Absolute path to the intended production src directory')
    p.add_argument('--python-venv', required=True)
    p.add_argument('--service-user', required=True,
                   help='Unprivileged account that runs the services')
    p.add_argument('--power-actions', action=argparse.BooleanOptionalAction, default=True,
                   help='POWER button may shut down/restart; renders the polkit rule')
    p.add_argument('--drive-activity-events', action=argparse.BooleanOptionalAction,
                   default=True, help='button presses wake an idle optical drive')
    p.add_argument('--connect-gestures', action=argparse.BooleanOptionalAction,
                   default=True, help='CONNECT short/long gestures and speaker_select')
    p.add_argument('--systemd-dir', type=Path, default=Path('/etc/systemd/system'),
                   help='Read-only check for installed drop-ins overriding rendered units')
    args = p.parse_args(argv)
    try:
        check_checkout(args.checkout)
    except ValueError as exc:
        p.error(str(exc))
    units = render(args.repo_root / 'systemd',
                   checkout=args.checkout, python_venv=args.python_venv,
                   user=args.service_user, power_actions=args.power_actions,
                   drive_activity_events=args.drive_activity_events,
                   connect_gestures=args.connect_gestures)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, text in units.items():
        out = args.output_dir / name
        out.write_text(text, encoding='utf-8')
        print(f'Rendered {out}')
    for name in existing_dropins(args.systemd_dir, units):
        print(f'WARNING: {args.systemd_dir}/{name}.d contains drop-ins; review them '
              'before installing, since they may override the rendered unit.')
    print(f'Configuration is read from {config_paths.config_dir()}')
    print('Preparation only. No systemd configuration was installed or changed.')


if __name__ == '__main__':
    main()
