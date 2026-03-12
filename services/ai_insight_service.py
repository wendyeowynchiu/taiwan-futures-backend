"""
AI 解讀與建議模組 (AI Insight Engine)

這個模組是系統的「大腦後半段」：
- Scoring Engine 負責算分數（穩定、可控、可回測）
- AI Insight Engine 負責解釋與建議（靈活、語意、像人判斷）

流程：
  news + market + scores + rules + positions + filter
  → 組 prompt
  → 呼叫 Claude API
  → 解析回傳 JSON
  → 回傳結構化結果給前端
"""
import json
import time
import logging
import os
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ─── 設定 ──────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
AI_CACHE_SECONDS = 120  # AI 分析快取 2 分鐘（避免頻繁呼叫）

# ─── System Prompt ─────────────────────────────────────────────────
SYSTEM_PROMPT = """你是一個專業的台指期交易分析助理。

你的任務是根據提供的資料，判斷目前台指期的多空方向，並給出清楚的交易建議。

重要原則：
1. 你不負責算分數，分數由系統的 Scoring Engine 提供
2. 你負責「解釋」和「建議」— 為什麼偏多？風險在哪？該怎麼做？
3. 回答必須用繁體中文
4. 必須嚴格按照指定的 JSON 格式回傳
5. 不要過度樂觀或悲觀，要客觀
6. 如果方向不明確，就建議觀望，不要硬給方向
7. 永遠要提醒風險

你特別擅長：
- 判斷新聞是真的重要還是只是雜訊
- 判斷利多是否已經被反映（利多出盡）
- 判斷市場情緒與價格是否一致
- 提醒哪些時段不適合交易"""

# ─── User Prompt 模板 ──────────────────────────────────────────────
USER_PROMPT_TEMPLATE = """請根據以下資料，分析目前台指期的方向與建議。

## 系統分數（v5 模型）
{scores_text}

## 最新重要新聞（前 8 則）
{news_text}

## 市場行情
{market_text}

## 規則狀態
{rules_text}

## 過濾器狀態
- 訊號是否有效：{filter_allowed}
- 原因：{filter_reason}

## 目前持倉
{positions_text}

## 目前時間
- 台北時間：{tw_time}
- 美東時間：{us_time}

---

請嚴格按照以下 JSON 格式回傳（不要加 markdown 符號）：

{{
  "conclusion": "一句話總結今天的方向判斷（例如：今日台指期偏多，但非強勢多頭，需留意政策面雜音）",
  "direction": "偏多 / 偏空 / 觀望 / 強烈做多 / 強烈做空",
  "reasons": [
    "原因一（最重要的）",
    "原因二",
    "原因三"
  ],
  "warnings": [
    "風險提醒一",
    "風險提醒二（如果有的話）"
  ],
  "newsHighlight": "今天最值得關注的新聞重點（1~2句）",
  "marketContext": "市場背景描述（美股、半導體、亞洲的整體狀態）",
  "suggestion": {{
    "action": "建議動作（例如：回踩支撐區偏多操作）",
    "timing": "建議時機（例如：等美股開盤確認方向後）",
    "stopLoss": "停損策略（例如：跌破 22500 停損）",
    "takeProfit": "停利策略（例如：22850 附近分批停利）",
    "size": "建議口數（例如：1~2 口）"
  }},
  "confidence": 0.72,
  "sessionNote": "目前時段適不適合交易的提醒"
}}"""


# ─── 資料格式化 ────────────────────────────────────────────────────
def _format_scores(scores: Dict) -> str:
    lines = []
    name_map = {
        "globalRisk": "全球風險", "semiconductor": "半導體", "tsmAdr": "台積電ADR",
        "policy": "政策關稅", "asia": "亞洲市場", "currency": "匯率",
        "priceStructure": "價格結構", "session": "時段流動性", "institutional": "法人籌碼",
    }
    for key, name in name_map.items():
        val = scores.get(key, 0)
        sign = "+" if val >= 0 else ""
        lines.append(f"- {name}: {sign}{val}")

    final = scores.get("finalScore", 0)
    signal = scores.get("signal", "未知")
    lines.append(f"- 綜合分數 (Final Score): {'+' if final >= 0 else ''}{final}")
    lines.append(f"- 系統訊號: {signal}")
    return "\n".join(lines)


def _format_news(news: List[Dict]) -> str:
    if not news:
        return "（目前無新聞資料）"
    lines = []
    for i, n in enumerate(news[:8], 1):
        sent = n.get("sentimentScore", 0)
        sign = "+" if sent >= 0 else ""
        lines.append(
            f"{i}. [{n.get('category', '其他')}] {n.get('title', '')} "
            f"(情緒: {sign}{sent}, 影響力: {n.get('impactScore', 0)}) "
            f"— {n.get('interpretation', '')}"
        )
    return "\n".join(lines)


