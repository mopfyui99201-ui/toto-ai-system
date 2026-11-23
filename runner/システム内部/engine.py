"""予測パイプラインの統合（取得→補正→予測→分析→DB保存→的中集計）。"""

from __future__ import annotations

import json

import database as db
import live_fetcher
import news_adjuster
import result_fetcher
import self_tuner
import upset_analyzer
from config import GAME_TYPES, LIVE_PATH, TOTO
from data_fetcher import generate_sample_round, save_sample
from predictor import predict_round


def ensure_initial_data() -> None:
    """初回起動時にDBを初期化し、サンプル開催回を投入する（ルール22）。"""
    db.init_db()
    if not db.has_any_match():
        sample = generate_sample_round()
        run_round(sample, enable_news=False)
        save_sample(sample)


def run_round(round_data: dict, enable_news: bool = False) -> dict:
    """1開催回を予測して保存し、分析結果を返す。"""
    db.init_db()
    matches = round_data["matches"]
    news_map = news_adjuster.analyze_round(matches, enabled=enable_news)
    preds = predict_round(matches, news_map)
    for p in preds:
        p["news_note"] = news_map.get(p["match_no"], {}).get("note", "")
    analyzed = upset_analyzer.analyze(matches, preds)
    for a, p in zip(analyzed, preds, strict=True):
        p["draw_risk"] = a["draw_risk"]
        p["upset"] = a["upset"]
        p["upset_note"] = a["upset_note"]

    db.save_round(round_data["round_no"], round_data.get("held_date", ""), round_data.get("source", "sample"))
    db.save_matches(round_data["round_no"], matches)
    db.save_predictions(round_data["round_no"], preds)
    return {
        "round_no": round_data["round_no"],
        "held_date": round_data.get("held_date", ""),
        "source": round_data.get("source", "sample"),
        "matches": matches,
        "analyzed": analyzed,
    }


def live_round_key(base_round_no: str, game_type: str) -> str:
    """くじ種ごとに独立した開催回キーを作る（試合番号の重複による衝突を防ぐ）。"""
    return base_round_no if game_type == TOTO else f"{base_round_no}｜{game_type}"


def fetch_and_store_live(enable_news: bool = False) -> dict:
    """最新のtoto開催情報をライブ取得し、4くじ種ぶんを予測して保存する。"""
    bundle = live_fetcher.fetch_live_bundle(enable_news=enable_news)
    LIVE_PATH.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    for game_type, game in bundle["games"].items():
        key = live_round_key(bundle["round_no"], game_type)
        data = {**game, "round_no": key, "base_round_no": bundle["round_no"], "game_type": game_type}
        if GAME_TYPES[game_type]["kind"] == "goal":
            db.save_round(key, data["held_date"], data["source"])
            db.save_matches(key, data["matches"])
        else:
            run_round(data, enable_news=enable_news)
    return bundle


def load_live_bundle() -> dict | None:
    """保存済みの最新ライブ取得データを読み込む。"""
    if LIVE_PATH.exists():
        return json.loads(LIVE_PATH.read_text(encoding="utf-8"))
    return None


def predict_stored(round_no: str, enable_news: bool = False) -> dict:
    """DBに保存済みの開催回を再予測する（ニュース補正の再適用にも使用）。"""
    matches = db.get_matches(round_no)
    rounds = {r["round_no"]: r for r in db.list_rounds()}
    meta = rounds.get(round_no, {})
    data = {
        "round_no": round_no,
        "held_date": meta.get("held_date", ""),
        "source": meta.get("source", "sample"),
        "matches": matches,
    }
    return run_round(data, enable_news=enable_news)


def evaluate_round(round_no: str, results: dict[int, str], investment: int) -> dict:
    """実際の結果を取り込み、的中試合数・的中率・回収を記録する。"""
    preds = db.get_predictions(round_no)
    hits = sum(1 for no, actual in results.items()
               if no in preds and preds[no]["predicted"] == actual)
    total = len(results)
    accuracy = round(hits / total * 100, 1) if total else 0.0
    # 回収シミュレーション：的中13試合で平均配当 5,000円 と仮定した簡易見積り
    payout = 5000 if hits == total else 0
    recovery = round(payout / investment * 100, 1) if investment else 0.0
    perf = {"hits": hits, "total": total, "accuracy": accuracy,
            "investment": investment, "payout": payout, "recovery": recovery}
    db.save_results(round_no, results)
    db.save_performance(round_no, perf)
    # 答え合わせ結果を学習データとして自己チューニング係数を自動更新する
    self_tuner.safe_recompute()
    return perf


def collect_training_samples() -> list[tuple[dict, str]]:
    """学習用サンプル（試合情報＋実結果）を返す。"""
    return db.all_labeled_samples()


def _match_stored_round(round_no: str) -> str:
    """公式の開催回に対応する、DB内の勝敗（toto）開催回キーを探す。"""
    if not round_no:
        return ""
    for r in db.list_rounds():
        key = r["round_no"]
        if key == round_no or (key.startswith(round_no) and "GOAL" not in key and "mini" not in key):
            return key
    return ""


def fetch_and_autofill_results(investment: int = 1000) -> dict:
    """公式から確定結果（スコア・1/0/2）を自動取得し、対応する開催回へ自動入力する。

    一部・全部が取得できない場合でも停止せず、取得できた分だけを反映して返す。
    呼び出し側（管理者UI）は未確定分を画面上で手動確定できる。
    """
    data = result_fetcher.fetch_latest_result()
    filled = {m["match_no"]: m["result"] for m in data.get("matches", []) if m.get("result")}
    target = _match_stored_round(data.get("round_no", ""))
    saved: dict[int, str] = {}
    if target and filled:
        stored = {m["match_no"] for m in db.get_matches(target)}
        saved = {no: r for no, r in filled.items() if no in stored}
        if saved:
            data["performance"] = evaluate_round(target, saved, investment=investment)
    data["filled_map"] = filled
    data["target_round"] = target
    data["auto_saved"] = saved
    return data
