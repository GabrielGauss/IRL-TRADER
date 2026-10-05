"""Historical OHLCV beyond the 1000-bar klines limit, with a CSV cache.

Closed ranges (start and end given) are cached, since they never change.
Open-ended ranges ("until now") are always refetched.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

import pandas as pd

DEFAULT_CACHE_DIR = Path("data") / "klines"


class HistorySource(Protocol):
    def get_historical_klines(
        self, symbol: str, interval: str, start: str, end: str | None
    ) -> pd.DataFrame: ...


def load_history(
    client: HistorySource,
    symbol: str,
    interval: str,
    start: str,
    end: str | None,
    *,
    cache_dir: str | os.PathLike[str] = DEFAULT_CACHE_DIR,
) -> pd.DataFrame:
    if end is None:
        return client.get_historical_klines(symbol, interval, start, end)
    path = Path(cache_dir) / f"{symbol}_{interval}_{start}_{end}.csv"
    if path.exists():
        return pd.read_csv(path, parse_dates=["open_time"])
    df = client.get_historical_klines(symbol, interval, start, end)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)
    return df
