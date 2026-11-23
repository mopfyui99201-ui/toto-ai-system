"""GitHub Actions 用 自動更新スクリプト（Mac不要・リポジトリ内データのみで完結）。

runner/システム内部 のエンジン一式と runner/ユーザー用データ のDB・モデル・
学習データを使い、以下を実行してリポジトリ直下の公開ファイルを更新する:

  1. 公式サイトから最新開催回・対戦カードを取得し全くじ種を再予測
  2. 開催終了回の確定結果を自動取得してDBへ反映
  3. 答え合わせ履歴からAI自己学習係数を再計算（tuning.json 更新）
  4. data.json / build.json を再生成し index.html のキャッシュバスターを更新

実行: python runner/ci_update.py [--news]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

RUNNER = Path(__file__).resolve().parent
REPO = RUNNER.parent                      # GitHub Pages のルート（data.json 等の出力先）
SRC = RUNNER / "システム内部"
sys.path.insert(0, str(SRC))

import engine  # noqa: E402
import self_tuner  # noqa: E402
import static_export  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="toto GitHub Actions 自動更新")
    parser.add_argument("--news", action="store_true", help="ニュース補正を有効化（失敗時は自動スキップ）")
    args = parser.parse_args()

    # 1. 最新開催情報の取得と再予測（最新回の自動判定は live_fetcher 側）
    bundle = engine.fetch_and_store_live(enable_news=args.news)
    print(f"[1/4] 最新情報取得: {bundle['round_no']}（{bundle.get('held_date')}・"
          f"{bundle.get('round_status', 'open')}）{len(bundle['games'])}くじ種")

    # 2. 開催終了回の確定結果を自動取得・自動入力
    try:
        res = engine.fetch_and_autofill_results()
        saved = len(res.get("auto_saved") or {})
        print(f"[2/4] 結果自動取得: {res.get('round_no') or '-'}（{res.get('filled_count', 0)}/"
              f"{res.get('total', 0)}試合・DB反映 {saved}試合）")
    except Exception as e:
        print(f"[2/4] 結果自動取得スキップ: {e}")

    # 3. AI自己学習（答え合わせ履歴→補正係数の再計算）
    try:
        tuning = self_tuner.recompute()
        n = tuning.get("metrics", {}).get("n", 0)
        print(f"[3/4] AI自己学習: 判定済 {n}件から補正係数を更新")
    except Exception as e:
        print(f"[3/4] AI自己学習スキップ: {e}")

    # 4. 公開データ生成（リポジトリ直下へ書き出し）
    data = static_export.build_site_data()
    (REPO / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    ts = str(int(time.time()))
    (REPO / "build.json").write_text(
        json.dumps({"ts": ts, "generated_at": data["generated_at"]}), encoding="utf-8")
    maint = REPO / "maintenance.json"
    if not maint.exists():
        maint.write_text('{"maintenance": false}', encoding="utf-8")
    index = REPO / "index.html"
    if index.exists():
        html = index.read_text(encoding="utf-8")
        index.write_text(re.sub(r'app\.js\?v=[^"]+', f"app.js?v={ts}", html), encoding="utf-8")
    print(f"[4/4] 公開データ生成: {data['generated_at']} round={data['round_no']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
