#!/usr/bin/env python3
"""Early boot GPIO12 breathe; independent of player, network and sensors."""
import argparse
import math
import signal
import time

PIN=12
PERIOD=2.0

def duty_at(seconds):
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError('elapsed seconds must be finite and nonnegative')
    phase=(seconds%PERIOD)/PERIOD
    return 70.0*(0.1+0.9*(1-math.cos(2*math.pi*phase))/2)

def run(gpio=None):
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
        gpio.setup(PIN,gpio.OUT,initial=gpio.HIGH)
        claimed=True
        pwm=gpio.PWM(PIN,200)
        pwm.start(duty_at(0))
        start=time.monotonic()
        while not stop:
            pwm.ChangeDutyCycle(duty_at(time.monotonic()-start))
            time.sleep(.025)
    finally:
        if pwm is not None: pwm.stop()
        if claimed:
            gpio.output(PIN,gpio.LOW)
            gpio.cleanup(PIN)
        for sig,handler in prev.items(): signal.signal(sig,handler)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    a=p.parse_args()
    if not a.execute: p.error('GPIO access requires --execute')
    run()

if __name__=='__main__': main()
