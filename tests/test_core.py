import json
import os
import tempfile
import unittest

from btbt import engine, fetch_mexc, report
from btbt.data import H1, Bars, build_4h, quality_report
from btbt.indicators import atr_wilder, ema_contiguous
from tests.helpers import T0, mk, small_cfg

FLAT = (100, 101, 99, 100)
BREAK = (100, 110, 100, 108)   # bar 12: closes above prior 3 highs (101)
HOLD = (108, 109, 107, 108)


def scenario(after, n_flat=12):
    return mk([FLAT] * n_flat + [BREAK] + after)


def run(bars, cfg, variant="no_filter", funding=None, stress=False):
    prep = engine.prepare(bars, cfg)
    return engine.run_backtest(prep, cfg, 0, len(bars) - 1, funding, variant, stress)


class IndicatorTests(unittest.TestCase):
    def test_ema_seed_and_update(self):
        ts = [i * 14400 for i in range(5)]
        e = ema_contiguous([1, 3, 5, 7, 9], ts, 14400, 2)
        self.assertEqual(e[0], None)
        self.assertAlmostEqual(e[1], 2.0)              # SMA seed
        self.assertAlmostEqual(e[2], 5 * (2 / 3) + 2.0 * (1 / 3))

    def test_ema_resets_after_gap(self):
        ts = [0, 14400, 28800, 57600, 72000]
        e = ema_contiguous([1, 3, 5, 7, 9], ts, 14400, 2)
        self.assertIsNotNone(e[2])
        self.assertIsNone(e[3])                        # restart after the gap
        self.assertAlmostEqual(e[4], 8.0)

    def test_atr_wilder(self):
        ts = [i * 3600 for i in range(6)]
        h = [11, 12, 13, 14, 20, 15]; l = [9, 10, 11, 12, 12, 13]; c = [10, 11, 12, 13, 18, 14]
        a = atr_wilder(h, l, c, ts, 3600, 3)
        self.assertEqual(a[:3], [None, None, None])
        self.assertAlmostEqual(a[3], 2.0)              # mean of first 3 TRs (2,2,2)
        self.assertAlmostEqual(a[4], (2.0 * 2 + 8) / 3)  # TR=max(8,7,1)=8


class DataTests(unittest.TestCase):
    def test_incomplete_4h_is_skipped(self):
        b = mk([FLAT] * 6)                             # 4 + 2 bars
        b4 = build_4h(b)
        self.assertEqual(len(b4), 1)
        self.assertEqual(b4.skipped_incomplete, 1)

    def test_quality_gap_and_invalid(self):
        b = mk([FLAT] * 5)
        del b.ts[2]; del b.o[2]; del b.h[2]; del b.l[2]; del b.c[2]; del b.v[2]
        b.h[0] = 50                                    # high below close -> invalid
        q = quality_report(b)
        self.assertEqual(q["gap_count"], 1)
        self.assertEqual(q["missing_bars_total"], 1)
        self.assertEqual(q["invalid_ohlc_count"], 1)


