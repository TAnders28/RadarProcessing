"""Side quest: RX-only LTE cell search (PSS/SSS -> Physical Cell ID) around 700 MHz.

IQ is captured into RAM only. For each 100 kHz raster candidate inside the
capture we mix to baseband, decimate to 1.92 MS/s, correlate the three PSS
Zadoff-Chu roots (N_ID_2), then coherently detect SSS (N_ID_1, subframe
0/5, normal vs extended CP). PCI = 3*N_ID_1 + N_ID_2.
MIB/PBCH (bandwidth, SFN) is NOT decoded -- see notes/lte_notes.md.
TX never touched. Writes a plot + small JSON only.

usage: python3 lte_cellsearch_700.py [--centers 740 760] [--dur 0.06] [--gain 40]
"""
import argparse, json, os, sys, time
import numpy as np
from scipy.signal import resample_poly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sq_common as C

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plots")
FS = 1.92e6
NFFT = 128
BANDS = [(729, 746, "B12 DL"), (746, 756, "B13 DL"), (758, 768, "B14 DL (FirstNet)"), (734, 746, "B17 DL")]


def _map62(d):
    X = np.zeros(NFFT, complex)
    X[(np.arange(-31, 0)) % NFFT] = d[:31]
    X[np.arange(1, 32)] = d[31:]
    return X


def pss_seq(nid2):
    u = (25, 29, 34)[nid2]
    n = np.arange(62)
    d = np.where(n < 31, np.exp(-1j * np.pi * u * n * (n + 1) / 63), np.exp(-1j * np.pi * u * (n + 1) * (n + 2) / 63))
    return d


PSS_F = [_map62(pss_seq(i)) for i in range(3)]
PSS_T = [np.fft.ifft(X) for X in PSS_F]


def _mseq(taps, n=31):
    x = [0, 0, 0, 0, 1]
    while len(x) < n:
        i = len(x) - 5
        x.append(sum(x[i + t] for t in taps) % 2)
    return 1 - 2 * np.array(x[:n])


S_T = _mseq([2, 0]); C_T = _mseq([3, 0]); Z_T = _mseq([4, 2, 1, 0])


