"""Event-driven hourly backtest of spec v0.3. Decisions use only confirmed bars; fills at next open.

Conservative conventions (see docs in REPORT / config notes):
- signal on bar i close -> fill at open of bar i+1 with adverse slippage
- stop is checked against the bar's low/high from the entry bar onward; gap -> worse of stop/open
- stop bar, exit bar: no new entry decision on that bar
- funding at time t is charged if position was opened strictly before t and is still held at t
"""
import math
from dataclasses import dataclass, field

from .data import H1, H4, build_4h
from .indicators import atr_wilder, ema_contiguous

VARIANTS = {
    "main": dict(use_filter=True, allow_long=True, allow_short=True),
    "no_filter": dict(use_filter=False, allow_long=True, allow_short=True),
    "long_only": dict(use_filter=True, allow_long=True, allow_short=False),
    "short_only": dict(use_filter=True, allow_long=False, allow_short=True),
}


@dataclass
class Prepared:
    bars: object
    atr: list
    ema4: dict
    close4: dict


def prepare(bars, cfg):
    s = cfg["strategy"]
    b4 = build_4h(bars)
    ema = ema_contiguous(b4.c, b4.ts, H4, s["ema_period_4h"])
    return Prepared(
        bars,
        atr_wilder(bars.h, bars.l, bars.c, bars.ts, H1, s["atr_n"]),
        {t: e for t, e in zip(b4.ts, ema) if e is not None},
        {t: c for t, c in zip(b4.ts, b4.c)},
    )


def _floor_step(x, step):
    return math.floor(x / step + 1e-9) * step


