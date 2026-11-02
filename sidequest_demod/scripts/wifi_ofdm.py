"""Minimal pure-numpy 802.11a/g (legacy OFDM, 20 MHz) receiver.

Covers L-STF/L-LTF sync, CFO, channel estimate, L-SIG, and DATA at all
legacy rates 6..54 Mbps (soft Viterbi, K=7). HT/VHT (11n/ac) payloads are
NOT decoded -- their L-SIG still is, which gives duration/length.

A software-only modulator (`modulate`) exists purely for the loopback
self-test; it is never sent to the radio.
"""
import zlib
import numpy as np

FS = 20e6
# ---------------------------------------------------------------- constants
_L = np.array([1,1,-1,-1,1,1,-1,1,-1,1,1,1,1,1,1,-1,-1,1,1,-1,1,-1,1,1,1,1,0,
               1,-1,-1,1,1,-1,1,-1,1,-1,-1,-1,-1,-1,1,1,-1,-1,1,-1,1,-1,1,1,1,1], float)
LTF_F = np.zeros(64, complex)
for i, k in enumerate(range(-26, 27)):
    LTF_F[k % 64] = _L[i]
LTF_T = np.fft.ifft(LTF_F)
_S = {-24: 1, -20: -1, -16: 1, -12: -1, -8: -1, -4: 1, 4: -1, 8: -1, 12: 1, 16: 1, 20: 1, 24: 1}
STF_F = np.zeros(64, complex)
for k, v in _S.items():
    STF_F[k % 64] = np.sqrt(13 / 6) * v * (1 + 1j)
STF_T = np.fft.ifft(STF_F)
PILOT_K = np.array([-21, -7, 7, 21])
PILOT_V = np.array([1, 1, 1, -1], float)
DATA_K = np.array([k for k in range(-26, 27) if k != 0 and k not in PILOT_K])
DATA_BIN = DATA_K % 64
PILOT_BIN = PILOT_K % 64
USED_BIN = np.array([k % 64 for k in range(-26, 27) if k != 0])

# rate bits R1..R4 -> (Mbps, N_BPSC, coding rate num, den)
RATES = {(1,1,0,1): (6, 1, 1, 2), (1,1,1,1): (9, 1, 3, 4), (0,1,0,1): (12, 2, 1, 2),
         (0,1,1,1): (18, 2, 3, 4), (1,0,0,1): (24, 4, 1, 2), (1,0,1,1): (36, 4, 3, 4),
         (0,0,0,1): (48, 6, 2, 3), (0,0,1,1): (54, 6, 3, 4)}
RATE_BITS = {v[0]: k for k, v in RATES.items()}
PUNCT = {(1, 2): np.array([1, 1]), (2, 3): np.array([1, 1, 1, 0]), (3, 4): np.array([1, 1, 1, 0, 0, 1])}


def _scram_seq(state7, n):
    """x^7+x^4+1 sequence; state7 = list of the 7 previous bits x[-7..-1]."""
    s = list(state7)
    out = np.zeros(n, np.uint8)
    for i in range(n):
        b = s[-7] ^ s[-4]
        out[i] = b
        s.append(b)
    return out


POLARITY = 1 - 2 * _scram_seq([1] * 7, 127).astype(float)

# ------------------------------------------------------------- conv code K=7
G0, G1 = 0o133, 0o171
_par = np.array([bin(i).count("1") & 1 for i in range(128)], np.uint8)


def conv_encode(bits):
    reg = 0
    out = np.zeros(2 * len(bits), np.uint8)
    for i, b in enumerate(bits):
        reg = ((reg >> 1) | (int(b) << 6)) & 0x7F
        out[2 * i] = _par[reg & G0]
        out[2 * i + 1] = _par[reg & G1]
    return out


# state = last 6 input bits, newest in bit5.  next_state = (b<<5)|(s>>1)
_S_ = np.arange(64)
_out0 = np.zeros((64, 2), np.uint8); _out1 = np.zeros((64, 2), np.uint8)
for s in range(64):
    for b in (0, 1):
        reg = (b << 6) | s
        o = (_par[reg & G0], _par[reg & G1])
        (_out0 if b == 0 else _out1)[s] = o