def _format_market(market: List[Dict]) -> str:
    if not market:
        return "（目前無市場資料）"
    lines = []
    for m in market:
        pct = m.get("changePct", 0)
        sign = "+" if pct >= 0 else ""
        price_str = f"{m['price']:,.2f}" if m.get("price") else "-"
        lines.append(f"- {m['symbol']}: {price_str} ({sign}{pct:.2f}%)")
    return "\n".join(lines)


def _format_rules(rules: List[Dict]) -> str:
    if not rules:
        return "（無自訂規則）"
    lines = []
    for r in rules:
        status = "✓ 成立" if r.get("status") else "✗ 未成立"
        lines.append(f"- {r.get('name', '')}: {status}")
    return "\n".join(lines)


def _format_positions(positions: List[Dict]) -> str:
    if not positions:
        return "目前無持倉"
    lines = []
    for p in positions:
        direction = "多單" if p.get("direction") == "long" else "空單"
        pnl = p.get("unrealizedPnl", 0)
        sign = "+" if pnl >= 0 else ""
        lines.append(
            f"- {p.get('symbol', '')}: {direction} ×{p.get('qty', 0)}, "
            f"均價 {p.get('avgCost', 0)}, 現價 {p.get('marketPrice', 0)}, "
            f"未實現損益 {sign}{pnl}"
        )
    return "\n".join(lines)


from anthropic import Anthropic

_client = None
if ANTHROPIC_API_KEY:
    _client = Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── 呼叫 Claude API ──────────────────────────────────────────────
def _call_claude_api(prompt: str) -> Optional[str]:
    if not _client:
        logger.warning("ANTHROPIC_API_KEY not set, using fallback analysis")
        return None

    try:
        resp = _client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": prompt}
            ],
        )

        text_blocks = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text_blocks.append(block.text)

        return "\n".join(text_blocks) if text_blocks else None

    except Exception as e:
        logger.error(f"Claude API call failed: {e}")
        return None


# ─── 解析 AI 回傳 ─────────────────────────────────────────────────
def _parse_ai_response(raw: str) -> Optional[Dict]:
    """解析 AI 回傳的 JSON"""
    try:
        # 移除可能的 markdown 包裝
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse AI response as JSON: {e}")
        logger.debug(f"Raw response: {raw[:500]}")
        return None


# ─── Fallback 分析（沒有 API key 時）──────────────────────────────
def _generate_fallback_analysis(scores: Dict, news: List[Dict], market: List[Dict],
                                 rules: List[Dict], filter_status: Dict) -> Dict:
    """
    當沒有 AI API key 時，用規則產生基本分析
    不如真正的 AI 細膩，但至少能給出結構化的結果
    """
    final = scores.get("finalScore", 0)
    signal = scores.get("signal", "不交易")

    # 方向判斷
    if final >= 35:
        direction = "強烈做多"
        conclusion = "目前多方訊號明確，市場情緒偏強。"
    elif final >= 18:
        direction = "偏多"
        conclusion = "整體偏多，但尚未達到強勢標準，建議謹慎偏多。"
    elif final >= 10:
        direction = "觀察偏多"
        conclusion = "方向略偏多方，但訊號不夠強，建議觀察為主。"
    elif final > -10:
        direction = "觀望"
        conclusion = "市場方向不明確，不建議主動進場。"
    elif final > -18:
        direction = "觀察偏空"
        conclusion = "方向略偏空方，但訊號不夠強，建議觀察為主。"
    elif final > -35:
        direction = "偏空"
        conclusion = "整體偏空，但尚未達到強勢標準，建議謹慎偏空。"
    else:
        direction = "強烈做空"
        conclusion = "目前空方訊號明確，市場情緒偏弱。"

    # 主要原因
    reasons = []
    if scores.get("semiconductor", 0) > 30:
        reasons.append("半導體情緒偏多，SOX / NVIDIA 表現強勢")
    elif scores.get("semiconductor", 0) < -30:
        reasons.append("半導體情緒偏空，科技股承壓")

    if scores.get("tsmAdr", 0) > 20:
        reasons.append("台積電 ADR 表現強勢，對台指電子權值有正向支撐")
    elif scores.get("tsmAdr", 0) < -20:
        reasons.append("台積電 ADR 表現弱勢，台指電子權值承壓")

    if scores.get("globalRisk", 0) > 15:
        reasons.append("全球風險偏好回升，有利多方操作")
    elif scores.get("globalRisk", 0) < -15:
        reasons.append("全球風險趨避情緒上升")

    if scores.get("priceStructure", 0) > 20:
        reasons.append("價格結構偏多，指數方向明確")
    elif scores.get("priceStructure", 0) < -20:
        reasons.append("價格結構偏空，指數方向明確")

    if not reasons:
        reasons = ["目前各項因子分數都在中性區間", "沒有明確的主導因素", "建議等待更明確的訊號"]

    # 風險提醒
    warnings = []
    policy_news = [n for n in news if n.get("category") == "政策" and n.get("sentiment") == "bearish"]
    if policy_news:
        warnings.append(f"政策面：{policy_news[0].get('title', '有利空消息')}")

    if scores.get("session", 0) < -5:
        warnings.append("目前時段流動性較低，訊號可靠度下降")

    if not filter_status.get("allowed", True):
        warnings.append(f"過濾器已封鎖訊號：{filter_status.get('reason', '')}")

    if not warnings:
        warnings = ["暫無特別風險提醒，但請隨時留意突發事件"]

    # 新聞重點
    top_news = sorted(news[:5], key=lambda n: abs(n.get("impactScore", 0)), reverse=True)
    news_highlight = top_news[0].get("title", "暫無重大新聞") if top_news else "暫無重大新聞"

    # 建議
    if direction in ("偏多", "強烈做多"):
        suggestion = {
            "action": "偏多操作",
            "timing": "等待回踩支撐確認後",
            "stopLoss": "跌破前低停損",
            "takeProfit": "接近壓力區分批停利",
            "size": "1~2 口",
        }
    elif direction in ("偏空", "強烈做空"):
        suggestion = {
            "action": "偏空操作",
            "timing": "等待反彈壓力確認後",
            "stopLoss": "站回前高停損",
            "takeProfit": "接近支撐區分批停利",
            "size": "1~2 口",
        }
    else:
        suggestion = {
            "action": "觀望不操作",
            "timing": "等待方向明確",
            "stopLoss": "-",
            "takeProfit": "-",
            "size": "0 口",
        }

    # 信心度
    confidence = min(0.95, max(0.1, abs(final) / 50))

    return {
        "conclusion": conclusion,
        "direction": direction,
        "reasons": reasons[:4],
        "warnings": warnings[:3],
        "newsHighlight": news_highlight,
        "marketContext": f"綜合分數 {'+' if final >= 0 else ''}{final}，系統訊號為「{signal}」",
        "suggestion": suggestion,
        "confidence": round(confidence, 2),
        "sessionNote": "請注意交易時段，凌晨 2 點後流動性下降不建議開新倉",
        "source": "rule_based",  # 標記非 AI 產生
    }


