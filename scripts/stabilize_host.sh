#!/usr/bin/env bash
# One-shot N210/Linux host hardening for the ThinkCentre ↔ USRP link.
# Run: sudo ./scripts/stabilize_host.sh
set -euo pipefail

IFACE="${IFACE:-eno1}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Re-run as root: sudo $0" >&2
  exit 1
fi

echo "== sysctl buffers =="
install -m 644 "$REPO_ROOT/host/99-usrp-buffers.conf" /etc/sysctl.d/99-usrp-buffers.conf
sysctl --system >/dev/null || sysctl -p /etc/sysctl.d/99-usrp-buffers.conf
sysctl net.core.wmem_max net.core.rmem_max net.core.wmem_default net.core.rmem_default

echo "== NIC ring buffers on ${IFACE} =="
if command -v ethtool >/dev/null; then
  ethtool -G "$IFACE" rx 4096 tx 4096 || true
  ethtool -g "$IFACE" | sed -n '1,20p'
  # Disable offloads that sometimes hurt latency with USRP (best-effort)
  ethtool -K "$IFACE" gro off gso off tso off 2>/dev/null || true
else
  echo "ethtool not installed; skip rings"
fi

echo "== realtime limits for UHD =="
install -m 644 "$REPO_ROOT/host/99-usrp-limits.conf" /etc/security/limits.d/99-usrp-limits.conf
echo "Installed; log out/in (or reboot) for rtprio to apply to new sessions."

echo "== NetworkManager wired profile sanity =="
# Keep internet on Wi-Fi; USRP subnet never becomes default route
if nmcli -t -f NAME,DEVICE connection show | grep -q ":${IFACE}$"; then
  CON=$(nmcli -t -f NAME,DEVICE connection show | awk -F: -v i="$IFACE" '$2==i{print $1; exit}')
  nmcli connection modify "$CON" ipv4.never-default yes connection.autoconnect yes ipv4.may-fail no || true
  nmcli connection up "$CON" || true
  echo "Profile: $CON"
fi

echo "== verify =="
ip -br addr show "$IFACE" || true
ping -c 2 -W 1 192.168.10.36 || true
uhd_find_devices --args="addr=192.168.10.36" || uhd_find_devices || true
echo "Done. If UHD still warns about wmem, re-open a shell and recheck sysctl."