# predecessors of state ns: ns=(b<<5)|(s>>1) -> b = ns>>5, s in {(ns&31)<<1, ((ns&31)<<1)|1}
_PRED = np.stack([((_S_ & 31) << 1), ((_S_ & 31) << 1) | 1], 1)
_PB = _S_ >> 5


def viterbi(llr, nbits):
    """Soft Viterbi. llr: (2*nbits,) positive => bit 0 more likely. Returns bits."""
    llr = np.asarray(llr, float)[: 2 * nbits].reshape(-1, 2)
    nb = len(llr)
    pm = np.full(64, -1e18); pm[0] = 0.0
    surv = np.zeros((nb, 64), np.uint8)
    # branch expected outputs for each (ns, which pred)
    o = np.where(_PB[:, None, None] == 0, _out0[_PRED], _out1[_PRED])   # (64,2,2)
    sgn = 1.0 - 2.0 * o                                                  # +1 for bit 0
    for t in range(nb):
        bm = (sgn * llr[t]).sum(-1)                                       # (64,2)
        cand = pm[_PRED] + bm
        ch = np.argmax(cand, 1)
        surv[t] = ch
        pm = cand[_S_, ch]
        pm -= pm.max()
    s = int(np.argmax(pm))
    bits = np.zeros(nb, np.uint8)
    for t in range(nb - 1, -1, -1):
        bits[t] = s >> 5
        s = _PRED[s, surv[t, s]]
    return bits


