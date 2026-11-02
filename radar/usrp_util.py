"""UHD helpers for the Ettus N210 (pure Python, no GNU Radio).

Only real UHD Python APIs are used: uhd.usrp.MultiUSRP, uhd.usrp.StreamArgs,
uhd.types.TuneRequest / StreamCMD / StreamMode / TXMetadata / RXMetadata /
TimeSpec.

Timed commands (important for radar):
    The N210 has a 64-bit device time counter clocked by its sample clock.
    We set it to 0 with ``usrp.set_time_now(TimeSpec(0.0))`` and then schedule
    both the TX burst (``TXMetadata.has_time_spec = True; time_spec = t0``) and
    the RX capture (``StreamCMD.stream_now = False; time_spec = t0``) at the
    SAME device time t0, a little in the future (e.g. now + 0.2 s). Because
    both paths start on the same clock tick, the TX->RX offset is fixed and
    repeatable (apart from fixed FPGA/analog group delay, which calibration
    can measure). Without timed commands, host/OS jitter makes the alignment
    random from run to run.

    If you later add a GPSDO or external 10 MHz/PPS, call
    ``set_clock_source("external")`` / ``set_time_source("external")`` and use
    ``set_time_next_pps`` / ``set_time_unknown_pps`` instead of set_time_now.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, asdict

DEFAULT_ADDR = "192.168.10.36"

try:  # UHD is a system package (python3-uhd / built from source), not pip.
    import uhd  # type: ignore
    HAVE_UHD = True
    _UHD_IMPORT_ERROR = None
except ImportError as exc:  # soft-fail so --dry-run works without UHD
    uhd = None  # type: ignore
    HAVE_UHD = False
    _UHD_IMPORT_ERROR = exc


def require_uhd():
    """Return the uhd module or raise a helpful error."""
    if not HAVE_UHD:
        raise RuntimeError(
            "UHD Python bindings not importable "
            f"({_UHD_IMPORT_ERROR}). Install the system package "
            "(e.g. `sudo apt install python3-uhd uhd-host`) or use --dry-run."
        )
    return uhd


def device_args(addr_or_args: str) -> str:
    """Accept either a bare IP ('192.168.10.36') or a UHD args string."""
    s = (addr_or_args or DEFAULT_ADDR).strip()
    if "=" in s:
        return s
    return f"addr={s}"


@dataclass
class RadioConfig:
    """Radio settings. Units: Hz for rate/freq, dB for gain."""
    args: str = DEFAULT_ADDR
    rate: float = 1e6
    freq: float = 2.45e9
    tx_gain: float = 0.0
    rx_gain: float = 20.0
    tx_antenna: str = "TX/RX"
    rx_antenna: str = "RX2"      # keep RX on RX2 so TX/RX port is TX-only
    clock_source: str = "internal"  # "external" for 10 MHz ref / GPSDO
    chan: int = 0

    def to_dict(self):
        return asdict(self)


def open_usrp(cfg: RadioConfig):
    """Open a MultiUSRP and apply cfg. Returns the usrp object."""
    u = require_uhd()
    usrp = u.usrp.MultiUSRP(device_args(cfg.args))
    usrp.set_clock_source(cfg.clock_source)
    configure_rx(usrp, cfg)
    configure_tx(usrp, cfg)
    # Let LOs lock, then zero the device clock for timed commands.
    wait_for_lo_lock(usrp, cfg.chan)
    usrp.set_time_now(u.types.TimeSpec(0.0))
    return usrp


def configure_rx(usrp, cfg: RadioConfig):
    u = require_uhd()
    c = cfg.chan
    usrp.set_rx_rate(cfg.rate, c)
    usrp.set_rx_freq(u.types.TuneRequest(cfg.freq), c)
    usrp.set_rx_gain(cfg.rx_gain, c)
    try:
        usrp.set_rx_antenna(cfg.rx_antenna, c)
    except RuntimeError:
        pass  # some daughterboards only have one RX antenna name


def configure_tx(usrp, cfg: RadioConfig):
    u = require_uhd()
    c = cfg.chan
    usrp.set_tx_rate(cfg.rate, c)
    usrp.set_tx_freq(u.types.TuneRequest(cfg.freq), c)
    usrp.set_tx_gain(cfg.tx_gain, c)
    try:
        usrp.set_tx_antenna(cfg.tx_antenna, c)
    except RuntimeError:
        pass


def wait_for_lo_lock(usrp, chan: int = 0, timeout: float = 1.0):
    """Poll the 'lo_locked' sensor on RX and TX (if present)."""
    deadline = time.time() + timeout
    for getter_names, getter in (
        (usrp.get_rx_sensor_names, usrp.get_rx_sensor),
        (usrp.get_tx_sensor_names, usrp.get_tx_sensor),
    ):
        try:
            if "lo_locked" not in getter_names(chan):
                continue
            while not getter("lo_locked", chan).to_bool():
                if time.time() > deadline:
                    raise RuntimeError("LO failed to lock")
                time.sleep(0.01)
        except AttributeError:
            continue


def stream_args(chan: int = 0):
    """fc32 on the host (complex64 NumPy), sc16 over the wire (N210 max)."""
    u = require_uhd()
    sa = u.usrp.StreamArgs("fc32", "sc16")
    sa.channels = [chan]
    return sa


def get_streamers(usrp, chan: int = 0):
    """Return (tx_streamer, rx_streamer)."""
    tx = usrp.get_tx_stream(stream_args(chan))
    rx = usrp.get_rx_stream(stream_args(chan))
    return tx, rx


def describe(usrp, chan: int = 0) -> dict:
    """Collect device info for logs / calibration files."""
    info = {
        "mboard": usrp.get_mboard_name(),
        "pp_string": usrp.get_pp_string(),
        "rx_subdev": usrp.get_rx_subdev_name(chan),
        "tx_subdev": usrp.get_tx_subdev_name(chan),
        "rx_rate": usrp.get_rx_rate(chan),
        "tx_rate": usrp.get_tx_rate(chan),
        "rx_freq": usrp.get_rx_freq(chan),
        "tx_freq": usrp.get_tx_freq(chan),
        "rx_gain": usrp.get_rx_gain(chan),
        "tx_gain": usrp.get_tx_gain(chan),
        "rx_gain_range": str(usrp.get_rx_gain_range(chan)),
        "tx_gain_range": str(usrp.get_tx_gain_range(chan)),
        "rx_antenna": usrp.get_rx_antenna(chan),
        "tx_antenna": usrp.get_tx_antenna(chan),
        "clock_source": usrp.get_clock_source(0),
    }
    try:
        info.update({f"rx_info_{k}": v for k, v in dict(usrp.get_usrp_rx_info(chan)).items()})
    except Exception:
        pass
    return info


def device_time(usrp) -> float:
    return usrp.get_time_now().get_real_secs()


def rx_capture(usrp, rx_streamer, num_samps: int, start_time: float | None = None,
               timeout: float = 3.0):
    """Capture exactly num_samps complex64 samples (num_done mode).

    If start_time (device seconds) is given the capture is a timed command;
    otherwise it starts now. Returns (samples, list_of_error_strings).
    """
    import numpy as np
    u = require_uhd()
    cmd = u.types.StreamCMD(u.types.StreamMode.num_done)
    cmd.num_samps = int(num_samps)
    if start_time is None:
        cmd.stream_now = True
    else:
        cmd.stream_now = False
        cmd.time_spec = u.types.TimeSpec(float(start_time))
    rx_streamer.issue_stream_cmd(cmd)

    max_samps = rx_streamer.get_max_num_samps()
    buf = np.zeros((1, max_samps), dtype=np.complex64)
    out = np.zeros(num_samps, dtype=np.complex64)
    md = u.types.RXMetadata()
    errors = []
    got = 0
    # First recv waits until start_time, so allow extra timeout.
    first_timeout = timeout + (0 if start_time is None
                               else max(0.0, start_time - device_time(usrp)))
    t_out = first_timeout
    while got < num_samps:
        n = rx_streamer.recv(buf, md, t_out)
        t_out = timeout
        if md.error_code != u.types.RXMetadataErrorCode.none:
            errors.append(md.strerror())
            if md.error_code == u.types.RXMetadataErrorCode.timeout:
                break
            if md.error_code == u.types.RXMetadataErrorCode.overflow:
                continue  # 'O' printed by UHD; data has a gap
            break
        take = min(n, num_samps - got)
        out[got:got + take] = buf[0, :take]
        got += take
        if md.end_of_burst:
            break
    if got < num_samps:
        errors.append(f"short capture: {got}/{num_samps}")
    return out[:got], errors


def tx_burst(tx_streamer, samples, start_time: float | None = None,
             timeout: float = 3.0) -> int:
    """Send one burst (start_of_burst .. end_of_burst), optionally timed."""
    import numpy as np
    u = require_uhd()
    samples = np.ascontiguousarray(samples, dtype=np.complex64)
    max_samps = tx_streamer.get_max_num_samps()
    md = u.types.TXMetadata()
    md.start_of_burst = True
    md.end_of_burst = False
    if start_time is not None:
        md.has_time_spec = True
        md.time_spec = u.types.TimeSpec(float(start_time))
    sent = 0
    n_total = len(samples)
    while sent < n_total:
        chunk = samples[sent:sent + max_samps]
        md.end_of_burst = sent + len(chunk) >= n_total
        n = tx_streamer.send(chunk.reshape(1, -1), md, timeout)
        if n == 0:
            break
        sent += n
        md.start_of_burst = False
        md.has_time_spec = False
    if sent < n_total:  # make sure the burst is terminated
        md.end_of_burst = True
        tx_streamer.send(np.zeros((1, 0), dtype=np.complex64), md, timeout)
    return sent


def tx_async_errors(tx_streamer, timeout: float = 0.5) -> list:
    """Drain async TX messages (underflow 'U', late time 'L', burst ack)."""
    u = require_uhd()
    msgs = []
    amd = u.types.TXAsyncMetadata()
    while tx_streamer.recv_async_msg(amd, timeout):
        msgs.append(str(amd.event_code))
        timeout = 0.05
    return msgs
