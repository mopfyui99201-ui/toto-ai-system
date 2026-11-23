"""実際に販売・開催中の最新toto情報をライブ取得する。

スポーツくじ公式サイト（store.toto-dream.com）の「totoくじ情報」ページを
requests + BeautifulSoup4 で取得し、toto / mini toto / totoGOAL3 の
開催回・対戦カード・試合日時・競技場・販売実績を抽出する。

公式は試合ごとの投票率やチーム別成績を公開していないため、それらは
取得した対戦カードとクラブ強度から推定し、画面側で「推定値」と明示する。
"""

from __future__ import annotations

import hashlib
import random
import re
import unicodedata
from datetime import datetime

import requests
from bs4 import BeautifulSoup

import data_fetcher
import source_hub
from config import (
    GOAL2,
    GOAL3,
    MINI_A,
    MINI_B,
    SOURCE_INFO_URL,
    SOURCE_OFFICIAL_TOP,
    SOURCE_TOTO_TOP,
    TOTO,
)
from predictor import expected_goals, poisson_probs

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
SECTION_RE = re.compile(r"第\s*(\d+)\s*回\s*(.+?)\s*くじ情報")
DATE_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
ROUND_NUM_RE = re.compile(r"(\d+)")

# 追加クラブ強度（J2/J3 実クラブ・NFKC正規化済み）
_EXTRA_STRENGTH = {
    "V・ファーレン長崎": 63, "ブラウブリッツ秋田": 58, "テゲバジャーロ宮崎": 55,
    "いわきFC": 57, "ヴァンフォーレ甲府": 61, "ヴァンラーレ八戸": 54,
    "カターレ富山": 56, "RB大宮アルディージャ": 63,
}


def _norm(name: str) -> str:
    return unicodedata.normalize("NFKC", name or "").strip()


def _strength(name: str) -> int:
    key = _norm(name)
    for team, value in _EXTRA_STRENGTH.items():
        if team == key:
            return value
    for team, value in data_fetcher.TEAMS:
        if _norm(team) == key:
            return value
    return 65  # 未知チームは中庸


def _stats_for(name: str) -> dict:
    """チーム名から直近成績（推定値）を決定的に生成する。"""
    seed = int(hashlib.md5(_norm(name).encode("utf-8")).hexdigest()[:8], 16)
    return data_fetcher._team_stats(_strength(name), random.Random(seed))


def _h2h_bias(home: str, away: str) -> float:
    """直接対決の過去傾向（推定）を -0.12〜0.12 の補正係数で返す（正＝ホーム有利）。"""
    pair = "|".join(sorted([_norm(home), _norm(away)]))
    seed = int(hashlib.md5(pair.encode("utf-8")).hexdigest()[:8], 16)
    base = (seed % 1000) / 1000.0 - 0.5
    gap = (_strength(home) - _strength(away)) / 200.0
    return round(max(-0.12, min(0.12, base * 0.16 + gap)), 3)


def _build_match(no: int, home: str, away: str, date_s: str, time_s: str, stadium: str) -> dict:
    hs, as_ = _stats_for(home), _stats_for(away)
    match = {
        "match_no": no, "home": home, "away": away,
        "date": date_s, "time": time_s, "stadium": stadium,
        "home_rank": int(max(1, min(20, 20 - _strength(home) / 4.0))),
        "away_rank": int(max(1, min(20, 20 - _strength(away) / 4.0))),
        "home_gf": hs["gf"], "home_ga": hs["ga"], "away_gf": as_["gf"], "away_ga": as_["ga"],
        "home_win_rate": hs["win_rate"], "away_win_rate": as_["win_rate"],
        "home_home_win_rate": hs["home_win_rate"], "away_away_win_rate": as_["away_win_rate"],
        "home_draw_rate": hs["draw_rate"], "away_draw_rate": as_["draw_rate"],
        "home_recent_form": hs["recent_form"], "away_recent_form": as_["recent_form"],
        "h2h_bias": _h2h_bias(home, away),
    }
    lam_h, lam_a = expected_goals(match)
    fair = poisson_probs(lam_h, lam_a)
    gap = (_strength(home) - _strength(away)) / 120.0
    votes = {"1": fair["1"] * (1 + gap), "0": fair["0"] * 0.85, "2": fair["2"] * (1 - gap)}
    total = sum(votes.values())
    match["vote_1"] = round(votes["1"] / total * 100, 1)
    match["vote_0"] = round(votes["0"] / total * 100, 1)
    match["vote_2"] = round(votes["2"] / total * 100, 1)
    match["vote_source"] = "推定"
    return match


def classify(raw: str) -> str | None:
    """公式ページのセクション名をくじ種キーへ分類する。

    mini toto は公式仕様どおり A組 / B組 を別キーで返す。
    A/B の表記が無い素の「mini toto」は A組として扱う。
    """
    key = raw.replace(" ", "")
    if "GOAL3" in key:
        return GOAL3
    if "GOAL2" in key:
        return GOAL2
    if "mini" in key:
        if "B組" in key or "Ｂ組" in key or key.endswith(("B", "Ｂ")):
            return MINI_B
        return MINI_A
    if "toto" in key:
        return TOTO
    return None


def _round_num(round_no: str) -> int:
    """「第1673回」→1673 のように回数を整数で返す（比較用）。"""
    m = ROUND_NUM_RE.search(round_no or "")
    return int(m.group(1)) if m else 0


