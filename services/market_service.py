"""
市場行情抓取服務
- 國際行情：yfinance（免費）
- 台指期/微台指：永豐金 Shioaji（有連線時）或 fallback
"""
import time
import logging
from typing import List, Dict, Optional

import yfinance as yf

from config import MARKET_SYMBOLS

logger = logging.getLogger(__name__)

# ─── 台指期 / 微台指 fallback（永豐金沒連線時用）─────────────────
TAIWAN_FALLBACK = [
    {"symbol": "台指期", "price": 0, "change": 0, "changePct": 0, "status": "unknown", "note": "永豐金未連線"},
    {"symbol": "微台指", "price": 0, "change": 0, "changePct": 0, "status": "unknown", "note": "永豐金未連線"},
]


def _fetch_single(display_name: str, ticker_symbol: str) -> Optional[Dict]:
    """抓單一標的即時資料（yfinance）"""
    try:
        tk = yf.Ticker(ticker_symbol)
        info = tk.fast_info

        price = getattr(info, "last_price", None) or 0
        prev_close = getattr(info, "previous_close", None) or getattr(info, "regular_market_previous_close", None) or 0

        if price and prev_close:
            change = round(price - prev_close, 2)
            change_pct = round((change / prev_close) * 100, 2) if prev_close else 0
        else:
            change = 0
            change_pct = 0

        is_vix = "VIX" in display_name
        if is_vix:
            status = "down" if change < 0 else ("up" if change > 0 else "flat")
        else:
            status = "up" if change > 0 else ("down" if change < 0 else "flat")

        return {
            "symbol": display_name,
            "price": round(price, 2) if price else 0,
            "change": change,
            "changePct": change_pct,
            "status": status,
        }

    except Exception as e:
        logger.warning(f"Failed to fetch {display_name} ({ticker_symbol}): {e}")
        return {
            "symbol": display_name,
            "price": 0,
            "change": 0,
            "changePct": 0,
            "status": "error",
        }


def _get_taiwan_futures() -> List[Dict]:
    """
    取得台指期/微台指報價
    有永豐金連線 → 回傳即時報價
    沒有 → 回傳 fallback
    """
    try:
        from services.broker_service import is_connected, get_broker_quotes

        if is_connected():
            quotes = get_broker_quotes()
            if quotes and any(q.get("price", 0) > 0 for q in quotes):
                return quotes
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Broker quotes failed: {e}")

    return TAIWAN_FALLBACK


# ─── 快取 ─────────────────────────────────────────────────────────
_market_cache: List[Dict] = []
_market_cache_time: float = 0


def get_market_data(force_refresh: bool = False) -> List[Dict]:
    """取得所有觀察標的行情（快取 30 秒）"""
    global _market_cache, _market_cache_time

    if not force_refresh and _market_cache and (time.time() - _market_cache_time < 10):
        return _market_cache

    results = []

    # 國際行情（yfinance）
    for display_name, ticker in MARKET_SYMBOLS.items():
        data = _fetch_single(display_name, ticker)
        if data:
            results.append(data)

    # 台指期 / 微台指（永豐金或 fallback）
    results.extend(_get_taiwan_futures())

    _market_cache = results
    _market_cache_time = time.time()

    logger.info(f"Market data refreshed: {len(results)} symbols")
    return results


def get_symbol_price(display_name: str) -> float:
    """取得單一標的價格（從快取）"""
    data = get_market_data()
    for item in data:
        if item["symbol"] == display_name:
            return item["price"]
    return 0