def run_backtest(prep, cfg, i0, i1, funding=None, variant="main", stress=False):
    b = prep.bars
    s, r, ct, co = cfg["strategy"], cfg["risk"], cfg["contract"], cfg["costs"]
    fee, slip = co["taker_fee"], co["slippage"]
    if stress:
        fee *= cfg["cost_stress"]["fee_mult"]
        slip *= cfg["cost_stress"]["slippage_mult"]
    v = VARIANTS[variant]
    cs = ct["contract_size_btc"]
    init = cfg["initial_capital_usdt"]
    funding = funding or []
    f_first = funding[0][0] if funding else None
    f_last = funding[-1][0] if funding else None
    fund_by_hour = {}
    for t, rate in funding:
        fund_by_hour.setdefault(t - t % H1, []).append((t, rate))

    cash = init
    pos = None
    pending = None
    halted = False
    trades, equity = [], []
    st = dict(skipped_below_min=0, skipped_liq=0, skipped_no_qty=0, same_bar_entry_stop=0,
              data_gap_closes=0, halted_at=None)

    def close(i_price_ts, raw_px, reason, ts_exit):
        nonlocal cash, pos
        side = pos["side"]
        px = raw_px * (1 - side * slip)  # adverse slippage: sells lower, buys back higher
        notional = px * pos["qty"] * cs
        efee = notional * fee
        gross = side * (px - pos["entry_px"]) * pos["qty"] * cs
        cash += gross - efee
        net = gross - efee - pos["entry_fee"] - pos["funding"]
        fm = bool(not funding or pos["entry_ts"] < f_first or ts_exit > f_last)
        trades.append(dict(
            side="long" if side == 1 else "short", signal_ts=pos["signal_ts"],
            entry_ts=pos["entry_ts"], exit_ts=ts_exit, h4_ts=pos["h4_ts"], ema200_4h=pos["ema"],
            h4_close=pos["h4c"], range_ref=pos["range_ref"], atr20=pos["atr"],
            planned_entry=pos["planned"], entry_px=pos["entry_px"], stop_px=pos["stop"],
            exit_px=px, qty_contracts=pos["qty"], entry_fee=pos["entry_fee"], exit_fee=efee,
            funding_paid=pos["funding"], funding_data_missing=fm, exit_reason=reason,
            gross_pnl=gross, net_pnl=net, bars_held=(ts_exit - pos["entry_ts"]) // H1))
        pos = None

    for i in range(i0, i1 + 1):
        t = b.ts[i]
        exit_bar = False
        if i > i0 and t - b.ts[i - 1] != H1:  # gap: never trade across missing data
            pending = None
            if pos:
                st["data_gap_closes"] += 1
                close(t, b.c[i - 1], "data_gap", b.ts[i - 1] + H1)
        # funding settlements falling inside this hour
        if pos:
            for ft, rate in fund_by_hour.get(t, []):
                if pos["entry_ts"] < ft:
                    amt = pos["side"] * b.o[i] * pos["qty"] * cs * rate  # long pays if rate>0
                    cash -= amt
                    pos["funding"] += amt
        # execute pending decision at this open
        if pending and pending[0] == "exit" and pos:
            close(t, b.o[i], pending[1], t)
            exit_bar = True
        elif pending and pending[0] == "enter" and not pos and not halted:
            _, side, sig = pending
            fill = b.o[i] * (1 + side * slip)
            stop_dist = s["atr_mult"] * sig["atr"]
            stop = fill - side * stop_dist
            if stop_dist <= 0 or stop <= 0:
                st["skipped_no_qty"] += 1
            else:
                per_btc = stop_dist + (fill + stop) * fee + stop * slip
                q_risk = r["risk_per_trade"] * cash / per_btc
                q_lev = r["max_effective_leverage"] * cash / fill
                qty = _floor_step(min(q_risk, q_lev) / cs, ct["vol_step"])
                liq_frac = 1.0 / r["isolated_leverage_setting"] - r["maintenance_margin_rate"]
                if qty < ct["min_vol"]:
                    st["skipped_below_min"] += 1
                elif stop_dist / fill > r["stop_within_liq_fraction"] * liq_frac:
                    st["skipped_liq"] += 1
                else:
                    efee = fill * qty * cs * fee
                    cash -= efee
                    pos = dict(side=side, qty=qty, entry_px=fill, stop=stop, entry_i=i,
                               entry_ts=t, entry_fee=efee, funding=0.0, signal_ts=sig["ts"],
                               h4_ts=sig["h4_ts"], ema=sig["ema"], h4c=sig["h4c"],
                               range_ref=sig["range_ref"], atr=sig["atr"], planned=b.o[i])
        pending = None
        # stop within the bar (conservative: from entry bar onward; gap -> worse price)
        if pos:
            side = pos["side"]
            hit = b.l[i] <= pos["stop"] if side == 1 else b.h[i] >= pos["stop"]
            if hit:
                raw = min(pos["stop"], b.o[i]) if side == 1 else max(pos["stop"], b.o[i])
                if pos["entry_i"] == i:
                    st["same_bar_entry_stop"] += 1
                close(t, raw, "stop", t + H1)
                exit_bar = True
        eq = cash + (pos["side"] * (b.c[i] - pos["entry_px"]) * pos["qty"] * cs if pos else 0.0)
        equity.append((t, eq))
        if not halted and eq <= init * (1 - r["halt_drawdown"]):
            halted = True
            st["halted_at"] = t
        if i == i1:
            if pos:
                close(t, b.c[i], "end_of_period", t + H1)
                equity[-1] = (t, cash)
            break
        if exit_bar:
            continue
        # ---- decisions on the close of confirmed bar i ----
        if i - s["exit_n"] >= 0 and t - b.ts[i - s["exit_n"]] == s["exit_n"] * H1 and pos:
            lo = min(b.l[i - s["exit_n"]:i])
            hi = max(b.h[i - s["exit_n"]:i])
            if pos["side"] == 1 and b.c[i] < lo or pos["side"] == -1 and b.c[i] > hi:
                pending = ("exit", "range_exit")
        if pos and not pending and (i - pos["entry_i"] + 1) >= s["max_hold_bars"]:
            pending = ("exit", "time_exit")
        if pos or halted:
            continue
        n = s["entry_n"]
        atr = prep.atr[i]
        if atr is None or atr <= 0 or i < n or t - b.ts[i - n] != n * H1:
            continue
        h4 = ((t + H1 - H4) // H4) * H4  # latest 4h bar fully closed by the end of bar i
        ema = prep.ema4.get(h4)
        h4c = prep.close4.get(h4)
        if v["use_filter"] and ema is None:
            continue
        hi = max(b.h[i - n:i])
        lo = min(b.l[i - n:i])
        side = 0
        if v["allow_long"] and b.c[i] > hi and (not v["use_filter"] or h4c > ema):
            side, ref = 1, hi
        elif v["allow_short"] and b.c[i] < lo and (not v["use_filter"] or h4c < ema):
            side, ref = -1, lo
        if side:
            pending = ("enter", side, dict(ts=t + H1, atr=atr, h4_ts=h4, ema=ema, h4c=h4c,
                                           range_ref=ref))
    return dict(trades=trades, equity=equity, stats=st)
