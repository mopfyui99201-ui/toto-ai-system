"""AI推奨買い目の「通算収支・回収率トラッカー」。

確定結果が登録済みの勝敗系開催回について、DBに保存された予測・対戦カード・
結果から「予算1,000円のAI最適買い目を毎回買い続けた場合」の投資額・払戻額・
回収率を試算集計する。配当金は公式の1〜3等払戻（payouts テーブル）を使用し、
未取得の回は払戻0円として扱う（画面上は「配当未取得」と明示する）。

等級判定は「フォーメーションが完全中を含むか」「期待的中数」で近似した試算値。
"""

from __future__ import annotations

import database as db
from config import OUTCOMES
from optimizer import MODE_STEADY, optimize

TRACKER_BUDGET = 1000


def _round_rows(round_key: str) -> list[dict]:
    """DBの matches+predictions から optimizer が使える analyzed 形式を再構成する。"""
    preds = db.get_predictions(round_key)
    rows = []
    for m in db.get_matches(round_key):
        p = preds.get(m["match_no"])
        if not p:
            continue
        rows.append({
            "match_no": m["match_no"], "home": m["home"], "away": m["away"],
            "p1": float(p["p1"]), "p0": float(p["p0"]), "p2": float(p["p2"]),
            "vote_1": m.get("vote_1", 0.0), "vote_0": m.get("vote_0", 0.0),
            "vote_2": m.get("vote_2", 0.0),
        })
    return rows


def _grade_payout(hits_expected: float, total: int, payouts: dict | None) -> tuple[str, int]:
    """期待的中数から等級と払戻額を試算する（未取得時は0円）。"""
    if not payouts:
        return "", 0
    if hits_expected >= total:
        grade, key = "1等", "1"
    elif hits_expected >= total - 1:
        grade, key = "2等", "2"
    elif hits_expected >= total - 2:
        grade, key = "3等", "3"
    else:
        return "", 0
    return grade, int(payouts.get(key) or 0)


def round_tracker(round_key: str, budget: int = TRACKER_BUDGET) -> dict | None:
    """1開催回のAI買い目シミュレーション結果を返す（結果未確定なら None）。"""
    results = db.get_results(round_key)
    rows = _round_rows(round_key)
    if not results or not rows:
        return None
    judged = {m["match_no"]: results[m["match_no"]] for m in rows
              if m["match_no"] in results and results[m["match_no"]] in OUTCOMES}
    if len(judged) < len(rows):
        return None  # 一部未確定の回は集計対象外
    plan = optimize(rows, budget=budget, mode=MODE_STEADY)
    covered = 0
    expected_hits = 0.0
    for m in plan["marks"]:
        actual = judged.get(m["match_no"])
        if actual in m["outcomes"]:
            covered += 1
            expected_hits += 1.0 / len(m["outcomes"])
    total = len(rows)
    payouts = db.get_payouts(round_key.split("｜")[0])
    grade, payout = _grade_payout(expected_hits, total, payouts)
    return {
        "round_no": round_key, "lines": plan["lines"], "cost": plan["cost"],
        "covered": covered, "total": total,
        "expected_hits": round(expected_hits, 1),
        "grade": grade, "payout": payout,
        "has_payout": bool(payouts),
    }


def summary() -> dict:
    """全勝敗系開催回の通算収支サマリを返す。"""
    rounds = []
    for r in db.list_rounds():
        rkey = r["round_no"]
        if "GOAL" in rkey:
            continue
        t = round_tracker(rkey)
        if t:
            t["held_date"] = r.get("held_date", "")
            rounds.append(t)
    rounds.sort(key=lambda x: x["round_no"])
    inv = sum(t["cost"] for t in rounds)
    pay = sum(t["payout"] for t in rounds)
    return {
        "budget": TRACKER_BUDGET,
        "rounds": rounds[-30:],
        "totals": {
            "investment": inv, "payout": pay,
            "recovery": round(pay / inv * 100, 1) if inv else None,
            "rounds": len(rounds),
        },
    }
