#!/usr/bin/env python3
"""Transmit an LFM pulse train and capture RX with time-aligned UHD streams.

TX and RX are both scheduled at the same device time t0 (timed commands), so
sample k of the capture is k/fs seconds after the first pulse left the FPGA.
The capture is saved to data/captures/ (.npy + .json) and pulse-compressed
with a matched filter; the strongest range bins are printed.

Safety: TX is OFF unless --allow-tx. Without it the script still captures
RX (useful to check the noise/interference picture).

Examples:
    python3 scripts/lfm_pulse_train.py --dry-run
    python3 scripts/lfm_pulse_train.py                  # RX only, real radio
    python3 scripts/lfm_pulse_train.py --allow-tx --tx-gain 5
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

import _bootstrap  # noqa: F401
import numpy as np

from radar import usrp_util, waveforms, io_util, processing


def parse():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--args", "--addr", dest="args", default=usrp_util.DEFAULT_ADDR,
                   help="IP or UHD device args")
    p.add_argument("--rate", type=float, default=1e6, help="sample rate, S/s")
    # 2.45 GHz ISM default. Change for your daughterboard/licence, e.g. 915e6
    # (US ISM) or 5.8e9 (CBX only). WBX tops out at 2.2 GHz!
    p.add_argument("--freq", type=float, default=2.45e9, help="center freq, Hz")
    p.add_argument("--tx-gain", type=float, default=0.0, help="dB")
    p.add_argument("--rx-gain", type=float, default=20.0, help="dB")
    p.add_argument("--bandwidth", type=float, default=200e3, help="chirp BW, Hz")
    p.add_argument("--pulse-width", type=float, default=50e-6, help="s")
    p.add_argument("--pri", type=float, default=1e-3, help="pulse repetition interval, s")
    p.add_argument("--num-pulses", type=int, default=32)
    p.add_argument("--amplitude", type=float, default=0.5, help="TX digital amplitude (<=0.7)")
    p.add_argument("--cal", default=str(io_util.DATA_DIR / "cal" / "cal_latest.json"),
                   help="calibration JSON (optional; used for DC removal)")
    p.add_argument("--skip-bins", type=int, default=None,
                   help="ignore first N range bins in peak report (default: pulse length)")
    p.add_argument("--allow-tx", action="store_true", help="actually transmit")
    p.add_argument("--dry-run", action="store_true", help="no hardware, synthetic echo")
    p.add_argument("--target-range", type=float, default=30e3,
                   help="dry-run synthetic target range, m")
    p.add_argument("--out-dir", default=str(io_util.DATA_DIR / "captures"))
    return p.parse_args()


def synth_rx(tx: np.ndarray, fs: float, delay_s: float, doppler_hz: float = 50.0,
             snr_db: float = 10.0, leakage: float = 0.05, seed: int = 1) -> np.ndarray:
    """Dry-run channel: direct leakage + delayed/Doppler-shifted echo + noise."""
    rng = np.random.default_rng(seed)
    n = len(tx)
    d = int(round(delay_s * fs))
    echo = np.zeros(n, dtype=np.complex128)
    echo[d:] = tx[:n - d]
    t = np.arange(n) / fs
    echo *= 0.01 * np.exp(2j * np.pi * doppler_hz * t)
    noise_amp = 0.01 * 0.5 / (10 ** (snr_db / 20)) / np.sqrt(2)
    noise = noise_amp * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return (leakage * tx + echo + noise).astype(np.complex64)


def main() -> int:
    a = parse()
    fs = a.rate
    pulse = waveforms.lfm_pulse(fs, a.bandwidth, a.pulse_width, a.amplitude)
    train = waveforms.pulse_train(pulse, fs, a.pri, a.num_pulses)
    pri_n = waveforms.n_samples(a.pri, fs)
    params = waveforms.pulse_params(fs, a.bandwidth, a.pulse_width, a.pri, a.num_pulses)
    print(f"LFM: B={a.bandwidth/1e3:.0f} kHz, T={a.pulse_width*1e6:.0f} us, "
          f"PRI={a.pri*1e3:.2f} ms, N={a.num_pulses}, fs={fs/1e6:.2f} MS/s, "
          f"f0={a.freq/1e9:.4f} GHz")
    for k, v in params.items():
        print(f"  {k:>24}: {v:.6g}")

    cfg = usrp_util.RadioConfig(args=a.args, rate=fs, freq=a.freq,
                                tx_gain=a.tx_gain, rx_gain=a.rx_gain)
    meta = {"script": "lfm_pulse_train", "dry_run": a.dry_run, "allow_tx": a.allow_tx,
            "config": cfg.to_dict(), "bandwidth_hz": a.bandwidth,
            "pulse_width_s": a.pulse_width, "pri_s": a.pri,
            "num_pulses": a.num_pulses, "pri_samples": pri_n,
            "pulse_samples": len(pulse), "amplitude": a.amplitude,
            "derived": params}
    n_rx = len(train)
    errors: list = []

    if a.dry_run:
        delay = 2 * a.target_range / processing.C
        print(f"[dry-run] synthetic target at {a.target_range:.0f} m "
              f"(delay {delay*1e6:.1f} us = {delay*fs:.1f} samples)")
        # Dry-run always simulates TX so the processing chain can be exercised.
        rx = synth_rx(train, fs, delay)
        meta["synthetic_target_range_m"] = a.target_range
    else:
        if not a.allow_tx:
            print("TX disabled (no --allow-tx): RX-only capture")
        try:
            usrp = usrp_util.open_usrp(cfg)
        except Exception as exc:
            print(f"ERROR: could not open USRP: {exc}", file=sys.stderr)
            return 1
        meta["device"] = usrp_util.describe(usrp, cfg.chan)
        tx_st, rx_st = usrp_util.get_streamers(usrp, cfg.chan)
        t0 = usrp_util.device_time(usrp) + 0.25  # same start tick for TX and RX
        th = None
        if a.allow_tx:
            th = threading.Thread(target=usrp_util.tx_burst, args=(tx_st, train, t0))
            th.start()
        rx, errors = usrp_util.rx_capture(usrp, rx_st, n_rx, start_time=t0)
        if th is not None:
            th.join()
            meta["tx_async"] = usrp_util.tx_async_errors(tx_st)
            print(f"TX async events: {meta['tx_async']}")
        meta["t0_device_s"] = t0
        if errors:
            print(f"WARNING: RX errors: {errors}")
        if len(rx) < pri_n:
            print("ERROR: capture shorter than one PRI", file=sys.stderr)
            return 1
    meta["rx_errors"] = errors

    npy = io_util.save_iq(rx, meta, a.out_dir, name=f"lfm_{io_util.timestamp()}")
    print(f"Saved {npy} (+ .json), {len(rx)} samples")

    # --- processing (swap in custom algorithms here) ---
    try:
        import json
        from pathlib import Path
        cal = json.loads(Path(a.cal).read_text())
        dc = complex(cal["rx_noise"]["dc_offset_i"], cal["rx_noise"]["dc_offset_q"])
        # Only apply a real-hardware cal to real-hardware data.
        if not cal.get("dry_run") and not a.dry_run:
            rx = rx - np.complex64(dc)
            print(f"Removed DC offset from cal: {dc:.4g}")
    except (OSError, KeyError, ValueError):
        pass

    cube = processing.to_cube(rx, pri_n, a.num_pulses)
    comp = processing.matched_filter(cube, pulse)
    skip = a.skip_bins if a.skip_bins is not None else len(pulse)
    rep = processing.peak_report(comp, fs, skip_bins=skip)
    print(f"Pulse compression: {cube.shape[0]} pulses x {pri_n} bins, "
          f"noise floor {rep['noise_floor_db']:.1f} dB, skipped first {skip} bins")
    for p in rep["peaks"]:
        print(f"  bin {p['bin']:5d}  delay {p['delay_s']*1e6:8.1f} us  "
              f"range {p['range_m_monostatic']/1e3:7.2f} km  "
              f"{p['power_db']:6.1f} dB  SNR {p['snr_db']:5.1f} dB")
    if rep["peaks"]:
        rd = processing.doppler_fft(comp)
        k = rep["peaks"][0]["bin"]
        dbin = int(np.argmax(np.abs(rd[:, k]))) - rd.shape[0] // 2
        fd = dbin / (rd.shape[0] * a.pri)
        print(f"  strongest peak Doppler ~ {fd:.1f} Hz "
              f"(resolution {1/(rd.shape[0]*a.pri):.1f} Hz)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
