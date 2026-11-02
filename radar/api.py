"""Barebones public API for the N210 radar demo.

Two entry points (PascalCase, Tim's style):

    Calibrate(...)              -> calibration dict (+ writes data/cal/)
    TransmitReceivePulses(...)  -> IQ capture + metadata (+ optional save)

CLI scripts under scripts/ are thin wrappers around these.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

from . import io_util, processing, usrp_util, waveforms


def IqStats(x: np.ndarray) -> dict:
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


def TonePowerDbfs(x: np.ndarray, fs: float, f_hz: float, half_bw_bins: int = 3) -> float:
    n = len(x)
    win = np.hanning(n)
    X = np.fft.fft(x * win) / (np.sum(win))
    freqs = np.fft.fftfreq(n, 1 / fs)
    k = int(np.argmin(np.abs(freqs - f_hz)))
    sl = [(k + d) % n for d in range(-half_bw_bins, half_bw_bins + 1)]
    return float(10 * np.log10(np.sum(np.abs(X[sl]) ** 2) + 1e-20))


def SynthEcho(tx: np.ndarray, fs: float, delay_s: float, doppler_hz: float = 50.0,
              snr_db: float = 10.0, leakage: float = 0.05, seed: int = 1) -> np.ndarray:
    """Dry-run channel: leakage + delayed/Doppler echo + noise."""
    rng = np.random.default_rng(seed)
    n = len(tx)
    d = int(round(delay_s * fs))
    echo = np.zeros(n, dtype=np.complex128)
    echo[d:] = tx[: n - d]
    t = np.arange(n) / fs
    echo *= 0.01 * np.exp(2j * np.pi * doppler_hz * t)
    noise_amp = 0.01 * 0.5 / (10 ** (snr_db / 20)) / np.sqrt(2)
    noise = noise_amp * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return (leakage * tx + echo + noise).astype(np.complex64)


def Calibrate(
    args: str = usrp_util.DEFAULT_ADDR,
    rate: float = 1e6,
    freq: float = 2.45e9,
    rx_gain: float = 20.0,
    tx_gain: float = 0.0,
    duration: float = 0.5,
    allow_tx: bool = False,
    tone_offset: Optional[float] = None,
    out_dir: Optional[str] = None,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict[str, Any]:
    """One-shot N210 health / calibration.

    RX-only by default (safe with no TX antenna). Pass allow_tx=True only with
    a load or antenna on the TX port.

    Returns the calibration dict and writes data/cal/cal_*.json + cal_latest.json.
    Raises RuntimeError on hard failure.
    """
    fs = rate
    n = waveforms.n_samples(duration, fs)
    tone_hz = tone_offset if tone_offset is not None else fs / 10
    out = Path(out_dir) if out_dir else (io_util.DATA_DIR / "cal")
    cfg = usrp_util.RadioConfig(
        args=args, rate=fs, freq=freq, tx_gain=tx_gain, rx_gain=rx_gain
    )
    cal: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dry_run": dry_run,
        "config": cfg.to_dict(),
    }
    errors: list = []

    if dry_run:
        if verbose:
            print("[dry-run] no hardware; synthesising RX noise")
        rng = np.random.default_rng(0)
        cal["device"] = {"mboard": "DRY-RUN", "rx_subdev": "synthetic"}
        rx = (
            0.003 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
            + (0.002 - 0.001j)
        ).astype(np.complex64)
        tx_st = rx_st = usrp = None
    else:
        try:
            usrp = usrp_util.open_usrp(cfg)
        except Exception as exc:
            raise RuntimeError(
                f"could not open/configure USRP ({cfg.args}): {exc}. "
                "check: ip addr show eno1 ; ping 192.168.10.36 ; uhd_find_devices"
            ) from exc
        cal["device"] = usrp_util.describe(usrp, cfg.chan)
        tx_st, rx_st = usrp_util.get_streamers(usrp, cfg.chan)
        usrp_util.rx_capture(usrp, rx_st, int(0.05 * fs))  # settle
        rx, errors = usrp_util.rx_capture(usrp, rx_st, n)

    if verbose:
        print("Device:")
        for k, v in cal["device"].items():
            print(f"  {k:>16}: {v}")

    if len(rx) < n or not np.all(np.isfinite(rx)):
        raise RuntimeError(f"bad RX capture ({len(rx)}/{n} samples) errors={errors}")
    if errors and verbose:
        print(f"WARNING: RX errors during capture: {errors}")

    rx_stats = IqStats(rx)
    cal["rx_noise"] = rx_stats
    cal["rx_errors"] = errors
    if verbose:
        print("RX-only (TX idle):")
        for k, v in rx_stats.items():
            print(f"  {k:>16}: {v:.6g}")
        if rx_stats["clipping_frac"] > 1e-4:
            print("WARNING: RX clipping with TX idle -> lower rx_gain or strong interferer")

    if allow_tx:
        if verbose:
            print(
                f"TX leakage test: low-gain tone at {tone_hz/1e3:.1f} kHz offset, "
                f"tx_gain={tx_gain} dB"
            )
            print(
                "  WARNING: antenna or 50-ohm load required on TX; "
                "never transmit into an open port at high gain."
            )
        tone = waveforms.cw_tone(fs, duration + 0.2, tone_hz, amplitude=0.3)
        if dry_run:
            m = len(rx)
            t = np.arange(m) / fs
            rx_tx = (rx + 0.01 * np.exp(2j * np.pi * tone_hz * t)).astype(np.complex64)
            tx_errs: list = []
        else:
            t0 = usrp_util.device_time(usrp) + 0.2
            th = threading.Thread(target=usrp_util.tx_burst, args=(tx_st, tone, t0))
            th.start()
            rx_tx, rx_errs = usrp_util.rx_capture(usrp, rx_st, n, start_time=t0 + 0.1)
            th.join()
            tx_errs = usrp_util.tx_async_errors(tx_st)
            if rx_errs and verbose:
                print(f"WARNING: RX errors in leakage test: {rx_errs}")
        if len(rx_tx) < n:
            raise RuntimeError("short capture in leakage test")
        leak = TonePowerDbfs(rx_tx, fs, tone_hz)
        floor = TonePowerDbfs(rx, fs, tone_hz)
        cal["tx_leakage"] = {
            "tone_offset_hz": tone_hz,
            "tx_gain_db": tx_gain,
            "rx_tone_dbfs": leak,
            "rx_floor_at_tone_dbfs": floor,
            "leakage_above_floor_db": leak - floor,
            "tx_async": tx_errs,
            "rx_stats_with_tx": IqStats(rx_tx),
        }
        if verbose:
            print(
                f"  tone at RX: {leak:.1f} dBFS "
                f"(floor {floor:.1f} dBFS, +{leak - floor:.1f} dB)"
            )
            if IqStats(rx_tx)["clipping_frac"] > 1e-4:
                print("  WARNING: RX clipping with TX on -> more isolation / lower gains")
    elif verbose:
        print("TX leakage test skipped (pass allow_tx=True once an antenna/load is fitted)")

    out.mkdir(parents=True, exist_ok=True)
    path = io_util.save_json(str(out / f"cal_{io_util.timestamp()}.json"), cal)
    io_util.save_json(str(out / "cal_latest.json"), cal)
    cal["path"] = str(path)
    if verbose:
        print(f"Wrote {path}")
    return cal


def TransmitReceivePulses(
    args: str = usrp_util.DEFAULT_ADDR,
    rate: float = 1e6,
    freq: float = 2.45e9,
    tx_gain: float = 0.0,
    rx_gain: float = 20.0,
    bandwidth: float = 200e3,
    pulse_width: float = 50e-6,
    pri: float = 1e-3,
    num_pulses: int = 32,
    amplitude: float = 0.5,
    allow_tx: bool = False,
    dry_run: bool = False,
    target_range: float = 30e3,
    cal_path: Optional[str] = None,
    out_dir: Optional[str] = None,
    save: bool = True,
    compress: bool = True,
    skip_bins: Optional[int] = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Transmit an LFM pulse train and collect RX (timed TX/RX).

    TX is off unless allow_tx=True. Returns a result dict with:
      iq, pulse, meta, and optionally peaks / compressed cube stats.

    Raises RuntimeError on hard failure.
    """
    fs = rate
    pulse = waveforms.lfm_pulse(fs, bandwidth, pulse_width, amplitude)
    train = waveforms.pulse_train(pulse, fs, pri, num_pulses)
    pri_n = waveforms.n_samples(pri, fs)
    params = waveforms.pulse_params(fs, bandwidth, pulse_width, pri, num_pulses)
    cfg = usrp_util.RadioConfig(
        args=args, rate=fs, freq=freq, tx_gain=tx_gain, rx_gain=rx_gain
    )
    meta: dict[str, Any] = {
        "api": "TransmitReceivePulses",
        "dry_run": dry_run,
        "allow_tx": allow_tx,
        "config": cfg.to_dict(),
        "bandwidth_hz": bandwidth,
        "pulse_width_s": pulse_width,
        "pri_s": pri,
        "num_pulses": num_pulses,
        "pri_samples": pri_n,
        "pulse_samples": len(pulse),
        "amplitude": amplitude,
        "derived": params,
    }
    n_rx = len(train)
    errors: list = []

    if verbose:
        print(
            f"LFM: B={bandwidth/1e3:.0f} kHz, T={pulse_width*1e6:.0f} us, "
            f"PRI={pri*1e3:.2f} ms, N={num_pulses}, fs={fs/1e6:.2f} MS/s, "
            f"f0={freq/1e9:.4f} GHz"
        )
        for k, v in params.items():
            print(f"  {k:>24}: {v:.6g}")

    if dry_run:
        delay = 2 * target_range / processing.C
        if verbose:
            print(
                f"[dry-run] synthetic target at {target_range:.0f} m "
                f"(delay {delay*1e6:.1f} us = {delay*fs:.1f} samples)"
            )
        rx = SynthEcho(train, fs, delay)
        meta["synthetic_target_range_m"] = target_range
    else:
        if verbose and not allow_tx:
            print("TX disabled (allow_tx=False): RX-only capture")
        try:
            usrp = usrp_util.open_usrp(cfg)
        except Exception as exc:
            raise RuntimeError(f"could not open USRP: {exc}") from exc
        meta["device"] = usrp_util.describe(usrp, cfg.chan)
        tx_st, rx_st = usrp_util.get_streamers(usrp, cfg.chan)
        t0 = usrp_util.device_time(usrp) + 0.25
        th = None
        if allow_tx:
            th = threading.Thread(target=usrp_util.tx_burst, args=(tx_st, train, t0))
            th.start()
        rx, errors = usrp_util.rx_capture(usrp, rx_st, n_rx, start_time=t0)
        if th is not None:
            th.join()
            meta["tx_async"] = usrp_util.tx_async_errors(tx_st)
            if verbose:
                print(f"TX async events: {meta['tx_async']}")
        meta["t0_device_s"] = t0
        if errors and verbose:
            print(f"WARNING: RX errors: {errors}")
        if len(rx) < pri_n:
            raise RuntimeError("capture shorter than one PRI")

    meta["rx_errors"] = errors
    result: dict[str, Any] = {"iq": rx, "pulse": pulse, "meta": meta, "pri_samples": pri_n}

    if save:
        dest = out_dir or str(io_util.DATA_DIR / "captures")
        npy = io_util.save_iq(rx, meta, dest, name=f"lfm_{io_util.timestamp()}")
        result["path"] = str(npy)
        if verbose:
            print(f"Saved {npy} (+ .json), {len(rx)} samples")

    # Optional DC removal from a prior Calibrate()
    cal_file = Path(cal_path) if cal_path else (io_util.DATA_DIR / "cal" / "cal_latest.json")
    try:
        cal = json.loads(Path(cal_file).read_text())
        dc = complex(cal["rx_noise"]["dc_offset_i"], cal["rx_noise"]["dc_offset_q"])
        if not cal.get("dry_run") and not dry_run:
            rx = rx - np.complex64(dc)
            result["iq"] = rx
            if verbose:
                print(f"Removed DC offset from cal: {dc:.4g}")
    except (OSError, KeyError, ValueError, TypeError):
        pass

    if compress:
        cube = processing.to_cube(rx, pri_n, num_pulses)
        comp = processing.matched_filter(cube, pulse)
        skip = skip_bins if skip_bins is not None else len(pulse)
        rep = processing.peak_report(comp, fs, skip_bins=skip)
        result["peaks"] = rep["peaks"]
        result["noise_floor_db"] = rep["noise_floor_db"]
        if verbose:
            print(
                f"Pulse compression: {cube.shape[0]} pulses x {pri_n} bins, "
                f"noise floor {rep['noise_floor_db']:.1f} dB, skipped first {skip} bins"
            )
            for p in rep["peaks"]:
                print(
                    f"  bin {p['bin']:5d}  delay {p['delay_s']*1e6:8.1f} us  "
                    f"range {p['range_m_monostatic']/1e3:7.2f} km  "
                    f"{p['power_db']:6.1f} dB  SNR {p['snr_db']:5.1f} dB"
                )
            if rep["peaks"]:
                rd = processing.doppler_fft(comp)
                k = rep["peaks"][0]["bin"]
                dbin = int(np.argmax(np.abs(rd[:, k]))) - rd.shape[0] // 2
                fd = dbin / (rd.shape[0] * pri)
                print(
                    f"  strongest peak Doppler ~ {fd:.1f} Hz "
                    f"(resolution {1/(rd.shape[0]*pri):.1f} Hz)"
                )
                result["doppler_hz"] = fd

    return result
