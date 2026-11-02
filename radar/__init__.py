"""Pure-Python UHD radar toolkit for the Ettus N210.

Modules:
    usrp_util  - open/configure a MultiUSRP and build TX/RX streamers
    waveforms  - NumPy waveform generators (LFM pulse, pulse train, CW tone)
    io_util    - save/load IQ captures (.npy) with JSON sidecar metadata
    processing - matched filter / pulse compression (plug custom algorithms here)

No GNU Radio. Everything is raw complex64 NumPy arrays so later stages can
manipulate the IQ directly.
"""

__version__ = "0.1.0"
