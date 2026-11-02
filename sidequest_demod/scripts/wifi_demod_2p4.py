"""Side quest: RX-only 2.4 GHz Wi-Fi demod on the N210/SBX.

For each channel: capture IQ into RAM only, decode legacy OFDM (11a/g) and
DSSS (11b 1/2 Mbps) frames, check FCS, parse MAC headers with scapy
(already on this box; not added to radar requirements). Writes only a
plot + a small JSON of decoded metadata (MACs masked). No raw IQ on disk.
TX is never touched.

usage: python3 wifi_demod_2p4.py [--chans 1 6 11] [--dur 0.5] [--gain 30]
"""
import argparse, json, os, sys, time, hashlib
from collections import Counter, defaultdict
import numpy as np
from scipy.signal import resample_poly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sq_common as C
import wifi_ofdm as W
import wifi_dsss as D

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "plots")

SUBTYPES = {(0, 8): "beacon", (0, 5): "probe-resp", (0, 4): "probe-req", (0, 11): "auth", (0, 0): "assoc-req",
            (0, 1): "assoc-resp", (0, 13): "action", (0, 12): "deauth", (1, 13): "ack", (1, 11): "rts", (1, 12): "cts",
            (1, 9): "block-ack", (1, 8): "bar", (2, 0): "data", (2, 4): "null", (2, 8): "qos-data", (2, 12): "qos-null"}


def redact_ssid(v):
    if v is None or v == "<hidden>":
        return v
    return f"<redacted len={len(v)} sha1={hashlib.sha1(v.encode()).hexdigest()[:6]}>"


def mask_mac(m):
    return None if m is None else m[:8] + ":xx:xx:xx"


def parse80211(psdu):
    out = {"len": len(psdu)}
    try:
        from scapy.layers.dot11 import Dot11, Dot11Elt
        f = Dot11(psdu[:-4])
        out["kind"] = SUBTYPES.get((f.type, f.subtype), f"t{f.type}/s{f.subtype}")
        out["addr2"] = f.addr2; out["addr1"] = f.addr1
        e = f.getlayer(Dot11Elt)
        while e is not None:
            if e.ID == 0:
                out["ssid"] = e.info.decode("utf-8", "replace") if e.info else "<hidden>"
            elif e.ID == 3 and e.info:
                out["ds_chan"] = e.info[0]
            e = e.payload.getlayer(Dot11Elt)
        if out["kind"] == "beacon":
            from scapy.layers.dot11 import Dot11Beacon
            b = f.getlayer(Dot11Beacon)
            out["beacon_int_tu"] = int(b.beacon_interval)
    except Exception as ex:  # noqa
        out["kind"] = out.get("kind", "?"); out["parse_err"] = str(ex)[:60]
    return out


def bursts(x, rate, thr_db=8, min_us=20):
    k = int(rate * 1e-6)
    p = np.convolve(np.abs(x) ** 2, np.ones(k) / k, "same")[::k]
    fl = np.percentile(p, 20)
    on = p > fl * 10 ** (thr_db / 10)
    d = np.diff(np.concatenate([[0], on.astype(np.int8), [0]]))
    s, e = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    keep = (e - s) >= min_us
    return s[keep] * k, e[keep] * k, fl


