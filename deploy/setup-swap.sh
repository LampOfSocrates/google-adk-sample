#!/usr/bin/env sh
# Add a 2 GB swapfile so the box survives memory spikes. Run ONCE on the host.
# The box has no swap today; without this, one PDF-parse spike can OOM-kill
# whatever else is running.
set -e

if swapon --show | grep -q /swapfile; then
  echo "swapfile already active:"
  free -m
  exit 0
fi

sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Persist across reboots.
grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

echo "swap enabled:"
free -m
