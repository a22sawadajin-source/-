"""CLI: run (dev/mid/test) and freeze. The test period can be evaluated exactly once."""
import argparse
import bisect
import glob
import json
import os
import sys
import time

from . import engine, report
from .data import iso, load_bars_csv, load_funding_csv, parse_time, quality_report, sha256_file

FREEZE = "results/freeze.json"
LOCK = "results/test_evaluated.lock"


def _hashes(args):
    files = ["spec-v0.3.md", args.config, args.data] + sorted(glob.glob("btbt/*.py"))
    if args.funding and os.path.exists(args.funding):
        files.append(args.funding)
    return {f: sha256_file(f) for f in files if os.path.exists(f)}


def cmd_freeze(args):
    cfg = json.load(open(args.config))
    if not cfg["periods"].get("test"):
        sys.exit("periods.test が未設定です。データ範囲確認後に日付を固定してください。")
    os.makedirs("results", exist_ok=True)
    json.dump(dict(frozen_at=iso(int(time.time())), hashes=_hashes(args)),
              open(FREEZE, "w"), indent=1)
    print("frozen ->", FREEZE)


def cmd_run(args):
    cfg = json.load(open(args.config))
    per = cfg["periods"].get(args.period)
    if not per:
        sys.exit(f"periods.{args.period} が未設定です。")
    final = args.period == "test"
    if final:
        if not os.path.exists(FREEZE):
            sys.exit("freeze 未実行。先に `freeze` してください。")
        if json.load(open(FREEZE))["hashes"] != _hashes(args):
            sys.exit("凍結後に仕様・設定・コード・データが変更されています。未使用期間は評価しません。")
        if os.path.exists(LOCK):
            sys.exit("未使用期間は評価済みです。結果に合わせた再評価は禁止(変更は v0.4 として別期間で)。")
    bars = load_bars_csv(args.data)
    funding = load_funding_csv(args.funding) if args.funding and os.path.exists(args.funding) else []
    a, z = parse_time(per[0]), parse_time(per[1])
    i0 = bisect.bisect_left(bars.ts, a)
    i1 = bisect.bisect_left(bars.ts, z) - 1
    if i1 <= i0:
        sys.exit("期間内のデータがありません。")
    prep = engine.prepare(bars, cfg)
    results, variants = {}, {}
    for stress in (False, True):
        for v in engine.VARIANTS:
            key = f"{v}/{'stress' if stress else 'base'}"
            res = engine.run_backtest(prep, cfg, i0, i1, funding, v, stress)
            results[key.replace("/", "_")] = res
            variants[key] = report.summarize(res, cfg["initial_capital_usdt"])
    baselines = {"buy_and_hold/base": report.buy_and_hold(bars, cfg, i0, i1),
                 "buy_and_hold/stress": report.buy_and_hold(bars, cfg, i0, i1, True),
                 "no_trade": dict(net_pnl=0.0, max_drawdown=0.0, trades=0)}
    f_ok = bool(funding) and funding[0][0] <= bars.ts[i0] and funding[-1][0] >= bars.ts[i1]
    vinfo = report.verdict(variants["main/base"], variants["main/stress"], cfg, f_ok, final)
    q = quality_report(bars)
    name = f"{args.period} {iso(a)}〜{iso(z)}"
    report.write_outputs(args.out, name, results, q, cfg, vinfo,
                         dict(variants=variants, baselines=baselines))
    if final:
        open(LOCK, "w").write(iso(int(time.time())))
    print(name, "->", vinfo[0], "| out:", args.out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="btbt")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("run", "freeze"):
        p = sub.add_parser(n)
        p.add_argument("--config", default="config/v0.3.json")
        p.add_argument("--data", default="data/bars_1h.csv")
        p.add_argument("--funding", default="data/funding.csv")
        if n == "run":
            p.add_argument("--period", choices=["dev", "mid", "test"], required=True)
            p.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        a.out = a.out or f"results/{a.period}"
        cmd_run(a)
    else:
        cmd_freeze(a)


if __name__ == "__main__":
    main()