class EngineTests(unittest.TestCase):
    def test_no_lookahead_range_excludes_signal_bar_and_fills_next_open(self):
        r = run(scenario([HOLD] * 8), small_cfg())
        self.assertEqual(len(r["trades"]), 1)
        t = r["trades"][0]
        self.assertEqual(t["range_ref"], 101)          # signal bar's own high 110 excluded
        self.assertEqual(t["entry_ts"], T0 + 13 * H1)  # next bar, not signal bar
        self.assertEqual(t["entry_px"], 108)           # next open, not signal close-derived

    def test_unconfirmed_4h_not_used(self):
        # bars 12-15 form an in-progress 4h bar at signal time; its (partial) close would beat EMA
        r = run(scenario([HOLD] * 8), small_cfg(), variant="main")
        self.assertEqual(r["trades"], [])
        r2 = run(scenario([HOLD] * 8), small_cfg(), variant="no_filter")
        self.assertEqual(len(r2["trades"]), 1)

    def test_same_bar_entry_and_stop(self):
        r = run(scenario([(108, 109, 95, 100)] + [HOLD] * 6), small_cfg())
        t = r["trades"][0]
        self.assertEqual(t["exit_reason"], "stop")
        self.assertAlmostEqual(t["exit_px"], 108 - 2 * (2 * 2 + 10) / 3)
        self.assertEqual(r["stats"]["same_bar_entry_stop"], 1)

    def test_stop_gap_uses_worse_open(self):
        r = run(scenario([HOLD, (90, 91, 85, 88)] + [(88, 89, 87, 88)] * 5), small_cfg())
        t = r["trades"][0]
        self.assertEqual(t["exit_reason"], "stop")
        self.assertEqual(t["exit_px"], 90)

    def test_time_exit_next_open(self):
        cfg = small_cfg(strategy__max_hold_bars=3)
        r = run(scenario([HOLD] * 8), cfg)
        t = r["trades"][0]
        self.assertEqual(t["exit_reason"], "time_exit")
        self.assertEqual(t["exit_ts"], T0 + 16 * H1)   # bars 13,14,15 held -> exit at open of 16
        self.assertEqual(t["bars_held"], 3)

    def test_range_exit(self):
        r = run(scenario([HOLD, (99, 100, 99, 99)] + [(99, 100, 98.9, 99)] * 5), small_cfg())
        t = r["trades"][0]
        self.assertEqual(t["exit_reason"], "range_exit")
        self.assertEqual(t["exit_ts"], T0 + 15 * H1)

    def test_no_reentry_on_exit_bar(self):
        # range exit executes at open of bar 15; even with a fresh breakout close there, no entry
        after = [HOLD, (99, 100, 99, 99), (99, 200, 99, 199)] + [(199, 200, 198, 199)] * 4
        r = run(scenario(after), small_cfg())
        self.assertEqual(len(r["trades"]), 1)

    def test_quantity_floors_to_step(self):
        cfg = small_cfg(contract__vol_step=100000)
        t = run(scenario([HOLD] * 6), cfg)["trades"][0]
        self.assertEqual(t["qty_contracts"], 200000)   # 214285.7 floored to step

    def test_min_volume_blocks_trade(self):
        cfg = small_cfg(contract__min_vol=10**9)
        r = run(scenario([HOLD] * 6), cfg)
        self.assertEqual(r["trades"], [])
        self.assertEqual(r["stats"]["skipped_below_min"], 1)

    def test_leverage_cap(self):
        cfg = small_cfg(risk__risk_per_trade=0.9)
        t = run(scenario([HOLD] * 6), cfg)["trades"][0]
        self.assertLessEqual(t["entry_px"] * t["qty_contracts"] * 0.0001, 3 * 10000 + 1e-6)

    def test_halt_after_50pct_loss_blocks_new_entries(self):
        cfg = small_cfg(risk__risk_per_trade=0.9)
        after = [HOLD, (60, 61, 59, 60)] + [(60, 61, 59, 60)] * 8 + [(60, 100, 60, 90)] + \
                [(90, 91, 89, 90)] * 5
        r = run(scenario(after), cfg)
        self.assertIsNotNone(r["stats"]["halted_at"])
        self.assertEqual(len(r["trades"]), 1)

    def test_fees_and_slippage_reduce_pnl(self):
        base = run(scenario([HOLD] * 6), small_cfg())["trades"][0]
        cost = run(scenario([HOLD] * 6), small_cfg(costs__taker_fee=0.001,
                                                   costs__slippage=0.001))["trades"][0]
        self.assertLess(cost["net_pnl"] / max(cost["qty_contracts"], 1),
                        base["net_pnl"] / base["qty_contracts"])
        self.assertGreater(cost["entry_px"], 108)      # long buys worse than open

    def test_funding_sign_and_entry_instant_not_charged(self):
        cfg = small_cfg(strategy__max_hold_bars=4)
        f = [(T0 + 13 * H1, 0.01), (T0 + 14 * H1, 0.01)]
        t = run(scenario([HOLD] * 8), cfg, funding=f)["trades"][0]
        expected = 108 * t["qty_contracts"] * 0.0001 * 0.01   # only the 14:00 settlement, long pays
        self.assertAlmostEqual(t["funding_paid"], expected)
        self.assertLess(t["net_pnl"], t["gross_pnl"])

    def test_funding_missing_flagged_not_zero_filled(self):
        t = run(scenario([HOLD] * 6), small_cfg(), funding=[])["trades"][0]
        self.assertTrue(t["funding_data_missing"])

    def test_data_gap_closes_position(self):
        b = scenario([HOLD] * 8)
        keep = [i for i in range(len(b)) if i != 15]
        b = Bars(*[[x[i] for i in keep] for x in (b.ts, b.o, b.h, b.l, b.c, b.v)])
        r = run(b, small_cfg())
        self.assertEqual(r["trades"][0]["exit_reason"], "data_gap")
        self.assertEqual(r["stats"]["data_gap_closes"], 1)

    def test_short_side_symmetry(self):
        rows = [FLAT] * 12 + [(100, 100, 90, 92)] + [(92, 93, 91, 92)] * 6
        t = run(mk(rows), small_cfg(), variant="no_filter")["trades"][0]
        self.assertEqual(t["side"], "short")
        self.assertEqual(t["entry_ts"], T0 + 13 * H1)


class ReportTests(unittest.TestCase):
    def _m(self, pnl, dd=0.1, best=None, n=50):
        return dict(net_pnl=pnl, max_drawdown=dd, net_pnl_without_best_trade=pnl if best is None else best,
                    all=dict(trades=n))

    def test_unverified_assumptions_never_pass(self):
        cfg = small_cfg()
        v = report.verdict(self._m(10), self._m(5), cfg, True, True)
        self.assertEqual(v[0], "判定保留")
        self.assertTrue(any("実行可能性未確認" in x for x in v[1]))

    def test_verified_and_profitable_passes(self):
        cfg = small_cfg(contract__verified=True, costs__verified=True)
        self.assertEqual(report.verdict(self._m(10), self._m(5), cfg, True, True)[0], "合格")

    def test_losing_stress_fails_and_dev_has_no_verdict(self):
        cfg = small_cfg(contract__verified=True, costs__verified=True)
        self.assertEqual(report.verdict(self._m(10), self._m(-1), cfg, True, True)[0], "不合格")
        self.assertIn("参考値", report.verdict(self._m(10), self._m(5), cfg, True, False)[0])

    def test_max_drawdown(self):
        self.assertAlmostEqual(report.max_drawdown([(0, 100), (1, 150), (2, 75), (3, 200)]), 0.5)


class FetchParseTests(unittest.TestCase):
    """Payload shapes are ASSUMED from docs recalled offline; verify against real raw responses."""
    def test_kline_and_funding_parse(self):
        k = {"success": True, "data": {"time": [1, 2], "open": [1, 2], "high": [2, 3],
                                       "low": [0, 1], "close": [1, 2], "vol": [5, 6]}}
        self.assertEqual(fetch_mexc.parse_kline(k)[1], (2, 2.0, 3.0, 1.0, 2.0, 6.0))
        f = {"success": True, "data": {"resultList": [{"settleTime": 1700000000000,
                                                       "fundingRate": 0.0001}]}}
        self.assertEqual(fetch_mexc.parse_funding(f), [(1700000000, 0.0001)])


if __name__ == "__main__":
    unittest.main()
