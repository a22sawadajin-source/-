"""Loading, quality checks and 4h aggregation. Never fills gaps with invented values."""
import csv
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

H1 = 3600
H4 = 14400


@dataclass
class Bars:
    ts: list  # bar OPEN time, epoch seconds (UTC)
    o: list
    h: list
    l: list
    c: list
    v: list

    def __len__(self):
        return len(self.ts)


def parse_time(s):
    s = str(s).strip()
    try:
        x = float(s)
        return int(x / 1000) if x > 1e11 else int(x)
    except ValueError:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_bars_csv(path):
    """CSV columns: time,open,high,low,close,volume. Duplicates keep first; sorted by time."""
    rows = {}
    dups = 0
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            t = parse_time(r["time"])
            if t in rows:
                dups += 1
                continue
            rows[t] = (float(r["open"]), float(r["high"]), float(r["low"]),
                       float(r["close"]), float(r.get("volume") or 0))
    ts = sorted(rows)
    b = Bars(ts, [rows[t][0] for t in ts], [rows[t][1] for t in ts],
             [rows[t][2] for t in ts], [rows[t][3] for t in ts], [rows[t][4] for t in ts])
    b.duplicates = dups
    return b


def load_funding_csv(path):
    """CSV columns: time,rate. Returns sorted list of (settle_ts, rate)."""
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            out.append((parse_time(r["time"]), float(r["rate"])))
    out.sort()
    return out


def quality_report(b, now_ts=None):
    """Detect gaps, duplicates, invalid OHLC, off-grid times, and a possibly unconfirmed last bar."""
    gaps, invalid, offgrid = [], [], []
    for i, t in enumerate(b.ts):
        if t % H1:
            offgrid.append(iso(t))
        o, h, l, c = b.o[i], b.h[i], b.l[i], b.c[i]
        if min(o, h, l, c) <= 0 or h < max(o, c, l) or l > min(o, c, h):
            invalid.append(iso(t))
        if i and t - b.ts[i - 1] != H1:
            gaps.append({"after": iso(b.ts[i - 1]), "next": iso(t),
                         "missing_bars": (t - b.ts[i - 1]) // H1 - 1})
    unconfirmed_last = bool(now_ts is not None and b.ts and b.ts[-1] + H1 > now_ts)
    return {
        "bars": len(b), "first": iso(b.ts[0]) if b.ts else None,
        "last": iso(b.ts[-1]) if b.ts else None,
        "duplicates_dropped": getattr(b, "duplicates", 0),
        "gap_count": len(gaps), "missing_bars_total": sum(g["missing_bars"] for g in gaps),
        "gaps": gaps[:200], "invalid_ohlc": invalid[:200], "invalid_ohlc_count": len(invalid),
        "off_grid_times": offgrid[:200], "last_bar_maybe_unconfirmed": unconfirmed_last,
    }


def build_4h(b):
    """Aggregate to UTC-aligned 4h bars. Only complete groups (4 consecutive hourly bars) are kept.
    Returns Bars whose ts is the 4h OPEN time, plus the count of skipped incomplete groups."""
    groups = {}
    for i, t in enumerate(b.ts):
        groups.setdefault(t - t % H4, []).append(i)
    ts, o, h, l, c, v, skipped = [], [], [], [], [], [], 0
    for k in sorted(groups):
        idx = groups[k]
        if len(idx) != 4 or [b.ts[i] for i in idx] != [k + j * H1 for j in range(4)]:
            skipped += 1
            continue
        ts.append(k)
        o.append(b.o[idx[0]])
        h.append(max(b.h[i] for i in idx))
        l.append(min(b.l[i] for i in idx))
        c.append(b.c[idx[-1]])
        v.append(sum(b.v[i] for i in idx))
    r = Bars(ts, o, h, l, c, v)
    r.skipped_incomplete = skipped
    return r
