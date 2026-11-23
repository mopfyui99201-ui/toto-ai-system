"""予算別・買い目最適化（数理最適化ロジック）。

PuLP の混合整数計画（MILP）で「どの試合をシングル/ダブル/トリプルにするか」
を決定し、予算上限内で的中期待度が最大になる買い目を提案する。
対数線形化（Σ ln(k_i) ≦ ln(口数上限)）により口数積の制約を線形に扱う。
求解に失敗した場合は貪欲法へ自動フォールバックする（ルール4）。
"""

from __future__ import annotations

from math import log

from config import GOAL_CATEGORIES, OUTCOMES, TICKET_UNIT

MODE_STEADY = "本命堅実モード"
MODE_UPSET = "高配当波乱狙いモード"
MODE_AUTO = "自動（キャリーオーバー連動）"


def auto_mode(carryover: str) -> str:
    """キャリーオーバー発生時は波乱狙い（高配当・穴厚め）、通常時は堅実回収を選ぶ。"""
    return MODE_UPSET if carryover else MODE_STEADY


def resolve_mode(mode: str, carryover: str = "") -> str:
    """MODE_AUTO を実際のモードへ解決する。"""
    return auto_mode(carryover) if mode == MODE_AUTO else mode


def _utility(match: dict, outcome: str, mode: str) -> float:
    p = {"1": match["p1"], "0": match["p0"], "2": match["p2"]}[outcome] / 100
    if mode == MODE_UPSET:
        vote = {"1": match.get("vote_1", 0), "0": match.get("vote_0", 0), "2": match.get("vote_2", 0)}[outcome] / 100
        return p + 0.5 * max(0.0, p - vote)
    return p


def _decode(analyzed: list[dict], x, count_by_match: dict[int, int], mode: str) -> dict:
    marks = []
    coverage = 1.0
    for a in analyzed:
        no = a["match_no"]
        k = max(1, count_by_match[no])
        scored = sorted(OUTCOMES, key=lambda o: _utility(a, o, mode), reverse=True)
        picked = scored[:k]
        cov = sum({"1": a["p1"], "0": a["p0"], "2": a["p2"]}[o] for o in picked) / 100
        coverage *= cov
        marks.append({
            "match_no": no, "home": a["home"], "away": a["away"],
            "outcomes": sorted(picked, key=lambda o: OUTCOMES.index(o)),
            "type": {1: "シングル", 2: "ダブル", 3: "トリプル"}[k],
        })
    lines = 1
    for m in marks:
        lines *= len(m["outcomes"])
    return {
        "marks": marks, "lines": lines, "cost": lines * TICKET_UNIT,
        "coverage": round(coverage * 100, 4), "mode": mode,
    }


