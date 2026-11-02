"""Software loopback for wifi_dsss.py (no radio involved)."""
import os, sys, zlib
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
import wifi_dsss as D
from scipy.signal import resample_poly
rng = np.random.default_rng(2); allok = True
for mbps in (1, 2):
    body = rng.integers(0, 256, 60, dtype=np.uint8).tobytes(); psdu = body + zlib.crc32(body).to_bytes(4, "little")
    tx = D.modulate(psdu, mbps)
    y = np.concatenate([np.zeros(3000), tx, np.zeros(3000)]) * np.exp(2j*np.pi*25e3/D.FS*np.arange(len(tx)+6000) + 0.7j)
    y25 = resample_poly(y, 25, 22); y25 += 0.3*(rng.standard_normal(len(y25))+1j*rng.standard_normal(len(y25)))
    x = resample_poly(y25, 22, 25)
    r = D.decode_burst(x[2000:])
    ok = bool(r and r.get("fcs_ok") and r["psdu"] == psdu); allok &= ok
    print(mbps, "Mbps", ok, {k: v for k, v in r.items() if k != "psdu"})
print("ALL OK" if allok else "FAILURES")
