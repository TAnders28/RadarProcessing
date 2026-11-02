#!/usr/bin/env python3
"""CLI wrapper around radar.TransmitReceivePulses."""

from __future__ import annotations

import argparse
import sys

import _bootstrap  # noqa: F401
from radar import TransmitReceivePulses, usrp_util
from radar import io_util


def Parse():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--args", "--addr", dest="args", default=usrp_util.DEFAULT_ADDR)
    p.add_argument("--rate", type=float, default=1e6)
    p.add_argument("--freq", type=float, default=2.45e9)
    p.add_argument("--tx-gain", type=float, default=0.0)
    p.add_argument("--rx-gain", type=float, default=20.0)
    p.add_argument("--bandwidth", type=float, default=200e3)
    p.add_argument("--pulse-width", type=float, default=50e-6)
    p.add_argument("--pri", type=float, default=1e-3)
    p.add_argument("--num-pulses", type=int, default=32)
    p.add_argument("--amplitude", type=float, default=0.5)
    p.add_argument("--cal", default=str(io_util.DATA_DIR / "cal" / "cal_latest.json"))
    p.add_argument("--skip-bins", type=int, default=None)
    p.add_argument("--allow-tx", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--target-range", type=float, default=30e3)
    p.add_argument("--out-dir", default=str(io_util.DATA_DIR / "captures"))
    return p.parse_args()


def main() -> int:
    a = Parse()
    try:
        TransmitReceivePulses(
            args=a.args, rate=a.rate, freq=a.freq, tx_gain=a.tx_gain,
            rx_gain=a.rx_gain, bandwidth=a.bandwidth, pulse_width=a.pulse_width,
            pri=a.pri, num_pulses=a.num_pulses, amplitude=a.amplitude,
            allow_tx=a.allow_tx, dry_run=a.dry_run, target_range=a.target_range,
            cal_path=a.cal, out_dir=a.out_dir, skip_bins=a.skip_bins,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
