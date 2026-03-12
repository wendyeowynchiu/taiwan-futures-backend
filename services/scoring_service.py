"""
v5 計分引擎
根據市場資料 + 新聞分析，計算九大因子分數
"""
import time
import logging
from typing import Dict, List, Optional

from services.market_service import get_market_data
from services.news_service import get_latest_news

logger = logging.getLogger(__name__)

# ─── 權重設定（與前端一致）─────────────────────────────────────────
WEIGHTS = {
    "globalRisk":     0.15,
    "semiconductor":  0.18,
    "tsmAdr":         0.15,
    "policy":         0.08,
    "asia":           0.06,
    "currency":       0.05,
    "priceStructure": 0.18,
    "session":        0.05,
    "institutional":  0.10,
}

SCORE_RANGES = {
    "globalRisk":     (-40, 40),
    "semiconductor":  (-60, 60),
    "tsmAdr":         (-50, 50),
    "policy":         (-50, 50),
    "asia":           (-25, 25),
    "currency":       (-20, 20),
    "priceStructure": (-70, 70),
    "session":        (-30, 15),
    "institutional":  (-60, 60),
}


def _clamp(value: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, value))


def _get_market_item(market_data: List[Dict], name: str) -> Optional[Dict]:
    for item in market_data:
        if item["symbol"] == name:
            return item
    return None


# ─── 1. Global Risk Score ─────────────────────────────────────────
def calc_global_risk(market_data: List[Dict], news: List[Dict]) -> float:
    """全球風險情緒：看 Nasdaq、S&P、VIX"""
    score = 0

    nasdaq = _get_market_item(market_data, "那斯達克期貨")
    sp500 = _get_market_item(market_data, "S&P 500 期貨")
    vix = _get_market_item(market_data, "VIX 恐慌指數")

    # Nasdaq 期貨漲跌
    if nasdaq and nasdaq["changePct"]:
        pct = nasdaq["changePct"]
        if pct > 1.5:
            score += 20
        elif pct > 0.5:
            score += 12
        elif pct > 0:
            score += 5
        elif pct < -1.5:
            score -= 20
        elif pct < -0.5:
            score -= 12
        else:
            score -= 5

    # S&P 同步
    if sp500 and sp500["changePct"]:
        pct = sp500["changePct"]
        if pct > 0.5:
            score += 8
        elif pct < -0.5:
            score -= 8

    # VIX
    if vix and vix["price"]:
        if vix["price"] > 30:
            score -= 15
        elif vix["price"] > 25:
            score -= 8
        elif vix["price"] < 15:
            score += 5
        # VIX 變化
        if vix["changePct"] < -5:
            score += 5
        elif vix["changePct"] > 10:
            score -= 10

    # 總經新聞情緒
    macro_news = [n for n in news if n["category"] == "總經"]
    if macro_news:
        avg_sent = sum(n["sentimentScore"] for n in macro_news[:5]) / min(5, len(macro_news))
        score += avg_sent * 0.1  # 微調

    return _clamp(round(score), -40, 40)


# ─── 2. Semiconductor Score ──────────────────────────────────────
def calc_semiconductor(market_data: List[Dict], news: List[Dict]) -> float:
    """半導體情緒：看 SOX、NVIDIA"""
    score = 0

    sox = _get_market_item(market_data, "費半指數")
    nvidia = _get_market_item(market_data, "NVIDIA 輝達")

    if sox and sox["changePct"]:
        pct = sox["changePct"]
        if pct > 2:
            score += 30
        elif pct > 1:
            score += 18
        elif pct > 0:
            score += 8
        elif pct < -2:
            score -= 30
        elif pct < -1:
            score -= 18
        else:
            score -= 8

    if nvidia and nvidia["changePct"]:
        pct = nvidia["changePct"]
        if pct > 3:
            score += 20
        elif pct > 1:
            score += 10
        elif pct < -3:
            score -= 20
        elif pct < -1:
            score -= 10

    # 半導體新聞
    semi_news = [n for n in news if n["category"] == "半導體"]
    if semi_news:
        avg_sent = sum(n["sentimentScore"] for n in semi_news[:5]) / min(5, len(semi_news))
        score += avg_sent * 0.15

    return _clamp(round(score), -60, 60)


