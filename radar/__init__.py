"""Pure-Python UHD radar toolkit for the Ettus N210.

Public API (start here):
    from radar import Calibrate, TransmitReceivePulses

Modules:
    api        - Calibrate / TransmitReceivePulses
    usrp_util  - open/configure MultiUSRP + timed TX/RX streamers
    waveforms  - NumPy LFM / pulse train / CW
    io_util    - IQ .npy + JSON sidecar
    processing - matched filter / Doppler (plug custom algorithms here)

No GNU Radio. IQ is always complex64 NumPy for later custom stages.
"""

from .api import Calibrate, TransmitReceivePulses

__version__ = "0.2.0"
__all__ = ["Calibrate", "TransmitReceivePulses"]
