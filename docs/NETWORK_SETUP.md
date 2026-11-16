# Wi-Fi setup (setup hotspot)

The player can join a new Wi-Fi network without a keyboard, screen or SSH access. It opens a temporary hotspot; a phone or laptop connects to it and enters the Wi-Fi details in a small web page.

## Starting and ending

| Action | Result |
| --- | --- |
| Hold CONNECT for 10 seconds | Starts the setup (a countdown appears from 5 seconds) |
| Hold CONNECT for 3–10 seconds, release | Opens the network menu with **Set up Wi-Fi** selected |
| Menu **Network → Set up Wi-Fi** | Starts the setup |
| Hold CONNECT for 10 seconds again | Ends the setup |
| No input for 15 minutes | The hotspot ends by itself |

The setup does not start while a CD is playing or paused; stop playback first. While the setup screen is shown, other buttons are ignored (POWER still opens the power menu).

## Flow

1. The player scans the visible Wi-Fi networks, then opens the hotspot `SonosCDPlayer-XXXX` (`XXXX`: last four digits of the Wi-Fi hardware address).
2. The display shows the hotspot name, a random 8-character password (WPA2, without the easily confused characters 0/O, 1/I/L) and the address to open, normally `http://10.42.0.1`.
3. Connect the phone to the hotspot and open the address. Choose a network from the list or enter the name of a hidden network, enter its password (empty for an open network) and press **Connect**.
4. The hotspot ends and the player connects to the chosen network. The display shows the result.
   - Success: "Connected to …"; the hotspot profile is deleted.
   - Failure (for example a wrong password): the hotspot opens again with the same name and password, and the page shows a hint. The 15-minute limit starts again.

The page has no other function, loads no external resources and is reachable only on the hotspot while it is active. Phones often show it automatically as a sign-in page.

## Stored networks

- Networks added here are saved as NetworkManager profiles named `sonoscdplayer-wifi-<hash>`.
- The newest one gets autoconnect priority 100; earlier ones added here drop to 90.
- Other Wi-Fi profiles (for example from Raspberry Pi Imager) are kept unchanged with their own priority, so the player still falls back to them when the new network is out of reach.
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
