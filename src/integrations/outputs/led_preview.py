"""Offline LED model preview: python3 -m src.integrations.outputs.led_preview.

No GPIO, network requests, control socket commands, or background processes.
"""
import json
from .led_model import Connection, PowerFault, derive_led_state
from .led_patterns import PatternConfig, intensity


def main():
    for mode in ('NO_DISC', 'STOPPED', 'STARTING', 'PLAYING', 'PAUSED', 'ERROR'):
        snapshot = {'schema_version': 1, 'player': {'mode': mode}}
        leds = derive_led_state(snapshot)
        print(json.dumps({'mode': mode, 'power': leds.power.pattern.value,
                          'connection': leds.connection.pattern.value,
                          'connection_reason': leds.connection.reason}))
    print('Power diagnostics (explicit only):')
    for fault in PowerFault:
        led = derive_led_state({'schema_version': 1, 'player': {'mode': 'STOPPED'}}, power_fault=fault).power
        print(json.dumps({'power_fault': fault.value, 'pattern': led.pattern.value, 'reason': led.reason}))
    print('Explicit connectivity observations (future read-only adapter):')
    for connection in Connection:
        leds = derive_led_state({'schema_version': 1, 'player': {'mode': 'STOPPED'}},
                                connection=connection)
        print(json.dumps({'observation': connection.value,
                          'connection_led': leds.connection.pattern.value}))


if __name__ == '__main__':
    main()

