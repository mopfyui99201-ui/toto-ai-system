"""最新ニュース検索による自動補正（怪我・出場停止・連戦疲労）。

ddgs（旧 duckduckgo-search）で無料・キーなしに両チームの直近ニュースを検索し、
キーワード解析で勝率への補正量（shift）と根拠テキストを生成する。
"""

from __future__ import annotations

import functools

# キーワード辞書（日本語）
NEGATIVE = {
    "injury": (["怪我", "負傷", "欠場", "離脱", "コンディション不良", "全治"], -0.10),
    "suspension": (["出場停止", "累積警告", "退場"], -0.08),
    "fatigue": (["連戦", "中2日", "中3日", "過密日程", "疲労"], -0.04),
    "poor_form": (["不振", "未勝利", "連敗"], -0.05),
}
POSITIVE = {
    "return": (["復帰", "先発復帰", "実戦復帰", "再起"], 0.07),
    "good_form": (["連勝", "好調", "無敗"], 0.05),
    "reinforce": (["新加入", "補強", "加入"], 0.03),
}


@functools.lru_cache(maxsize=64)
def search_news(query: str, max_results: int = 5) -> tuple[str, ...]:
    """DuckDuckGo でニュース見出しを検索して返す（失敗時は空）。"""
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            hits = ddgs.news(query, max_results=max_results, region="jp-jp")
            return tuple(f"{h.get('title', '')} {h.get('body', '')}" for h in hits)
    except Exception:
        return ()


def _scan(text: str, dictionary: dict) -> tuple[float, list[str]]:
    shift = 0.0
    reasons: list[str] = []
    for label, (words, delta) in dictionary.items():
        hit = [w for w in words if w in text]
        if hit:
            shift += delta
            reasons.append(f"{label}({hit[0]})")
    return shift, reasons


def analyze_team(team: str) -> dict:
    """1チームのニュースを解析して補正量と根拠を返す。"""
    text = " ".join(search_news(f"{team} 最新 ニュース 怪我 出場停止"))
    neg, neg_reasons = _scan(text, NEGATIVE)
    pos, pos_reasons = _scan(text, POSITIVE)
    return {
        "team": team,
        "shift": round(neg + pos, 3),
        "reasons": neg_reasons + pos_reasons,
        "has_news": bool(text),
    }


def analyze_match(match: dict) -> dict:
    """1試合（ホーム・アウェイ）のニュース補正を統合する。"""
    home = analyze_team(match["home"])
    away = analyze_team(match["away"])
    # ホームに不利な情報は shift を下げ、アウェイに不利なら shift を上げる
    shift = home["shift"] - away["shift"]
    notes: list[str] = []
    if home["reasons"]:
        notes.append(f"【{match['home']}】" + "・".join(home["reasons"]))
    if away["reasons"]:
        notes.append(f"【{match['away']}】" + "・".join(away["reasons"]))
    return {
        "match_no": match["match_no"],
        "shift": round(max(-0.25, min(0.25, shift)), 3),
        "note": " / ".join(notes) if notes else "特筆すべき最新情報なし",
    }


def analyze_round(matches: list[dict], enabled: bool = True) -> dict[int, dict]:
    """開催回全試合のニュース補正を生成する。"""
    if not enabled:
        return {}
    return {m["match_no"]: analyze_match(m) for m in matches}