# ------------------------------------------------------------- interleaver
def _interleave_perm(ncbps, nbpsc):
    s = max(nbpsc // 2, 1)
    k = np.arange(ncbps)
    i = (ncbps // 16) * (k % 16) + k // 16
    j = s * (i // s) + (i + ncbps - (16 * i // ncbps)) % s
    return j     # coded bit k goes to position j[k]


# ------------------------------------------------------------- QAM
def _pam(nb):
    if nb == 1:
        return np.array([-1.0, 1.0]), [[0], [1]]
    if nb == 2:  # per axis for 16QAM
        return np.array([-3.0, -1.0, 1.0, 3.0]), [[0, 0], [0, 1], [1, 1], [1, 0]]
    if nb == 3:
        return np.array([-7.0, -5, -3, -1, 1, 3, 5, 7]), [[0,0,0],[0,0,1],[0,1,1],[0,1,0],[1,1,0],[1,1,1],[1,0,1],[1,0,0]]


def _const(nbpsc):
    if nbpsc == 1:
        lv, lb = _pam(1); return lv.astype(complex), np.array(lb, np.uint8), 1.0
    half = nbpsc // 2
    lv, lb = _pam(half)
    pts, bits = [], []
    for qi, qv in enumerate(lv):
        for ii, iv in enumerate(lv):
            pts.append(iv + 1j * qv); bits.append(list(lb[ii]) + list(lb[qi]))
    norm = {1: 1, 2: np.sqrt(2), 4: np.sqrt(10), 6: np.sqrt(42)}[nbpsc]
    return np.array(pts) / norm, np.array(bits, np.uint8), norm


_CONST = {n: _const(n) for n in (1, 2, 4, 6)}


def qam_map(bits, nbpsc):
    pts, cb, _ = _CONST[nbpsc]
    b = bits.reshape(-1, nbpsc)
    idx = np.array([np.where((cb == r).all(1))[0][0] for r in b])
    return pts[idx]


def qam_llr(sym, nbpsc, noise_var):
    """Max-log LLRs (positive => 0). sym: (N,) equalised."""
    pts, cb, _ = _CONST[nbpsc]
    d = np.abs(sym[:, None] - pts[None, :]) ** 2     # (N, M)
    out = np.zeros((len(sym), nbpsc))
    for b in range(nbpsc):
        d0 = np.where(cb[:, b] == 0, d, np.inf).min(1)
        d1 = np.where(cb[:, b] == 1, d, np.inf).min(1)
        out[:, b] = (d1 - d0) / max(noise_var, 1e-6)
    return out.ravel()


# ------------------------------------------------------------- TX (self-test only)
def modulate(psdu, mbps=6, scram_seed=(1, 0, 1, 1, 1, 0, 1)):
    rb = RATE_BITS[mbps]; _, nbpsc, cn, cd = RATES[rb]
    ncbps = 48 * nbpsc; ndbps = ncbps * cn // cd
    L = len(psdu)
    sig = list(rb) + [0] + [(L >> i) & 1 for i in range(12)]
    sig = sig + [sum(sig) & 1] + [0] * 6
    syms = [_ofdm_sym(qam_map(_interleave(conv_encode(np.array(sig, np.uint8)), 48, 1), 1), 0)]
    bits = np.concatenate([np.zeros(16, np.uint8), np.unpackbits(np.frombuffer(psdu, np.uint8), bitorder="little"), np.zeros(6, np.uint8)])
    nsym = int(np.ceil(len(bits) / ndbps))
    bits = np.concatenate([bits, np.zeros(nsym * ndbps - len(bits), np.uint8)])
    sc = bits ^ _scram_seq(list(scram_seed), len(bits))
    tail0 = 16 + 8 * L
    sc[tail0:tail0 + 6] = 0
    coded = _puncture(conv_encode(sc), (cn, cd))
    for n in range(nsym):
        blk = coded[n * ncbps:(n + 1) * ncbps]
        syms.append(_ofdm_sym(qam_map(_interleave(blk, ncbps, nbpsc), nbpsc), n + 1))
    stf = np.tile(STF_T[:16], 10)
    ltf = np.concatenate([LTF_T[-32:], LTF_T, LTF_T])
    return np.concatenate([stf, ltf] + syms)


def _interleave(bits, ncbps, nbpsc):
    out = np.zeros(ncbps, np.uint8); out[_interleave_perm(ncbps, nbpsc)] = bits; return out


def _puncture(c, cr):
    p = PUNCT[cr]; keep = np.resize(p, len(c)).astype(bool); return c[keep]


def _ofdm_sym(d, n):
    X = np.zeros(64, complex); X[DATA_BIN] = d; X[PILOT_BIN] = PILOT_V * POLARITY[n % 127]
    t = np.fft.ifft(X); return np.concatenate([t[-16:], t])


# ------------------------------------------------------------- RX
def find_packets(x, thr=0.75, minlen=80):
    """Schmidl-Cox style lag-16 autocorrelation detector on 20 MS/s IQ."""
    a = x[16:] * np.conj(x[:-16])
    p = np.abs(x[16:]) ** 2
    k = np.ones(48)
    c = np.convolve(a, k, "valid"); e = np.convolve(p, k, "valid")
    m = np.abs(c) / (e + 1e-12)
    pw = e / 48
    floor = np.percentile(pw, 10)
    on = (m > thr) & (pw > 4 * floor)
    starts = []
    i = 0
    idx = np.flatnonzero(np.diff(on.astype(np.int8)) == 1) + 1
    ends = np.flatnonzero(np.diff(on.astype(np.int8)) == -1) + 1
    for s in idx:
        e_ = ends[ends > s]
        if len(e_) and e_[0] - s >= minlen and (not starts or s - starts[-1] > 400):
            starts.append(int(s))
    return starts, floor


def decode_packet(x, s0, backoff=3, max_syms=600):
    """Try to decode one packet whose STF plateau begins near x[s0]."""
    r = {"start": s0}
    seg = x[s0: s0 + 160 + 160 + 80 * (max_syms + 2) + 200].astype(complex)
    if len(seg) < 600:
        return None
    # coarse CFO from STF (samples 16..144 of plateau)
    a = np.sum(seg[16:144] * np.conj(seg[:128]))
    cfo1 = np.angle(a) / (2 * np.pi * 16) * FS
    n = np.arange(len(seg))
    seg = seg * np.exp(-2j * np.pi * cfo1 / FS * n)
    # fine timing on LTF: correlate a window
    win = seg[100:100 + 360]
    cc = np.abs(np.correlate(win, LTF_T, "valid"))
    # pairs of peaks 64 apart
    sc = cc[:-64] + cc[64:]
    t1 = int(np.argmax(sc)) + 100
    pk = sc.max() / (np.linalg.norm(LTF_T) * np.sqrt(np.mean(np.abs(win) ** 2)) * 8 + 1e-12)
    T1, T2 = seg[t1:t1 + 64], seg[t1 + 64:t1 + 128]
    cfo2 = np.angle(np.sum(T2 * np.conj(T1))) / (2 * np.pi * 64) * FS
    seg = seg * np.exp(-2j * np.pi * cfo2 / FS * n)
    r["cfo_hz"] = float(cfo1 + cfo2)
    b = backoff
    Y1 = np.fft.fft(seg[t1 - b:t1 - b + 64]); Y2 = np.fft.fft(seg[t1 + 64 - b:t1 + 128 - b])
    H = np.zeros(64, complex)
    H[USED_BIN] = (Y1[USED_BIN] + Y2[USED_BIN]) / 2 / LTF_F[USED_BIN]
    nv = np.mean(np.abs(Y1[USED_BIN] - Y2[USED_BIN]) ** 2) / 2
    sp = np.mean(np.abs(H[USED_BIN]) ** 2)
    r["snr_db"] = float(10 * np.log10(sp / max(nv, 1e-12)))
    r["H"] = H
    nv_eq = nv / sp   # noise var after equalisation (approx)

    def sym(i):
        st = t1 + 128 + 80 * i + 16 - b
        Y = np.fft.fft(seg[st:st + 64])
        Z = Y / np.where(H == 0, 1, H)
        p = Z[PILOT_BIN] * PILOT_V * POLARITY[i % 127]
        ph = np.angle(np.sum(p * np.abs(H[PILOT_BIN]) ** 2))
        return Z[DATA_BIN] * np.exp(-1j * ph)

    # L-SIG
    d = sym(0)
    llr = qam_llr(d, 1, nv_eq)
    perm = _interleave_perm(48, 1)
    sigbits = viterbi(llr[perm], 24)
    rb = tuple(int(v) for v in sigbits[:4])
    L = int(sum(int(sigbits[5 + i]) << i for i in range(12)))
    par_ok = (int(sigbits[:17].sum()) & 1) == int(sigbits[17])
    r.update(sig_parity_ok=par_ok, length=L, rate_bits=rb)
    if not par_ok or rb not in RATES or L == 0:
        r["status"] = "bad L-SIG"
        return r
    mbps, nbpsc, cn, cd = RATES[rb]
    r["mbps"] = mbps
    ncbps = 48 * nbpsc; ndbps = ncbps * cn // cd
    nsym = int(np.ceil((16 + 8 * L + 6) / ndbps))
    r["nsym"] = nsym
    r["dur_us"] = 20 + 4 + 4 * nsym
    # HT-SIG check: symbol after L-SIG rotated QBPSK -> 11n frame
    d1 = sym(1)
    r["ht_hint"] = bool(np.mean(np.abs(d1.imag)) > 1.5 * np.mean(np.abs(d1.real)))
    if nsym > max_syms or t1 + 128 + 80 * (nsym + 1) + 16 > len(seg):
        r["status"] = "too long / truncated"
        return r
    perm = _interleave_perm(ncbps, nbpsc)
    p = PUNCT[(cn, cd)]
    allllr = []
    evm = []
    for i in range(nsym):
        d = sym(i + 1)
        if nbpsc == 1:
            evm.append(np.mean(np.abs(np.abs(d.real) - 1) ** 2))
        llr = qam_llr(d, nbpsc, nv_eq)[perm]
        allllr.append(llr)
    llr = np.concatenate(allllr)
    nmother = int(len(llr) / p.sum() * len(p))
    full = np.zeros(nmother)
    keep = np.resize(p, nmother).astype(bool)
    full[keep] = llr[: keep.sum()]
    bits = viterbi(full, nsym * ndbps)
    seed = list(bits[:7])            # SERVICE bits 0..6 are zero before scrambling
    # recover scrambler state: first 7 output bits = seq bits; seq[n]=seq[n-7]^seq[n-4]
    seq = np.zeros(len(bits), np.uint8); seq[:7] = seed
    for k in range(7, len(bits)):
        seq[k] = seq[k - 7] ^ seq[k - 4]
    desc = bits ^ seq
    psdu = np.packbits(desc[16:16 + 8 * L], bitorder="little").tobytes()
    r["psdu"] = psdu
    r["fcs_ok"] = len(psdu) >= 4 and zlib.crc32(psdu[:-4]) == int.from_bytes(psdu[-4:], "little")
    r["status"] = "ok" if r["fcs_ok"] else "fcs fail"
    return r
