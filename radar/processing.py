"""Pulse compression and simple reporting. Plug custom algorithms in here.

Data layout convention (used everywhere):
    rx  : 1-D complex64 capture, len >= num_pulses * pri_n
    cube: 2-D complex64 [num_pulses (slow time), pri_n (fast time)]
Anything that takes/returns a cube can be swapped in (CFAR, MTI, custom
Doppler processing, etc.) without touching the streaming scripts.
"""

from __future__ import annotations

import numpy as np

C = 299_792_458.0


def to_cube(rx: np.ndarray, pri_n: int, num_pulses: int, offset: int = 0) -> np.ndarray:
    """Reshape a flat capture into [pulse, fast-time]. `offset` removes a
    fixed TX->RX hardware delay measured during calibration."""
    need = offset + pri_n * num_pulses
    if len(rx) < need:
        num_pulses = (len(rx) - offset) // pri_n
        need = offset + pri_n * num_pulses
    return rx[offset:need].reshape(num_pulses, pri_n)


def matched_filter(cube: np.ndarray, pulse: np.ndarray) -> np.ndarray:
    """Pulse compression: linear cross-correlation with the TX pulse.

    Output index k (0..pri_n-1) = target delay of k samples relative to the
    start of each PRI. Zero-padded FFT so there is no circular wrap (strong
    zero-delay leakage cannot alias into the last range bins). Echoes whose
    tail crosses into the next PRI are partially compressed only.
    """
    n = cube.shape[-1]
    L = len(pulse)
    nfft = 1 << int(np.ceil(np.log2(n + L)))
    H = np.conj(np.fft.fft(pulse, nfft))
    y = np.fft.ifft(np.fft.fft(cube, nfft, axis=-1) * H, axis=-1)
    return y[..., :n].astype(np.complex64)


def doppler_fft(compressed: np.ndarray, window: bool = True) -> np.ndarray:
    """Slow-time FFT -> range-Doppler map [doppler_bin, range_bin]."""
    x = compressed
    if window:
        x = x * np.hanning(x.shape[0])[:, None]
    return np.fft.fftshift(np.fft.fft(x, axis=0), axes=0)


def peak_report(compressed: np.ndarray, fs: float, skip_bins: int = 0,
                top: int = 3) -> dict:
    """Non-coherent integration over pulses then report strongest range bins.

    skip_bins: ignore the first bins (direct TX leakage / blind range).
    """
    prof = np.mean(np.abs(compressed) ** 2, axis=0)
    prof_db = 10 * np.log10(prof + 1e-20)
    search = prof_db.copy()
    search[:skip_bins] = -np.inf
    noise_db = float(np.median(prof_db))
    idx = np.argsort(search)[::-1]
    peaks, used = [], []
    for k in idx:
        if len(peaks) >= top or not np.isfinite(search[k]):
            break
        if any(abs(int(k) - u) < 3 for u in used):
            continue
        used.append(int(k))
        peaks.append({
            "bin": int(k),
            "delay_s": k / fs,
            "range_m_monostatic": C * k / fs / 2,
            "power_db": float(prof_db[k]),
            "snr_db": float(prof_db[k] - noise_db),
        })
    return {"noise_floor_db": noise_db, "peaks": peaks, "profile_db": prof_db}
