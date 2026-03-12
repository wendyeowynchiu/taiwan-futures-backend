"""
新聞抓取與分析服務
優先用 RSS（免費無限制），有 NewsAPI key 時加上 NewsAPI

優化重點：
1. 擴充 RSS 來源
2. 每個來源抓更多則
3. 不再用嚴格關鍵字先篩掉，改成先評分再排序
4. 提高總回傳量
5. 縮短快取時間，讓畫面更新更靈活
"""
import re
import time
import logging
from datetime import datetime, timezone
from typing import List, Dict

import feedparser

from config import NEWSAPI_KEY, NEWS_KEYWORDS

logger = logging.getLogger(__name__)

# ─── RSS 來源 ──────────────────────────────────────────────────────
RSS_FEEDS = [
    ("Reuters Business", "https://feeds.reuters.com/reuters/businessNews"),
    ("Reuters Tech", "https://feeds.reuters.com/reuters/technologyNews"),
    ("Reuters World", "https://feeds.reuters.com/Reuters/worldNews"),
    ("CNBC Top", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("CNBC World", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362"),
    ("Bloomberg Markets", "https://feeds.bloomberg.com/markets/news.rss"),
    ("Nikkei Asia", "https://asia.nikkei.com/rss"),
    ("MarketWatch Top", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
]

# 每個來源最多抓幾則
PER_FEED_LIMIT = 20

# 最終回傳幾則
MAX_NEWS_ITEMS = 30

# 快取秒數
CACHE_SECONDS = 180

# ─── 分類規則 ──────────────────────────────────────────────────────
CATEGORY_RULES = {
    "半導體": [
        "nvidia", "tsmc", "semiconductor", "chip", "soc", "amd", "intel",
        "foundry", "wafer", "ai chip", "gpu", "hbm", "asml", "fabricat",
        "micron", "broadcom", "qualcomm", "packaging",
    ],
    "總經": [
        "fed ", "federal reserve", "cpi", "inflation", "nonfarm", "payroll",
        "rate cut", "rate hike", "interest rate", "gdp", "employment",
        "treasury", "yield", "fomc", "ppi", "consumer confidence", "recession",
    ],
    "政策": [
        "tariff", "trade war", "export control", "chip ban", "sanction",
        "chips act", "subsid", "regulation", "antitrust", "policy", "ban",
    ],
    "亞洲": [
        "nikkei", "boj", "bank of japan", "yen", "japan", "asia",
        "hang seng", "shanghai", "kospi", "taiwan", "china",
    ],
    "科技": [
        "apple", "google", "meta", "microsoft", "amazon", "ai ",
        "artificial intelligence", "cloud", "saas", "tech earning",
        "openai", "data center", "server", "software",
    ],
    "地緣": [
        "war", "conflict", "military", "geopolitic", "tension",
        "missile", "strait", "nuclear", "iran", "ukraine", "taiwan strait",
    ],
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

# 額外關聯詞：即使 config 的 NEWS_KEYWORDS 不夠寬，也能補強
DEFAULT_RELEVANCE_KEYWORDS = [
    "tsmc", "taiwan semiconductor", "nvidia", "semiconductor", "chip", "ai chip",
    "fed", "federal reserve", "cpi", "inflation", "yield", "treasury", "fomc",
    "tariff", "trade war", "export control", "sanction", "policy",
    "nikkei", "boj", "yen", "japan", "asia", "taiwan",
    "oil", "crude", "vix", "geopolitical", "war", "conflict", "recession",
]

# ─── 工具函式 ──────────────────────────────────────────────────────
def _normalize_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _published_to_str(entry) -> str:
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if published:
        dt = datetime(*published[:6], tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


# ─── 影響對象判斷 ─────────────────────────────────────────────────
def _classify_target(title_lower: str) -> str:
    if any(w in title_lower for w in ["tsmc", "nvidia", "chip", "semiconductor", "soc", "amd"]):
        return "電子權值股"
    if any(w in title_lower for w in ["fed ", "cpi", "rate", "treasury", "yield", "inflation"]):
        return "整體台股 / 台指期"
    if any(w in title_lower for w in ["nikkei", "japan", "boj", "yen"]):
        return "夜盤波動"
    if any(w in title_lower for w in ["war", "conflict", "sanction", "tariff", "oil", "crude"]):
        return "整體台股 / 台指期"
    return "觀察用"


# ─── 單則新聞分析 ─────────────────────────────────────────────────
def analyze_article(title: str, summary: str = "", source: str = "") -> Dict:
    """將一則新聞轉換成結構化的市場訊號"""
    text = _normalize_text(f"{title} {summary}")

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
        base_impact = 78
    elif category == "總經":
        base_impact = 72
    elif category == "政策":
        base_impact = 68
    elif category == "地緣":
        base_impact = 70
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


def _calc_relevance_score(title: str, summary: str = "", source: str = "") -> int:
    """
    先全抓，再打分，避免過早過濾掉重要新聞。
    分數高者更容易進 Key News。
    """
    text = _normalize_text(f"{title} {summary}")
    score = 0

    keywords = [k.lower() for k in NEWS_KEYWORDS] + DEFAULT_RELEVANCE_KEYWORDS
    for kw in keywords:
        if kw and kw in text:
            score += 10

    # 重點主題加權
    if any(w in text for w in ["tsmc", "nvidia", "semiconductor", "chip", "hbm", "asml"]):
        score += 18
    if any(w in text for w in ["fed", "cpi", "inflation", "yield", "treasury", "fomc"]):
        score += 16
    if any(w in text for w in ["tariff", "trade war", "export control", "sanction"]):
        score += 14
    if any(w in text for w in ["nikkei", "boj", "yen", "japan"]):
        score += 10
    if any(w in text for w in ["war", "conflict", "oil", "crude", "vix", "geopolitical"]):
        score += 14

    # 來源加分
    trusted_sources = {"Reuters Business", "Reuters Tech", "Reuters World", "Bloomberg Markets", "CNBC Top", "CNBC World", "Nikkei Asia"}
    if source in trusted_sources:
        score += 5

    return score


# ─── 從 RSS 抓新聞 ────────────────────────────────────────────────
def fetch_rss_news() -> List[Dict]:
    """從 RSS feeds 抓取最新新聞"""
    articles = []
    for name, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:PER_FEED_LIMIT]:
                title = entry.get("title", "") or ""
                summary = entry.get("summary", "") or ""
                time_str = _published_to_str(entry)

                if not title.strip():
                    continue

                analyzed = analyze_article(title, summary, name)
                analyzed["time"] = time_str
                analyzed["id"] = hash(f"{name}:{title}") & 0xFFFFFFFF
                analyzed["relevanceScore"] = _calc_relevance_score(title, summary, name)

                # 避免完全不相關的新聞進來太多
                if analyzed["relevanceScore"] >= 8:
                    articles.append(analyzed)
        except Exception as e:
            logger.warning(f"RSS fetch failed for {name}: {e}")

    # 去重複（同標題）
    seen_titles = set()
    unique = []
    for a in articles:
        norm_title = a["title"].strip().lower()
        if norm_title not in seen_titles:
            seen_titles.add(norm_title)
            unique.append(a)

    # 先按關聯度，再按影響分數，再按時間
    unique.sort(
        key=lambda x: (
            x.get("relevanceScore", 0),
            x.get("impactScore", 0),
            abs(x.get("sentimentScore", 0)),
            x.get("time", ""),
        ),
        reverse=True,
    )
    return unique[:MAX_NEWS_ITEMS]


# ─── 從 NewsAPI 抓新聞（選用）─────────────────────────────────────
def fetch_newsapi_news() -> List[Dict]:
    """如果有 API key 就用 NewsAPI 補充"""
    if not NEWSAPI_KEY:
        return []

    try:
        import requests

        query = " OR ".join([
            "NVIDIA", "TSMC", "semiconductor", "Fed", "CPI", "tariff",
            "Treasury yield", "Bank of Japan", "oil", "geopolitics"
        ])
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": query,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 20,
            "apiKey": NEWSAPI_KEY,
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        articles = []
        for item in data.get("articles", []):
            title = item.get("title", "") or ""
            desc = item.get("description", "") or ""
            source_name = item.get("source", {}).get("name", "") or "NewsAPI"
            pub_time = (item.get("publishedAt", "") or "")[:16].replace("T", " ")

            if not title.strip():
                continue

            analyzed = analyze_article(title, desc, source_name)
            analyzed["time"] = pub_time or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            analyzed["id"] = hash(f"{source_name}:{title}") & 0xFFFFFFFF
            analyzed["relevanceScore"] = _calc_relevance_score(title, desc, source_name)

            if analyzed["relevanceScore"] >= 8:
                articles.append(analyzed)

        return articles
    except Exception as e:
        logger.warning(f"NewsAPI fetch failed: {e}")
        return []


# ─── 主函式 ───────────────────────────────────────────────────────
_news_cache: List[Dict] = []
_news_cache_time: float = 0


def get_latest_news(force_refresh: bool = False) -> List[Dict]:
    """取得最新新聞（有快取）"""
    global _news_cache, _news_cache_time

    if not force_refresh and _news_cache and (time.time() - _news_cache_time < CACHE_SECONDS):
        return _news_cache

    rss_articles = fetch_rss_news()
    api_articles = fetch_newsapi_news()

    # 合併去重
    all_articles = rss_articles + api_articles
    seen = set()
    merged = []
    for a in all_articles:
        norm_title = a["title"].strip().lower()
        if norm_title not in seen:
            seen.add(norm_title)
            merged.append(a)

    merged.sort(
        key=lambda x: (
            x.get("relevanceScore", 0),
            x.get("impactScore", 0),
            abs(x.get("sentimentScore", 0)),
            x.get("time", ""),
        ),
        reverse=True,
    )

    _news_cache = merged[:MAX_NEWS_ITEMS]
    _news_cache_time = time.time()

    logger.info(f"News refreshed: {len(_news_cache)} articles")
    return _news_cache
