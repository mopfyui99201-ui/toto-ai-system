"""GitHub Pages 閲覧専用サイト用の最新予測JSONエクスポート。

Macが電源オフでもスマホから固定URLで閲覧できるよう、
LightGBM等で計算済みの予測結果・対戦カード・結果データを
静的JSON（data.json）として「ユーザー用データ/GitHub公開サイト」へ書き出す。
"""

from __future__ import annotations

import json
import re
import time

import database as db
import engine
import goal_predictor
import self_tuner
from config import GAME_TYPES, USER_DIR

SITE_DIR = USER_DIR / "GitHub公開サイト"
DATA_PATH = SITE_DIR / "data.json"
BUILD_PATH = SITE_DIR / "build.json"
MAINTENANCE_PATH = SITE_DIR / "maintenance.json"


def _win_draw_payload(round_key: str, matches: list[dict]) -> dict:
    """勝敗予想（toto/mini toto）の試合別予測を predictions+matches から組み立てる。"""
    preds = db.get_predictions(round_key)
    rows = []
    for m in matches:
        p = preds.get(m["match_no"], {})
        rows.append({
            "match_no": m["match_no"], "home": m["home"], "away": m["away"],
            "date": m.get("date", ""), "time": m.get("time", ""), "stadium": m.get("stadium", ""),
            "vote_1": m.get("vote_1", 0.0), "vote_0": m.get("vote_0", 0.0), "vote_2": m.get("vote_2", 0.0),
            "p1": p.get("p1"), "p0": p.get("p0"), "p2": p.get("p2"),
            "predicted": p.get("predicted"), "draw_risk": p.get("draw_risk"),
            "upset": bool(p.get("upset")), "upset_note": p.get("upset_note", ""),
            "news_note": p.get("news_note", ""),
        })
    return {"rows": rows, "results": {str(k): v for k, v in db.get_results(round_key).items()}}


def _goal_payload(game: dict) -> dict:
    """得点予想（totoGOAL3/totoGOAL2）をエクスポート時に再計算して同梱する。"""
    analyzed = goal_predictor.analyze_goal_round(game["matches"])
    ticket = goal_predictor.best_ticket(analyzed)
    return {
        "rows": analyzed,
        "ticket": ticket,
        "matches_meta": [
            {"match_no": m["match_no"], "home": m["home"], "away": m["away"],
             "date": m.get("date", ""), "time": m.get("time", "")}
            for m in game["matches"]
        ],
    }


def build_site_data() -> dict:
    """公開サイトが読み込む data.json の中身を組み立てる。"""
    bundle = engine.load_live_bundle()
    if not bundle:
        raise RuntimeError("ライブ取得データがありません。先に「🔄 最新情報を取得」を実行してください。")
    games: dict[str, dict] = {}
    for game_type, game in bundle["games"].items():
        meta = GAME_TYPES.get(game_type, {})
        payload: dict = {"kind": meta.get("kind", "win_draw"), "label": meta.get("label", game_type)}
        if meta.get("kind") == "goal":
            payload.update(_goal_payload(game))
        else:
            round_key = engine.live_round_key(bundle["round_no"], game_type)
            payload["round_key"] = round_key
            payload.update(_win_draw_payload(round_key, db.get_matches(round_key)))
        games[game_type] = payload
    return {
        "round_no": bundle["round_no"],
        "held_date": bundle.get("held_date", ""),
        "round_status": bundle.get("round_status", "open"),
        "fetched_at": bundle.get("fetched_at", ""),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sources": bundle.get("sources", []),
        "sales": bundle.get("sales") or {},
        "carryover": bundle.get("carryover", ""),
        "learning": self_tuner.summary(),
        "games": games,
    }


def export_site() -> dict:
    """data.json / build.json / maintenance.json をサイトフォルダへ書き出す。"""
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    data = build_site_data()
    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    ts = str(int(time.time()))
    BUILD_PATH.write_text(json.dumps({"ts": ts, "generated_at": data["generated_at"]}),
                          encoding="utf-8")
    if not MAINTENANCE_PATH.exists():
        MAINTENANCE_PATH.write_text('{"maintenance": false}', encoding="utf-8")
    # index.html 内の app.js キャッシュバスターを最新タイムスタンプへ更新
    index_path = SITE_DIR / "index.html"
    if index_path.exists():
        html = index_path.read_text(encoding="utf-8")
        html = re.sub(r'app\.js\?v=[^"]+', f'app.js?v={ts}', html)
        index_path.write_text(html, encoding="utf-8")
    return data


def set_maintenance(on: bool) -> None:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    MAINTENANCE_PATH.write_text(
        json.dumps({"maintenance": bool(on), "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}),
        encoding="utf-8")
