#!/usr/bin/env python3
"""One-shot N210 calibration / health check.

1. Connect and print motherboard / daughterboard, rates, freqs, gains.
2. RX-only capture (TX idle): noise floor, DC offset, rms, peak, overflows.
3. Optional (--allow-tx): low-gain offset tone, measure TX->RX leakage.
4. Write a timestamped JSON cal file under data/cal/ (and cal_latest.json).

Exit code 1 on hard failure (no device, short/invalid capture).

Examples:
    python3 scripts/calibrate_n210.py --dry-run
    python3 scripts/calibrate_n210.py --args 192.168.10.2 --freq 2.45e9
    python3 scripts/calibrate_n210.py --allow-tx --tx-gain 0   # needs load/antenna!
"""

from __future__ import annotations

import argparse
import sys
import time

import _bootstrap  # noqa: F401
import numpy as np

from radar import usrp_util, waveforms, io_util


def iq_stats(x: np.ndarray) -> dict:
    dc = complex(np.mean(x))
    ac = x - dc
    p = np.abs(x) ** 2
    return {
        "dc_offset_i": dc.real,
        "dc_offset_q": dc.imag,
        "dc_offset_dbfs": 20 * np.log10(abs(dc) + 1e-12),
        "mean_power_dbfs": 10 * np.log10(np.mean(p) + 1e-20),
        "rms": float(np.sqrt(np.mean(p))),
        "rms_ac": float(np.sqrt(np.mean(np.abs(ac) ** 2))),
        "peak_abs": float(np.max(np.abs(x))),
        "peak_dbfs": 20 * np.log10(np.max(np.abs(x)) + 1e-12),
        "iq_amp_ratio": float(np.std(x.real) / (np.std(x.imag) + 1e-20)),
        "clipping_frac": float(np.mean((np.abs(x.real) > 0.99) | (np.abs(x.imag) > 0.99))),
    }


def tone_power_dbfs(x: np.ndarray, fs: float, f_hz: float, half_bw_bins: int = 3) -> float:
    n = len(x)
    X = np.fft.fft(x * np.hanning(n)) / (np.sum(np.hanning(n)))
    freqs = np.fft.fftfreq(n, 1 / fs)
    k = int(np.argmin(np.abs(freqs - f_hz)))
    sl = [(k + d) % n for d in range(-half_bw_bins, half_bw_bins + 1)]
    return float(10 * np.log10(np.sum(np.abs(X[sl]) ** 2) + 1e-20))


def parse():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--args", default=usrp_util.DEFAULT_ADDR,
                   help="IP or UHD device args (default addr=192.168.10.2)")
    p.add_argument("--rate", type=float, default=1e6, help="sample rate, S/s")
    p.add_argument("--freq", type=float, default=2.45e9,
                   help="center freq, Hz (must be in daughterboard range)")
    p.add_argument("--rx-gain", type=float, default=20.0, help="dB")
    p.add_argument("--tx-gain", type=float, default=0.0, help="dB (keep low)")
    p.add_argument("--duration", type=float, default=0.5, help="capture length, s")
    p.add_argument("--allow-tx", action="store_true",
                   help="enable leakage test (radiates! needs antenna or 50-ohm load)")
    p.add_argument("--tone-offset", type=float, default=None,
                   help="tone baseband offset, Hz (default rate/10)")
    p.add_argument("--out-dir", default=str(io_util.DATA_DIR / "cal"))
    p.add_argument("--dry-run", action="store_true",
                   help="no hardware: synthetic noise (+ leakage if --allow-tx)")
    return p.parse_args()


