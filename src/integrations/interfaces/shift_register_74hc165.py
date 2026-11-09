"""GPIO-driven 74HC165 input sampler; no player or Sonos commands.

The nearest chip to the DATA pin uses channels 0..7 (D0..D7).
Further cascaded chips use channels 8..15, and so on.
"""

class ShiftRegisterReader:
    def __init__(self, gpio, registers):
        self.gpio = gpio
        self.registers = registers

    def setup(self):
        gpio = self.gpio
        for config in self.registers.values():
            gpio.setup(config.load_pin, gpio.OUT, initial=gpio.HIGH)
            gpio.setup(config.clock_pin, gpio.OUT, initial=gpio.LOW)
            gpio.setup(config.data_pin, gpio.IN)

    def sample(self):
        """Latch parallel inputs and read all bits; return source-keyed levels."""
        gpio = self.gpio
        levels = {}
        for name, config in self.registers.items():
            gpio.output(config.clock_pin, gpio.LOW)
            gpio.output(config.load_pin, gpio.LOW)
            gpio.output(config.load_pin, gpio.HIGH)
            # Q7 of the chip closest to the Pi is shifted out first.
            for serial_index in range(config.chips * 8):
                chip = serial_index // 8
                parallel_bit = 7 - serial_index % 8
                levels[f'{name}:{chip * 8 + parallel_bit}'] = int(bool(
                    gpio.input(config.data_pin)))
                if serial_index + 1 < config.chips * 8:
                    gpio.output(config.clock_pin, gpio.HIGH)
                    gpio.output(config.clock_pin, gpio.LOW)
        return levels
