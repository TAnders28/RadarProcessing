"""Software loopback for wifi_ofdm.py (no radio involved)."""
import os, sys, zlib
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
import wifi_ofdm as W
from scipy.signal import resample_poly

rng = np.random.default_rng(1)
allok = True
for mbps in (6, 9, 12, 18, 24, 36, 48, 54):
    body = rng.integers(0, 256, 120, dtype=np.uint8).tobytes()
    psdu = body + zlib.crc32(body).to_bytes(4, "little")
    tx = W.modulate(psdu, mbps)
    # channel: 2-tap multipath, CFO 37 kHz, 25 dB SNR, embedded in noise, via 25 MS/s and back
    h = np.array([1, 0, 0.3j])
    y = np.convolve(tx, h)
    y = y * np.exp(2j * np.pi * 37e3 / W.FS * np.arange(len(y)))
    y = np.concatenate([np.zeros(500), y, np.zeros(500)])
    y25 = resample_poly(y, 5, 4)
    sig = np.sqrt(np.mean(np.abs(y25[700:-700]) ** 2))
    y25 = y25 + sig * 10 ** (-25 / 20) / np.sqrt(2) * (rng.standard_normal(len(y25)) + 1j * rng.standard_normal(len(y25)))
    x = resample_poly(y25, 4, 5)
    st, _ = W.find_packets(x)
    r = W.decode_packet(x, st[0]) if st else None
    ok = bool(r and r.get("fcs_ok") and r["psdu"] == psdu)
    allok &= ok
    print(f"{mbps:2d} Mbps: starts={st} ok={ok} cfo={r and r.get('cfo_hz'):.0f} snr={r and r.get('snr_db'):.1f} status={r and r.get('status')}")
print("ALL OK" if allok else "FAILURES")
