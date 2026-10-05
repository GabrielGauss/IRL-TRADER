from __future__ import annotations

import pandas as pd

from trading_bot.backtest.data import load_history


class FakeClient:
    def __init__(self):
        self.calls = 0

    def get_historical_klines(self, symbol: str, interval: str, start: str, end: str | None):
        self.calls += 1
        return pd.DataFrame(
            {
                "open_time": pd.date_range("2024-01-01", periods=3, freq="1h"),
                "open": [1.0, 2.0, 3.0],
                "high": [1.0, 2.0, 3.0],
                "low": [1.0, 2.0, 3.0],
                "close": [1.0, 2.0, 3.0],
                "volume": [1.0, 1.0, 1.0],
            }
        )


def test_load_history_fetches_once_then_reads_cache(tmp_path):
    client = FakeClient()

    first = load_history(client, "BTCUSDT", "1h", "2024-01-01", "2024-01-02", cache_dir=tmp_path)
    second = load_history(client, "BTCUSDT", "1h", "2024-01-01", "2024-01-02", cache_dir=tmp_path)

    assert client.calls == 1
    pd.testing.assert_frame_equal(first, second)
    assert next(iter(tmp_path.iterdir())).name == "BTCUSDT_1h_2024-01-01_2024-01-02.csv"


def test_open_ended_history_is_never_cached(tmp_path):
    client = FakeClient()

    load_history(client, "BTCUSDT", "1h", "2024-01-01", None, cache_dir=tmp_path)
    load_history(client, "BTCUSDT", "1h", "2024-01-01", None, cache_dir=tmp_path)

    assert client.calls == 2  # "until now" changes, so a cache would go stale