def run_channel(ch, dur, gain):
    fc = 2407e6 + 5e6 * ch
    t0 = time.time()
    x, rate, rf = C.capture_rx_only(fc, 25e6, gain, dur)
    res = {"ch": ch, "fc_mhz": fc / 1e6, "rate": rate, "gain": gain, "n": len(x), "frames": []}
    res["peak_abs"] = float(np.max(np.abs(x)))
    f, avg, mx, msk = C.welch_db(x, rate, rf)
    res["spec"] = (f, avg, mx, msk)
    # ---- OFDM
    x20 = resample_poly(x, 4, 5).astype(np.complex64)
    starts, _ = W.find_packets(x20)
    res["ofdm_candidates"] = len(starts)
    best_ofdm = None
    for s in starts:
        r = W.decode_packet(x20, max(s - 8, 0))
        if r is None:
            continue
        fr = {"phy": "ofdm", "t_ms": s / 20e3, "status": r.get("status"), "mbps": r.get("mbps"), "length": r.get("length"),
              "snr_db": round(r.get("snr_db", np.nan), 1), "cfo_khz": round(r.get("cfo_hz", np.nan) / 1e3, 1), "ht": r.get("ht_hint")}
        if fr["status"] == "fcs fail" and fr["ht"]:
            fr["status"] = "HT/11n payload (L-SIG only)"
        if r.get("fcs_ok"):
            fr.update(parse80211(r["psdu"]))
            if best_ofdm is None or r["snr_db"] > best_ofdm[0]["snr_db"]:
                best_ofdm = (r, x20[s - 8: s + 2000].copy())
        res["frames"].append(fr)
    del x20
    # ---- DSSS: try every burst >= 150 us (long preamble alone is 192 us)
    x22 = resample_poly(x, 22, 25).astype(np.complex64)
    bs, be, _ = bursts(x, rate)
    res["bursts"] = len(bs)
    nd = 0
    for s, e in zip(bs, be):
        if (e - s) / rate < 150e-6:
            continue
        a = int(s * 22 / 25) - 200; b = int(e * 22 / 25) + 200
        r = D.decode_burst(x22[max(a, 0): b])
        if r is None or r.get("status") == "not DSSS":
            continue
        nd += 1
        fr = {"phy": "dsss", "t_ms": s / rate * 1e3, "status": r.get("status"), "mbps": r.get("mbps"),
              "length": r.get("length_us"), "cfo_khz": round(r.get("cfo_hz", np.nan) / 1e3, 1), "barker_ratio": round(r["barker_peak_ratio"], 1)}
        if r.get("fcs_ok"):
            fr.update(parse80211(r["psdu"]))
        res["frames"].append(fr)
    res["dsss_candidates"] = nd
    del x22, x
    res["best_ofdm"] = best_ofdm
    res["secs"] = time.time() - t0
    return res


