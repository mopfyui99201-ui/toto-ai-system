"""波乱（大穴）期待値アラートと引き分け特化判定。

- AI算出勝率と公式投票率のギャップから「波乱狙い目」を自動判定する。
- 両チームのドロー率・得失点の均衡度から「引き分け危険度スコア」を独立算出する。
"""

from __future__ import annotations

from config import OUTCOMES, UPSET_GAP_THRESHOLD


def gap_analysis(match: dict, pred: dict) -> dict:
    """AI勝率と公式投票率の乖離を試合単位で集計する。"""
    ai = {"1": pred["p1"], "0": pred["p0"], "2": pred["p2"]}
    vote = {
        "1": match.get("vote_1", 0.0),
        "0": match.get("vote_0", 0.0),
        "2": match.get("vote_2", 0.0),
    }
    gaps = {o: round(ai[o] - vote[o], 1) for o in OUTCOMES}
    ai_top = max(OUTCOMES, key=lambda o: ai[o])
    vote_top = max(OUTCOMES, key=lambda o: vote[o])
    # 大衆が本命視する結果をAIが否定し、かつ乖離が大きい試合を波乱と判定
    largest_gap_outcome = max(OUTCOMES, key=lambda o: gaps[o])
    upset = (
        ai_top != vote_top
        and gaps[ai_top] >= UPSET_GAP_THRESHOLD * 0.5
        and vote[vote_top] - ai[vote_top] >= UPSET_GAP_THRESHOLD
    )
    note = ""
    if upset:
        note = (
            f"大衆は「{vote_top}」に{vote[vote_top]:.0f}%と偏重。"
            f"AIは「{ai_top}」を{ai[ai_top]:.0f}%と評価（乖離{gaps[ai_top]:+.0f}pt）。"
            f"波乱狙いで高配当期待。"
        )
    return {
        "gaps": gaps,
        "ai_top": ai_top, "vote_top": vote_top,
        "upset": upset, "note": note,
        "largest_gap_outcome": largest_gap_outcome,
        "max_gap": gaps[largest_gap_outcome],
    }


def draw_risk_score(match: dict, pred: dict) -> float:
    """引き分け危険度スコア（0〜100）。高いほど「0」本命度が高い。"""
    lam_h = pred.get("lam_h", 1.2)
    lam_a = pred.get("lam_a", 1.2)
    balance = 1 - abs(lam_h - lam_a) / max(0.5, lam_h + lam_a)          # 実力均衡度
    total_goals = lam_h + lam_a
    low_scoring = max(0.0, 1 - total_goals / 3.0)                        # 低得点ほどドロー
    draw_rate = (
        match.get("home_draw_rate", 0.25) + match.get("away_draw_rate", 0.25)
    ) / 2
    ai_draw = pred.get("p0", 25.0) / 100
    score = 100 * (
        0.30 * balance + 0.25 * low_scoring + 0.25 * min(1.0, draw_rate / 0.30) + 0.20 * ai_draw
    )
    return round(min(100.0, max(0.0, score)), 1)


def analyze(matches: list[dict], preds: list[dict]) -> list[dict]:
    """波乱・引き分け判定を全試合へ適用して統合する。"""
    by_no = {p["match_no"]: p for p in preds}
    out: list[dict] = []
    for m in matches:
        pred = by_no[m["match_no"]]
        gap = gap_analysis(m, pred)
        merged = dict(pred)
        merged.update({
            "match_no": m["match_no"],
            "home": m["home"], "away": m["away"],
            "vote_1": m.get("vote_1", 0.0),
            "vote_0": m.get("vote_0", 0.0),
            "vote_2": m.get("vote_2", 0.0),
            "draw_risk": draw_risk_score(m, pred),
            "upset": gap["upset"], "upset_note": gap["note"],
            "gaps": gap["gaps"],
        })
        out.append(merged)
    return out


def upset_ranking(analyzed: list[dict], top: int = 5) -> list[dict]:
    """波乱期待度の高い順に並べる。"""
    ranked = sorted(analyzed, key=lambda a: (a["upset"], a["gaps"][a["predicted"]]), reverse=True)
    return ranked[:top]


def draw_ranking(analyzed: list[dict], top: int = 5) -> list[dict]:
    """引き分け危険度の高い順に並べる。"""
    return sorted(analyzed, key=lambda a: a["draw_risk"], reverse=True)[:top]
