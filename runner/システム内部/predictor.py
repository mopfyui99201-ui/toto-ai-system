"""多角的な勝敗予想アルゴリズム（1 / 0 / 2 の確率算出）。

ポアソン分布による得点期待値シミュレーションと LightGBM を組み合わせ、
各試合のホーム勝ち(1)・引き分け(0)・アウェイ勝ち(2) の勝率％を算出する。
"""

from __future__ import annotations

import contextlib
import functools
from math import exp, factorial

import numpy as np

import self_tuner
from config import HOME_ADVANTAGE, LEAGUE_AVG_GOALS, MODEL_PATH, OUTCOME_LABEL, OUTCOMES

LABEL_INDEX = {"1": 0, "0": 1, "2": 2}


# --- ポアソンによる本質確率 ----------------------------------------------
def expected_goals(match: dict) -> tuple[float, float]:
    """両チームの得点期待値（λ）を算出する。"""
    atk_h = match.get("home_gf", 1.3) / LEAGUE_AVG_GOALS
    def_a = match.get("away_ga", 1.3) / LEAGUE_AVG_GOALS
    atk_a = match.get("away_gf", 1.3) / LEAGUE_AVG_GOALS
    def_h = match.get("home_ga", 1.3) / LEAGUE_AVG_GOALS
    lam_h = LEAGUE_AVG_GOALS * atk_h * def_a * (1 + HOME_ADVANTAGE)
    lam_a = LEAGUE_AVG_GOALS * atk_a * def_h * (1 - HOME_ADVANTAGE * 0.5)
    return max(0.2, lam_h), max(0.2, lam_a)


def poisson_probs(lam_h: float, lam_a: float, max_goals: int = 8) -> dict[str, float]:
    """ポアソン分布の同時確率行列から 1/0/2 の勝率を求める。"""
    def p(k: int, lam: float) -> float:
        return exp(-lam) * lam ** k / factorial(k)

    home = draw = away = 0.0
    for i in range(max_goals):
        pi = p(i, lam_h)
        for j in range(max_goals):
            prob = pi * p(j, lam_a)
            if i > j:
                home += prob
            elif i == j:
                draw += prob
            else:
                away += prob
    total = home + draw + away
    return {"1": home / total, "0": draw / total, "2": away / total}


# --- LightGBM による補正 --------------------------------------------------
def feature_vector(match: dict) -> list[float]:
    lam_h, lam_a = expected_goals(match)
    return [
        float(match.get("home_rank", 10) - match.get("away_rank", 10)),
        float(match.get("home_win_rate", 0.45) - match.get("away_win_rate", 0.45)),
        float(match.get("home_recent_form", 0.5) - match.get("away_recent_form", 0.5)),
        lam_h, lam_a, lam_h - lam_a,
        float(match.get("home_draw_rate", 0.25) + match.get("away_draw_rate", 0.25)) / 2,
    ]


def _synthetic_training(seed: int = 7, n: int = 800) -> tuple[np.ndarray, np.ndarray]:
    """ラベル付き実データが無い初回でも学習できるよう合成標本を生成する。"""
    from data_fetcher import TEAMS, _team_stats

    rng = _np_random(seed)
    import random as _random

    py_rng = _random.Random(seed)
    xs: list[list[float]] = []
    ys: list[int] = []
    for _ in range(n):
        (_, hs_str), (_, as_str) = py_rng.sample(TEAMS, 2)
        hs = _team_stats(hs_str, py_rng)
        as_ = _team_stats(as_str, py_rng)
        match = {
            "home_rank": int(20 - hs_str / 4.0), "away_rank": int(20 - as_str / 4.0),
            "home_gf": hs["gf"], "home_ga": hs["ga"], "away_gf": as_["gf"], "away_ga": as_["ga"],
            "home_win_rate": hs["win_rate"], "away_win_rate": as_["win_rate"],
            "home_recent_form": hs["recent_form"], "away_recent_form": as_["recent_form"],
            "home_draw_rate": hs["draw_rate"], "away_draw_rate": as_["draw_rate"],
        }
        lam_h, lam_a = expected_goals(match)
        probs = poisson_probs(lam_h, lam_a)
        order = OUTCOMES
        ys.append(LABEL_INDEX[rng.choice(order, p=[probs[o] for o in order])])
        xs.append(feature_vector(match))
    return np.array(xs), np.array(ys)


