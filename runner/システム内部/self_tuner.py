"""答え合わせ履歴を使った AI 自己学習・勝率自動チューニング。

DBに登録済みの「予測確率(p1/p0/p2)」と「確定結果(1/0/2)」を突き合わせ、
ホーム勝ち・引き分け・アウェイ勝ちそれぞれの予測バイアスを分析する。

- 引き分けの過小/過大評価 → draw 係数
- ホーム/アウェイの勝率バイアス → home / away 係数
- 波乱（低確信での番狂わせ）の発生率も記録して可視化する

学習結果は ユーザー用データ/tuning.json へ保存し、
predictor.predict_match が次回予測へ自動反映する。
係数はサンプル数が少ないうちは縮小（shrinkage）して過学習を防ぐ。
"""

from __future__ import annotations

import json
import math
from datetime import datetime

import database as db
from config import GOAL_CATEGORIES, USER_DIR

TUNING_PATH = USER_DIR / "tuning.json"
MIN_SAMPLES = 8          # これ未満では係数をゼロのままにする
SHRINK_N = 30.0          # サンプル数による縮小係数の半減定数
MAX_SHIFT = 0.08         # 各結果への補正は ±8pt（確率）まで
GAIN = 0.7               # バイアス→補正への変換ゲイン
OUTCOMES = ("1", "0", "2")
# 結果 → 係数名 / 表示名
_META = {"1": ("home", "ホーム勝ち"), "0": ("draw", "引き分け"), "2": ("away", "アウェイ勝ち")}

def _default_goal_coeffs() -> dict:
    return {"home": dict.fromkeys(GOAL_CATEGORIES, 1.0),
            "away": dict.fromkeys(GOAL_CATEGORIES, 1.0)}


_cache: dict = {"mtime": None, "coeffs": {"draw": 0.0, "home": 0.0, "away": 0.0},
               "goal_coeffs": _default_goal_coeffs()}


def collect_judged() -> list[dict]:
    """予測と確定結果が両方ある試合を全開催回から集める。"""
    rows: list[dict] = []
    for r in db.list_rounds():
        rkey = r["round_no"]
        results = db.get_results(rkey)
        if not results:
            continue
        preds = db.get_predictions(rkey)
        matches = {m["match_no"]: m for m in db.get_matches(rkey)}
        for no, actual in results.items():
            p = preds.get(no)
            if not p or actual not in OUTCOMES:
                continue
            m = matches.get(no, {})
            rows.append({
                "round_no": rkey, "match_no": no, "actual": actual,
                "home": m.get("home", ""), "away": m.get("away", ""),
                "p1": float(p.get("p1") or 0), "p0": float(p.get("p0") or 0),
                "p2": float(p.get("p2") or 0), "predicted": p.get("predicted", ""),
            })
    return rows


def analyze(rows: list[dict]) -> dict:
    """的中履歴から予測バイアスと各種指標を算出する。"""
    n = len(rows)
    if not n:
        return {"n": 0}
    probs = {o: f"p{o}" for o in OUTCOMES}
    mean_pred = {o: sum(r[probs[o]] for r in rows) / n / 100.0 for o in OUTCOMES}
    actual_rate = {o: sum(1 for r in rows if r["actual"] == o) / n for o in OUTCOMES}
    bias = {o: actual_rate[o] - mean_pred[o] for o in OUTCOMES}
    hits = sum(1 for r in rows if r["predicted"] == r["actual"])
    # 波乱：AIが自信を持てなかった試合（本命確率55%未満）での番狂わせ
    upsets = sum(1 for r in rows
                 if r["predicted"] != r["actual"]
                 and max(r["p1"], r["p0"], r["p2"]) < 55)
    logloss = 0.0
    for r in rows:
        p = max(0.01, r[probs[r["actual"]]] / 100.0)
        logloss -= math.log(p)
    logloss /= n
    # チーム別残差（そのチームが絡む試合での「勝つ確率」の予実差）
    team_res: dict[str, list[float]] = {}
    for r in rows:
        for team, is_home in ((r["home"], True), (r["away"], False)):
            if not team:
                continue
            p_win = (r["p1"] if is_home else r["p2"]) / 100.0
            actual_win = 1.0 if ((is_home and r["actual"] == "1")
                                 or (not is_home and r["actual"] == "2")) else 0.0
            team_res.setdefault(team, []).append(actual_win - p_win)
    team_bias = sorted(
        ({"team": t, "bias_pt": round(sum(v) / len(v) * 100, 1), "n": len(v)}
         for t, v in team_res.items() if len(v) >= 3),
        key=lambda x: x["bias_pt"])
    return {
        "n": n,
        "accuracy": round(hits / n * 100, 1),
        "logloss": round(logloss, 4),
        "mean_pred": {o: round(mean_pred[o] * 100, 1) for o in OUTCOMES},
        "actual_rate": {o: round(actual_rate[o] * 100, 1) for o in OUTCOMES},
        "bias_pt": {o: round(bias[o] * 100, 1) for o in OUTCOMES},
        "upset_rate": round(upsets / n * 100, 1),
        "team_bias": team_bias,
    }


