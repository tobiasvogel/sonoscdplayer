#!/usr/bin/env python3
"""Early boot power LED breathe; independent of player, network and sensors."""
import argparse
import math
import signal
import time
import tomllib

PIN=12
PERIOD=2.0

def duty_at(seconds):
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError('elapsed seconds must be finite and nonnegative')
    phase=(seconds%PERIOD)/PERIOD
    return 70.0*(0.1+0.9*(1-math.cos(2*math.pi*phase))/2)

def configured_power_pin(path):
    """Read only the LED power pin from a local TOML configuration."""
    with open(path, 'rb') as stream:
        document = tomllib.load(stream)
    led = document.get('led_outputs', {})
    if not isinstance(led, dict):
        raise ValueError('led_outputs must be a TOML table')
    power = led.get('power_pin', PIN)
    connection = led.get('connection_pin', 13)
    if type(power) is not int or type(connection) is not int or {power, connection} != {12, 13}:
        raise ValueError('LED outputs must use BCM12 and BCM13 exactly once')
    return power


def run(gpio=None, *, pin=PIN):
    if type(pin) is not int or pin not in (12, 13):
        raise ValueError('early power LED pin must be BCM12 or BCM13')
    if gpio is None:
        import RPi.GPIO as gpio
    stop=False
    def on_signal(*_):
        nonlocal stop
        stop=True
    prev={sig:signal.getsignal(sig) for sig in (signal.SIGINT,signal.SIGTERM)}
    pwm=None
    claimed=False
    try:
        for sig in prev: signal.signal(sig,on_signal)
        gpio.setmode(gpio.BCM)
        gpio.setup(pin,gpio.OUT,initial=gpio.HIGH)
        claimed=True
        pwm=gpio.PWM(pin,200)
        pwm.start(duty_at(0))
        start=time.monotonic()
        while not stop:
            pwm.ChangeDutyCycle(duty_at(time.monotonic()-start))
            time.sleep(.025)
    finally:
        if pwm is not None: pwm.stop()
        if claimed:
            gpio.output(pin,gpio.LOW)
            gpio.cleanup(pin)
        for sig,handler in prev.items(): signal.signal(sig,handler)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--config', help='optional local TOML containing [led_outputs] pins')
    a=p.parse_args()
    if not a.execute: p.error('GPIO access requires --execute')
    run(pin=configured_power_pin(a.config) if a.config else PIN)

if __name__=='__main__': main()
