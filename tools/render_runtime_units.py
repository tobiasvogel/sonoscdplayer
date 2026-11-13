#!/usr/bin/env python3
"""Render four standalone SonosCDPlayer runtime units for a local deployment.

The versioned files under systemd/ are templates with @SONOSCDPLAYER_*@
placeholders; they contain no installation paths or account names. With
--power-actions the power flags are added and the polkit rule is rendered too.

Preparation only: never installs, removes drop-ins, calls systemctl, or restarts.
All deployment-specific paths must be passed explicitly and remain local.
"""
import argparse
from pathlib import Path
import re


UNITS = (
    'sonoscdplayer-player.service',
    'sonoscdplayer-led.service',
    'sonoscdplayer-oled.service',
    'sonoscdplayer-buttons.service',
)
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


def render(source_dir, *, checkout, player_config, interface_config,
           ui_config, python_venv, user, power_actions=False):
    values = [checkout, player_config, interface_config, ui_config, python_venv]
    checkout, player_config, interface_config, ui_config, python_venv = (
        absolute_path(str(value)) for value in values
    )
    user = service_user(user)
    root = Path(source_dir)
    rendered = {}
    oled_power = ' --ui-power-actions' if power_actions else ''
    buttons_power = ' --power-actions' if power_actions else ''
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
            f'--execute --selected-target-execute{buttons_power}'
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
    if power_actions:
        rule = (root / 'polkit' / POLKIT_RULE).read_text(encoding='utf-8')
        rendered[POLKIT_RULE] = require_rendered(
            POLKIT_RULE, rule.replace('@SONOSCDPLAYER_USER@', user))
    return rendered


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo-root', type=Path,
                   default=Path(__file__).resolve().parents[1])
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--checkout', required=True,
                   help='Absolute path to the intended production src directory')
    p.add_argument('--player-config', required=True)
    p.add_argument('--interface-config', required=True)
    p.add_argument('--ui-config', required=True)
    p.add_argument('--python-venv', required=True)
    p.add_argument('--service-user', required=True,
                   help='Unprivileged account that runs the services')
    p.add_argument('--power-actions', action='store_true',
                   help='Enable POWER button actions and render the polkit rule')
    args = p.parse_args(argv)
    units = render(args.repo_root / 'systemd',
                   checkout=args.checkout, player_config=args.player_config,
                   interface_config=args.interface_config,
                   ui_config=args.ui_config, python_venv=args.python_venv,
                   user=args.service_user, power_actions=args.power_actions)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, text in units.items():
        out = args.output_dir / name
        out.write_text(text, encoding='utf-8')
        print(f'Rendered {out}')
    print('Preparation only. No systemd configuration was installed or changed.')


if __name__ == '__main__':
    main()
