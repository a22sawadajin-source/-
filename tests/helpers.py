import copy
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T0 = 1_700_000_000 - 1_700_000_000 % 14400  # 4h-aligned epoch


def cfg(**over):
    with open(os.path.join(ROOT, "config", "v0.3.json")) as f:
        c = json.load(f)
    c = copy.deepcopy(c)
    for k, v in over.items():
        sec, key = k.split("__")
        c[sec][key] = v
    return c


def mk(rows, t0=T0):
    """rows: list of (o,h,l,c) hourly bars starting at t0."""
    from btbt.data import Bars
    return Bars([t0 + i * 3600 for i in range(len(rows))], [r[0] for r in rows],
                [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows],
                [1.0] * len(rows))


def small_cfg(**over):
    """Tiny windows so hand-made bar lists can trigger signals; zero costs unless overridden."""
    base = dict(strategy__ema_period_4h=2, strategy__entry_n=3, strategy__exit_n=2,
                strategy__atr_n=3, costs__taker_fee=0.0, costs__slippage=0.0,
                initial_capital_usdt=10000.0)
    base.update(over)
    top = {k: v for k, v in base.items() if "__" not in k}
    nested = {k: v for k, v in base.items() if "__" in k}
    c = cfg(**nested)
    c.update(top)
    return c
