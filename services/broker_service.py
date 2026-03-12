"""
永豐金 Shioaji 券商服務

整合進主後端，不需要另外跑一支程式。

功能：
1. 自動登入永豐金
2. 訂閱台指期 (TXF) + 微台指 (MXF) 即時行情
3. 計算漲跌 / 漲跌幅
4. 提供 dashboard API 使用

運作模式：
有憑證 → 自動登入 + 訂閱即時行情
沒憑證 → 靜默跳過（系統仍可正常運作）

環境變數（.env）：
SINOPAC_API_KEY=你的API KEY
SINOPAC_SECRET_KEY=你的SECRET KEY
SINOPAC_CA_PATH=C:/sinopac-broker/ekey/憑證.pfx
SINOPAC_CA_PASSWORD=憑證密碼
SINOPAC_PERSON_ID=身分證字號
"""

import os
import time
import logging
from threading import Thread
from typing import Dict

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# 系統狀態
# ─────────────────────────────────────────────────────────
# _connected
#   是否已成功連線到永豐金券商
#
# _api
#   Shioaji API instance
# ─────────────────────────────────────────────────────────
_connected = False
_api = None


# ─────────────────────────────────────────────────────────
# 即時報價快取
# ─────────────────────────────────────────────────────────
# 儲存最新行情資料
#
# TXF = 台指期
# MXF = 微台指
#
# Dashboard / market_service 會直接讀取這裡的資料
# ─────────────────────────────────────────────────────────
_quotes: Dict[str, Dict] = {
    "TXF": {
        "symbol": "台指期",
        "price": None,
        "change": None,
        "changePct": None,
        "status": "unknown",
        "bid": None,
        "ask": None,
        "volume": None,
        "updateTime": None,
    },
    "MXF": {
        "symbol": "微台指",
        "price": None,
        "change": None,
        "changePct": None,
        "status": "unknown",
        "bid": None,
        "ask": None,
        "volume": None,
        "updateTime": None,
    },
}


# ─────────────────────────────────────────────────────────
# 昨收價格
# ─────────────────────────────────────────────────────────
# 用來計算：
# change
# changePct
#
# 透過 Shioaji snapshot API 取得
# ─────────────────────────────────────────────────────────
_prev_close: Dict[str, float] = {
    "TXF": 0,
    "MXF": 0,
}


# ─────────────────────────────────────────────────────────
# 更新報價
# ─────────────────────────────────────────────────────────
# 每次收到 tick 時會呼叫
#
# 功能：
# 1. 更新最新價格
# 2. 計算漲跌
# 3. 更新 bid / ask / volume
# ─────────────────────────────────────────────────────────
def _update_quote(code: str, close_price: float, tick=None):

    if close_price is None or close_price <= 0:
        return

    q = _quotes.get(code)
    if not q:
        return

    q["price"] = close_price

    prev = _prev_close.get(code, 0)

    # 計算漲跌
    if prev > 0:

        change = round(close_price - prev, 2)
        change_pct = round((change / prev) * 100, 2)

        q["change"] = change
        q["changePct"] = change_pct

        if change > 0:
            q["status"] = "up"
        elif change < 0:
            q["status"] = "down"
        else:
            q["status"] = "flat"

    else:
        q["status"] = "active"

    # 更新其他 tick 資訊
    if tick:
        q["bid"] = getattr(tick, "bid_price", None)
        q["ask"] = getattr(tick, "ask_price", None)
        q["volume"] = getattr(tick, "volume", None)

    q["updateTime"] = time.strftime("%H:%M:%S")