def optimize(analyzed: list[dict], budget: int = 1000, mode: str = MODE_STEADY) -> dict:
    """予算・モードから最適な買い目を算出する。"""
    max_lines = max(1, int(budget) // TICKET_UNIT)
    try:
        result = _optimize_milp(analyzed, max_lines, mode)
        if result:
            return result
    except Exception:
        pass
    return _optimize_greedy(analyzed, max_lines, mode)


def _optimize_milp(analyzed: list[dict], max_lines: int, mode: str) -> dict | None:
    import pulp

    prob = pulp.LpProblem("toto_portfolio", pulp.LpMaximize)
    x, z = {}, {}
    for a in analyzed:
        no = a["match_no"]
        for o in OUTCOMES:
            x[(no, o)] = pulp.LpVariable(f"x_{no}_{o}", cat="Binary")
        for k in (1, 2, 3):
            z[(no, k)] = pulp.LpVariable(f"z_{no}_{k}", cat="Binary")

    prob += pulp.lpSum(_utility(a, o, mode) * x[(a["match_no"], o)]
                       for a in analyzed for o in OUTCOMES)
    for a in analyzed:
        no = a["match_no"]
        prob += pulp.lpSum(z[(no, k)] for k in (1, 2, 3)) == 1
        prob += pulp.lpSum(x[(no, o)] for o in OUTCOMES) == pulp.lpSum(k * z[(no, k)] for k in (1, 2, 3))
    prob += pulp.lpSum(log(k) * z[(a["match_no"], k)]
                       for a in analyzed for k in (1, 2, 3)) <= log(max_lines)

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[prob.status] not in ("Optimal", "Not Solved"):
        return None
    count_by_match = {
        a["match_no"]: max(1, round(sum(pulp.value(x[(a["match_no"], o)]) or 0 for o in OUTCOMES)))
        for a in analyzed
    }
    return _decode(analyzed, x, count_by_match, mode)


def _optimize_greedy(analyzed: list[dict], max_lines: int, mode: str) -> dict:
    """貪欲法：コスト効率の良い昇格（シングル→ダブル→トリプル）を順に採用する。"""
    counts = {a["match_no"]: 1 for a in analyzed}
    lines = 1

    def coverage(a, k):
        scored = sorted(OUTCOMES, key=lambda o: _utility(a, o, mode), reverse=True)[:k]
        return sum({"1": a["p1"], "0": a["p0"], "2": a["p2"]}[o] for o in scored) / 100

    while True:
        best = None
        for a in analyzed:
            no = a["match_no"]
            k = counts[no]
            if k >= 3:
                continue
            new_lines = lines // k * (k + 1)
            if new_lines > max_lines:
                continue
            gain = coverage(a, k + 1) - coverage(a, k)
            efficiency = gain / (new_lines - lines)
            if best is None or efficiency > best[0]:
                best = (efficiency, no, k, new_lines)
        if best is None:
            break
        _, no, k, new_lines = best
        counts[no] = k + 1
        lines = new_lines
    return _decode(analyzed, None, counts, mode)


# --- 得点予想（totoGOAL3 / totoGOAL2）のフォーメーション最適化 -----------------
GOAL_TYPE = {1: "シングル", 2: "ダブル", 3: "トリプル", 4: "フォーメイ"}


def _goal_teams(analyzed: list[dict]) -> list[dict]:
    """各試合をホーム/アウェイの「得点チーム」単位へ展開する。"""
    teams = []
    for a in analyzed:
        for side, dist_k in (("home", "home_dist"), ("away", "away_dist")):
            teams.append({
                "match_no": a["match_no"], "side": side,
                "home": a["home"], "away": a["away"], "dist": a[dist_k],
            })
    return teams


def _goal_cov(team: dict, k: int) -> float:
    top = sorted(team["dist"].values(), reverse=True)[:k]
    return sum(top) / 100


def optimize_goal(analyzed: list[dict], budget: int = 1000, mode: str = MODE_STEADY) -> dict:
    """予算内で的中期待度が最大になる得点買い目（チーム別カテゴリ数）を貪欲法で決める。

    各チームは 0/1/2/3+ の4カテゴリから上位k個を買う。口数は全チームの
    カテゴリ数の積で増えるため、限界効率（期待度ゲイン÷口数増）の高い順に昇格する。
    """
    max_lines = max(1, int(budget) // TICKET_UNIT)
    teams = _goal_teams(analyzed)
    counts = {(t["match_no"], t["side"]): 1 for t in teams}
    lines = 1
    while True:
        best = None
        for t in teams:
            key = (t["match_no"], t["side"])
            k = counts[key]
            if k >= len(GOAL_CATEGORIES):
                continue
            new_lines = lines // k * (k + 1)
            if new_lines > max_lines:
                continue
            gain = _goal_cov(t, k + 1) - _goal_cov(t, k)
            efficiency = gain / (new_lines - lines)
            if best is None or efficiency > best[0]:
                best = (efficiency, key, k, new_lines)
        if best is None:
            break
        _, key, k, new_lines = best
        counts[key] = k + 1
        lines = new_lines

    marks, coverage = [], 1.0
    for a in analyzed:
        mh = {"match_no": a["match_no"], "home": a["home"], "away": a["away"],
              "confidence": a.get("confidence", 0)}
        for side, pick_k in (("home", "home_pick"), ("away", "away_pick")):
            team = next(t for t in teams
                        if t["match_no"] == a["match_no"] and t["side"] == side)
            k = counts[(a["match_no"], side)]
            ranked = sorted(GOAL_CATEGORIES, key=lambda c: team["dist"][c], reverse=True)
            picked = sorted(ranked[:k], key=lambda c: GOAL_CATEGORIES.index(c))
            coverage *= _goal_cov(team, k)
            mh[f"{side}_picks"] = picked
            mh[f"{side}_pick"] = a[pick_k]
            mh[f"{side}_type"] = GOAL_TYPE[k]
        marks.append(mh)
    return {"marks": marks, "lines": lines, "cost": lines * TICKET_UNIT,
            "coverage": round(coverage * 100, 4), "mode": mode, "teams": len(teams)}
