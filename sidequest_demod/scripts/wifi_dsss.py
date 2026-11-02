"""Minimal 802.11b DSSS receiver: long-preamble, 1 Mbps DBPSK / 2 Mbps DQPSK.

Barker-11 matched filter at 22 MS/s (2 samples/chip), early/late peak
tracking, differential detection, self-sync descrambler, SFD search,
PLCP header CRC-16, PSDU + FCS. CCK (5.5/11 Mbps) payloads are not
decoded; their PLCP header still is.  `modulate` is for the software
self-test only.
"""
import zlib
import numpy as np

FS = 22e6
SPC = 2  # samples per chip
BARKER = np.array([1, -1, 1, 1, -1, 1, 1, 1, -1, -1, -1], float)
MF = np.repeat(BARKER, SPC)[::-1]
SFD_LONG = np.array([(0xF3A0 >> i) & 1 for i in range(16)], np.uint8)
SFD_SHORT = SFD_LONG[::-1].copy()
SIGNALS = {0x0A: 1, 0x14: 2, 0x37: 5.5, 0x6E: 11}


def crc16_hdr(bits):
    crc = 0xFFFF
    for b in bits:
        fb = ((crc >> 15) & 1) ^ int(b)
        crc = (crc << 1) & 0xFFFF
        if fb:
            crc ^= 0x1021
    crc = ~crc & 0xFFFF
    return np.array([(crc >> (15 - i)) & 1 for i in range(16)], np.uint8)


def scramble(bits, state=0x6C):
    s = [(state >> i) & 1 for i in range(7)]  # s[0]=z1 .. s[6]=z7
    out = np.zeros(len(bits), np.uint8)
    for i, b in enumerate(bits):
        o = int(b) ^ s[3] ^ s[6]
        out[i] = o
        s = [o] + s[:6]
    return out


def descramble(bits):
    b = np.asarray(bits, np.uint8)
    out = b.copy()
    out[7:] = b[7:] ^ b[3:-4] ^ b[:-7]
    out[:7] = 0
    return out


def _bits(v, n):
    return [(v >> i) & 1 for i in range(n)]


def modulate(psdu, mbps=1):
    L = len(psdu)
    us = L * 8 // mbps
    hdr = _bits({1: 0x0A, 2: 0x14}[mbps], 8) + _bits(0x04, 8) + _bits(us, 16)
    hdr = np.array(hdr, np.uint8); hdr = np.concatenate([hdr, crc16_hdr(hdr)])
    pre = np.concatenate([np.ones(128, np.uint8), SFD_LONG, hdr])
    data = np.unpackbits(np.frombuffer(psdu, np.uint8), bitorder="little")
    sc = scramble(np.concatenate([pre, data]))
    ph = 0.0; syms = []
    for b in sc[:len(pre)]:
        ph += np.pi * b; syms.append(np.exp(1j * ph))
    d = sc[len(pre):]
    if mbps == 1:
        for b in d:
            ph += np.pi * b; syms.append(np.exp(1j * ph))
    else:
        tab = {(0, 0): 0, (0, 1): np.pi / 2, (1, 1): np.pi, (1, 0): -np.pi / 2}
        for i in range(0, len(d), 2):
            ph += tab[(int(d[i]), int(d[i + 1]))]; syms.append(np.exp(1j * ph))
    chips = (np.array(syms)[:, None] * BARKER[None, :]).ravel()
    return np.repeat(chips, SPC)


