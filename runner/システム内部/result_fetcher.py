"""開催終了した回の確定結果（スコア・1/0/2）を公式サイトから自動取得する。

取得に失敗しても例外で止めず、ok=False と取得できた分だけを返す。
呼び出し側（管理者UI）は未確定分を画面上で手動補完・確定保存できる。
"""

from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from config import SOURCE_RESULT_DETAIL, SOURCE_RESULT_LIST

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
ROUND_RE = re.compile(r"第\s*(\d+)\s*回")
HOLD_RE = re.compile(r"holdCntId=(\d+)")
SCORE_RE = re.compile(r"(\d+)\s*-\s*(\d+)")
VALID_RESULTS = ("1", "0", "2")


def _get(url: str) -> str:
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.raise_for_status()
    return res.text


def list_completed_rounds(html: str) -> list[dict]:
    """結果一覧HTMLから開催回と各回詳細URLの候補を抽出する。"""
    soup = BeautifulSoup(html, "lxml")
    rounds: list[dict] = []
    for link in soup.find_all("a", href=True):
        hit = HOLD_RE.search(link["href"])
        if not hit:
            continue
        label = link.get_text(" ", strip=True)
        round_hit = ROUND_RE.search(label)
        rounds.append({
            "label": label,
            "round_no": f"第{round_hit.group(1)}回" if round_hit else label,
            "hold_cnt_id": hit.group(1),
            "url": f"{SOURCE_RESULT_DETAIL}?holdCntId={hit.group(1)}",
        })
    return rounds


def parse_result_html(html: str) -> dict:
    """結果詳細HTMLから各試合のスコアとくじ結果（1/0/2）を抽出する。"""
    soup = BeautifulSoup(html, "lxml")
    round_hit = ROUND_RE.search(soup.get_text(" ", strip=True))
    round_no = f"第{round_hit.group(1)}回" if round_hit else ""
    matches: list[dict] = []
    for table in soup.find_all("table"):
        head = table.get_text(" ", strip=True)
        if "試合結果" not in head or "くじ結果" not in head:
            continue
        for row in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < 7 or not cells[2].isdigit():
                continue
            score = SCORE_RE.search(cells[4])
            matches.append({
                "match_no": int(cells[2]), "date": cells[0], "stadium": cells[1],
                "home": cells[3], "away": cells[5], "score": cells[4],
                "home_goals": int(score.group(1)) if score else None,
                "away_goals": int(score.group(2)) if score else None,
                "result": cells[6] if cells[6] in VALID_RESULTS else "",
            })
        break
    return {"round_no": round_no, "matches": matches}


def fetch_latest_result() -> dict:
    """最新の開催終了回の確定結果を取得する（失敗時も停止せず ok=False を返す）。"""
    sources = [{"label": "スポーツくじ公式 totoくじ結果一覧", "url": SOURCE_RESULT_LIST, "ok": True}]
    try:
        rounds = list_completed_rounds(_get(SOURCE_RESULT_LIST))
    except Exception as e:
        return {"ok": False, "error": f"結果一覧の取得に失敗：{e}", "round_no": "",
                "matches": [], "total": 0, "filled_count": 0, "sources": sources}
    if not rounds:
        return {"ok": False, "error": "結果一覧から開催回を検出できませんでした。", "round_no": "",
                "matches": [], "total": 0, "filled_count": 0, "sources": sources}

    target = rounds[0]
    sources.append({"label": f"公式 {target['label']} の試合結果", "url": target["url"], "ok": True})
    try:
        parsed = parse_result_html(_get(target["url"]))
    except Exception as e:
        return {"ok": False, "error": f"結果詳細の取得に失敗：{e}", "round_no": target["round_no"],
                "matches": [], "total": 0, "filled_count": 0, "sources": sources}

    matches = parsed["matches"]
    filled = [m for m in matches if m["result"]]
    return {
        "ok": bool(matches), "error": "", "round_no": parsed["round_no"] or target["round_no"],
        "matches": matches, "total": len(matches), "filled_count": len(filled), "sources": sources,
    }
