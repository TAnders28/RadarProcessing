# Linux host stability for the N210

Goal: a link that comes up the same way every boot and never drops samples.

## 1. Dedicated NIC, static IP, never-default

Use a wired port only for the N210 (here `eno1`). With NetworkManager:

```bash
sudo nmcli con add type ethernet ifname eno1 con-name usrp \
     ipv4.method manual ipv4.addresses 192.168.10.1/24 \
     ipv4.never-default yes ipv6.method disabled \
     802-3-ethernet.mtu 1500 connection.autoconnect yes
sudo nmcli con up usrp
# if an old "Wired connection 1" exists on eno1, delete or disable it:
nmcli con show ; sudo nmcli con mod "Wired connection 1" connection.autoconnect no
```

* **never-default** stops the radio link stealing the default route from Wi-Fi
  (and Wi-Fi/DHCP stealing ours). Check with `ip route` — default should be via
  Wi-Fi, `192.168.10.0/24 dev eno1` only.
* **MTU**: the N210 uses ≤1472-byte payloads; standard **1500** is correct.
  Jumbo frames are not needed (unlike X3xx/N3xx).
* Disable power-saving on the NIC: `sudo ethtool -s eno1 wol d` and avoid
  `TLP`/`powertop --auto-tune` touching it. Check link: `ethtool eno1`
  (expect `Speed: 1000Mb/s`, `Duplex: Full`).
* Plug the N210 straight into the PC (no switch) for the cleanest timing.

## 2. Verify

```bash
ip addr show eno1                 # 192.168.10.1/24, state UP
ping -c 3 192.168.10.36
uhd_find_devices --args addr=192.168.10.36
uhd_usrp_probe   --args addr=192.168.10.36   # FPGA/firmware compat + daughterboard
benchmark_rate --args addr=192.168.10.36 --rx_rate 10e6 --tx_rate 10e6 --duration 30
```

`uhd_usrp_probe` complaining about **firmware/FPGA compat number** → reflash
with `uhd_image_loader --args="type=usrp2,addr=192.168.10.36"` after
`sudo uhd_images_downloader`. Keep host UHD and images in step.

## ThinkCentre quick harden (this machine)

Already done without root:
- NM `Wired connection 1` on `eno1`: static `192.168.10.1/24`, `never-default yes`, autoconnect on
- CPU governor: `performance`
- This N210: `192.168.10.36`, serial `E8R23N4UP`, SBX, GPSDO present

Needs one root pass (buffers + NIC rings + UHD rtprio):

```bash
cd ~/RadarProcessing
sudo ./scripts/stabilize_host.sh
```

That installs `host/99-usrp-buffers.conf` and `host/99-usrp-limits.conf`, raises `eno1` rings to 4096, and re-checks ping/find.

## 3. Socket buffers

UHD warns if kernel socket buffers are small. Make them persistent:

```bash
sudo tee /etc/sysctl.d/90-uhd.conf <<'X'
net.core.rmem_max = 33554432
net.core.wmem_max = 33554432
net.core.rmem_default = 33554432
net.core.wmem_default = 33554432
X
sudo sysctl --system
```

You can also pass `recv_buff_size=...,send_buff_size=...` in the device args.
Increase NIC ring buffers if supported: `sudo ethtool -G eno1 rx 4096 tx 4096`.

## 4. CPU / scheduling

* Governor to performance while capturing:
  `sudo cpupower frequency-set -g performance` (package `linux-tools-common`)
  or `echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`.
* Allow UHD to raise thread priority (removes "Unable to set thread priority"):
  add `@usrp - rtprio 99` to `/etc/security/limits.conf`, `sudo groupadd usrp`,
  `sudo usermod -aG usrp $USER`, log out/in.
* Keep heavy processing out of the recv loop: capture to RAM, save, process
  afterwards (the scripts do this). Start at 1–5 MS/s; N210 max is 25 MS/s sc16.

## 5. Common failure modes

| Symptom | Cause / fix |
|---|---|
| `eno1` NO-CARRIER / `ethtool` link no | cable not seated in N210, N210 not powered/booted (~10–30 s), bad cable |
| ping fails, ARP incomplete | wrong host IP/subnet, N210 IP changed (try `uhd_find_devices` broadcast; recover with N210 safe-mode button: typically boots at 192.168.10.2; this unit currently uses 192.168.10.36) |
| Internet dies when N210 connected | eno1 grabbed the default route → set `ipv4.never-default yes` |
| N210 unreachable after reboot | DHCP/auto profile on eno1 overriding static → disable other profiles |
| `O`/overflow printed | rate too high, small buffers, CPU throttling, processing in recv loop |
| `U`/underflow or `L` late | TX not fed fast enough / `t0` too close to now — schedule further ahead |
| compat number mismatch | reflash FPGA/firmware to match host UHD |
| `ImportError: uhd` in venv | venv lacks system site-packages → `python3 -m venv --system-site-packages .venv` |