def _coefficients_from(metrics: dict) -> dict[str, float]:
    """分析結果から予測へ適用する補正係数（確率の加算値）を求める。"""
    coeffs = {"draw": 0.0, "home": 0.0, "away": 0.0}
    n = metrics.get("n", 0)
    if n < MIN_SAMPLES:
        return coeffs
    shrink = n / (n + SHRINK_N)
    for o, (name, _label) in _META.items():
        shift = metrics["bias_pt"][o] / 100.0 * GAIN * shrink
        coeffs[name] = round(max(-MAX_SHIFT, min(MAX_SHIFT, shift)), 4)
    return coeffs


def recompute() -> dict:
    """履歴を再分析して tuning.json を更新し、保存内容を返す。"""
    rows = collect_judged()
    metrics = analyze(rows)
    coeffs = _coefficients_from(metrics)
    goal_rows = collect_judged_goals()
    goal_metrics = analyze_goals(goal_rows)
    goal_coeffs = _goal_coefficients_from(goal_metrics)
    history = []
    if TUNING_PATH.exists():
        try:
            history = json.loads(TUNING_PATH.read_text(encoding="utf-8")).get("history", [])
        except (ValueError, OSError):
            history = []
    history.append({
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n": metrics.get("n", 0),
        "accuracy": metrics.get("accuracy"),
        "bias_pt": metrics.get("bias_pt", {}),
        "coeffs": coeffs,
        "goal_n": goal_metrics.get("n", 0),
        "goal_accuracy": goal_metrics.get("team_accuracy"),
    })
    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "min_samples": MIN_SAMPLES,
        "coefficients": coeffs,
        "metrics": metrics,
        "goal": {"coefficients": goal_coeffs, "metrics": goal_metrics},
        "history": history[-30:],
    }
    TUNING_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _cache.update({"mtime": TUNING_PATH.stat().st_mtime, "coeffs": dict(coeffs),
                   "goal_coeffs": goal_coeffs})
    return payload


def coefficients() -> dict[str, float]:
    """predictor が使う現在の補正係数（ファイル更新を検知して自動リロード）。"""
    try:
        mtime = TUNING_PATH.stat().st_mtime
    except OSError:
        return {"draw": 0.0, "home": 0.0, "away": 0.0}
    if _cache["mtime"] != mtime:
        try:
            data = json.loads(TUNING_PATH.read_text(encoding="utf-8"))
            coeffs = data.get("coefficients", {})
            _cache["coeffs"] = {k: float(coeffs.get(k, 0.0)) for k in ("draw", "home", "away")}
            goal = (data.get("goal") or {}).get("coefficients") or {}
            merged = _default_goal_coeffs()
            for side in ("home", "away"):
                for cat in GOAL_CATEGORIES:
                    if cat in (goal.get(side) or {}):
                        merged[side][cat] = float(goal[side][cat])
            _cache["goal_coeffs"] = merged
        except (ValueError, OSError, TypeError):
            pass
        _cache["mtime"] = mtime
    return dict(_cache["coeffs"])


def goal_coefficients() -> dict:
    """得点予想へ適用するカテゴリ別乗数（predictor/goal_predictor が使用）。"""
    coefficients()  # mtimeキャッシュを最新化するため呼ぶ
    return _cache["goal_coeffs"]


