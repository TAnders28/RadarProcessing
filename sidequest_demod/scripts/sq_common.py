"""Shared helpers for the demod side quest (RX-only, IQ stays in RAM).

Nothing here imports radar/ -- this side quest is deliberately decoupled
from the radar core so it can be deleted without touching anything else.
"""
import time
import numpy as np

N210_ADDR = "addr=192.168.10.36"


def capture_rx_only(fc, rate=25e6, gain=31.5, dur=0.5, settle=0.05, antenna="RX2", addr=N210_ADDR,
                    lo_offset=0.0, return_meta=False):
    """Grab `dur` seconds of complex64 IQ into RAM. RX streamer only.

    TX is never configured or streamed (no tx_streamer is created).
    Returns (iq, actual_rate, actual_fc[, meta]). Nothing is written to disk.
    meta = {"overflows": n, "gaps": n} -- a gap means samples were dropped,
    which breaks anything that relies on frame periodicity (e.g. LTE PSS).
    """
    import uhd
    u = uhd.usrp.MultiUSRP(addr)
    u.set_rx_rate(rate, 0)
    u.set_rx_antenna(antenna, 0)
    u.set_rx_gain(gain, 0)
    u.set_rx_freq(uhd.types.TuneRequest(fc, lo_offset), 0)
    time.sleep(0.1)
    rr = u.get_rx_rate(0)
    rf = u.get_rx_freq(0)
    st = u.get_rx_stream(uhd.usrp.StreamArgs("fc32", "sc16"))
    n = int((dur + settle) * rr)
    out = np.empty(n, np.complex64)
    buf = np.zeros((1, st.get_max_num_samps()), np.complex64)
    md = uhd.types.RXMetadata()
    cmd = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
    cmd.num_samps = n
    cmd.stream_now = True
    st.issue_stream_cmd(cmd)
    got = 0; ovf = 0; gaps = 0; last_t = None
    while got < n:
        k = st.recv(buf, md, 1.0)
        if md.error_code == uhd.types.RXMetadataErrorCode.overflow:
            ovf += 1; gaps += 1
            continue
        if md.error_code == uhd.types.RXMetadataErrorCode.timeout:
            break
        if md.error_code != uhd.types.RXMetadataErrorCode.none:
            raise RuntimeError(str(md.strerror()))
        k = min(k, n - got)
        out[got:got + k] = buf[0, :k]
        got += k
    x = out[int(settle * rr):got]
    meta = {"overflows": ovf, "gaps": gaps, "rate": rr, "fc": rf}
    return (x, rr, rf, meta) if return_meta else (x, rr, rf)


# ---- spur masking (Tim's standing rule: always mask radio clock spurs on spectra)
SPUR_COMBS_HZ = (10e6, 100e6, 6.25e6)   # GPSDO 10 MHz ref, 100 MHz master clock, 6.25 MHz set


# Measured 2026-11-02 against four LTE towers (lte_cellsearch_700.py): the N210
# sample/LO clock runs ~-46 ppm because the GPSDO has no GPS fix and the
# board reports ref_locked=False. Spurs that come from the (accurate) GPSDO
# 10 MHz therefore appear at n*10MHz*(1 - eps) on our frequency axis.
# Set to 0 once the reference actually locks.
CLOCK_ERR_PPM = -46.0


def spur_freqs(f_lo, f_hi, combs=SPUR_COMBS_HZ, clock_err_ppm=CLOCK_ERR_PPM):
    out = []
    eps = clock_err_ppm * 1e-6
    for c in combs:
        n0, n1 = int(np.ceil(f_lo / c)) - 1, int(np.floor(f_hi / c)) + 1
        for n in range(n0, n1 + 1):
            out += [n * c, n * c * (1 - eps)]     # nominal + clock-error-shifted copy
    out = np.array(out)
    return np.unique(out[(out >= f_lo) & (out <= f_hi)])


def spur_mask_psd(f_hz, psd_lin, half_width_hz=60e3, dc_hz=None, dc_half_width_hz=150e3):
    """Replace bins near known clock spurs (and optional LO/DC) with the local
    median noise floor. Returns (masked_psd, bool_mask). Spectrum-only: never
    applied to time-domain IQ used for demod."""
    p = np.array(psd_lin, dtype=float, copy=True)
    m = np.zeros(p.shape, bool)
    for s in spur_freqs(f_hz.min(), f_hz.max()):
        m |= np.abs(f_hz - s) <= half_width_hz
    if dc_hz is not None:
        m |= np.abs(f_hz - dc_hz) <= dc_half_width_hz
    if m.any() and (~m).any():
        from scipy.ndimage import median_filter
        good = np.where(m, np.nan, p)
        fill = np.nanmedian(good)
        k = max(9, int(len(p) // 100) | 1)
        med = median_filter(np.where(m, fill, p), size=k, mode="nearest")
        p[m] = med[m]
    return p, m


def welch_db(x, rate, fc, nfft=2048):
    win = np.hanning(nfft)
    nseg = len(x) // nfft
    fr = x[: nseg * nfft].reshape(nseg, nfft) * win
    P = np.abs(np.fft.fftshift(np.fft.fft(fr, axis=1), axes=1)) ** 2 / (np.sum(win ** 2) * nfft)
    f = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / rate)) + fc
    avg, m = spur_mask_psd(f, P.mean(0), dc_hz=fc)
    mx, _ = spur_mask_psd(f, P.max(0), dc_hz=fc)
    return f, 10 * np.log10(avg + 1e-20), 10 * np.log10(mx + 1e-20), m
