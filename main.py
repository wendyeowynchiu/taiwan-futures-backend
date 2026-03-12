"""
台指期 AI 交易輔助系統 — FastAPI 後端
Railway 部署：自動讀取 PORT 環境變數
本機開發：uvicorn main:app --reload --port 8000
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

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

# CORS — 允許 Vercel 前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS + ["*"],  # 開發階段先開，正式上線可移除 "*"
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
        "endpoints": ["/api/news", "/api/market", "/api/scores", "/api/positions", "/api/ai-analysis", "/api/health"],
    }


@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "services": {
            "news": "active",
            "market": "active",
            "scoring": "active",
            "broker": "not_connected",
        },
    }


@app.get("/api/news")
def api_news(refresh: bool = Query(False)):
    """取得最新國際新聞（已分析）"""
    return get_latest_news(force_refresh=refresh)


@app.get("/api/market")
def api_market(refresh: bool = Query(False)):
    """取得市場行情"""
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
    """取得帳戶持倉（目前 placeholder）"""
    return {
        "connected": False,
        "broker": "永豐金（尚未接入）",
        "positions": [],
        "pendingOrders": [],
        "margin": 0,
        "realizedPnl": 0,
        "unrealizedPnl": 0,
        "note": "永豐金 API 尚未接入，請手動操作下單",
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

    positions = []

    return generate_ai_analysis(
        scores=scores,
        news=news,
        market=market,
        rules=rules,
        positions=positions,
        filter_status=filter_status,
        force_refresh=refresh,
    )


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


# ─── 啟動提示 ─────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    logger.info("=" * 60)
    logger.info("台指期 AI 交易輔助系統 後端啟動")
    logger.info("API 文件：/docs")
    logger.info("=" * 60)


# ─── Railway 部署用 ───────────────────────────────────────────────
if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
