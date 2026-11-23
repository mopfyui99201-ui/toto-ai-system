"""totoGOAL3 / totoGOAL2 の得点予想（ポアソン分布・scipy）。

各チームの得点期待値λから「0点・1点・2点・3点以上」の確率％を scipy で算出し、
最も確率の高いカテゴリを最適な得点買い目として提示する。
"""

from __future__ import annotations

from scipy.stats import poisson

from config import GOAL_CATEGORIES
from predictor import expected_goals


def team_goal_distribution(lam: float) -> dict[str, float]:
    """1チームの0/1/2/3+得点確率（％）をポアソン分布で算出する。"""
    raw = [poisson.pmf(k, lam) for k in range(3)]
    raw.append(max(0.0, 1.0 - sum(raw)))          # 3点以上は裾確率
    total = sum(raw) or 1.0
    return {cat: round(p / total * 100, 1) for cat, p in zip(GOAL_CATEGORIES, raw, strict=True)}


def analyze_goal_match(match: dict) -> dict:
    """1試合（両チーム）の得点予想と最適買い目を算出する。"""
    lam_h, lam_a = expected_goals(match)
    bias = float(match.get("h2h_bias", 0.0))  # 直接対決の過去傾向（推定）をλへ反映
    if bias:
        lam_h, lam_a = max(0.2, lam_h * (1 + bias)), max(0.2, lam_a * (1 - bias))
    home_dist = team_goal_distribution(lam_h)
    away_dist = team_goal_distribution(lam_a)
    home_pick = max(GOAL_CATEGORIES, key=lambda c: home_dist[c])
    away_pick = max(GOAL_CATEGORIES, key=lambda c: away_dist[c])
    return {
        "match_no": match["match_no"],
        "home": match["home"], "away": match["away"],
        "date": match.get("date", ""), "time": match.get("time", ""),
        "stadium": match.get("stadium", ""),
        "lam_h": round(lam_h, 2), "lam_a": round(lam_a, 2),
        "home_dist": home_dist, "away_dist": away_dist,
        "home_pick": home_pick, "away_pick": away_pick,
        "confidence": round((home_dist[home_pick] + away_dist[away_pick]) / 2, 1),
    }


def analyze_goal_round(matches: list[dict]) -> list[dict]:
    return [analyze_goal_match(m) for m in matches]


def best_ticket(analyzed: list[dict]) -> dict:
    """最適な得点買い目（各チームの最有力カテゴリ）を1口ぶんにまとめる。"""
    marks = [
        {"match_no": a["match_no"], "home": a["home"], "away": a["away"],
         "home_pick": a["home_pick"], "away_pick": a["away_pick"],
         "confidence": a["confidence"]}
        for a in analyzed
    ]
    avg = round(sum(m["confidence"] for m in marks) / len(marks), 1) if marks else 0.0
    return {"marks": marks, "teams": len(marks) * 2, "confidence": avg}
