"""
市場行情抓取服務
使用 yfinance（免費、不需 API key）
"""
import time
import logging
from typing import List, Dict, Optional

import yfinance as yf

from config import MARKET_SYMBOLS

logger = logging.getLogger(__name__)

# ─── 台指期 / 微台指（yfinance 不直接支援）──────────────────────
# 方案：用台灣加權指數 ETF 或直接由前端手動輸入
# 之後接永豐金 Shioaji 時再換成真正的台指期報價
TAIWAN_FALLBACK = [
    {"symbol": "台指期",  "price": 0, "change": 0, "changePct": 0, "status": "unknown", "note": "需接永豐金報價"},
    {"symbol": "微台指",  "price": 0, "change": 0, "changePct": 0, "status": "unknown", "note": "需接永豐金報價"},
]


def _fetch_single(display_name: str, ticker_symbol: str) -> Optional[Dict]:
    """抓單一標的即時資料"""
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

        # VIX 反轉邏輯：VIX 下跌 = 偏多
        is_vix = "VIX" in display_name
        if is_vix:
            status = "down" if change < 0 else ("up" if change > 0 else "flat")
        else:
            status = "up" if change > 0 else ("down" if change < 0 else "flat")

        # USD/JPY 特殊處理：yfinance 回傳的是 JPY per USD
        if ticker_symbol == "JPY=X":
            # JPY=X 回傳的是 1 USD = ? JPY，需要反轉
            # 但實際上 yfinance 直接回傳的就是 USD/JPY，不需轉換
            pass

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


# ─── 快取 ─────────────────────────────────────────────────────────
_market_cache: List[Dict] = []
_market_cache_time: float = 0


def get_market_data(force_refresh: bool = False) -> List[Dict]:
    """取得所有觀察標的行情（快取 30 秒）"""
    global _market_cache, _market_cache_time

    if not force_refresh and _market_cache and (time.time() - _market_cache_time < 30):
        return _market_cache

    results = []
    for display_name, ticker in MARKET_SYMBOLS.items():
        data = _fetch_single(display_name, ticker)
        if data:
            results.append(data)

    # 加上台指期 / 微台指 placeholder
    results.extend(TAIWAN_FALLBACK)

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
