"""
新聞抓取與分析服務
優先用 RSS（免費無限制），有 NewsAPI key 時加上 NewsAPI
"""
import re
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict

import feedparser

from config import NEWSAPI_KEY, NEWS_KEYWORDS

logger = logging.getLogger(__name__)

# ─── RSS 來源 ──────────────────────────────────────────────────────
RSS_FEEDS = [
    ("Reuters Business",   "https://feeds.reuters.com/reuters/businessNews"),
    ("Reuters Tech",       "https://feeds.reuters.com/reuters/technologyNews"),
    ("CNBC Top",           "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("Bloomberg Markets",  "https://feeds.bloomberg.com/markets/news.rss"),
    ("Nikkei Asia",        "https://asia.nikkei.com/rss"),
]

# ─── 分類規則 ──────────────────────────────────────────────────────
CATEGORY_RULES = {
    "半導體": ["nvidia", "tsmc", "semiconductor", "chip", "soc", "amd", "intel",
               "foundry", "wafer", "ai chip", "gpu", "hbm", "asml", "fabricat"],
    "總經":   ["fed ", "federal reserve", "cpi", "inflation", "nonfarm", "payroll",
               "rate cut", "rate hike", "interest rate", "gdp", "employment",
               "treasury", "yield", "fomc"],
    "政策":   ["tariff", "trade war", "export control", "chip ban", "sanction",
               "chips act", "subsid", "regulation", "antitrust"],
    "亞洲":   ["nikkei", "boj", "bank of japan", "yen", "japan", "asia",
               "hang seng", "shanghai", "kospi"],
    "科技":   ["apple", "google", "meta", "microsoft", "amazon", "ai ",
               "artificial intelligence", "cloud", "saas", "tech earning"],
    "地緣":   ["war", "conflict", "military", "geopolitic", "tension",
               "missile", "strait", "nuclear"],
}

# ─── 情緒關鍵字 ──────────────────────────────────────────────────
BULLISH_WORDS = [
    "surge", "soar", "rally", "beat", "exceed", "strong", "gain", "jump",
    "optimis", "bull", "upgrade", "boost", "record high", "upbeat", "rise",
    "rebound", "recover", "accelerat", "profit", "outperform",
]
BEARISH_WORDS = [
    "drop", "fall", "crash", "plunge", "miss", "weak", "loss", "decline",
    "pessimis", "bear", "downgrade", "cut", "fear", "panic", "slump",
    "recession", "warn", "risk", "selloff", "sell-off", "tumbl",
]

# ─── 影響對象判斷 ─────────────────────────────────────────────────
def _classify_target(title_lower: str) -> str:
    if any(w in title_lower for w in ["tsmc", "nvidia", "chip", "semiconductor", "soc", "amd"]):
        return "電子權值股"
    if any(w in title_lower for w in ["fed ", "cpi", "rate", "treasury", "yield"]):
        return "整體台股 / 台指期"
    if any(w in title_lower for w in ["nikkei", "japan", "boj", "yen"]):
        return "夜盤波動"
    if any(w in title_lower for w in ["war", "conflict", "sanction", "tariff"]):
        return "整體台股 / 台指期"
    return "觀察用"


# ─── 單則新聞分析 ─────────────────────────────────────────────────
def analyze_article(title: str, summary: str = "", source: str = "") -> Dict:
    """將一則新聞轉換成結構化的市場訊號"""
    text = f"{title} {summary}".lower()

    # 分類
    category = "其他"
    for cat, keywords in CATEGORY_RULES.items():
        if any(kw in text for kw in keywords):
            category = cat
            break

    # 情緒分數
    bull_count = sum(1 for w in BULLISH_WORDS if w in text)
    bear_count = sum(1 for w in BEARISH_WORDS if w in text)
    total = bull_count + bear_count
    if total == 0:
        sentiment = "neutral"
        sentiment_score = 0
    elif bull_count > bear_count:
        sentiment = "bullish"
        sentiment_score = min(95, int(40 + (bull_count / total) * 55))
    else:
        sentiment = "bearish"
        sentiment_score = max(-95, int(-40 - (bear_count / total) * 55))

    # 影響力：半導體 & 總經 權重較高
    base_impact = 50
    if category == "半導體":
        base_impact = 75
    elif category == "總經":
        base_impact = 70
    elif category == "政策":
        base_impact = 65
    impact_score = min(100, base_impact + abs(sentiment_score) // 4)

    # 影響對象
    target = _classify_target(text)

    # 中文解讀
    direction_zh = "偏多" if sentiment == "bullish" else ("偏空" if sentiment == "bearish" else "中性")
    interpretation = f"{category}相關消息，情緒{direction_zh}，影響對象：{target}"

    return {
        "title": title,
        "source": source,
        "category": category,
        "sentiment": sentiment,
        "sentimentScore": sentiment_score,
        "impactScore": impact_score,
        "target": target,
        "interpretation": interpretation,
    }


# ─── 從 RSS 抓新聞 ────────────────────────────────────────────────
def fetch_rss_news() -> List[Dict]:
    """從 RSS feeds 抓取最新新聞"""
    articles = []
    for name, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                published = entry.get("published_parsed") or entry.get("updated_parsed")

                if published:
                    dt = datetime(*published[:6], tzinfo=timezone.utc)
                    time_str = dt.strftime("%Y-%m-%d %H:%M")
                else:
                    time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

                # 只要跟我們關心的關鍵字有關的
                text_lower = f"{title} {summary}".lower()
                if any(kw.lower() in text_lower for kw in NEWS_KEYWORDS):
                    analyzed = analyze_article(title, summary, name)
                    analyzed["time"] = time_str
                    analyzed["id"] = hash(title) & 0xFFFFFFFF
                    articles.append(analyzed)
        except Exception as e:
            logger.warning(f"RSS fetch failed for {name}: {e}")

    # 去重複（同標題）
    seen_titles = set()
    unique = []
    for a in articles:
        if a["title"] not in seen_titles:
            seen_titles.add(a["title"])
            unique.append(a)

    # 按時間排序，最新的在前面
    unique.sort(key=lambda x: x["time"], reverse=True)
    return unique[:20]  # 最多 20 則


# ─── 從 NewsAPI 抓新聞（選用）─────────────────────────────────────
def fetch_newsapi_news() -> List[Dict]:
    """如果有 API key 就用 NewsAPI 補充"""
    if not NEWSAPI_KEY:
        return []

    try:
        import requests
        query = " OR ".join(["NVIDIA", "TSMC", "semiconductor", "Fed", "CPI", "tariff"])
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": query,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 15,
            "apiKey": NEWSAPI_KEY,
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        articles = []
        for item in data.get("articles", []):
            title = item.get("title", "")
            desc = item.get("description", "")
            source_name = item.get("source", {}).get("name", "")
            pub_time = item.get("publishedAt", "")[:16].replace("T", " ")

            analyzed = analyze_article(title, desc, source_name)
            analyzed["time"] = pub_time
            analyzed["id"] = hash(title) & 0xFFFFFFFF
            articles.append(analyzed)

        return articles
    except Exception as e:
        logger.warning(f"NewsAPI fetch failed: {e}")
        return []


# ─── 主函式 ───────────────────────────────────────────────────────
_news_cache: List[Dict] = []
_news_cache_time: float = 0

def get_latest_news(force_refresh: bool = False) -> List[Dict]:
    """取得最新新聞（有快取，5 分鐘更新一次）"""
    global _news_cache, _news_cache_time

    if not force_refresh and _news_cache and (time.time() - _news_cache_time < 300):
        return _news_cache

    rss_articles = fetch_rss_news()
    api_articles = fetch_newsapi_news()

    # 合併去重
    all_articles = rss_articles + api_articles
    seen = set()
    merged = []
    for a in all_articles:
        if a["title"] not in seen:
            seen.add(a["title"])
            merged.append(a)

    merged.sort(key=lambda x: x["time"], reverse=True)
    _news_cache = merged[:20]
    _news_cache_time = time.time()

    logger.info(f"News refreshed: {len(_news_cache)} articles")
    return _news_cache