def _np_random(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


@functools.lru_cache(maxsize=1)
def get_model():
    """LightGBM モデルを学習（または読込）して返す。失敗時は None。"""
    try:
        import lightgbm as lgb

        model_file = str(MODEL_PATH)
        if MODEL_PATH.exists():
            return lgb.Booster(model_file=model_file)
        xs, ys = _synthetic_training()
        train = lgb.Dataset(xs, label=ys)
        params = {
            "objective": "multiclass", "num_class": 3, "learning_rate": 0.08,
            "num_leaves": 15, "min_data_in_leaf": 10, "verbose": -1,
            "seed": 7, "deterministic": True, "num_threads": 2,
        }
        booster = lgb.train(params, train, num_boost_round=60)
        with contextlib.suppress(Exception):
            booster.save_model(str(MODEL_PATH))
        return booster
    except Exception:
        return None


def predict_match(match: dict, news_adjust: dict | None = None) -> dict:
    """1試合の勝率（1/0/2）を算出する。news_adjust で補正する。"""
    lam_h, lam_a = expected_goals(match)
    base = poisson_probs(lam_h, lam_a)

    model = get_model()
    if model is not None:
        try:
            lgb_probs = model.predict([feature_vector(match)])[0]
            probs = {o: 0.7 * base[o] + 0.3 * float(lgb_probs[LABEL_INDEX[o]]) for o in OUTCOMES}
        except Exception:
            probs = dict(base)
    else:
        probs = dict(base)

    probs = _apply_news(probs, news_adjust)
    probs = _apply_h2h(probs, match)
    probs = _apply_weights(probs, match)
    probs = _apply_learning(probs)
    total = sum(probs.values())
    probs = {o: probs[o] / total for o in OUTCOMES}
    predicted = max(OUTCOMES, key=lambda o: probs[o])
    return {
        "match_no": match["match_no"],
        "p1": round(probs["1"] * 100, 1),
        "p0": round(probs["0"] * 100, 1),
        "p2": round(probs["2"] * 100, 1),
        "lam_h": round(lam_h, 2), "lam_a": round(lam_a, 2),
        "predicted": predicted,
    }


def _apply_news(probs: dict[str, float], news_adjust: dict | None) -> dict[str, float]:
    """最新ニュース（怪我・出場停止・連戦疲労）を勝率へ反映する。"""
    if not news_adjust:
        return probs
    out = dict(probs)
    shift = float(news_adjust.get("shift", 0.0))  # 正: ホーム有利 / 負: アウェイ有利
    out["1"] *= 1 + shift
    out["2"] *= 1 - shift
    out["0"] *= 1 + abs(shift) * 0.3
    if news_adjust.get("rain"):  # 雨天はロースコア・引き分け増
        out["0"] *= 1.10
        out["1"] *= 0.96
        out["2"] *= 0.96
    return out


def _apply_weights(probs: dict[str, float], match: dict) -> dict[str, float]:
    """確定結果の蓄積から学習した特徴量重みモデルを勝率へブレンドする。

    self_tuner.learn_feature_weights() が tuning.json の "weights" を更新するたび
    最新モデルを使う。サンプル数に応じてブレンド率を 0〜35% へ縮小する。
    """
    w = self_tuner.feature_weights()
    if not w or not w.get("ready"):
        return probs
    try:
        import numpy as np

        x = np.asarray(self_tuner.match_weight_features(match), dtype=float)
        xs = (x - np.asarray(w["mean"])) / np.asarray(w["std"])
        xb = np.append(xs, 1.0)
        logits = xb @ np.asarray(w["W"])
        logits -= logits.max()
        exp = np.exp(logits)
        model = exp / exp.sum()
        n = float(w.get("n", 0))
        alpha = 0.35 * n / (n + 60.0)
        return {o: probs[o] * (1 - alpha) + float(model[i]) * alpha
                for i, o in enumerate(OUTCOMES)}
    except Exception:
        return probs


def _apply_learning(probs: dict[str, float]) -> dict[str, float]:
    """答え合わせ履歴から学習した自己チューニング係数を勝率へ加算する。

    self_tuner.recompute() が tuning.json を更新するたび自動で最新係数を使う。
    """
    coeffs = self_tuner.coefficients()
    if not any(coeffs.values()):
        return probs
    key = {"1": "home", "0": "draw", "2": "away"}
    return {o: max(0.005, probs[o] + float(coeffs.get(key[o], 0.0))) for o in OUTCOMES}


def _apply_h2h(probs: dict[str, float], match: dict) -> dict[str, float]:
    """直接対決の過去傾向（推定）を勝率へ反映する。"""
    bias = float(match.get("h2h_bias", 0.0))  # 正: ホーム有利 / 負: アウェイ有利
    if not bias:
        return probs
    out = dict(probs)
    out["1"] *= 1 + bias
    out["2"] *= 1 - bias
    return out


def match_reason(a: dict) -> str:
    """予測根拠を簡潔な日本語1〜2行で生成する（UI表示用）。

    a は predict_match/analyze の戻り値（lam_h/lam_a/p1/p0/p2/predicted/
    draw_risk/upset/upset_note/news_note を参照）。
    """
    parts: list[str] = []
    lh, la = a.get("lam_h"), a.get("lam_a")
    if lh and la:
        if lh > la * 1.15:
            parts.append(f"得点期待値はホーム有利（{lh}−{la}）")
        elif la > lh * 1.15:
            parts.append(f"得点期待値はアウェイ有利（{lh}−{la}）")
        else:
            parts.append("得点期待値は拮抗")
    probs = {"1": a.get("p1", 0), "0": a.get("p0", 0), "2": a.get("p2", 0)}
    top_o = a.get("predicted") or max(OUTCOMES, key=lambda o: probs[o])
    top_p = probs.get(top_o, 0)
    if top_p >= 55:
        parts.append(f"本命{OUTCOME_LABEL[top_o]}が{top_p}%で優位")
    elif top_p < 40:
        parts.append("三つ巴の接戦")
    if a.get("upset"):
        parts.append("波乱警戒：波乱狙い目")
    if (a.get("draw_risk") or 0) >= 70:
        parts.append("引分危険度が高い")
    if probs.get("0", 0) >= 30:
        parts.append(f"引き分け確率{probs['0']}%と高め")
    note = (a.get("news_note") or "").strip()
    if note and "なし" not in note and "特筆" not in note:
        parts.append("ニュース・天候補正あり")
    return "／".join(parts[:4]) if parts else "統計モデルによる予測"


def predict_round(matches: list[dict], news_map: dict[int, dict] | None = None) -> list[dict]:
    news_map = news_map or {}
    return [predict_match(m, news_map.get(m["match_no"])) for m in matches]
