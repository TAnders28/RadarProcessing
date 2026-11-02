#!/usr/bin/env python3
"""CLI wrapper around radar.Calibrate."""

from __future__ import annotations

import argparse
import sys

import _bootstrap  # noqa: F401
from radar import Calibrate, usrp_util
from radar import io_util


def Parse():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--args", default=usrp_util.DEFAULT_ADDR)
    p.add_argument("--rate", type=float, default=1e6)
    p.add_argument("--freq", type=float, default=2.45e9)
    p.add_argument("--rx-gain", type=float, default=20.0)
    p.add_argument("--tx-gain", type=float, default=0.0)
    p.add_argument("--duration", type=float, default=0.5)
    p.add_argument("--allow-tx", action="store_true")
    p.add_argument("--tone-offset", type=float, default=None)
    p.add_argument("--out-dir", default=str(io_util.DATA_DIR / "cal"))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    a = Parse()
    try:
        Calibrate(
            args=a.args, rate=a.rate, freq=a.freq, rx_gain=a.rx_gain,
            tx_gain=a.tx_gain, duration=a.duration, allow_tx=a.allow_tx,
            tone_offset=a.tone_offset, out_dir=a.out_dir, dry_run=a.dry_run,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