def sss_seq(nid1, nid2, sf):
    qp = nid1 // 30
    q = (nid1 + qp * (qp + 1) // 2) // 30
    mp = nid1 + q * (q + 1) // 2
    m0 = mp % 31
    m1 = (m0 + mp // 31 + 1) % 31
    n = np.arange(31)
    s0 = S_T[(n + m0) % 31]; s1 = S_T[(n + m1) % 31]
    c0 = C_T[(n + nid2) % 31]; c1 = C_T[(n + nid2 + 3) % 31]
    z0 = Z_T[(n + (m0 % 8)) % 31]; z1 = Z_T[(n + (m1 % 8)) % 31]
    d = np.zeros(62)
    if sf == 0:
        d[0::2] = s0 * c0; d[1::2] = s1 * c1 * z0
    else:
        d[0::2] = s1 * c0; d[1::2] = s0 * c1 * z1
    return d


SSS_TAB = {(n1, n2, sf): sss_seq(n1, n2, sf) for n1 in range(168) for n2 in range(3) for sf in (0, 5)}
IDX62 = np.concatenate([np.arange(-31, 0) % NFFT, np.arange(1, 32)])


def to_bb(x, rate, df):
    from fractions import Fraction
    n = np.arange(len(x))
    y = x * np.exp(-2j * np.pi * df / rate * n).astype(np.complex64)
    fr = Fraction(int(FS), int(rate)).limit_denominator(5000)
    return resample_poly(y, fr.numerator, fr.denominator)   # -> 1.92 MS/s (also low-pass)


def pss_metric(y):
    e = np.convolve(np.abs(y) ** 2, np.ones(NFFT), "valid")
    best = []
    for nid2 in range(3):
        c = np.abs(np.correlate(y, PSS_T[nid2], "valid")) ** 2 / (e[: len(y) - NFFT + 1] * np.sum(np.abs(PSS_T[nid2]) ** 2) + 1e-20)
        L = len(c) // 9600 * 9600
        fold = c[:L].reshape(-1, 9600).mean(0)        # PSS repeats every 5 ms
        k = int(np.argmax(fold))
        best.append((float(fold[k]), nid2, k, c))
    return max(best, key=lambda t: t[0])


def sss_detect(y, nid2, off, eps=0.0):
    """Combine SSS evidence over every PSS instance (alternating sf0/sf5)."""
    out = {}
    for cp, back in (("normal", 137), ("extended", 160)):
        zs = []
        for i in range(len(y) // 9600):
            st = off + i * 9600 + int(round(i * 9600 * eps))
            a = st - back
            if st + NFFT > len(y):
                break
            if a < 0:
                continue
            Yp = np.fft.fft(y[st:st + NFFT])[IDX62]
            Ys = np.fft.fft(y[a:a + NFFT])[IDX62]
            H = Yp / pss_seq(nid2)
            Hs = np.convolve(H, np.ones(7) / 7, "same")
            z = (Ys * np.conj(Hs)).real
            zs.append(z / (np.linalg.norm(z) + 1e-20))
        if not zs:
            continue
        Z = np.array(zs)                              # (ninst, 62)
        A = np.array([[SSS_TAB[(n1, nid2, 0)], SSS_TAB[(n1, nid2, 5)]] for n1 in range(168)]) / np.sqrt(62)  # (168,2,62)
        sc = np.einsum("hsk,ik->hsi", A, Z)          # (168, 2, ninst)
        even = np.arange(len(zs)) % 2 == 0
        tot0 = sc[:, 0, even].sum(1) + sc[:, 1, ~even].sum(1)   # first instance is sf0
        tot5 = sc[:, 1, even].sum(1) + sc[:, 0, ~even].sum(1)   # first instance is sf5
        tot = np.stack([tot0, tot5], 1) / len(zs)
        h, w = np.unravel_index(int(np.argmax(tot)), tot.shape)
        other = np.delete(tot.max(1), h)
        flat = [float(other.max()), float(tot[h, w])]
        out[cp] = {"nid1": int(h), "first_sf": (0, 5)[w], "score": float(flat[-1]), "second": float(flat[-2]),
                   "ninst": len(zs), "per_inst": [float(sc[h, (w + i) % 2, i]) for i in range(len(zs))]}
    return out


def pss_corr(y, nid2):
    from scipy.signal import fftconvolve
    e = np.convolve(np.abs(y) ** 2, np.ones(NFFT), "valid")
    c = np.abs(fftconvolve(y, np.conj(PSS_T[nid2][::-1]), "valid")) ** 2
    return c / (e * np.sum(np.abs(PSS_T[nid2]) ** 2) + 1e-20)


def warped_fold(c, eps):
    """Fold 5 ms blocks, shifting block i by i*9600*eps samples (our clock error)."""
    n = len(c) // 9600 - 1
    acc = np.zeros(9600)
    for i in range(n):
        sh = int(round(i * 9600 * eps))
        acc += c[i * 9600 + sh: i * 9600 + sh + 9600] if i * 9600 + sh + 9600 <= len(c) and i * 9600 + sh >= 0 else 0
    return acc / max(n, 1)


def search_eps(yb, rate_bb, f0, eps_grid):
    """yb: 1.92 MS/s baseband (no CFO corr). Returns best (metric, eps, nid2, k)."""
    n = np.arange(len(yb))
    best = None
    for eps in eps_grid:
        y = yb * np.exp(-2j * np.pi * (-eps * f0) / FS * n)   # signal sits at -eps*f0 in our baseband... sign searched by grid
        for nid2 in range(3):
            fo = warped_fold(pss_corr(y, nid2), eps)
            k = int(np.argmax(fo))
            if best is None or fo[k] > best[0]:
                best = (float(fo[k]), float(eps), nid2, k)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--centers", type=float, nargs="+", default=[740.0, 760.0])
    ap.add_argument("--dur", type=float, default=0.1)
    ap.add_argument("--gain", type=float, default=31.5)  # SBX max
    ap.add_argument("--thr", type=float, default=0.08)
    ap.add_argument("--refine-dur", dest="refine_dur", type=float, default=0.4)
    a = ap.parse_args()
    cells = []; specs = []; sweep = []; clusters_all = []
    for fcm in a.centers:
        fc = fcm * 1e6
        x, rate, rf = C.capture_rx_only(fc, 25e6, a.gain, a.dur)
        specs.append(C.welch_db(x, rate, rf, nfft=4096))
        xs = x[: int(0.021 * rate)]          # 21 ms for the search (4 PSS)
        cands = np.arange(np.ceil((fc - 11.4e6) / 1e5), np.floor((fc + 11.4e6) / 1e5) + 1) * 1e5
        for f0 in cands:
            m, nid2, k, _ = pss_metric(to_bb(xs, rate, f0 - rf))
            sweep.append((f0 / 1e6, m, nid2))
        # cluster above-threshold raster points (ZC ambiguity smears each cell over ~+-300 kHz)
        sw = sorted([t for t in sweep if abs(t[0] * 1e6 - fc) < 11.5e6 and t[1] >= a.thr])
        clusters = []
        for t in sw:
            if clusters and t[0] - clusters[-1][-1][0] <= 0.45:
                clusters[-1].append(t)
            else:
                clusters.append([t])
        clusters_all.extend(clusters)
        del x
    eps_glob = None
    clusters_all.sort(key=lambda cl: -max(t[1] for t in cl))   # strongest first -> measures our clock error
    for cl in clusters_all:
        peak = max(cl, key=lambda t: t[1])[0]
        xr, rate2, rf2, meta = C.capture_rx_only(peak * 1e6, 6.25e6, a.gain, a.refine_dur, lo_offset=3e6, return_meta=True)
        best = None
        grid = np.arange(-80e-6, 80.1e-6, 4e-6) if eps_glob is None else np.arange(eps_glob - 3e-6, eps_glob + 3.1e-6, 1e-6)
        for f0 in np.round(np.arange(peak - 0.3, peak + 0.31, 0.1), 1):
            yb = to_bb(xr[: int(0.06 * rate2)], rate2, f0 * 1e6 - rf2)
            b = search_eps(yb, FS, f0 * 1e6, grid)
            if best is None or b[0] > best[0]:
                best = b + (f0,)
        m2, eps, nid2, k, f0 = best
        yb = to_bb(xr, rate2, f0 * 1e6 - rf2)
        b = search_eps(yb, FS, f0 * 1e6, np.arange(eps - 2e-6, eps + 2.1e-6, 0.25e-6))
        m2, eps, nid2, k = b
        if eps_glob is None:
            eps_glob = eps
        nn = np.arange(len(yb))
        y = yb * np.exp(-2j * np.pi * (-eps * f0 * 1e6) / FS * nn)
        fine = -eps * f0 * 1e6
        del xr, yb
        res = sss_detect(y, nid2, k, eps)
        cp = max(res, key=lambda c: res[c]["score"] - res[c]["second"])
        r = res[cp]
        band = [b[2] for b in BANDS if b[0] <= f0 <= b[1]]
        margin = r["score"] - r["second"]
        cells.append({"f_mhz": float(f0), "apparent_cfo_hz": round(float(fine), 0), "clock_err_ppm": round(eps * 1e6, 2), "band_guess": band, "pss_metric": round(float(m2), 3),
                      "nid2": nid2, "nid1": r["nid1"], "pci": 3 * r["nid1"] + nid2, "cp": cp, "first_sss_subframe": r["first_sf"],
                      "sss_score": round(r["score"], 3), "sss_runner_up": round(r["second"], 3), "n_pss": r["ninst"],
                      # other N_ID_1 values share one m-sequence half, so ~0.5x runner-up is structural
                          "confident": bool(r["score"] > 0.3 and r["score"] > 1.4 * r["second"]),
                      "rx_overflows": meta["overflows"],
                      "per_inst_sss_mean_std": [round(float(np.mean(r["per_inst"])), 3), round(float(np.std(r["per_inst"])), 3)]})
    ded = {}
    for cl in cells:
        key = (cl["f_mhz"], cl.get("pci"))
        if key not in ded or cl["sss_score"] > ded[key]["sss_score"]:
            ded[key] = cl
    cells = sorted(ded.values(), key=lambda c: c["f_mhz"])
    for cl in cells:
        print(cl)
    if cells:
        print("N210 clock error vs LTE towers: %.2f ppm (median over cells)" % np.median([c["clock_err_ppm"] for c in cells]))
    # plot
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    for f, avg, mx, msk in specs:
        a1.plot(f / 1e6, mx, c="tab:orange", lw=.5); a1.plot(f / 1e6, avg, c="tab:blue", lw=.7)
        if msk.any():
            a1.plot(f[msk] / 1e6, avg[msk], "|", c="0.5", ms=5)
    for b0, b1, nm in BANDS[:3]:
        a1.axvspan(b0, b1, color="g", alpha=.06); a1.text((b0 + b1) / 2, a1.get_ylim()[1], nm, ha="center", va="top", fontsize=8)
    a1.set_ylabel("dBFS/bin (spur/DC masked)"); a1.grid(alpha=.3); a1.set_title("Spectrum (blue avg, orange max-hold)")
    sw = np.array(sorted(sweep))
    for nid2 in range(3):
        k = sw[:, 2] == nid2
        a2.plot(sw[k, 0], sw[k, 1], ".", ms=4, label=f"best root N_ID_2={nid2}")
    for cl in cells:
        if "pci" in cl and cl["confident"]:
            a2.annotate(f"PCI {cl['pci']}\n({cl['nid1']},{cl['nid2']})", (cl["f_mhz"], max(t[1] for t in sweep if abs(t[0] - cl["f_mhz"]) < 0.05)), fontsize=8, ha="center", xytext=(0, 8), textcoords="offset points")
    a2.axhline(a.thr, c="k", ls=":", lw=.7); a2.set_ylabel("PSS norm. corr (5 ms folded)"); a2.set_xlabel("candidate carrier centre (MHz, 100 kHz raster)")
    a2.legend(fontsize=8); a2.grid(alpha=.3); a2.set_title("PSS search")
    if cells:
        a2.text(0.01, 0.95, "N210 clock err vs towers: %.1f ppm (GPSDO unlocked)\nsearch dots: coarse 21 ms, uncorrected" % np.median([c["clock_err_ppm"] for c in cells]), transform=a2.transAxes, va="top", fontsize=8)
    fig.suptitle("SIDE QUEST demod — LTE 700 MHz cell search (PSS/SSS), N210 RX-only, IQ in RAM only", fontweight="bold")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "lte_cellsearch_700.png"), dpi=90)
    with open(os.path.join(OUT, "lte_cellsearch_700_summary.json"), "w") as fh:
        json.dump({"generated": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "centers_mhz": a.centers, "gain_db": a.gain,
                   "n210_clock_err_ppm_vs_lte": float(np.median([c["clock_err_ppm"] for c in cells])) if cells else None, "cells": cells}, fh, indent=1)
    print("wrote", os.path.join(OUT, "lte_cellsearch_700.png"))


if __name__ == "__main__":
    main()
