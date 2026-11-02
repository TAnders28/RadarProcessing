# sidequest_demod — exploratory RX-only demodulation (NOT radar)

This folder is a **side quest**. It doesn't import anything from `radar/` and
nothing in `radar/` imports from here. It adds no entries to
`requirements.txt`. You can delete the whole folder without affecting the
radar project.

Rules for this folder:

* **RX only.** Nothing here creates a TX streamer or sets a TX frequency or gain.
* **No raw IQ on disk.** Captures live in RAM. We only save plots and small
  JSON metadata. To save IQ, ask for it explicitly.
* **Spur masking on every spectrum.** The masked lines are the 10 MHz,
  100 MHz and 6.25 MHz combs, plus a copy of each line shifted by the measured
  clock error (see notes). The mask is applied to spectra only and never to
  the IQ used for demodulation.
* **Public repo hygiene.** The committed JSON masks MAC addresses to the OUI
  (`aa:bb:cc:xx:xx:xx`). SSIDs are replaced with a length and a hash. Full
  SSIDs are printed to the console only.

## Layout

```
scripts/
  sq_common.py             RX-only capture into RAM (counts overflows), spur mask, Welch PSD
  wifi_ofdm.py             pure-numpy 802.11a/g legacy OFDM PHY (all rates 6–54 Mbps, soft Viterbi)
  wifi_dsss.py             802.11b DSSS PHY: 1 Mbps DBPSK, 2 Mbps DQPSK, 11 Mbps CCK (hard decision)
  wifi_demod_2p4.py        capture ch 1/6/11, decode frames, check FCS, parse with scapy, plot + JSON
  lte_cellsearch_700.py    LTE PSS/SSS cell search 728–772 MHz -> Physical Cell IDs + clock-error estimate
  selftest_wifi_ofdm.py    software loopback for every OFDM rate (no radio)
  selftest_wifi_dsss.py    software loopback for 1/2 Mbps DSSS (no radio)
plots/                     outputs: PNGs + metadata JSON only
notes/                     results and what's left / not feasible
```

## Run

```bash
cd sidequest_demod/scripts
python3 selftest_wifi_ofdm.py && python3 selftest_wifi_dsss.py   # no radio needed
python3 wifi_demod_2p4.py --chans 1 6 11 --dur 1.0               # ~35 s, ~1.7 GB RAM peak
python3 lte_cellsearch_700.py                                    # ~2 min, ~0.35 GB RAM peak
```

Dependencies are numpy, scipy, matplotlib and python3-uhd, which the radar
side already needs. `scapy` is optional and only parses MAC headers. It's
already on the ThinkCentre (2.7.0) and isn't added to the radar requirements.

See `notes/results_2026-11-02.md` for what was decoded.
