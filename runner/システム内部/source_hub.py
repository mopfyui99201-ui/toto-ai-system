"""無料・APIキー不要の公開情報源を並列収集し、取得成功した情報元URLを束ねる。

- 公開サイトへ ThreadPoolExecutor で並列アクセスし、成功（HTTP<400）した分を情報源として採用する。
- 最新ニュース補正が有効なときは、チーム別の DuckDuckGo 検索URLも情報源に加える。
"""

from __future__ import annotations

import concurrent.futures as futures
from urllib.parse import quote

import requests

from config import PUBLIC_SOURCES, SOURCE_DDG_NEWS

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
TIMEOUT = 8
NEWS_LIMIT = 20


def _probe(src: dict) -> dict:
    """1情報源へアクセスし、取得成功可否を付けて返す。"""
    try:
        res = requests.get(src["url"], headers=HEADERS, timeout=TIMEOUT)
        return {"label": src["label"], "url": src["url"], "ok": res.status_code < 400, "status": res.status_code}
    except Exception:
        return {"label": src["label"], "url": src["url"], "ok": False, "status": 0}


def _ddg(query: str) -> str:
    return f"{SOURCE_DDG_NEWS}?q={quote(query)}"


def news_links(teams: list[str]) -> list[dict]:
    """チーム別の最新ニュース（怪我・出場停止）検索URLを生成する。"""
    seen: list[str] = []
    for team in teams:
        if team and team not in seen:
            seen.append(team)
    return [
        {"label": f"{t} の最新ニュース（怪我・出場停止・天候）", "url": _ddg(f"{t} 怪我 出場停止 最新"), "ok": True}
        for t in seen[:NEWS_LIMIT]
    ]


def collect_sources(enable_news: bool = False, teams: list[str] | None = None) -> list[dict]:
    """公開情報源を並列収集し、情報源一覧（取得成功フラグ付き）を返す。"""
    with futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_probe, PUBLIC_SOURCES))
    if enable_news:
        results.extend(news_links(teams or []))
    return results


def ok_sources(sources: list[dict]) -> list[dict]:
    """取得に成功した情報源のみを返す。"""
    return [s for s in sources if s.get("ok")]
