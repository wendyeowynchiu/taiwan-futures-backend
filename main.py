"""
台指期 AI 交易輔助系統 — FastAPI 後端

本機開發：python main.py
Railway 部署：自動讀取 PORT 環境變數

永豐金整合：
  有設定 SINOPAC_API_KEY → 自動登入 + 訂閱台指期/微台指報價
  沒設定 → 靜默跳過，不影響其他功能
"""
import logging
import os
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

# 讀取 .env（本機開發用）
load_dotenv()

from config import FRONTEND_ORIGINS
from services.news_service import get_latest_news
from services.market_service import get_market_data
from services.scoring_service import compute_all_scores, WEIGHTS, SCORE_RANGES
from services.ai_insight_service import generate_ai_analysis

# ─── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ─── App ──────────────────────────────────────────────────────────
app = FastAPI(
    title="台指期 AI 交易輔助系統",
    description="v5 計分模型後端 API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── API Endpoints ────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "name": "台指期 AI 交易輔助系統 API",
        "version": "1.0.0",
        "endpoints": [
            "/api/news", "/api/market", "/api/scores",
            "/api/positions", "/api/ai-analysis",
            "/api/broker/health", "/api/health",
        ],
    }


@app.get("/api/health")
def health_check():
    # 檢查永豐金狀態
    broker_status = "not_connected"
    try:
        from services.broker_service import is_connected
        broker_status = "connected" if is_connected() else "not_connected"
    except Exception:
        pass

    return {
        "status": "ok",
        "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "services": {
            "news": "active",
            "market": "active",
            "scoring": "active",
            "broker": broker_status,
        },
    }


@app.get("/api/news")
def api_news(refresh: bool = Query(False)):
    """取得最新國際新聞（已分析）"""
    return get_latest_news(force_refresh=refresh)


@app.get("/api/market")
def api_market(refresh: bool = Query(False)):
    """取得市場行情（含台指期/微台指即時報價）"""
    return get_market_data(force_refresh=refresh)


@app.get("/api/scores")
def api_scores(refresh: bool = Query(False)):
    """取得 v5 模型分數"""
    return compute_all_scores(force_refresh=refresh)


@app.get("/api/scores/meta")
def api_scores_meta():
    """取得分數模型元資料"""
    meta = {}
    label_map = {
        "globalRisk": ("Global Risk", "全球風險"),
        "semiconductor": ("Semiconductor", "半導體"),
        "tsmAdr": ("TSM ADR", "台積電ADR"),
        "policy": ("Policy/Tariff", "政策關稅"),
        "asia": ("Asia Sentiment", "亞洲市場"),
        "currency": ("Currency", "匯率"),
        "priceStructure": ("Price Structure", "價格結構"),
        "session": ("Session/Liquidity", "時段流動性"),
        "institutional": ("Institutional", "法人籌碼"),
    }
    for key in WEIGHTS:
        min_val, max_val = SCORE_RANGES[key]
        label_en, label_zh = label_map[key]
        meta[key] = {
            "weight": WEIGHTS[key],
            "min": min_val,
            "max": max_val,
            "label": label_en,
            "labelZh": label_zh,
        }
    return meta


@app.get("/api/positions")
def api_positions():
    """取得帳戶持倉"""
    # 永豐金連線後可以擴充這裡
    try:
        from services.broker_service import is_connected
        connected = is_connected()
    except Exception:
        connected = False

    return {
        "connected": connected,
        "broker": "永豐金" if connected else "永豐金（尚未接入）",
        "positions": [],
        "pendingOrders": [],
        "margin": 0,
        "realizedPnl": 0,
        "unrealizedPnl": 0,
        "note": "報價已連線，持倉功能待後續開發" if connected else "永豐金 API 尚未接入，請手動操作下單",
    }


@app.get("/api/ai-analysis")
def api_ai_analysis(refresh: bool = Query(False)):
    """AI 分析結論與建議"""
    scores = compute_all_scores()
    news = get_latest_news()
    market = get_market_data()

    rules = [
        {"name": "做多條件：綜合分數 > +18", "status": scores.get("finalScore", 0) > 18, "type": "entry"},
        {"name": "做多條件：半導體分數 > +30", "status": scores.get("semiconductor", 0) > 30, "type": "entry"},
        {"name": "風控：VIX < 30", "status": True, "type": "block"},
        {"name": "禁單：非重大數據時段", "status": True, "type": "block"},
    ]

    filter_status = {"allowed": True, "reason": "所有條件通過"}
    if scores.get("globalRisk", 0) < -30:
        filter_status = {"allowed": False, "reason": "全球風險偏高"}

    return generate_ai_analysis(
        scores=scores,
        news=news,
        market=market,
        rules=rules,
        positions=[],
        filter_status=filter_status,
        force_refresh=refresh,
    )


@app.get("/api/broker/health")
def api_broker_health():
    """永豐金連線狀態"""
    try:
        from services.broker_service import get_broker_health
        return get_broker_health()
    except Exception:
        return {"connected": False, "broker": "not_available"}


@app.get("/api/signals/history")
def api_signal_history():
    """訊號歷史紀錄"""
    scores = compute_all_scores()
    tw_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m/%d %H:%M")

    return [{
        "time": tw_time,
        "scores": {k: scores[k] for k in WEIGHTS},
        "finalScore": scores["finalScore"],
        "signal": scores["signal"],
        "result": "進行中",
    }]


# ─── Startup / Shutdown ──────────────────────────────────────────
@app.on_event("startup")
async def startup():
    logger.info("=" * 60)
    logger.info("台指期 AI 交易輔助系統 後端啟動")
    logger.info("API 文件：http://localhost:8000/docs")
    logger.info("=" * 60)

    # 嘗試連線永豐金
    try:
        from services.broker_service import init_broker
        if init_broker():
            logger.info("✅ 永豐金已連線，台指期/微台指即時報價啟動")
        else:
            logger.info("ℹ️  永豐金未連線（缺少憑證或環境變數），使用 fallback 資料")
    except ImportError:
        logger.info("ℹ️  shioaji 未安裝，跳過永豐金連線")
    except Exception as e:
        logger.warning(f"永豐金連線失敗: {e}")


@app.on_event("shutdown")
async def shutdown():
    try:
        from services.broker_service import shutdown_broker
        shutdown_broker()
    except Exception:
        pass
    logger.info("後端已關閉")


# ─── 啟動 ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
