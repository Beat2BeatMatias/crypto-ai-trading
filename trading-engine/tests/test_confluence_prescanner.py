"""Tests for ConfluencePrescanner — deterministic pre-LLM confluence detection."""
import pytest
from agents.confluence_prescanner import ConfluencePrescanner


def _make_ctx(**overrides) -> dict:
    """Build a minimal context dict with sensible defaults for prescanner tests."""
    ctx = {
        "price": 80000.0,
        "confluence_primary_tf": "15m",
        "confluence_secondary_tf": "1h",
        "imbalance": 0.0,
        "bid_wall_dist_pct": 0.0,
        "ask_wall_dist_pct": 0.0,
        "volume_ratio": 1.0,
        "high_24h": 81000.0,
        "low_24h": 79000.0,
        "resistance_1h": 80500.0,
        "at_range_low": 0.0,
        "at_range_high": 0.0,
        "block_f_cross_tf": {
            "tf_assessments": {"1h": "n/d", "4h": "n/d"},
            "alignment": "neutral",
        },
        "block_c_tf_blocks": {
            "1m": {
                "rsi": 50.0, "macd": 0.0, "macd_signal": 0.0, "macd_hist": 0.0,
                "ema20": 80000.0, "ema50": 80000.0, "ema200": 80000.0, "adx": 20.0,
                "stoch_k": 50.0, "stoch_d": 50.0, "vwap": 80000.0,
                "bb_pct": 50.0, "atr": 500.0,
                "volume_current": 100.0, "volume_avg_20": 100.0,
                "obv_slope": 0.0, "structure": "range",
                "wick_upper_c2": 0.1, "wick_lower_c2": 0.1, "body_ratio_c2": 0.8,
            },
            "5m": {
                "rsi": 50.0, "macd": 0.0, "macd_signal": 0.0, "macd_hist": 0.0,
                "ema20": 80000.0, "ema50": 80000.0, "ema200": 80000.0, "adx": 20.0,
                "stoch_k": 50.0, "stoch_d": 50.0, "vwap": 80000.0,
                "bb_pct": 50.0, "atr": 500.0,
                "volume_current": 100.0, "volume_avg_20": 100.0,
                "obv_slope": 0.0, "structure": "range",
                "wick_upper_c2": 0.1, "wick_lower_c2": 0.1, "body_ratio_c2": 0.8,
            },
            "15m": {
                "rsi": 50.0, "macd": 0.0, "macd_signal": 0.0, "macd_hist": 0.0,
                "ema20": 80000.0, "ema50": 80000.0, "ema200": 80000.0, "adx": 20.0,
                "stoch_k": 50.0, "stoch_d": 50.0, "vwap": 80000.0,
                "bb_pct": 50.0, "atr": 500.0,
                "volume_current": 100.0, "volume_avg_20": 100.0,
                "obv_slope": 0.0, "structure": "range",
                "wick_upper_c2": 0.1, "wick_lower_c2": 0.1, "body_ratio_c2": 0.8,
            },
            "1h": {
                "rsi": 50.0, "macd": 0.0, "macd_signal": 0.0, "macd_hist": 0.0,
                "ema20": 80000.0, "ema50": 80000.0, "ema200": 80000.0, "adx": 20.0,
                "stoch_k": 50.0, "stoch_d": 50.0, "vwap": 80000.0,
                "bb_pct": 50.0, "atr": 500.0,
                "volume_current": 100.0, "volume_avg_20": 100.0,
                "obv_slope": 0.0, "structure": "range",
                "wick_upper_c2": 0.1, "wick_lower_c2": 0.1, "body_ratio_c2": 0.8,
            },
            "4h": {
                "rsi": 50.0, "macd": 0.0, "macd_signal": 0.0, "macd_hist": 0.0,
                "ema20": 80000.0, "ema50": 80000.0, "ema200": 80000.0, "adx": 20.0,
                "stoch_k": 50.0, "stoch_d": 50.0, "vwap": 80000.0,
                "bb_pct": 50.0, "atr": 500.0,
                "volume_current": 100.0, "volume_avg_20": 100.0,
                "obv_slope": 0.0, "structure": "range",
                "wick_upper_c2": 0.1, "wick_lower_c2": 0.1, "body_ratio_c2": 0.8,
            },
        },
    }
    ctx.update(overrides)
    return ctx


