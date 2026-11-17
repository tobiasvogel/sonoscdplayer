# Wi-Fi setup (setup hotspot)

The player can join a new Wi-Fi network without a keyboard, screen or SSH access. It opens a temporary hotspot; a phone or laptop connects to it and enters the Wi-Fi details in a small web page. Without a phone, the network can also be chosen and its password entered on the player itself with the rotary encoder.

## Starting and ending

| Action | Result |
| --- | --- |
| Hold CONNECT for 10 seconds | Starts the setup (from 5 seconds the countdown "Network setup via hotspot in …" appears) |
| Hold CONNECT for 3–10 seconds, release | Opens the network menu with **Set up Wi-Fi** selected |
| Menu **Network → Set up Wi-Fi** | Starts the setup |
| Hold CONNECT for 10 seconds again | Ends the setup (the countdown reads "Setup ends in …"; releasing earlier keeps the setup running) and returns to the screen shown before it |
| Press the encoder on the hotspot screen | Ends the hotspot and shows the network list on the display (see [Entering on the device](#entering-on-the-device)) |
| No input for 15 minutes | The setup ends by itself |

The setup does not start while a CD is playing or paused; stop playback first. While the setup screen is shown, other buttons are ignored (POWER still opens the power menu).

## Flow

1. The player scans the visible Wi-Fi networks, then opens the hotspot `SonosCDPlayer-XXXX` (`XXXX`: last four digits of the Wi-Fi hardware address).
2. The display shows the hotspot name, a random 8-character password (WPA2, without the easily confused characters 0/O, 1/I/L) and the address to open, normally `http://10.42.0.1`.
3. Connect the phone to the hotspot and open the address. Choose a network from the list or enter the name of a hidden network, enter its password (empty for an open network) and press **Connect**.
4. The hotspot ends and the player connects to the chosen network. The display shows the result.
   - Success: "Connected to …"; the hotspot profile is deleted.
   - Failure (for example a wrong password): the hotspot opens again with the same name and password, and the page shows a hint. The 15-minute limit starts again.

The page has no other function, loads no external resources and is reachable only on the hotspot while it is active. Phones often show it automatically as a sign-in page.

## Entering on the device

The hotspot screen alternates between the hints "Press: enter manually" and "CONNECT 10 s: cancel". Pressing the encoder ends the hotspot, searches again and shows:

- the visible networks, strongest first; a ✓ marks networks with a stored profile. The status line shows signal strength and whether the network is saved, secured or open;
- **Search networks** to search again (for example after switching on a phone hotspot; phones often show their hotspot only while its settings page is open);
- **Hidden network …** to type its name;
- **Back to hotspot**, which reopens the hotspot with the same name and password.

Choosing a network:

| Network | What happens |
| --- | --- |
| Stored profile (✓) | Connects right away without asking. If that fails, the password entry opens. |
| Open | Connects right away. |
| Secured | The password entry opens. |
| Hidden | Name entry, then password entry (leave it empty for an open network). |

**Text entry:** turning the encoder moves a character wheel, pressing takes the character shown in the frame. The wheel holds all printable ASCII characters (a–z, A–Z, 0–9, space shown as ␣, punctuation) and three actions, reached by turning left from **a**: ✓ **Done**, ⌫ **Delete last character**, ✕ **Cancel** (back to the list). Turning fast skips four characters per step. A short press on CONNECT works as **Done** as well. The password is shown in plain text, so typing errors can be seen; the counter shows the length used and allowed (WLAN names: bytes, at most 32; passwords: 8–63 characters).

Names can also contain the extra characters `äöüÄÖÜß`. Change them in `ui.toml` (see [Configuration](CONFIGURATION.md)):

```toml
[ui.text_input]
extra_characters = "äöüÄÖÜß"
```

Passwords are always ASCII, as WPA2 requires.

After a wrong password, the entry opens again with the text kept and a hint. Without input for 15 minutes the setup ends; CONNECT 10 s ends it at any time.

## Stored networks

- Networks added here are saved as NetworkManager profiles named `sonoscdplayer-wifi-<hash>`.
- The newest one gets autoconnect priority 100; earlier ones added here drop to 90.
- Other Wi-Fi profiles (for example from Raspberry Pi Imager) are kept unchanged with their own priority, so the player still falls back to them when the new network is out of reach. On the device they are only activated, never changed.
- Hidden networks are stored as hidden, so the player also finds them again after a restart.
- To remove a network: `nmcli connection delete id <profile>`.

## Connection LED

While the hotspot is active, the connection LED blinks fast (see [Status LEDs](STATUS_LEDS.md)).

## Requirements

- NetworkManager (default on Raspberry Pi OS Bookworm and later).
- The Wi-Fi country must be set (`raspi-config` → Localisation → WLAN Country). Without it, the Wi-Fi radio stays blocked and no hotspot can start.
- On a Raspberry Pi 4 the Wi-Fi chip cannot be hotspot and client at the same time. During the setup the Pi is therefore not connected over Wi-Fi; a LAN cable keeps working.
- The OLED service needs the rights described below. Units rendered with `tools/render_runtime_units.py` include them by default; `--no-network-setup` leaves the feature out.

## Permissions

The OLED service runs the setup as the unprivileged service account:

- `--ui-network-setup` in the OLED unit enables the feature.
- `AmbientCapabilities=CAP_NET_BIND_SERVICE` lets the page listen on port 80. Without it, port 8080 is used and shown on the display.
- The polkit rule [`systemd/polkit/50-sonoscdplayer-network.rules`](../systemd/polkit/50-sonoscdplayer-network.rules) allows this one account to scan Wi-Fi, share a hotspot and add or activate connection profiles. Do not install the template directly; `tools/render_runtime_units.py` renders a copy for the account given with `--service-user`. Install it with:

```sh
sudo install -m 0644 \
    /tmp/sonoscdplayer-units/50-sonoscdplayer-network.rules \
    /etc/polkit-1/rules.d/
```

To revoke the permission, remove the installed rule.
