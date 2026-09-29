"""Read-only public market data fetcher (no keys, no order endpoints).

ASSUMPTION: endpoint paths / response shapes below follow MEXC's public futures docs as recalled
and have NOT been verified from this environment (network blocked). Check with `--dry-run` output
and the saved raw JSON before trusting the data. Raw responses are stored with a SHA-256 manifest.
"""
import argparse
import csv
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

from .data import H1, iso, sha256_file

BASE = "https://contract.mexc.com/api/v1/contract"
UA = {"User-Agent": "btbt-readonly/0.3"}


def _get(url, retries=4):
    for k in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read())
        except Exception:
            if k == retries - 1:
                raise
            time.sleep(2 ** (k + 1))


def parse_kline(payload):
    d = payload["data"]
    return [(int(t), float(o), float(h), float(l), float(c), float(v)) for t, o, h, l, c, v in
            zip(d["time"], d["open"], d["high"], d["low"], d["close"], d["vol"])]


def parse_funding(payload):
    return [(int(x["settleTime"]) // 1000, float(x["fundingRate"]))
            for x in payload["data"]["resultList"]]


def fetch_klines(symbol, interval, start, end, outdir, chunk_bars=1500, step=H1):
    raw_dir = os.path.join(outdir, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    rows, manifest, s = {}, [], start
    while s < end:
        e = min(end, s + chunk_bars * step)
        url = f"{BASE}/kline/{symbol}?interval={interval}&start={s}&end={e}"
        payload = _get(url)
        path = os.path.join(raw_dir, f"kline_{interval}_{s}_{e}.json")
        with open(path, "w") as f:
            json.dump(payload, f)
        got = parse_kline(payload) if payload.get("success") else []
        for r in got:
            rows[r[0]] = r
        manifest.append(dict(url=url, file=path, sha256=sha256_file(path), rows=len(got),
                             fetched_at=iso(int(time.time()))))
        s = e
        time.sleep(0.3)
    return sorted(rows.values()), manifest


def fetch_funding(symbol, outdir, page_size=100, max_pages=2000):
    raw_dir = os.path.join(outdir, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    rows, manifest = {}, []
    for p in range(1, max_pages + 1):
        url = f"{BASE}/funding_rate/history?symbol={symbol}&page_num={p}&page_size={page_size}"
        payload = _get(url)
        path = os.path.join(raw_dir, f"funding_p{p}.json")
        with open(path, "w") as f:
            json.dump(payload, f)
        got = parse_funding(payload) if payload.get("success") else []
        manifest.append(dict(url=url, file=path, sha256=sha256_file(path), rows=len(got),
                             fetched_at=iso(int(time.time()))))
        if not got:
            break
        for t, r in got:
            rows[t] = r
        time.sleep(0.3)
    return sorted(rows.items()), manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fetch public BTC_USDT futures data (read-only)")
    ap.add_argument("--symbol", default="BTC_USDT")
    ap.add_argument("--start", required=True, help="UTC ISO date, e.g. 2022-01-01")
    ap.add_argument("--end", default=None, help="UTC ISO date; default now")
    ap.add_argument("--out", default="data")
    a = ap.parse_args(argv)
    st = int(datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc).timestamp())
    en = int(datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc).timestamp()) \
        if a.end else int(time.time())
    os.makedirs(a.out, exist_ok=True)
    k, m1 = fetch_klines(a.symbol, "Min60", st, en, a.out)
    with open(os.path.join(a.out, "bars_1h.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        w.writerows(k)
    fr, m2 = fetch_funding(a.symbol, a.out)
    with open(os.path.join(a.out, "funding.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "rate"])
        w.writerows(fr)
    detail = _get(f"{BASE}/detail?symbol={a.symbol}")
    dp = os.path.join(a.out, "raw", "contract_detail.json")
    with open(dp, "w") as f:
        json.dump(detail, f, indent=2)
    man = dict(klines=m1, funding=m2, contract_detail=dict(file=dp, sha256=sha256_file(dp)),
               bars_csv_sha256=sha256_file(os.path.join(a.out, "bars_1h.csv")),
               funding_csv_sha256=sha256_file(os.path.join(a.out, "funding.csv")))
    with open(os.path.join(a.out, "manifest.json"), "w") as f:
        json.dump(man, f, indent=1)
    print(f"bars={len(k)} funding={len(fr)} -> {a.out}; contract detail saved to {dp} (review it, then set contract.verified)")


if __name__ == "__main__":
    main()