def main() -> int:
    a = parse()
    fs = a.rate
    n = waveforms.n_samples(a.duration, fs)
    tone_hz = a.tone_offset if a.tone_offset is not None else fs / 10
    cfg = usrp_util.RadioConfig(args=a.args, rate=fs, freq=a.freq,
                                tx_gain=a.tx_gain, rx_gain=a.rx_gain)
    cal = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "dry_run": a.dry_run, "config": cfg.to_dict()}
    errors: list[str] = []

    if a.dry_run:
        print("[dry-run] no hardware; synthesising RX noise")
        rng = np.random.default_rng(0)
        cal["device"] = {"mboard": "DRY-RUN", "rx_subdev": "synthetic"}
        rx = (0.003 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
              + (0.002 - 0.001j)).astype(np.complex64)
    else:
        try:
            usrp = usrp_util.open_usrp(cfg)
        except Exception as exc:
            print(f"ERROR: could not open/configure USRP ({cfg.args}): {exc}", file=sys.stderr)
            print("  check: ip addr show eno1 ; ping 192.168.10.2 ; uhd_find_devices",
                  file=sys.stderr)
            return 1
        cal["device"] = usrp_util.describe(usrp, cfg.chan)
        tx_st, rx_st = usrp_util.get_streamers(usrp, cfg.chan)
        # Discard a short capture first: settles DC/AGC-free front end.
        usrp_util.rx_capture(usrp, rx_st, int(0.05 * fs))
        rx, errors = usrp_util.rx_capture(usrp, rx_st, n)

    print("Device:")
    for k, v in cal["device"].items():
        print(f"  {k:>16}: {v}")

    if len(rx) < n or not np.all(np.isfinite(rx)):
        print(f"ERROR: bad RX capture ({len(rx)}/{n} samples) errors={errors}", file=sys.stderr)
        return 1
    if errors:
        print(f"WARNING: RX errors during capture: {errors}")

    rx_stats = iq_stats(rx)
    cal["rx_noise"] = rx_stats
    cal["rx_errors"] = errors
    print("RX-only (TX idle):")
    for k, v in rx_stats.items():
        print(f"  {k:>16}: {v:.6g}")
    if rx_stats["clipping_frac"] > 1e-4:
        print("WARNING: RX clipping with TX idle -> lower --rx-gain or strong interferer")

    if a.allow_tx:
        print("TX leakage test: low-gain tone at "
              f"{tone_hz/1e3:.1f} kHz offset, tx_gain={a.tx_gain} dB")
        print("  WARNING: make sure an antenna or 50-ohm load is on TX/RX; "
              "never transmit into an open port at high gain.")
        tone = waveforms.cw_tone(fs, a.duration + 0.2, tone_hz, amplitude=0.3)
        if a.dry_run:
            m = len(rx)
            t = np.arange(m) / fs
            rx_tx = (rx + 0.01 * np.exp(2j * np.pi * tone_hz * t)).astype(np.complex64)
            tx_errs = []
        else:
            import threading
            t0 = usrp_util.device_time(usrp) + 0.2
            th = threading.Thread(target=usrp_util.tx_burst, args=(tx_st, tone, t0))
            th.start()
            rx_tx, rx_errs = usrp_util.rx_capture(usrp, rx_st, n, start_time=t0 + 0.1)
            th.join()
            tx_errs = usrp_util.tx_async_errors(tx_st)
            if rx_errs:
                print(f"WARNING: RX errors in leakage test: {rx_errs}")
        if len(rx_tx) < n:
            print("ERROR: short capture in leakage test", file=sys.stderr)
            return 1
        leak = tone_power_dbfs(rx_tx, fs, tone_hz)
        floor = tone_power_dbfs(rx, fs, tone_hz)
        cal["tx_leakage"] = {
            "tone_offset_hz": tone_hz, "tx_gain_db": a.tx_gain,
            "rx_tone_dbfs": leak, "rx_floor_at_tone_dbfs": floor,
            "leakage_above_floor_db": leak - floor,
            "tx_async": tx_errs, "rx_stats_with_tx": iq_stats(rx_tx),
        }
        print(f"  tone at RX: {leak:.1f} dBFS (floor at that bin {floor:.1f} dBFS, "
              f"+{leak - floor:.1f} dB)")
        if iq_stats(rx_tx)["clipping_frac"] > 1e-4:
            print("  WARNING: RX clipping with TX on -> more isolation / lower gains")
    else:
        print("TX leakage test skipped (pass --allow-tx once an antenna/load is fitted)")

    out = io_util.save_json(f"{a.out_dir}/cal_{io_util.timestamp()}.json", cal)
    io_util.save_json(f"{a.out_dir}/cal_latest.json", cal)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
