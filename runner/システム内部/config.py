"""共通設定・パス解決・.env 管理。

ルール2/12/15: フォルダを「ユーザー用データ」「システム内部」に厳密分離する。
ルール10: パスワード等の機密情報はコードに書かず .env へ自動分離する。
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv, set_key

# --- フォルダ構成 ---------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent          # toto勝敗予想システムフォルダ
USER_DIR = BASE_DIR / "ユーザー用データ"                    # ルール15(1) データ・設定
SRC_DIR = BASE_DIR / "システム内部"                         # ルール15(2) ソースコード

DB_PATH = USER_DIR / "toto_data.db"
SAMPLE_PATH = USER_DIR / "sample_data.json"
LIVE_PATH = USER_DIR / "live_bundle.json"   # ライブ取得した最新開催データの保存先
MODEL_PATH = USER_DIR / "predict_model.txt"
ENV_PATH = USER_DIR / ".env"
LOG_DIR = USER_DIR / "logs"
BACKUP_DIR = BASE_DIR.parent / "学習フォルダ" / "backup"   # ルール16 データ退避先

for _d in (USER_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- .env 読み込み（初回は自動生成） --------------------------------------
ENV_PATH.touch(exist_ok=True)
load_dotenv(ENV_PATH)


def _ensure(key: str, generator) -> str:
    """未設定のキーを生成して .env へ保存し、値を返す。"""
    value = os.getenv(key)
    if not value:
        value = generator()
        set_key(str(ENV_PATH), key, value)
        os.environ[key] = value
    return value


ADMIN_PASSWORD = _ensure("TOTO_ADMIN_PASSWORD", lambda: "toto-" + secrets.token_urlsafe(6))
# 他アプリ（8501）と競合しないよう、本システム専用ポートは 8502 を既定とする
APP_PORT = int(_ensure("TOTO_PORT", lambda: "8502"))
ENV_CREATED_NOW = ENV_PATH.stat().st_size > 0

# --- 表示・業務定数 -------------------------------------------------------
APP_TITLE = "toto勝敗予想システム"
OUTCOMES = ("1", "0", "2")
OUTCOME_LABEL = {"1": "ホーム勝ち", "0": "引き分け", "2": "アウェイ勝ち"}
TICKET_UNIT = 100          # 1口あたり単価（円）
LEAGUE_AVG_GOALS = 1.35    # 1試合あたり平均得点（Jリーグ目安）
HOME_ADVANTAGE = 0.18      # ホームアドバンテージ係数
UPSET_GAP_THRESHOLD = 12.0  # 波乱と判定するAI勝率と投票率の乖離（％pt）

# --- 自分で予想するくじ4種類の定義 ----------------------------------------
TOTO = "toto"
MINI = "mini toto"          # 旧キー（後方互換用。新規は A組/B組 の2種）
MINI_A = "mini toto A組"
MINI_B = "mini toto B組"
GOAL3 = "totoGOAL3"
GOAL2 = "totoGOAL2"
# kind: win_draw＝1/0/2 の勝敗予想 ／ goal＝各チームの得点数予想
GAME_TYPES: dict[str, dict] = {
    TOTO: {"matches": 13, "kind": "win_draw", "label": "toto（13試合・勝敗）"},
    MINI_A: {"matches": 5, "kind": "win_draw", "label": "mini toto A組（5試合・勝敗）"},
    MINI_B: {"matches": 5, "kind": "win_draw", "label": "mini toto B組（5試合・勝敗）"},
    GOAL3: {"matches": 3, "kind": "goal", "label": "totoGOAL3（3試合・得点）"},
    GOAL2: {"matches": 2, "kind": "goal", "label": "totoGOAL2（2試合・得点）"},
}
GOAL_CATEGORIES = ("0", "1", "2", "3+")   # 各チームの得点カテゴリ
GOAL_CATEGORY_LABEL = {"0": "0点", "1": "1点", "2": "2点", "3+": "3点以上"}

# --- ライブ情報の参照元URL（ルール: 情報元リンクの表示） --------------------
SOURCE_INFO_URL = ("https://store.toto-dream.com/dcs/subos/screen/pi01/spin000/"
                   "PGSPIN00001DisptotoLotInfo.form")
SOURCE_TOTO_TOP = "https://www.toto-dream.com/toto/"
SOURCE_OFFICIAL_TOP = "https://www.toto-dream.com/"
SOURCE_DDG_NEWS = "https://duckduckgo.com/"
# 追加の公開情報源（無料・APIキー不要）
SOURCE_JLEAGUE = "https://www.jleague.jp/"
SOURCE_JLEAGUE_STANDINGS = "https://www.jleague.jp/standings/j1/"
SOURCE_JLEAGUE_NEWS = "https://www.jleague.jp/news/"
SOURCE_YAHOO_SPORTS = "https://sports.yahoo.co.jp/"
SOURCE_YAHOO_NEWS = "https://news.yahoo.co.jp/search?p=Jリーグ"
SOURCE_GEKISAKA = "https://web.gekisaka.jp/"
SOURCE_SOCCERKING = "https://www.soccer-king.jp/"
SOURCE_WEATHER_TENKI = "https://tenki.jp/"
SOURCE_WEATHER_JMA = "https://www.jma.go.jp/bosai/forecast/"
# 公式の結果取得ページ（一覧→各回詳細）
SOURCE_RESULT_LIST = ("https://store.toto-dream.com/dcs/subos/screen/pi04/spin011/"
                      "PGSPIN01101InitLotResultLsttoto.form")
SOURCE_RESULT_DETAIL = ("https://store.toto-dream.com/dcs/subos/screen/pi04/spin011/"
                        "PGSPIN01101LnkHoldCntLotResultLsttoto.form")

# 並列収集する公開情報源（取得成功したものだけを画面へリンク表示する）
PUBLIC_SOURCES: list[dict] = [
    {"label": "スポーツくじ公式 totoくじ情報（対戦カード・販売実績）", "url": SOURCE_INFO_URL},
    {"label": "スポーツくじ公式 toto（予想専門情報）", "url": SOURCE_TOTO_TOP},
    {"label": "スポーツくじ公式サイト（くじ結果）", "url": SOURCE_OFFICIAL_TOP},
    {"label": "Jリーグ公式サイト", "url": SOURCE_JLEAGUE},
    {"label": "Jリーグ公式 順位表（順位・得失点）", "url": SOURCE_JLEAGUE_STANDINGS},
    {"label": "Jリーグ公式 ニュース（直近戦績・怪我人情報）", "url": SOURCE_JLEAGUE_NEWS},
    {"label": "Yahoo!スポーツナビ", "url": SOURCE_YAHOO_SPORTS},
    {"label": "Yahoo!ニュース（Jリーグ最新情報）", "url": SOURCE_YAHOO_NEWS},
    {"label": "ゲキサカ（サッカーニュース）", "url": SOURCE_GEKISAKA},
    {"label": "サッカーキング", "url": SOURCE_SOCCERKING},
    {"label": "tenki.jp（開催地の天候）", "url": SOURCE_WEATHER_TENKI},
    {"label": "気象庁 天気予報（開催地の天候）", "url": SOURCE_WEATHER_JMA},
]