def plot(results, path):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    n = len(results)
    fig, axs = plt.subplots(3, n, figsize=(6 * n, 12), squeeze=False)
    for i, r in enumerate(results):
        f, avg, mx, msk = r["spec"]
        ax = axs[0, i]
        ax.plot(f / 1e6, mx, c="tab:orange", lw=.6, label="max-hold"); ax.plot(f / 1e6, avg, c="tab:blue", lw=.8, label="avg")
        if msk.any():
            ax.plot(f[msk] / 1e6, mx[msk], "|", c="0.5", ms=5, label="spur/DC masked")
        ax.set_title(f"ch{r['ch']} {r['fc_mhz']:.0f} MHz spectrum (spur-masked)"); ax.set_xlabel("MHz"); ax.set_ylabel("dBFS/bin"); ax.grid(alpha=.3); ax.legend(fontsize=7)
        ax = axs[1, i]
        cols = {"ok": "g", "fcs fail": "orange", "HT/11n payload (L-SIG only)": "tab:blue"}
        for fr in r["frames"]:
            y = {"ofdm": 1, "dsss": 0}[fr["phy"]]
            ax.plot(fr["t_ms"], y + (0.15 if fr["status"] == "ok" else -0.15), "|", ms=14, c=cols.get(fr["status"], "r"))
        ax.set_yticks([0, 1]); ax.set_yticklabels(["DSSS 11b", "OFDM 11a/g/n L-SIG"]); ax.set_ylim(-.6, 1.6)
        ok = sum(fr["status"] == "ok" for fr in r["frames"])
        ax.set_title(f"ch{r['ch']} frames: green=FCS ok ({ok}), blue=11n L-SIG only,\norange=FCS fail, red=other", fontsize=9); ax.set_xlabel("ms"); ax.grid(alpha=.3)
        ax = axs[2, i]
        if r["best_ofdm"]:
            rr, seg = r["best_ofdm"]
            H = np.fft.fftshift(rr["H"]); k = np.arange(-32, 32); u = np.abs(H) > 0
            ax.plot(k[u], 20 * np.log10(np.abs(H[u])), "o-", ms=3, label="|H| dB")
            ax.set_ylim(20 * np.log10(np.abs(H[u]).max()) - 25, 20 * np.log10(np.abs(H[u]).max()) + 3)
            ax2 = ax.twinx(); ax2.plot(k[u], np.unwrap(np.angle(H[u])), "r.", ms=3); ax2.set_ylabel("phase (rad, slope = timing)", color="r")
            ax.set_title(f"L-LTF channel est., best FCS-ok OFDM frame:\n{rr.get('mbps')} Mbps, SNR {rr['snr_db']:.1f} dB, apparent CFO {rr['cfo_hz']/1e3:.1f} kHz", fontsize=9)
            ax.set_xlabel("subcarrier"); ax.set_ylabel("|H| dB"); ax.grid(alpha=.3)
        else:
            ax.text(.5, .5, "no FCS-good OFDM frame", ha="center", transform=ax.transAxes)
    fig.suptitle("SIDE QUEST demod — N210/SBX RX-only 2.4 GHz, IQ in RAM only, TX off", fontweight="bold")
    fig.tight_layout(); fig.savefig(path, dpi=90)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chans", type=int, nargs="+", default=[1, 6, 11])
    ap.add_argument("--dur", type=float, default=0.5)
    ap.add_argument("--gain", type=float, default=31.5)  # SBX max gain
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    results = []
    for ch in a.chans:
        r = run_channel(ch, a.dur, a.gain)
        results.append(r)
        st = Counter(fr["status"] for fr in r["frames"])
        print(f"ch{ch}: peak|x|={r['peak_abs']:.3f} bursts={r['bursts']} ofdm_cand={r['ofdm_candidates']} dsss_cand={r['dsss_candidates']} status={dict(st)} ({r['secs']:.1f}s)")
    plot(results, os.path.join(OUT, "wifi_demod_2p4.png"))
    # summary JSON (metadata only, MACs masked)
    summ = {"generated": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "dur_s": a.dur, "gain_db": a.gain, "channels": []}
    for r in results:
        red = {"addr1": mask_mac, "addr2": mask_mac, "ssid": redact_ssid}
        frames = [{k: red.get(k, lambda z: z)(v) for k, v in fr.items()} for fr in r["frames"]]
        aps = defaultdict(lambda: {"n": 0})
        for fr in r["frames"]:
            if fr.get("kind") in ("beacon", "probe-resp") and fr.get("addr2"):
                key = (fr["kind"], mask_mac(fr["addr2"]), fr.get("ssid"))
                d = aps[key]; d["n"] += 1
                for k in ("ssid", "ds_chan", "beacon_int_tu", "phy", "mbps"):
                    if k in fr: d[k] = fr[k]
                d.setdefault("snr_db", []).append(fr.get("snr_db"))
        kinds = Counter(fr.get("kind", "(no FCS)") for fr in r["frames"] if fr["status"] == "ok")
        summ["channels"].append({"ch": r["ch"], "fc_mhz": r["fc_mhz"], "bursts": r["bursts"], "ofdm_candidates": r["ofdm_candidates"],
                                 "dsss_candidates": r["dsss_candidates"], "status_counts": dict(Counter(fr["status"] for fr in r["frames"])),
                                 "fcs_ok_kinds": dict(kinds),
                                 "bss": [dict(kind=k[0], bssid=k[1], ssid=redact_ssid(k[2]), **{kk: vv for kk, vv in v.items() if kk != "ssid"}) for k, v in aps.items()],
                                 "rates_seen_mbps": dict(Counter(fr.get("mbps") for fr in r["frames"] if fr.get("mbps"))), "frames": frames})
        print(f"  ch{r['ch']} FCS-ok kinds: {dict(kinds)}")
        for (kd, m, ss), d in aps.items():
            print(f"    {kd} {m} ssid={d.get('ssid')!r} ds_chan={d.get('ds_chan')} BI={d.get('beacon_int_tu')}TU {d.get('phy')} {d.get('mbps')}Mbps n={d['n']}")
    with open(os.path.join(OUT, "wifi_demod_2p4_summary.json"), "w") as fh:
        json.dump(summ, fh, indent=1, default=str)
    print("wrote", os.path.join(OUT, "wifi_demod_2p4.png"))


if __name__ == "__main__":
    main()