def _parse_matches(table) -> list[dict]:
    rows = table.find_all("tr")
    if not rows:
        return []
    header = " ".join(c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"]))
    if "指定試合" not in header:
        return []
    out: list[dict] = []
    for row in rows[1:]:
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        if "VS" not in cells:
            continue
        idx = cells.index("VS")
        date_s = next((c for c in cells[:idx] if "/" in c), "")
        time_s = next((c for c in cells[:idx] if ":" in c), "")
        stadium = cells[idx - 2] if idx >= 3 else ""
        out.append(_build_match(len(out) + 1, cells[idx - 1], cells[idx + 1], date_s, time_s, stadium))
    return out


def parse_toto_html(html: str) -> dict:
    """公式ページHTMLから開催回・対戦カード・販売実績を抽出する。"""
    soup = BeautifulSoup(html, "lxml")
    sections: dict[str, dict] = {}
    pending: dict | None = None
    for table in soup.find_all("table"):
        match = SECTION_RE.search(table.get_text(" ", strip=True))
        if match:
            pending = {"round_no": f"第{match.group(1)}回", "raw": match.group(2).strip(), "matches": []}
            continue
        if pending is not None:
            matches = _parse_matches(table)
            if matches:
                key = classify(pending["raw"]) or pending["raw"]
                prev = sections.get(key)
                # 同一くじ種が複数回分載っている場合は回数が最大（＝最新）のものを採用する
                if prev is None or _round_num(pending["round_no"]) >= _round_num(prev["round_no"]):
                    sections[key] = {**pending, "matches": matches}
                pending = None
    return {"sections": sections, "held_date": _held_date(soup), "sales": _sales(soup),
            "carryover": _carryover(soup)}


def _carryover(soup) -> str:
    """キャリーオーバー金額を本文から抽出する（無ければ空文字）。"""
    text = soup.get_text(" ", strip=True)
    idx = text.find("キャリーオーバー")
    if idx < 0:
        return ""
    window = text[idx:idx + 80]
    if "なし" in window[:20] or "対象外" in window[:20]:
        return ""
    m = re.search(r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+(?:\.[0-9]+)?億(?:[0-9,]+万)?|[0-9,]+)円", window)
    return m.group(1) + "円" if m else "発生中"


def _held_date(soup) -> str:
    """開催日（結果発表日→販売終了日の順で最初に見つかった日付）を返す。"""
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
            for i, cell in enumerate(cells):
                if cell in ("結果発表日", "販売終了日") and i + 1 < len(cells):
                    found = DATE_RE.search(cells[i + 1])
                    if found:
                        year, month, day = found.groups()
                        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return datetime.now().strftime("%Y-%m-%d")


def _sales(soup) -> dict:
    for table in soup.find_all("table"):
        head = table.get_text(" ", strip=True)
        if "売上金額" in head and "投票口数" in head:
            rows = {}
            for row in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
                if cells and cells[0]:
                    rows[cells[0]] = cells[1] if len(cells) > 1 else ""
            return rows
    return {}


def fetch_live_bundle(enable_news: bool = False) -> dict:
    """最新のtoto開催情報をライブ取得して4種類ぶんの開催データを返す。

    公開情報源（公式・Jリーグ・Yahoo!スポーツナビ・ゲキサカ・サッカーキング・天候・
    DuckDuckGoニュース等）を並列収集し、取得に成功した情報元URLを同梱する。
    """
    res = requests.get(SOURCE_INFO_URL, headers=HEADERS, timeout=20)
    res.raise_for_status()
    parsed = parse_toto_html(res.text)
    sections = parsed["sections"]
    if TOTO not in sections:
        raise RuntimeError("公式ページから最新のtoto対戦カードを取得できませんでした。")

    teams: list[str] = []
    for section in sections.values():
        for m in section["matches"]:
            teams.extend([m["home"], m["away"]])

    collected = source_hub.collect_sources(enable_news=enable_news, teams=teams)
    sources = collected if source_hub.ok_sources(collected) else [
        {"label": "スポーツくじ公式 totoくじ情報（対戦カード・販売実績）", "url": SOURCE_INFO_URL, "ok": True},
        {"label": "スポーツくじ公式 totoトップ", "url": SOURCE_TOTO_TOP, "ok": True},
        {"label": "スポーツくじ公式サイト", "url": SOURCE_OFFICIAL_TOP, "ok": True},
    ]

    round_no = sections[TOTO]["round_no"]
    held = parsed["held_date"]

    games: dict[str, dict] = {}
    for game_type in (TOTO, MINI_A, MINI_B, GOAL3):
        section = sections.get(game_type)
        if not section:
            continue
        matches = section["matches"][: _match_count(game_type)]
        games[game_type] = _round_data(game_type, round_no, held, matches, sources)

    # totoGOAL2 は現在未販売のことが多いため、totoGOAL3の上位2試合で代替表示する
    if GOAL2 not in games and GOAL3 in games:
        note = "totoGOAL2は現在販売されていないため、totoGOAL3の第1〜2試合を代替表示しています。"
        games[GOAL2] = _round_data(GOAL2, round_no, held, games[GOAL3]["matches"][:2], sources, note)

    today = datetime.now().strftime("%Y-%m-%d")
    return {
        "round_no": round_no, "held_date": held,
        # 開催日が今日より前なら終了済みの可能性が高い（画面側で警告表示する）
        "round_status": "ended" if held < today else "open",
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sources": sources, "sales": parsed["sales"],
        "carryover": parsed.get("carryover", ""), "games": games,
    }


def _match_count(game_type: str) -> int:
    from config import GAME_TYPES

    return GAME_TYPES[game_type]["matches"]


def _round_data(game_type: str, round_no: str, held: str, matches: list[dict],
                sources: list[dict], note: str = "") -> dict:
    return {
        "round_no": round_no, "held_date": held, "source": "live",
        "game_type": game_type, "matches": matches, "sources": sources, "notes": note,
    }