@pytest.fixture
def prescanner():
    return ConfluencePrescanner()


class TestScannerSmoke:
    def test_scan_returns_all_16_codes(self, prescanner):
        ctx = _make_ctx()
        results = prescanner.scan(ctx)
        assert set(results.keys()) == set("ABCDEFGHIJKLMNOP")
        for code in results:
            assert "status" in results[code]
            assert "detail" in results[code]

    def test_render_contains_all_codes(self, prescanner):
        ctx = _make_ctx()
        results = prescanner.scan(ctx)
        rendered = prescanner.render(results)
        for code in "ABCDEFGHIJKLMNOP":
            assert code in rendered


class TestConfluenceA:
    def test_detected_oversold_with_bullish_wick(self, prescanner):
        ctx = _make_ctx(**{
            "block_c_tf_blocks": {
                "15m": {"rsi": 30.0, "wick_lower_c2": 0.5, "wick_upper_c2": 0.1, "body_ratio_c2": 0.4},
                "1h": {"rsi": 45.0, "wick_lower_c2": 0.1, "wick_upper_c2": 0.1, "body_ratio_c2": 0.8},
            }
        })
        r = prescanner._a(ctx, "15m", "1h")
        assert r["status"] == "DETECTADO"

    def test_marginal_when_rsi_low_no_wick(self, prescanner):
        ctx = _make_ctx(**{
            "block_c_tf_blocks": {
                "15m": {"rsi": 30.0, "wick_lower_c2": 0.1, "wick_upper_c2": 0.1, "body_ratio_c2": 0.8},
            }
        })
        r = prescanner._a(ctx, "15m", "1h")
        assert r["status"] == "MARGINAL"

    def test_ausente_when_rsi_above_35(self, prescanner):
        ctx = _make_ctx(**{
            "block_c_tf_blocks": {
                "15m": {"rsi": 50.0, "wick_lower_c2": 0.5, "wick_upper_c2": 0.1, "body_ratio_c2": 0.4},
            }
        })
        r = prescanner._a(ctx, "15m", "1h")
        assert r["status"] == "AUSENTE"


class TestConfluenceB:
    def test_detected_bullish_cross_with_positive_hist(self, prescanner):
        ctx = _make_ctx(**{
            "block_c_tf_blocks": {
                "15m": {"macd": 10.0, "macd_signal": 5.0, "macd_hist": 3.0},
            }
        })
        r = prescanner._b(ctx, "15m", "1h")
        assert r["status"] == "DETECTADO"

    def test_marginal_cross_no_hist(self, prescanner):
        ctx = _make_ctx(**{
            "block_c_tf_blocks": {
                "15m": {"macd": 10.0, "macd_signal": 5.0, "macd_hist": 0.0},
            }
        })
        r = prescanner._b(ctx, "15m", "1h")
        assert r["status"] == "MARGINAL"

    def test_ausente_no_cross(self, prescanner):
        ctx = _make_ctx(**{
            "block_c_tf_blocks": {
                "15m": {"macd": 3.0, "macd_signal": 10.0, "macd_hist": -2.0},
            }
        })
        r = prescanner._b(ctx, "15m", "1h")
        assert r["status"] == "AUSENTE"


class TestConfluenceC:
    def test_detected_near_ema50_with_wick(self, prescanner):
        ctx = _make_ctx(**{
            "price": 80500.0,
            "block_c_tf_blocks": {
                "1h": {"ema50": 80800.0, "wick_lower_c2": 0.5, "wick_upper_c2": 0.1, "body_ratio_c2": 0.4},
                "15m": {},
            }
        })
        r = prescanner._c(ctx, "15m", "1h", 80500.0)
        assert r["status"] == "DETECTADO"

    def test_ausente_when_far_from_ema50(self, prescanner):
        ctx = _make_ctx(**{
            "price": 80000.0,
            "block_c_tf_blocks": {
                "1h": {"ema50": 82000.0},
            }
        })
        r = prescanner._c(ctx, "15m", "1h", 80000.0)
        assert r["status"] == "AUSENTE"


