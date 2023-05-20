#!/usr/bin/env bash


if [[ $(id -u) != 0 ]] ; then
  echo Run as root
 exit
fi

apt update
apt upgrade
pip3 install -r requirements.txt
chmod +x scanner.py



