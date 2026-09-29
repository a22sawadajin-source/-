"""EMA / ATR. Series restart after any time gap; value is None until warm-up completes."""


def ema_contiguous(closes, ts, step, period):
    """Standard EMA seeded with the SMA of the first `period` bars of each contiguous run."""
    out = [None] * len(closes)
    k = 2.0 / (period + 1)
    start = 0
    for i in range(len(closes)):
        if i and ts[i] - ts[i - 1] != step:
            start = i
        n = i - start + 1
        if n == period:
            out[i] = sum(closes[start:i + 1]) / period
        elif n > period:
            out[i] = closes[i] * k + out[i - 1] * (1 - k)
    return out


def atr_wilder(h, l, c, ts, step, period):
    """Wilder ATR. TR needs the previous close; first ATR = mean of first `period` TRs."""
    out = [None] * len(c)
    trs = []
    start = 0
    for i in range(len(c)):
        if i == 0 or ts[i] - ts[i - 1] != step:
            start = i
            trs = []
            continue
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        trs.append(tr)
        if len(trs) == period:
            out[i] = sum(trs) / period
        elif len(trs) > period:
            out[i] = (out[i - 1] * (period - 1) + tr) / period
    return out