class TestConfluenceD:
    def test_detected_low_bb_with_wick(self, prescanner):
        ctx = _make_ctx(**{
            "block_c_tf_blocks": {
                "5m": {"bb_pct": 3.0, "wick_lower_c2": 0.5, "wick_upper_c2": 0.1, "body_ratio_c2": 0.4},
            }
        })
        r = prescanner._d(ctx, "15m", "1h")
        assert r["status"] == "DETECTADO"

    def test_marginal_low_bb_no_wick(self, prescanner):
        ctx = _make_ctx(**{
            "block_c_tf_blocks": {
                "5m": {"bb_pct": 3.0, "wick_lower_c2": 0.1, "wick_upper_c2": 0.1, "body_ratio_c2": 0.8},
            }
        })
        r = prescanner._d(ctx, "15m", "1h")
        assert r["status"] == "MARGINAL"


class TestConfluenceE:
    def test_detected_high_imbalance_bid_wall(self, prescanner):
        ctx = _make_ctx(**{"imbalance": 2.0, "bid_wall_dist_pct": 0.1})
        r = prescanner._e(ctx)
        assert r["status"] == "DETECTADO"

    def test_ausente_low_imbalance(self, prescanner):
        ctx = _make_ctx(**{"imbalance": 1.0, "bid_wall_dist_pct": 0.1})
        r = prescanner._e(ctx)
        assert r["status"] == "AUSENTE"


class TestConfluenceF:
    def test_detected_breakout_with_volume(self, prescanner):
        ctx = _make_ctx(**{
            "price": 81500.0, "high_24h": 81000.0, "volume_ratio": 2.0,
            "block_c_tf_blocks": {
                "15m": {"obv_slope": 0.5, "rsi": 60.0, "macd": 0, "macd_signal": 0, "macd_hist": 0},
                "1h": {},
            }
        })
        r = prescanner._f(ctx, "15m", 81500.0)
        assert r["status"] == "DETECTADO"

    def test_marginal_no_obv(self, prescanner):
        ctx = _make_ctx(**{
            "price": 81500.0, "high_24h": 81000.0, "volume_ratio": 2.0,
            "block_c_tf_blocks": {
                "15m": {"obv_slope": 0.0, "rsi": 60.0, "macd": 0, "macd_signal": 0, "macd_hist": 0},
                "1h": {},
            }
        })
        r = prescanner._f(ctx, "15m", 81500.0)
        assert r["status"] == "MARGINAL"


class TestConfluenceG:
    def test_detected_alignment(self, prescanner):
        ctx = _make_ctx(**{
            "block_f_cross_tf": {"tf_assessments": {"1h": "uptrend"}},
            "block_c_tf_blocks": {
                "4h": {"rsi": 55.0, "ema20": 80000.0, "ema50": 79000.0},
            }
        })
        r = prescanner._g(ctx)
        assert r["status"] == "DETECTADO"

    def test_ausente_no_uptrend(self, prescanner):
        ctx = _make_ctx(**{
            "block_f_cross_tf": {"tf_assessments": {"1h": "downtrend"}},
        })
        r = prescanner._g(ctx)
        assert r["status"] == "AUSENTE"


class TestConfluenceH:
    def test_detected_at_range_low(self, prescanner):
        ctx = _make_ctx(**{"at_range_low": 1.0})
        r = prescanner._h(ctx, "15m", "1h")
        assert r["status"] == "DETECTADO"

    def test_marginal_stoch_low_only(self, prescanner):
        ctx = _make_ctx(**{
            "block_c_tf_blocks": {
                "15m": {"stoch_k": 15.0, "bb_pct": 50.0},
                "1h": {},
            }
        })
        r = prescanner._h(ctx, "15m", "1h")
        assert r["status"] == "MARGINAL"


class TestConfluenceI:
    def test_detected_overbought_with_bearish_wick(self, prescanner):
        ctx = _make_ctx(**{
            "block_c_tf_blocks": {
                "15m": {"rsi": 70.0, "wick_upper_c2": 0.5, "wick_lower_c2": 0.1, "body_ratio_c2": 0.4},
                "1h": {"rsi": 55.0, "wick_upper_c2": 0.1, "wick_lower_c2": 0.1, "body_ratio_c2": 0.8},
            }
        })
        r = prescanner._i(ctx, "15m", "1h")
        assert r["status"] == "DETECTADO"

    def test_ausente_rsi_not_high(self, prescanner):
        ctx = _make_ctx(**{
            "block_c_tf_blocks": {
                "15m": {"rsi": 40.0},
            }
        })
        r = prescanner._i(ctx, "15m", "1h")
        assert r["status"] == "AUSENTE"