def decode_burst(x):
    """x: 22 MS/s complex segment containing (start of) one DSSS burst."""
    y = np.convolve(x, MF, "full")[len(MF) - 1:]
    a = np.abs(y)
    P = 11 * SPC
    n = len(a) // P
    if n < 200:
        return None
    prof = a[: n * P].reshape(n, P).sum(0)
    ph = int(np.argmax(prof))
    pr = float(prof.max() / (np.median(prof) + 1e-12))
    r = {"barker_peak_ratio": pr}
    if pr < 2.5:
        r["status"] = "not DSSS"
        return r
    # start tracking where the Barker peaks rise out of the noise
    pk = a[ph: n * P: P]
    ref = np.percentile(pk, 90)
    first = int(np.argmax(pk > 0.4 * ref))
    pos = ph + first * P; samp = []; poss = []
    while pos + 1 < len(y):
        w = a[pos - 1:pos + 2] if pos >= 1 else a[pos:pos + 2]
        if len(w) == 3:
            pos += int(np.argmax(w)) - 1
        samp.append(y[pos]); poss.append(pos); pos += P
    s = np.array(samp)
    d = s[1:] * np.conj(s[:-1])
    # remove residual CFO rotation (BPSK-ish preamble -> square)
    rot = np.angle(np.sum(d[:120] ** 2)) / 2
    d = d * np.exp(-1j * rot)
    r["cfo_hz"] = float(rot / (2 * np.pi) * 1e6)
    hb = (d.real < 0).astype(np.uint8)
    desc = descramble(hb)
    # find SFD
    best = None
    for name, sfd in (("long", SFD_LONG), ("short", SFD_SHORT)):
        c = np.convolve(1 - 2.0 * desc[: min(len(desc), 400)], (1 - 2.0 * sfd)[::-1], "valid")
        k = int(np.argmax(c))
        if c[k] >= 15 and (best is None or c[k] > best[1]):
            best = (name, c[k], k)
    if best is None:
        r["status"] = "no SFD"
        return r
    r["preamble"] = best[0]
    k0 = best[2] + 16
    if best[0] == "short":
        r["status"] = "short preamble (2 Mbps DQPSK header) - not decoded"
        return r
    hdr = desc[k0:k0 + 48]
    if len(hdr) < 48:
        r["status"] = "truncated"; return r
    sig = int(sum(int(hdr[i]) << i for i in range(8)))
    us = int(sum(int(hdr[16 + i]) << i for i in range(16)))
    r.update(signal=sig, mbps=SIGNALS.get(sig), length_us=us, service=int(sum(int(hdr[8 + i]) << i for i in range(8))))
    r["hdr_crc_ok"] = bool((crc16_hdr(hdr[:32]) == hdr[32:48]).all())
    if not r["hdr_crc_ok"]:
        r["status"] = "bad PLCP CRC"; return r
    j = k0 + 48   # index into desc / d (d[i] is the bit for symbol i+1)
    if r["mbps"] == 1:
        nb = us
        if j + nb > len(desc):
            r["status"] = "truncated"; return r
        bits = desc[j:j + nb]
    elif r["mbps"] == 2:
        nsy = us
        dd = d[j:j + nsy]
        if len(dd) < nsy:
            r["status"] = "truncated"; return r
        q = np.round(np.angle(dd) / (np.pi / 2)).astype(int) % 4
        tab = {0: (0, 0), 1: (0, 1), 2: (1, 1), 3: (1, 0)}
        raw = np.array([b for v in q for b in tab[v]], np.uint8)
        # continue the self-sync descrambler across the boundary
        full = np.concatenate([hb[j - 7:j], raw])
        bits = descramble(full)[7:]
    elif r["mbps"] == 11:
        nsy = us * 11 // 8          # 8 bits per CCK symbol, 1.375 Msym/s
        p0 = poss[j] + P             # first PSDU chip (sample index in x)
        ref = s[j]                   # last header symbol (phase reference)
        best = None
        for conj in (False, True):
            raw = cck11_demod(x, p0, nsy, ref, rot, conj)
            if raw is None:
                break
            full = np.concatenate([hb[j - 7:j], raw])
            bits = descramble(full)[7:]
            psdu = np.packbits(bits[: len(bits) // 8 * 8], bitorder="little").tobytes()
            ok = len(psdu) > 4 and zlib.crc32(psdu[:-4]) == int.from_bytes(psdu[-4:], "little")
            if best is None or ok:
                best = (bits, ok)
            if ok:
                break
        if best is None:
            r["status"] = "truncated"; return r
        bits = best[0]
    else:
        r["status"] = f"CCK {r['mbps']} Mbps payload - header only"; return r
    psdu = np.packbits(bits[: len(bits) // 8 * 8], bitorder="little").tobytes()
    r["psdu"] = psdu
    r["fcs_ok"] = len(psdu) > 4 and zlib.crc32(psdu[:-4]) == int.from_bytes(psdu[-4:], "little")
    r["status"] = "ok" if r["fcs_ok"] else "fcs fail"
    return r


# ---------------------------------------------------------------- CCK 11 Mbps
_QP = np.array([0, np.pi / 2, np.pi, 3 * np.pi / 2])     # 00,01,10,11 for phi2..4
_CW = []   # 64 codewords with phi1 = 0, index = (d2d3, d4d5, d6d7)
_CWB = []
for a in range(4):
    for b in range(4):
        for c in range(4):
            p2, p3, p4 = _QP[a], _QP[b], _QP[c]
            _CW.append(np.exp(1j * np.array([p2 + p3 + p4, p3 + p4, p2 + p4, p4 + np.pi, p2 + p3, p3, p2 + np.pi, 0.0])))
            _CWB.append([a >> 1, a & 1, b >> 1, b & 1, c >> 1, c & 1])   # bit order d2,d3 / d4,d5 / d6,d7
_CW = np.array(_CW); _CWB = np.array(_CWB, np.uint8)
_DQ = {0: (0, 0), 1: (0, 1), 2: (1, 1), 3: (1, 0)}        # phi1 change (quadrants) -> d0d1


def cck11_demod(x, p0, nsy, ref, rot_per_us, conj=False):
    """Hard-decision CCK-11 demod. Returns scrambled bit stream or None."""
    idx = p0 + SPC * np.arange(nsy * 8)
    if idx[-1] >= len(x):
        return None
    chips = x[idx].astype(complex)
    chips = chips * np.exp(-1j * rot_per_us / 22 * (idx - p0))   # CFO per sample
    ref = ref * np.exp(-1j * rot_per_us / 22 * (-P_SYM))
    if conj:
        chips = np.conj(chips); ref = np.conj(ref)
    blk = chips.reshape(nsy, 8)
    m = blk @ np.conj(_CW).T                                    # (nsy, 64)
    k = np.argmax(np.abs(m), 1)
    ph1 = np.angle(m[np.arange(nsy), k])
    prev = np.concatenate([[np.angle(ref)], ph1[:-1]])
    dph = ph1 - prev - np.pi * (np.arange(nsy) % 2)             # odd symbols carry extra pi
    q = np.round(dph / (np.pi / 2)).astype(int) % 4
    out = np.zeros((nsy, 8), np.uint8)
    for i in range(nsy):
        out[i, :2] = _DQ[q[i]]; out[i, 2:] = _CWB[k[i]]
    return out.ravel()


P_SYM = 11 * SPC
