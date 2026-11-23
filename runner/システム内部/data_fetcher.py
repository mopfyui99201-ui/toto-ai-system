"""対戦カードのサンプル自動生成（ルール22）。

初回起動でも即動作確認できるようリアルなサンプルを自動生成する。
実際に販売・開催中の最新データのライブ取得は live_fetcher.py が担当する。
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta

from config import GAME_TYPES, LEAGUE_AVG_GOALS, SAMPLE_PATH, SOURCE_OFFICIAL_TOP, TOTO

# J1 クラブとベース強度（攻撃力・守備力の基準値）
TEAMS: list[tuple[str, int]] = [
    ("浦和レッズ", 78), ("川崎フロンターレ", 83), ("横浜F・マリノス", 80),
    ("鹿島アントラーズ", 76), ("ヴィッセル神戸", 79), ("名古屋グランパス", 72),
    ("セレッソ大阪", 73), ("FC東京", 70), ("ガンバ大阪", 69),
    ("サンフレッチェ広島", 74), ("アビスパ福岡", 66), ("北海道コンサドーレ札幌", 62),
    ("湘南ベルマーレ", 64), ("柏レイソル", 68), ("京都サンガF.C.", 63),
    ("アルビレックス新潟", 64), ("ジュビロ磐田", 61), ("東京ヴェルディ", 65),
    ("FC町田ゼルビア", 67), ("サガン鳥栖", 60), ("清水エスパルス", 68),
    ("ベガルタ仙台", 66), ("ファジアーノ岡山", 64), ("モンテディオ山形", 60),
    ("大分トリニータ", 59), ("レノファ山口", 58), ("横浜FC", 57),
]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _team_stats(strength: int, rng: random.Random) -> dict:
    """クラブ強度から直近成績・得失点・ドロー率などの統計を生成する。"""
    attack = _clamp(strength / 72.0 + rng.uniform(-0.10, 0.10), 0.55, 1.7)
    defense = _clamp((150 - strength) / 72.0 + rng.uniform(-0.10, 0.10), 0.45, 1.6)
    return {
        "gf": round(LEAGUE_AVG_GOALS * attack, 2),
        "ga": round(LEAGUE_AVG_GOALS * defense, 2),
        "win_rate": round(_clamp((strength - 52) / 42.0 + rng.uniform(-0.05, 0.05), 0.10, 0.85), 2),
        "home_win_rate": round(_clamp((strength - 52) / 40.0 + rng.uniform(0.02, 0.12), 0.12, 0.90), 2),
        "away_win_rate": round(_clamp((strength - 58) / 46.0 + rng.uniform(-0.06, 0.06), 0.05, 0.70), 2),
        "draw_rate": round(_clamp(0.30 - abs(strength - 70) / 160.0 + rng.uniform(-0.03, 0.03), 0.10, 0.36), 2),
        "recent_form": round(_clamp(0.5 + (strength - 70) / 60.0 + rng.uniform(-0.18, 0.18), 0.05, 0.95), 2),
    }


def _poisson_outcome(lam_h: float, lam_a: float) -> tuple[float, float, float]:
    """簡易ポアソンで公式投票率の土台となる本質確率を求める。"""
    from math import exp, factorial

    def p(k: int, lam: float) -> float:
        return exp(-lam) * lam ** k / factorial(k)

    home = draw = away = 0.0
    for i in range(8):
        for j in range(8):
            prob = p(i, lam_h) * p(j, lam_a)
            if i > j:
                home += prob
            elif i == j:
                draw += prob
            else:
                away += prob
    total = home + draw + away
    return home / total, draw / total, away / total


def generate_sample_round(round_no: str | None = None, seed: int = 2026,
                          game_type: str = TOTO) -> dict:
    """リアルなサンプル開催回を生成する（ルール22）。

    game_type により試合数が変わる（toto=13 / mini toto=5 / GOAL3=3 / GOAL2=2）。
    """
    n = GAME_TYPES[game_type]["matches"]
    if round_no is None:
        round_no = f"サンプル 第1440回（{game_type}）"
    rng = random.Random(seed)
    pool = rng.sample(TEAMS, 2 * n)
    base_day = date.today() + timedelta(days=5)
    matches: list[dict] = []
    for i in range(n):
        home_name, home_str = pool[i]
        away_name, away_str = pool[2 * n - 1 - i]
        hs = _team_stats(home_str, rng)
        as_ = _team_stats(away_str, rng)
        # 本質確率（ポアソン）→ 公式投票率（大衆の偏り＋ノイズを上乗せ）
        lam_h = LEAGUE_AVG_GOALS * (hs["gf"] / LEAGUE_AVG_GOALS) * (as_["ga"] / LEAGUE_AVG_GOALS) * 1.15
        lam_a = LEAGUE_AVG_GOALS * (as_["gf"] / LEAGUE_AVG_GOALS) * (hs["ga"] / LEAGUE_AVG_GOALS) * 0.90
        fair = _poisson_outcome(lam_h, lam_a)
        bias = {1: 1 + (home_str - away_str) / 120.0, 0: 0.85, 2: 1 - (home_str - away_str) / 160.0}
        votes = [max(0.02, fair[k] * bias[k] * rng.uniform(0.9, 1.12)) for k in range(3)]
        vsum = sum(votes)
        votes = [v / vsum * 100 for v in votes]
        matches.append({
            "match_no": i + 1,
            "home": home_name,
            "away": away_name,
            "date": base_day.strftime("%m/%d"), "time": "14:00", "stadium": "サンプル競技場",
            "home_rank": int(_clamp(20 - home_str / 4.0 + rng.randint(-2, 2), 1, 20)),
            "away_rank": int(_clamp(20 - away_str / 4.0 + rng.randint(-2, 2), 1, 20)),
            "home_gf": hs["gf"], "home_ga": hs["ga"],
            "away_gf": as_["gf"], "away_ga": as_["ga"],
            "home_win_rate": hs["win_rate"], "away_win_rate": as_["win_rate"],
            "home_home_win_rate": hs["home_win_rate"], "away_away_win_rate": as_["away_win_rate"],
            "home_draw_rate": hs["draw_rate"], "away_draw_rate": as_["draw_rate"],
            "home_recent_form": hs["recent_form"], "away_recent_form": as_["recent_form"],
            "vote_1": round(votes[0], 1), "vote_0": round(votes[1], 1), "vote_2": round(votes[2], 1),
        })
    return {
        "round_no": round_no,
        "held_date": base_day.isoformat(),
        "source": "sample",
        "game_type": game_type,
        "sources": [{"label": "サンプルデータ（初回起動時に自動生成）", "url": SOURCE_OFFICIAL_TOP}],
        "notes": "サンプルデータです。上部の「🔄 最新情報を取得」で実際の開催情報に更新できます。",
        "matches": matches,
    }


def save_sample(payload: dict) -> None:
    SAMPLE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_sample() -> dict | None:
    if SAMPLE_PATH.exists():
        return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    return None
