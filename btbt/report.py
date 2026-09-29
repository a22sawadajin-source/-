"""Metrics, comparison baselines, verdict and output files."""
import csv
import json
import os

from .data import H1, iso


def max_drawdown(eq):
    peak, mdd = None, 0.0
    for _, e in eq:
        peak = e if peak is None else max(peak, e)
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak)
    return mdd


def side_stats(trades):
    n = len(trades)
    pnl = sum(t["net_pnl"] for t in trades)
    wins = [t["net_pnl"] for t in trades if t["net_pnl"] > 0]
    losses = [-t["net_pnl"] for t in trades if t["net_pnl"] <= 0]
    return dict(trades=n, net_pnl=pnl, win_rate=(len(wins) / n if n else None),
                profit_factor=(sum(wins) / sum(losses) if losses and sum(losses) > 0 else None))


def summarize(res, init):
    tr = res["trades"]
    net = sum(t["net_pnl"] for t in tr)
    best = max((t["net_pnl"] for t in tr), default=0.0)
    return dict(
        net_pnl=net, return_pct=100 * net / init, max_drawdown=max_drawdown(res["equity"]),
        final_equity=res["equity"][-1][1] if res["equity"] else init,
        net_pnl_without_best_trade=net - best if tr else 0.0,
        all=side_stats(tr),
        long=side_stats([t for t in tr if t["side"] == "long"]),
        short=side_stats([t for t in tr if t["side"] == "short"]),
        exit_reasons={k: sum(1 for t in tr if t["exit_reason"] == k)
                      for k in sorted({t["exit_reason"] for t in tr})},
        funding_missing_trades=sum(1 for t in tr if t["funding_data_missing"]),
        engine=res["stats"])


def buy_and_hold(bars, cfg, i0, i1, stress=False):
    """Unleveraged BTC hold with entry/exit fee+slippage. Reference only (different risk profile)."""
    co = cfg["costs"]
    fee, slip = co["taker_fee"], co["slippage"]
    if stress:
        fee *= cfg["cost_stress"]["fee_mult"]
        slip *= cfg["cost_stress"]["slippage_mult"]
    init = cfg["initial_capital_usdt"]
    px = bars.o[i0] * (1 + slip)
    units = init * (1 - fee) / px
    eq = [(bars.ts[i], units * bars.c[i]) for i in range(i0, i1 + 1)]
    final = units * bars.c[i1] * (1 - slip) * (1 - fee)
    eq[-1] = (eq[-1][0], final)
    return dict(net_pnl=final - init, return_pct=100 * (final - init) / init,
                max_drawdown=max_drawdown(eq), trades=1)


def verdict(main_base, main_stress, cfg, funding_ok, is_final_test):
    reasons = []
    pc = cfg["pass_criteria"]
    if not is_final_test:
        return "参考値(未使用期間ではないため合否判定なし)", ["dev/mid期間の結果は合否に使わない"]
    hold = False
    if not cfg["contract"]["verified"]:
        hold = True
        reasons.append("実行可能性未確認: 契約仕様(最小数量・刻み・契約サイズ)が未検証の仮定")
    if not cfg["costs"]["verified"]:
        hold = True
        reasons.append("手数料・スリッページが未検証の仮定")
    if not funding_ok:
        hold = True
        reasons.append("ファンディング履歴が不足または未提供")
    n = main_base["all"]["trades"]
    if n < pc["min_trades"]:
        hold = True
        reasons.append(f"取引数{n}が最低{pc['min_trades']}件未満")
    fail = []
    for name, m in (("基本", main_base), ("コスト悪化", main_stress)):
        if m["net_pnl"] <= 0:
            fail.append(f"{name}条件の純損益がプラスでない")
        if m["max_drawdown"] >= pc["max_drawdown"]:
            fail.append(f"{name}条件の最大DDが{pc['max_drawdown']:.0%}以上")
    if main_base["net_pnl"] > 0 and main_base["net_pnl_without_best_trade"] <= 0:
        hold = True
        reasons.append("頑健性未確認: 最大利益1取引を除くと利益が消える")
    if fail:
        return "不合格", fail + reasons
    return ("判定保留" if hold else "合格"), reasons


def write_outputs(outdir, name, results, quality, cfg, verdict_info, comparisons):
    os.makedirs(outdir, exist_ok=True)
    for key, res in results.items():
        with open(os.path.join(outdir, f"trades_{key}.csv"), "w", newline="") as f:
            rows = res["trades"]
            cols = list(rows[0].keys()) if rows else ["no_trades"]
            w = csv.writer(f)
            w.writerow(cols)
            for t in rows:
                w.writerow([iso(t[c]) if c.endswith("_ts") and isinstance(t[c], int) else t[c]
                            for c in cols])
        with open(os.path.join(outdir, f"equity_hourly_{key}.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time_utc", "equity_usdt"])
            for t, e in res["equity"]:
                w.writerow([iso(t), f"{e:.6f}"])
    with open(os.path.join(outdir, "data_quality.json"), "w") as f:
        json.dump(quality, f, ensure_ascii=False, indent=2)
    with open(os.path.join(outdir, "summary.json"), "w") as f:
        json.dump(dict(name=name, verdict=verdict_info, comparisons=comparisons), f,
                  ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(outdir, "REPORT.md"), "w") as f:
        f.write(render_md(name, cfg, verdict_info, comparisons, quality))


def render_md(name, cfg, verdict_info, comp, quality):
    L = [f"# バックテスト結果: {name}", "",
         f"**判定: {verdict_info[0]}**", ""]
    L += [f"- {r}" for r in verdict_info[1]]
    L += ["", "## 前提(仮定を含む)",
          f"- 初期資金 {cfg['initial_capital_usdt']} USDT / 手数料 {cfg['costs']['taker_fee']} / "
          f"スリッページ {cfg['costs']['slippage']} (片道) / verified={cfg['costs']['verified']}",
          f"- 契約仕様 verified={cfg['contract']['verified']}",
          f"- データ: {quality['first']} 〜 {quality['last']} / {quality['bars']}本 / "
          f"欠損区間 {quality['gap_count']} / 欠損本数 {quality['missing_bars_total']}",
          "", "## 成績", "",
          "| 条件 | 純損益 | リターン% | 最大DD | 取引数 | ロング | ショート | ファンディング欠損取引 |",
          "|---|---|---|---|---|---|---|---|"]
    for k, m in comp["variants"].items():
        L.append(f"| {k} | {m['net_pnl']:.2f} | {m['return_pct']:.1f} | {m['max_drawdown']:.1%} | "
                 f"{m['all']['trades']} | {m['long']['trades']}件/{m['long']['net_pnl']:.2f} | "
                 f"{m['short']['trades']}件/{m['short']['net_pnl']:.2f} | "
                 f"{m['funding_missing_trades']} |")
    L += ["", "## 比較対象(参考)", ""]
    for k, m in comp["baselines"].items():
        L.append(f"- {k}: 純損益 {m['net_pnl']:.2f} / 最大DD {m['max_drawdown']:.1%}")
    L += ["", "## エンジン内訳(main/base)", "", "```",
          json.dumps(comp["variants"]["main/base"]["engine"], ensure_ascii=False, indent=1),
          json.dumps(comp["variants"]["main/base"]["exit_reasons"], ensure_ascii=False),
          "```", "",
          "※同一足内の順序不明ケースは保守側(損切りを先に判定)で処理し、"
          "`same_bar_entry_stop` に件数を記録。バックテストは実際の約定・清算を完全には再現しない。"]
    return "\n".join(L) + "\n"