def summary() -> dict:
    """UI・公開JSON用の学習状態サマリ（無ければ再計算せず現状を返す）。"""
    if TUNING_PATH.exists():
        try:
            return json.loads(TUNING_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {"updated_at": "", "coefficients": {"draw": 0.0, "home": 0.0, "away": 0.0},
            "metrics": {"n": 0}, "history": []}


# --- 得点予想（totoGOAL3 / totoGOAL2）の自己学習 ------------------------------
GOAL_MIN_SAMPLES = 8
GOAL_MAX_MULT = 0.4        # カテゴリ補正は ×0.6〜×1.4 の範囲
GOAL_SIDE = (("home", "home_dist", "home_pick"), ("away", "away_dist", "away_pick"))


def _parse_goal_actual(actual: str) -> tuple[str, str] | None:
    """"1-2" / "3+-0" 形式の実結果を (ホーム得点cat, アウェイ得点cat) へ分解する。"""
    if not actual or "-" not in actual:
        return None
    h, a = actual.split("-", 1)
    if h in GOAL_CATEGORIES and a in GOAL_CATEGORIES:
        return h, a
    return None


def collect_judged_goals() -> list[dict]:
    """得点予想の的中履歴を集める（結果は "H-A" カテゴリ形式で保存されている前提）。"""
    import goal_predictor  # 循環import回避のため関数内で読み込む

    rows: list[dict] = []
    for r in db.list_rounds():
        rkey = r["round_no"]
        if "GOAL" not in rkey:
            continue
        results = db.get_results(rkey)
        if not results:
            continue
        matches = db.get_matches(rkey)
        if not matches:
            continue
        analyzed = {a["match_no"]: a for a in goal_predictor.analyze_goal_round(matches)}
        for no, actual in results.items():
            parsed = _parse_goal_actual(actual)
            a = analyzed.get(no)
            if not parsed or not a:
                continue
            rows.append({
                "round_no": rkey, "match_no": no,
                "actual_h": parsed[0], "actual_a": parsed[1],
                "home_dist": a["home_dist"], "away_dist": a["away_dist"],
                "home_pick": a["home_pick"], "away_pick": a["away_pick"],
            })
    return rows


def analyze_goals(rows: list[dict]) -> dict:
    """得点予想のカテゴリ別バイアス・的中率を算出する。"""
    teams = []
    for r in rows:
        for side, dist_k, pick_k, act_k in (
            ("home", "home_dist", "home_pick", "actual_h"),
            ("away", "away_dist", "away_pick", "actual_a"),
        ):
            teams.append({"side": side, "dist": r[dist_k], "pick": r[pick_k],
                          "actual": r[act_k]})
    n = len(teams)
    if not n:
        return {"n": 0, "matches": 0}
    bias: dict[str, dict[str, float]] = {}
    rates: dict[str, dict[str, float]] = {}
    for side in ("home", "away"):
        subset = [t for t in teams if t["side"] == side]
        m = len(subset)
        bias[side] = {}
        rates[side] = {}
        for cat in GOAL_CATEGORIES:
            mean_pred = sum(t["dist"][cat] for t in subset) / m
            actual_rate = sum(1 for t in subset if t["actual"] == cat) / m * 100
            bias[side][cat] = round(actual_rate - mean_pred, 1)
            rates[side][cat] = round(actual_rate, 1)
    hits = sum(1 for t in teams if t["pick"] == t["actual"])
    exact = sum(1 for r in rows
                if r["home_pick"] == r["actual_h"] and r["away_pick"] == r["actual_a"])
    return {
        "n": n, "matches": len(rows),
        "team_accuracy": round(hits / n * 100, 1),
        "exact_accuracy": round(exact / len(rows) * 100, 1),
        "bias_pt": bias, "actual_rate": rates,
    }


def _goal_coefficients_from(metrics: dict) -> dict:
    """得点カテゴリ別の予測バイアスから確率乗数（1.0基準）を求める。"""
    coeffs = _default_goal_coeffs()
    n = metrics.get("n", 0)
    if n < GOAL_MIN_SAMPLES:
        return coeffs
    shrink = n / (n + SHRINK_N)
    for side in ("home", "away"):
        for cat in GOAL_CATEGORIES:
            bias_frac = metrics["bias_pt"][side][cat] / 100.0
            mult = 1.0 + bias_frac * GAIN * shrink
            coeffs[side][cat] = round(max(1 - GOAL_MAX_MULT, min(1 + GOAL_MAX_MULT, mult)), 4)
    return coeffs


def safe_recompute() -> dict:
    """例外を外へ出さず学習を更新する（予測パイプラインから呼ぶ用）。"""
    try:
        return recompute()
    except Exception:
        return summary()
