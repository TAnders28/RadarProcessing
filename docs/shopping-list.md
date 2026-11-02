# Shopping list — N210 + SBX radar add-ons

Prices are approximate USD (late 2026). Verify before buying.

**Key notes**
- Buy **TWO** of whichever antenna you choose (separate TX + RX).
- SBX ports are **SMA female**; panels/grids are **N female** → need N-male ↔ SMA-male jumpers.
- **TX stays off** until loopback is proven. Start loopback at TX gain 0 / RX gain 0 through ~39 dB of attenuation.

## 1. Loopback / bench
| Item | Qty | ~Price | Status |
|---|---|---|---|
| 20 dB + 10 dB + 9 dB SMA attenuators (≈39 dB) | 1 set | — | ✅ **Already have** |
| Short SMA-male ↔ SMA-male jumpers | 2–3 | $10–20 | if needed |

Chain: TX/RX → 20 → 10 → 9 → RX2.

## 2. Low tier (~$100–180)
| Item | Qty | ~Price |
|---|---|---|
| WA5VJB log-periodic PCB antennas (~6 dBi) | 2 | $25–40 each |
| SMA jumpers (short, 50 Ω) | 2–4 | $5–10 each |
| 50 Ω SMA terminator | 1–2 | $5–10 |
| DIY corner reflector (cardboard + aluminium foil) | 1 | ~$10 |

## 3. Medium tier (~$400–600)
| Item | Qty | ~Price | Link |
|---|---|---|---|
| L-com HG2414P 14 dBi 2.4 GHz flat panel (N-female, ~30° beam) | 2 | ~$113 each | [Newark](https://www.newark.com/l-com/hg2414p/antenna-flat-panel-wifi/dp/23T6439) · [L-com](https://www.l-com.com/wireless-antenna-24-ghz-14-dbi-flat-panel-antenna-hg2414p) (also DigiKey/Mouser, search "HG2414P") |
| N-male → SMA-male LMR-240 jumpers | 2 | $20–40 each | |
| Mounts / tripods / pole clamps (≥1 m TX–RX separation) | 2 | $30–80 each | |

## 4. High tier (up to ~$2k total)
| Item | Qty | ~Price | Link |
|---|---|---|---|
| TP-Link TL-ANT2424B 24 dBi 2.4 GHz grid (N-female, ~10° beam, ~39″×24″) | 2 | $50–80 each (varies by seller) | [TP-Link](https://www.tp-link.com/us/business-networking/antenna-and-accessory/tl-ant2424b/) · [Amazon](https://www.amazon.com/TP-Link-Directional-Parabolic-Connector-TL-ANT2424B/dp/B003CFATOW) |
| N-male → SMA-male LMR-240 jumpers | 2 | $20–40 each | |
| Optional Mini-Circuits LNA (only after isolation measured) | 1 | ~$150 | |

## 5. Optional az/el pointing
| Item | Qty | ~Price | Link |
|---|---|---|---|
| Yaesu G-5500DC az/el rotator + controller | 1 | ~$760 | [DX Engineering](https://www.dxengineering.com/parts/ysu-g-5500dc) |
| Rotator control cable (pre-made run) | 1 | $50–150 | |
| PC interface: K3NG/Arduino (DIY) **or** Yaesu GS-232 | 1 | ~$100 / $500+ | |
| Mast + cross-boom fitting the clamshell clamps | 1 | $50–150 | |
| Antenna-to-boom mount, soft coax with slack | — | $30–60 | |

Software: Gpredict / hamlib `rotctld` (free).

## 6. Don't buy yet
- **Circulator** (~$300–700) — worse isolation than two separated antennas.
- **GPSDO** (~$1.5k) — single N210 already shares TX/RX clock.
- **Power amplifier** (~$1.5k) — only after loopback + isolation proven; leaves hobby/ISM territory.
- **Wideband horns** — not needed for 2.4 GHz.
