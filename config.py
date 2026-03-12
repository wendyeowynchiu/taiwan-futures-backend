"""
台指期 AI 交易輔助系統 — 後端設定
"""
import os

# ─── News API ──────────────────────────────────────────────────────
# 免費方案：NewsAPI (https://newsapi.org) 每日 100 次
# 如果沒有 key，系統會改用 RSS feed（免費無限制）
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")

# ─── 新聞關鍵字 ───────────────────────────────────────────────────
NEWS_KEYWORDS = [
    # 總經
    "Fed", "CPI", "nonfarm payrolls", "rate cut", "rate hike", "inflation",
    # 半導體 / 科技
    "NVIDIA", "TSMC", "semiconductor", "AI chip", "SOX index",
    # 政策 / 貿易
    "chip ban", "export control", "tariff", "trade war",
    # 匯率 / 日本
    "USDJPY", "BOJ", "Nikkei", "yen",
    # 台灣
    "Taiwan futures", "TAIEX",
]

# ─── 市場資料 — yfinance 代碼對照 ─────────────────────────────────
MARKET_SYMBOLS = {
    "那斯達克期貨": "NQ=F",
    "S&P 500 期貨": "ES=F",
    "費半指數":     "^SOX",
    "NVIDIA 輝達":  "NVDA",
    "台積電 ADR":   "TSM",
    "VIX 恐慌指數": "^VIX",
    "美元/日圓":    "JPY=X",
    "日經 225":     "^N225",
}

# ─── 排程間隔（秒）─────────────────────────────────────────────────
NEWS_FETCH_INTERVAL = 300     # 5 分鐘抓一次新聞
MARKET_FETCH_INTERVAL = 30    # 30 秒抓一次行情
SCORE_CALC_INTERVAL = 30      # 30 秒算一次分數

# ─── CORS（前端網址）──────────────────────────────────────────────
# Railway 部署時，設定 FRONTEND_URL 環境變數指向你的 Vercel 網址
FRONTEND_URL = os.getenv("FRONTEND_URL", "")

FRONTEND_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]

# 加入 Vercel 的網址
if FRONTEND_URL:
    FRONTEND_ORIGINS.append(FRONTEND_URL)
    # 也加入不帶尾斜線的版本
    FRONTEND_ORIGINS.append(FRONTEND_URL.rstrip("/"))
