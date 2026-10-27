import RPi.GPIO as GPIO
import tomllib
import time

with open("interface.toml", "rb") as f:
    data = tomllib.load(f)

# Define GPIO pins of the Encoder
CLK = data["encoder"]["encoder_a"]
DT = data["encoder"]["encoder_b"] 
SW = data["encoder"]["encoder_sw"]
counter = 0
last_state = None

# Define GPIO pins of the Pushbutton-Interface
PWR = data["buttons"]["power"]
CONN = data["buttons"]["connect"]
EJE = data["buttons"]["eject"]
PREV = data["buttons"]["previous"]
STP = data["buttons"]["stop"]
PLAYP = data["buttons"]["playpause"]
SKP = data["buttons"]["skip"]
VOLUP = data["buttons"]["volume_up"]
VOLDN = data["buttons"]["volume_dn"]

# Setup GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(CLK, GPIO.IN)
GPIO.setup(DT, GPIO.IN)
GPIO.setup(SW, GPIO.IN)
GPIO.setup(PWR, GPIO.IN)
GPIO.setup(CONN, GPIO.IN)
GPIO.setup(EJE, GPIO.IN)
GPIO.setup(PREV, GPIO.IN)
GPIO.setup(STP, GPIO.IN)
GPIO.setup(PLAYP, GPIO.IN)
GPIO.setup(SKP, GPIO.IN)
GPIO.setup(VOLUP, GPIO.IN)
GPIO.setup(VOLDN, GPIO.IN)

button_messages = {
    PWR: "Power Button Pressed!",
    CONN: "Connect Button Pressed!",
    EJE: "Eject Button Pressed!",
    PREV: "Previous Button Pressed!",
    STP: "Stop Button Pressed!",
    PLAYP: "Play/Pause Button Pressed!",
    SKP: "Skip (Next) Button Pressed!",
    VOLUP: "Volume+ Button Pressed!",
    VOLDN: "Volume- Button Pressed!"
}

# Read initial state
last_state = GPIO.input(CLK)

def button_pressed(channel):
    # Holt die passende Nachricht aus dem Dictionary (mit Fallback, falls der Pin unbekannt ist)
    message = button_messages.get(channel, f"Unbekannter Pin {channel} gedrückt")
    print(message)

GPIO.add_event_detect(SW, GPIO.FALLING, callback=button_pressed)
GPIO.add_event_detect(PWR, GPIO.FALLING, callback=button_pressed)
GPIO.add_event_detect(CONN, GPIO.FALLING, callback=button_pressed)
GPIO.add_event_detect(EJE, GPIO.FALLING, callback=button_pressed)
GPIO.add_event_detect(PREV, GPIO.FALLING, callback=button_pressed)
GPIO.add_event_detect(STP, GPIO.FALLING, callback=button_pressed)
GPIO.add_event_detect(PLAYP, GPIO.FALLING, callback=button_pressed)
GPIO.add_event_detect(SKP, GPIO.FALLING, callback=button_pressed)
GPIO.add_event_detect(VOLUP, GPIO.FALLING, callback=button_pressed)
GPIO.add_event_detect(VOLDN, GPIO.FALLING, callback=button_pressed)

try:
    while True:
        current_state = GPIO.input(CLK)
        if current_state != last_state:
            if GPIO.input(DT) != current_state:
                counter += 1
                direction = "Clockwise"
            else:
                counter -= 1
                direction = "Counterclockwise"
            print(f"Position: {counter}, Direction: {direction}")
        last_state = current_state
        time.sleep(0.01)  # Debounce delay
except KeyboardInterrupt:
    print("Exiting...")
finally:
    GPIO.cleanup()