# ─── 3. TSM ADR Proxy Score ──────────────────────────────────────
def calc_tsm_adr(market_data: List[Dict], news: List[Dict]) -> float:
    """台積電 ADR 代理分數"""
    score = 0

    tsm = _get_market_item(market_data, "台積電 ADR")
    sox = _get_market_item(market_data, "費半指數")

    if tsm and tsm["changePct"]:
        pct = tsm["changePct"]
        if pct > 3:
            score += 30
        elif pct > 1.5:
            score += 20
        elif pct > 0:
            score += 10
        elif pct < -3:
            score -= 30
        elif pct < -1.5:
            score -= 20
        else:
            score -= 10

    # TSM 相對 SOX 強弱
    if tsm and sox and tsm["changePct"] and sox["changePct"]:
        relative = tsm["changePct"] - sox["changePct"]
        if relative > 1:
            score += 10  # TSM 比費半更強
        elif relative < -1:
            score -= 10  # TSM 比費半更弱

    return _clamp(round(score), -50, 50)


# ─── 4. Policy / Tariff Score ────────────────────────────────────
def calc_policy(news: List[Dict]) -> float:
    """政策 / 關稅分數"""
    score = 0
    policy_news = [n for n in news if n["category"] == "政策"]

    if not policy_news:
        return 0

    for n in policy_news[:5]:
        score += n["sentimentScore"] * 0.3

    return _clamp(round(score), -50, 50)


# ─── 5. Asia Sentiment Score ─────────────────────────────────────
def calc_asia(market_data: List[Dict], news: List[Dict]) -> float:
    """亞洲市場情緒"""
    score = 0

    nikkei = _get_market_item(market_data, "日經 225")
    usdjpy = _get_market_item(market_data, "美元/日圓")

    if nikkei and nikkei["changePct"]:
        pct = nikkei["changePct"]
        if pct > 1.5:
            score += 15
        elif pct > 0.5:
            score += 8
        elif pct < -1.5:
            score -= 15
        elif pct < -0.5:
            score -= 8

    # 日圓急升 = risk-off
    if usdjpy and usdjpy["changePct"]:
        pct = usdjpy["changePct"]
        if pct < -1:  # 日圓急升
            score -= 8
        elif pct > 0.5:
            score += 5

    return _clamp(round(score), -25, 25)


# ─── 6. Currency Score ───────────────────────────────────────────
def calc_currency(market_data: List[Dict]) -> float:
    """匯率分數"""
    usdjpy = _get_market_item(market_data, "美元/日圓")
    if not usdjpy or not usdjpy["changePct"]:
        return 0

    pct = usdjpy["changePct"]
    if pct > 1:
        return 10
    elif pct > 0.3:
        return 5
    elif pct < -1:
        return -10
    elif pct < -0.3:
        return -5
    return 0


# ─── 7. Price Structure Score ────────────────────────────────────
def calc_price_structure(market_data: List[Dict]) -> float:
    """
    價格結構分數
    注意：完整版需要台指期 K 線資料判斷突破 / 跌破
    現階段用 Nasdaq / SOX 方向性代替
    接永豐金後可以換成真正的台指期價格結構
    """
    score = 0

    nasdaq = _get_market_item(market_data, "那斯達克期貨")
    sox = _get_market_item(market_data, "費半指數")

    # 如果主要指數方向一致且明確，給正面分數
    if nasdaq and sox:
        both_up = (nasdaq["changePct"] or 0) > 0.5 and (sox["changePct"] or 0) > 0.5
        both_down = (nasdaq["changePct"] or 0) < -0.5 and (sox["changePct"] or 0) < -0.5

        if both_up:
            strength = min(abs(nasdaq["changePct"] or 0), abs(sox["changePct"] or 0))
            score += min(40, int(strength * 15))
        elif both_down:
            strength = min(abs(nasdaq["changePct"] or 0), abs(sox["changePct"] or 0))
            score -= min(40, int(strength * 15))
        else:
            # 方向不一致，分數較低
            score = 0

    return _clamp(round(score), -70, 70)


