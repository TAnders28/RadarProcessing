"""NumPy waveform generators. All outputs are complex64 baseband IQ.

Units: frequencies in Hz, times in seconds, sample rate fs in samples/s.
Keep |amplitude| <= ~0.7 to leave DAC headroom (fc32 full scale is 1.0).
"""

from __future__ import annotations

import numpy as np


def n_samples(duration_s: float, fs: float) -> int:
    return int(round(duration_s * fs))


def lfm_pulse(fs: float, bandwidth_hz: float, pulse_width_s: float,
              amplitude: float = 0.5, up: bool = True,
              center_hz: float = 0.0) -> np.ndarray:
    """Linear FM chirp sweeping [-B/2, +B/2] (+ center_hz) over pulse_width_s.

    Instantaneous freq f(t) = center + k*(t - T/2), k = B/T (Hz/s).
    Time-bandwidth product B*T sets compression gain; range resolution c/(2B).
    """
    n = n_samples(pulse_width_s, fs)
    if n < 2:
        raise ValueError("pulse_width_s * fs must be >= 2 samples")
    if bandwidth_hz > fs:
        raise ValueError("bandwidth exceeds sample rate (complex Nyquist)")
    t = np.arange(n) / fs
    T = n / fs
    k = (bandwidth_hz / T) * (1 if up else -1)
    phase = 2 * np.pi * (center_hz * t + 0.5 * k * (t - T / 2) ** 2)
    return (amplitude * np.exp(1j * phase)).astype(np.complex64)


def pulse_train(pulse: np.ndarray, fs: float, pri_s: float,
                num_pulses: int) -> np.ndarray:
    """Repeat `pulse` every PRI seconds, zero-filled between pulses."""
    pri_n = n_samples(pri_s, fs)
    if pri_n < len(pulse):
        raise ValueError("PRI shorter than pulse")
    one = np.zeros(pri_n, dtype=np.complex64)
    one[:len(pulse)] = pulse
    return np.tile(one, num_pulses)


def cw_tone(fs: float, duration_s: float, freq_hz: float = 0.0,
            amplitude: float = 0.5) -> np.ndarray:
    """Complex tone at baseband offset freq_hz (0 = carrier; use an offset
    such as fs/10 to move it away from the DC/LO-leakage spike)."""
    n = n_samples(duration_s, fs)
    t = np.arange(n) / fs
    return (amplitude * np.exp(2j * np.pi * freq_hz * t)).astype(np.complex64)


def pulse_params(fs, bandwidth_hz, pulse_width_s, pri_s, num_pulses, c=299_792_458.0):
    """Derived radar numbers handy for logs."""
    return {
        "range_resolution_m": c / (2 * bandwidth_hz),
        "max_unambiguous_range_m": c * pri_s / 2,
        "blind_range_m": c * pulse_width_s / 2,
        "time_bandwidth_product": bandwidth_hz * pulse_width_s,
        "duty_cycle": pulse_width_s / pri_s,
        "prf_hz": 1.0 / pri_s,
        "cpi_s": pri_s * num_pulses,
        "range_bin_m": c / (2 * fs),
    }