# ─── 主函式 ───────────────────────────────────────────────────────
_ai_cache: Dict = {}
_ai_cache_time: float = 0


def generate_ai_analysis(
    scores: Dict,
    news: List[Dict],
    market: List[Dict],
    rules: List[Dict],
    positions: List[Dict],
    filter_status: Dict,
    force_refresh: bool = False,
) -> Dict:
    """
    產生 AI 分析結果

    有 ANTHROPIC_API_KEY → 呼叫 Claude API
    沒有 → 用規則產生 fallback 分析
    """
    global _ai_cache, _ai_cache_time

    if not force_refresh and _ai_cache and (time.time() - _ai_cache_time < AI_CACHE_SECONDS):
        return _ai_cache

    # 時間
    from zoneinfo import ZoneInfo

    tw_time = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M")
    us_time = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M")

    result = None

   # 嘗試呼叫 Claude API
    if ANTHROPIC_API_KEY:
    prompt = USER_PROMPT_TEMPLATE.format(
        scores_text=_format_scores(scores),
        news_text=_format_news(news),
        market_text=_format_market(market),
        rules_text=_format_rules(rules),
        filter_allowed="是" if filter_status.get("allowed") else "否",
        filter_reason=filter_status.get("reason", ""),
        positions_text=_format_positions(positions),
        tw_time=tw_time,
        us_time=us_time,
    )

    raw = _call_claude_api(prompt)

    if raw:
        parsed = _parse_ai_response(raw)

        if parsed:
            parsed.setdefault("conclusion", "目前無法取得明確結論，建議觀望。")
            parsed.setdefault("direction", "觀望")
            parsed.setdefault("reasons", [])
            parsed.setdefault("warnings", [])
            parsed.setdefault("newsHighlight", "暫無")
            parsed.setdefault("marketContext", "暫無")
            parsed.setdefault("suggestion", {
                "action": "觀望不操作",
                "timing": "等待方向明確",
                "stopLoss": "-",
                "takeProfit": "-",
                "size": "0 口",
            })
            parsed.setdefault("confidence", 0.1)
            parsed.setdefault("sessionNote", "請留意交易時段流動性變化")

            parsed["source"] = "claude_api"
            parsed["generatedAt"] = tw_time

            result = parsed
            logger.info("AI analysis generated via Claude API")

    # Fallback
    if not result:
        result = _generate_fallback_analysis(scores, news, market, rules, filter_status)
        result["generatedAt"] = tw_time
        logger.info("AI analysis generated via fallback rules")

    _ai_cache = result
    _ai_cache_time = time.time()

    return result