#!/usr/bin/env python3
"""Barebones N210 radar demo: Calibrate() then TransmitReceivePulses().

    python3 examples/barebones_demo.py                     # dry-run (no hardware)
    python3 examples/barebones_demo.py --live              # real N210, RX only
    python3 examples/barebones_demo.py --live --allow-tx   # radiates! antenna/load required
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from radar import Calibrate, TransmitReceivePulses  # noqa: E402
from radar import usrp_util  # noqa: E402


def Parse():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--live", action="store_true", help="use the real N210 (default: dry-run)")
    p.add_argument("--allow-tx", action="store_true", help="enable TX (default: off)")
    p.add_argument("--args", "--addr", dest="args", default=usrp_util.DEFAULT_ADDR)
    p.add_argument("--freq", type=float, default=2.45e9)
    p.add_argument("--rate", type=float, default=1e6)
    return p.parse_args()


def main() -> int:
    a = Parse()
    dry = not a.live
    try:
        print("=== Calibrate ===")
        cal = Calibrate(args=a.args, rate=a.rate, freq=a.freq,
                        allow_tx=a.allow_tx, dry_run=dry)
        print("\n=== TransmitReceivePulses ===")
        cap = TransmitReceivePulses(args=a.args, rate=a.rate, freq=a.freq,
                                    allow_tx=a.allow_tx, dry_run=dry,
                                    cal_path=cal.get("path"))
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    iq = cap["iq"]
    print(f"\nDone: {len(iq)} IQ samples ({iq.dtype}), {len(cap.get('peaks', []))} peaks, "
          f"saved to {cap.get('path')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