class TestConfluenceJ:
    def test_detected_bearish_cross_with_negative_hist(self, prescanner):
        ctx = _make_ctx(**{
            "block_c_tf_blocks": {
                "15m": {"macd": -5.0, "macd_signal": 3.0, "macd_hist": -4.0},
            }
        })
        r = prescanner._j(ctx, "15m", "1h")
        assert r["status"] == "DETECTADO"


class TestConfluenceK:
    def test_detected_near_ema200_with_bearish_wick(self, prescanner):
        ctx = _make_ctx(**{
            "price": 79500.0,
            "block_c_tf_blocks": {
                "1h": {"ema200": 79800.0, "wick_upper_c2": 0.5, "wick_lower_c2": 0.1, "body_ratio_c2": 0.4},
                "15m": {},
            }
        })
        r = prescanner._k(ctx, "15m", "1h", 79500.0)
        assert r["status"] == "DETECTADO"


class TestConfluenceL:
    def test_detected_high_bb_with_wick(self, prescanner):
        ctx = _make_ctx(**{
            "block_c_tf_blocks": {
                "5m": {"bb_pct": 97.0, "wick_upper_c2": 0.5, "wick_lower_c2": 0.1, "body_ratio_c2": 0.4},
            }
        })
        r = prescanner._l(ctx)
        assert r["status"] == "DETECTADO"


class TestConfluenceM:
    def test_detected_low_imbalance_ask_wall(self, prescanner):
        ctx = _make_ctx(**{"imbalance": 0.5, "ask_wall_dist_pct": 0.1})
        r = prescanner._m(ctx)
        assert r["status"] == "DETECTADO"


class TestConfluenceN:
    def test_detected_breakdown_with_volume(self, prescanner):
        ctx = _make_ctx(**{
            "price": 78500.0, "low_24h": 79000.0, "volume_ratio": 2.0,
            "block_c_tf_blocks": {
                "15m": {"obv_slope": -0.5, "rsi": 40.0, "macd": 0, "macd_signal": 0, "macd_hist": 0},
                "1h": {},
            }
        })
        r = prescanner._n(ctx, "15m", 78500.0)
        assert r["status"] == "DETECTADO"


class TestConfluenceO:
    def test_detected_bearish_alignment(self, prescanner):
        ctx = _make_ctx(**{
            "block_f_cross_tf": {"tf_assessments": {"1h": "downtrend"}},
            "block_c_tf_blocks": {
                "4h": {"rsi": 45.0, "ema20": 79000.0, "ema50": 80000.0},
            }
        })
        r = prescanner._o(ctx)
        assert r["status"] == "DETECTADO"


class TestConfluenceP:
    def test_detected_at_range_high(self, prescanner):
        ctx = _make_ctx(**{"at_range_high": 1.0})
        r = prescanner._p(ctx, "15m", "1h")
        assert r["status"] == "DETECTADO"


class TestScanEdgeCases:
    def test_missing_tf_blocks_does_not_crash(self, prescanner):
        ctx = _make_ctx(**{"block_c_tf_blocks": {}})
        results = prescanner.scan(ctx)
        assert len(results) == 16  # all codes returned with AUSENTE

    def test_missing_price_does_not_crash(self, prescanner):
        ctx = _make_ctx(**{"price": 0.0})
        results = prescanner.scan(ctx)
        assert len(results) == 16

    def test_all_ausente_with_defaults(self, prescanner):
        ctx = _make_ctx()
        results = prescanner.scan(ctx)
        assert all(r["status"] == "AUSENTE" for r in results.values())

    def test_partial_tf_data_does_not_crash(self, prescanner):
        ctx = _make_ctx(**{
            "block_c_tf_blocks": {
                "15m": {"rsi": 30.0},
                "1h": {"macd": 10.0, "macd_signal": 5.0},
            }
        })
        results = prescanner.scan(ctx)
        assert len(results) == 16
