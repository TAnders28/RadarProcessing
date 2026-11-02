# RadarProcessing — pure-Python UHD radar on an Ettus N210

A from-scratch software-defined radar starter: NumPy waveforms, UHD Python
streaming, NumPy processing. **No GNU Radio / gr-radar** — every stage is a
plain `complex64` array so custom algorithms can be dropped in later.

```
radar/
  usrp_util.py    open/configure MultiUSRP, StreamArgs("fc32","sc16"), timed TX/RX
  waveforms.py    LFM pulse, pulse train (PRI zero-fill), CW tone, derived radar params
  io_util.py      save/load IQ .npy + JSON sidecar under data/
  processing.py   to_cube -> matched_filter -> doppler_fft / peak_report (extend here)
scripts/
  calibrate_n210.py   run once per setup/band: device info, noise floor, DC, optional TX leakage
  lfm_pulse_train.py  transmit + capture LFM pulse train, save, pulse-compress
docs/host_stability.md  Linux host hardening for a reliable N210 link
data/cal/  data/captures/   outputs (git-ignored)
```

## Quick API

```python
from radar import Calibrate, TransmitReceivePulses

cal = Calibrate()                          # RX-only health check → data/cal/
cap = TransmitReceivePulses(allow_tx=False)  # timed LFM capture → data/captures/
# cap["iq"] is complex64 NumPy for your later algorithms
```

Or run the demo:

```bash
python3 examples/barebones_demo.py            # dry-run
python3 examples/barebones_demo.py --live     # real N210, TX still off
```

## Host / network setup (summary — details in `docs/host_stability.md`)

* Dedicated wired NIC to the N210 (e.g. `eno1`), static **192.168.10.1/24**.
  This N210 is at **192.168.10.36** (factory default is often `.2`; discover with `uhd_find_devices`).
* Mark that connection **never-default** so it never steals the default route
  from Wi-Fi / your internet NIC.
* Install UHD as a **system package** (`uhd-host`, `python3-uhd`, or Ettus
  PPA / source build), then `sudo uhd_images_downloader`.
* Check: `ping 192.168.10.36` and `uhd_find_devices --args addr=192.168.10.36`
  then `uhd_usrp_probe --args addr=192.168.10.36`.

```bash
pip install -r requirements.txt     # numpy scipy matplotlib (uhd from system)
```

## Run

```bash
# 0. Without hardware (no uhd needed) — exercises the whole chain
python3 scripts/calibrate_n210.py --dry-run
python3 scripts/lfm_pulse_train.py --dry-run

# 1. Calibrate once per setup / band (RX only, TX stays off)
python3 scripts/calibrate_n210.py --args 192.168.10.36 --freq 2.45e9 --rx-gain 20

# 2. Capture (RX only until antennas are fitted)
python3 scripts/lfm_pulse_train.py --freq 2.45e9

# 3. With TX antenna/load fitted: leakage check, then real pulses
python3 scripts/calibrate_n210.py --allow-tx --tx-gain 0
python3 scripts/lfm_pulse_train.py --allow-tx --tx-gain 5
```

Defaults: `rate=1 MS/s, BW=200 kHz, PW=50 µs, PRI=1 ms, 32 pulses, f0=2.45 GHz`
(range resolution ≈ 750 m — this is a pipeline-validation config, not a
short-range radar; raise `--rate`/`--bandwidth` toward 10–25 MS/s for finer
range once the link is proven). Change `--freq` to suit your daughterboard
(WBX ≤ 2.2 GHz, SBX 0.4–4.4 GHz, CBX 1.2–6 GHz) and your licence/ISM band.

## Safety

* **TX is off by default.** Nothing radiates unless you pass `--allow-tx`.
* Never transmit into an open port at high gain — fit an antenna or 50 Ω load.
  Start with `--tx-gain 0` and low `--amplitude`.
* Stay within a band you are allowed to use; keep power low indoors.
* Monostatic on one N210 has strong TX→RX leakage; use separate, physically
  separated TX/RX antennas (TX on TX/RX port, RX on RX2), add attenuation or
  a circulator, or move to two MIMO-cabled N210s for bistatic.

## How timing works

`usrp_util.open_usrp` zeroes the device clock; `lfm_pulse_train.py` schedules
TX (`TXMetadata.time_spec`) and RX (`StreamCMD` `num_done`, `stream_now=False`)
at the same device time `t0`, so range bin *k* = *k / fs* seconds of delay plus
a fixed hardware offset you can measure later and pass to `processing.to_cube`.
