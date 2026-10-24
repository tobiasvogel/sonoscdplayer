#!/usr/bin/env bash
set -euo pipefail

PACKAGES="packages.lst"
WLANMAC="$(cat /sys/class/net/wlan0/address | cut -d ':' -f4-6 | tr -d ':')"

if [ "$(id -u)" -ne "0" ]; then
  echo "This script must be run as root!"
  exit 127
fi

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
apt install "${a[@]}"

# set hostname
raspi-config nonint do_hostname "sonoscdplayer-${WLANMAC}"
# enable i2c
raspi-config nonint do_i2c 0
# enable spi
raspi-config nonint do_spi 0
