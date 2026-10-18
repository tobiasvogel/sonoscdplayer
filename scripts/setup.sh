#!/usr/bin/env bash
set -euo pipefail

PACKAGES=$(. ./packages.lst)

if [ "$(id -u)" -ne "0" ]; then
  echo "This script must be run as root!"
  exit 127
fi

apt-get update
apt install ${PACKAGES}