# ─── 8. Session / Liquidity Score ────────────────────────────────
def calc_session() -> float:
    """
    時段 / 流動性分數
    根據目前時間（台北時間）判斷
    """
    from datetime import datetime, timezone, timedelta
    tw_tz = timezone(timedelta(hours=8))
    now_tw = datetime.now(tw_tz)
    hour = now_tw.hour

    # 台指日盤 08:45 ~ 13:45
    if 9 <= hour <= 13:
        return 10  # 日盤有效時段

    # 台指夜盤 15:00 ~ 05:00
    # 美股開盤 21:30 ~ 22:30 台北時間
    if 21 <= hour <= 23:
        return 8  # 美股開盤，訊號有效

    if 15 <= hour <= 20:
        return 3  # 夜盤前段，尚可

    # 凌晨 2:00 之後
    if 2 <= hour <= 5:
        return -15  # 低流動性

    if 0 <= hour <= 1:
        return -5  # 尚可但注意

    return 0


# ─── 9. Institutional Position Score ─────────────────────────────
def calc_institutional() -> float:
    """
    法人籌碼分數
    注意：需要台灣期交所外資未平倉資料
    現階段回傳 0，接資料源後再實作
    """
    # TODO: 接台灣期交所每日外資台指期淨部位
    # 資料來源：https://www.taifex.com.tw/cht/3/futContractsDate
    return 0


# ─── 綜合計算 ─────────────────────────────────────────────────────
_scores_cache: Dict = {}
_scores_cache_time: float = 0


def compute_all_scores(force_refresh: bool = False) -> Dict:
    """計算所有 v5 分數"""
    global _scores_cache, _scores_cache_time

    if not force_refresh and _scores_cache and (time.time() - _scores_cache_time < 30):
        return _scores_cache

    market = get_market_data()
    news = get_latest_news()

    scores = {
        "globalRisk":     calc_global_risk(market, news),
        "semiconductor":  calc_semiconductor(market, news),
        "tsmAdr":         calc_tsm_adr(market, news),
        "policy":         calc_policy(news),
        "asia":           calc_asia(market, news),
        "currency":       calc_currency(market),
        "priceStructure": calc_price_structure(market),
        "session":        calc_session(),
        "institutional":  calc_institutional(),
    }

    # 計算 final score
    final = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)
    scores["finalScore"] = round(final, 2)

    # 訊號判斷
    if final >= 35:
        scores["signal"] = "強烈做多"
    elif final >= 18:
        scores["signal"] = "偏多"
    elif final >= 10:
        scores["signal"] = "觀察偏多"
    elif final > -10:
        scores["signal"] = "不交易"
    elif final > -18:
        scores["signal"] = "觀察偏空"
    elif final > -35:
        scores["signal"] = "偏空"
    else:
        scores["signal"] = "強烈做空"

    # ─── 理由與警告（讓前端不用寫死文案）───────────────────────
    reasons = []
    warnings = []

    # 半導體
    if scores["semiconductor"] > 30:
        reasons.append("半導體情緒偏多，SOX / NVIDIA 表現強勢")
    elif scores["semiconductor"] < -30:
        reasons.append("半導體情緒偏空，科技股承壓")

    # 台積電 ADR
    if scores["tsmAdr"] > 20:
        reasons.append("台積電 ADR 表現強勢，對台指電子權值有正向支撐")
    elif scores["tsmAdr"] < -20:
        reasons.append("台積電 ADR 表現弱勢，台指電子權值承壓")

    # 全球風險
    if scores["globalRisk"] > 15:
        reasons.append("全球風險偏好回升，有利多方操作")
    elif scores["globalRisk"] < -15:
        reasons.append("全球風險趨避情緒上升，市場偏保守")

    # 價格結構
    if scores["priceStructure"] > 20:
        reasons.append("價格結構偏多，主要指數方向一致")
    elif scores["priceStructure"] < -20:
        reasons.append("價格結構偏空，主要指數同步轉弱")

    # 政策
    if scores["policy"] < -15:
        policy_news = [n for n in news if n.get("category") == "政策"]
        if policy_news:
            warnings.append(f"政策面利空：{policy_news[0].get('title', '有利空消息')}")
        else:
            warnings.append("政策面情緒偏空")

    # 時段
    if scores["session"] < -5:
        warnings.append("目前時段流動性較低，訊號可靠度下降")

    if not reasons:
        reasons.append("目前各因子分數在中性區間，方向不明確")

    if not warnings:
        warnings.append("暫無特別風險提醒")

    scores["reasons"] = reasons[:4]
    scores["warnings"] = warnings[:3]

    _scores_cache = scores
    _scores_cache_time = time.time()

    logger.info(f"Scores computed: final={scores['finalScore']}, signal={scores['signal']}")
    return scores