# ─────────────────────────────────────────────────────────
# 初始化券商連線
# ─────────────────────────────────────────────────────────
# 流程：
# 1. 讀取 .env
# 2. login 永豐金
# 3. activate CA 憑證
# 4. 背景訂閱行情
# ─────────────────────────────────────────────────────────
def init_broker() -> bool:

    global _connected, _api

    api_key = os.getenv("SINOPAC_API_KEY", "")
    secret_key = os.getenv("SINOPAC_SECRET_KEY", "")
    ca_path = os.getenv("SINOPAC_CA_PATH", "")
    ca_password = os.getenv("SINOPAC_CA_PASSWORD", "")
    person_id = os.getenv("SINOPAC_PERSON_ID", "")

    # 沒設定 API → 直接跳過
    if not api_key or not secret_key:
        logger.info("永豐金 API 未設定，跳過券商連線")
        return False

    try:

        import shioaji as sj

        logger.info("正在登入永豐金...")

        _api = sj.Shioaji()

        _api.login(
            api_key=api_key,
            secret_key=secret_key,
        )

        logger.info("永豐金登入成功")

        # 啟用憑證
        if ca_path and ca_password and person_id:

            _api.activate_ca(
                ca_path=ca_path,
                ca_passwd=ca_password,
                person_id=person_id,
            )

            logger.info("電子憑證啟用成功")

        _connected = True

        # 在背景執行緒訂閱行情
        Thread(target=_subscribe_quotes, daemon=True).start()

        return True

    except ImportError:

        logger.warning("shioaji 套件未安裝")
        return False

    except Exception as e:

        logger.error(f"永豐金登入失敗: {e}")
        _connected = False
        return False


# ─────────────────────────────────────────────────────────
# 訂閱台指期 / 微台指即時行情
# ─────────────────────────────────────────────────────────
# 1. 取得昨收
# 2. 設定 tick callback
# 3. 訂閱 TXF / MXF
# ─────────────────────────────────────────────────────────
def _subscribe_quotes():

    global _prev_close

    if not _api:
        return

    try:

        import shioaji as sj

        txf_contract = _api.Contracts.Futures.TXF.TXFR1
        mxf_contract = _api.Contracts.Futures.MXF.MXFR1

        # 取得昨收
        try:

            snapshots = _api.snapshots([txf_contract, mxf_contract])

            if len(snapshots) > 0:
                _prev_close["TXF"] = getattr(snapshots[0], "reference", 0) or 0

            if len(snapshots) > 1:
                _prev_close["MXF"] = getattr(snapshots[1], "reference", 0) or 0

        except Exception as e:

            logger.warning(f"取得昨收失敗: {e}")

        # tick callback
        @_api.on_tick_fop_v1()
        def on_tick(exchange, tick):

            code_str = str(getattr(tick, "code", ""))
            close = getattr(tick, "close", None)

            if code_str.startswith("TXF"):
                _update_quote("TXF", close, tick)

            elif code_str.startswith("MXF"):
                _update_quote("MXF", close, tick)

        # 訂閱台指期
        _api.quote.subscribe(
            txf_contract,
            quote_type=sj.constant.QuoteType.Tick,
            version="v1",
        )

        logger.info("已訂閱台指期")

        # 訂閱微台指
        _api.quote.subscribe(
            mxf_contract,
            quote_type=sj.constant.QuoteType.Tick,
            version="v1",
        )

        logger.info("已訂閱微台指")

    except Exception as e:

        logger.error(f"訂閱行情失敗: {e}")


# ─────────────────────────────────────────────────────────
# 對外 API
# ─────────────────────────────────────────────────────────


def is_connected() -> bool:
    """券商是否已連線"""
    return _connected


def get_broker_quotes() -> list:
    """
    取得台指期 / 微台指行情
    提供 market_service 使用
    """

    results = []

    for code in ["TXF", "MXF"]:

        q = _quotes[code]

        results.append({
            "symbol": q["symbol"],
            "price": q["price"] or 0,
            "change": q["change"] or 0,
            "changePct": q["changePct"] or 0,
            "status": q["status"],
        })

    return results


def get_broker_health() -> dict:
    """券商健康狀態"""

    return {
        "connected": _connected,
        "broker": "永豐金 Shioaji",
        "quotes": {
            "TXF": _quotes["TXF"]["price"] is not None,
            "MXF": _quotes["MXF"]["price"] is not None,
        },
    }


def shutdown_broker():
    """程式關閉時登出券商"""

    global _api, _connected

    if _api:

        try:
            _api.logout()
            logger.info("永豐金已登出")
        except Exception:
            pass

    _connected = False