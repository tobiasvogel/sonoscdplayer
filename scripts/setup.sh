#!/usr/bin/env bash
set -euo pipefail

PACKAGES="packages.lst"
NETDIR="${NETDIR:-/sys/class/net}"

# Hostname suffix: last three MAC octets of the first usable interface.
# Prefer wlan0, then eth0, then any other non-loopback interface, so wired-only
# setups (no wlan0) work as well. Prints nothing and fails if none is found.
mac_suffix() {
  local dev addr
  for dev in wlan0 eth0 $(ls "${NETDIR}" 2>/dev/null); do
    [ "${dev}" = "lo" ] && continue
    [ -r "${NETDIR}/${dev}/address" ] || continue
    addr="$(cat "${NETDIR}/${dev}/address")"
    if [ -n "${addr}" ] && [ "${addr}" != "00:00:00:00:00:00" ]; then
      echo "${addr}" | cut -d ':' -f4-6 | tr -d ':'
      return 0
    fi
  done
  return 1
}

if [ "$(id -u)" -ne "0" ]; then
  echo "This script must be run as root!"
  exit 127
fi

MACSUFFIX="$(mac_suffix)" || {
  echo "No network interface with a MAC address found; cannot derive hostname." >&2
  exit 1
}

while read line
do
    if ! [[ "$line" =~ ^# ]]
    then
        if [ -n "$line" ]
        then
            a=( "${a[@]}" "$line" )
        fi
    fi
done < ${PACKAGES}

apt-get update
apt -y install "${a[@]}"

# set hostname
raspi-config nonint do_hostname "sonoscdplayer-${MACSUFFIX}"
# enable i2c
raspi-config nonint do_i2c 0
# enable spi
raspi-config nonint do_spi 0
# enable beta-access for boot-splash firmware
raspi-config nonint do_beta_mode B1

apt -y full-upgrade

# setup python venv
mkdir -p ~/sonoscdplayer
python3 -m venv --system-site-packages ~/sonoscdplayer/.venv
~/sonoscdplayer/.venv/bin/python -m pip install soco
~/sonoscdplayer/.venv/bin/python -m pip install Pillow

# build raspberrypi-tools
mkdir -p ~/src
cd ~/src
git clone https://github.com/raspberrypi/utils.git
cd utils
sh ./prerequisites.sh
cmake .
make
make install