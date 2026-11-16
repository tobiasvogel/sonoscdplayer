"""WLAN setup through a temporary hotspot (NET-01, D-223).

CONNECT held for 10 s starts a WPA2 hotspot with a random password; a small
web page on the hotspot address lets the user pick their WLAN and enter its
password. Everything goes through NetworkManager (nmcli); nothing is written
outside NetworkManager's own connection profiles.
"""
