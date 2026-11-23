"""SQLite による永続化（ルール9：データはコードと完全分離し保護する）。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS rounds (
    round_no   TEXT PRIMARY KEY,
    held_date  TEXT,
    source     TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS matches (
    round_no  TEXT,
    match_no  INTEGER,
    home      TEXT, away TEXT,
    home_rank INTEGER, away_rank INTEGER,
    vote_1 REAL, vote_0 REAL, vote_2 REAL,
    payload   TEXT,
    PRIMARY KEY (round_no, match_no)
);
CREATE TABLE IF NOT EXISTS predictions (
    round_no TEXT, match_no INTEGER,
    p1 REAL, p0 REAL, p2 REAL,
    draw_risk REAL,
    upset INTEGER, upset_note TEXT, news_note TEXT,
    predicted TEXT,
    PRIMARY KEY (round_no, match_no)
);
CREATE TABLE IF NOT EXISTS results (
    round_no TEXT, match_no INTEGER,
    actual TEXT,
    PRIMARY KEY (round_no, match_no)
);
CREATE TABLE IF NOT EXISTS performance (
    round_no TEXT PRIMARY KEY,
    hits INTEGER, total INTEGER, accuracy REAL,
    investment INTEGER, payout INTEGER, recovery REAL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS payouts (
    round_no TEXT PRIMARY KEY,
    grade1 INTEGER, grade2 INTEGER, grade3 INTEGER,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript(SCHEMA)


def save_round(round_no: str, held_date: str, source: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO rounds(round_no, held_date, source) VALUES(?,?,?)",
            (round_no, held_date, source),
        )


def save_matches(round_no: str, matches: Iterable[dict]) -> None:
    import json

    with _conn() as con:
        for m in matches:
            con.execute(
                "INSERT OR REPLACE INTO matches(round_no, match_no, home, away,"
                " home_rank, away_rank, vote_1, vote_0, vote_2, payload)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    round_no, m["match_no"], m["home"], m["away"],
                    m.get("home_rank"), m.get("away_rank"),
                    m.get("vote_1"), m.get("vote_0"), m.get("vote_2"),
                    json.dumps(m, ensure_ascii=False),
                ),
            )


def save_predictions(round_no: str, preds: Iterable[dict]) -> None:
    with _conn() as con:
        for p in preds:
            con.execute(
                "INSERT OR REPLACE INTO predictions(round_no, match_no, p1, p0, p2,"
                " draw_risk, upset, upset_note, news_note, predicted)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    round_no, p["match_no"], p["p1"], p["p0"], p["p2"],
                    p.get("draw_risk"), int(bool(p.get("upset"))),
                    p.get("upset_note", ""), p.get("news_note", ""), p.get("predicted"),
                ),
            )


def save_results(round_no: str, results: dict[int, str]) -> None:
    with _conn() as con:
        for match_no, actual in results.items():
            con.execute(
                "INSERT OR REPLACE INTO results(round_no, match_no, actual) VALUES(?,?,?)",
                (round_no, int(match_no), actual),
            )


def save_performance(round_no: str, perf: dict) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO performance(round_no, hits, total, accuracy,"
            " investment, payout, recovery) VALUES(?,?,?,?,?,?,?)",
            (
                round_no, perf["hits"], perf["total"], perf["accuracy"],
                perf["investment"], perf["payout"], perf["recovery"],
            ),
        )


def list_rounds() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT round_no, held_date, source FROM rounds ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_matches(round_no: str) -> list[dict]:
    import json

    with _conn() as con:
        rows = con.execute(
            "SELECT payload FROM matches WHERE round_no=? ORDER BY match_no", (round_no,)
        ).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def get_predictions(round_no: str) -> dict[int, dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM predictions WHERE round_no=? ORDER BY match_no", (round_no,)
        ).fetchall()
    return {r["match_no"]: dict(r) for r in rows}


def get_results(round_no: str) -> dict[int, str]:
    with _conn() as con:
        rows = con.execute(
            "SELECT match_no, actual FROM results WHERE round_no=?", (round_no,)
        ).fetchall()
    return {r["match_no"]: r["actual"] for r in rows}


def get_performance(round_no: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM performance WHERE round_no=?", (round_no,)
        ).fetchone()
    return dict(row) if row else None


def all_labeled_samples() -> list[tuple[dict, str]]:
    """学習用に「試合情報＋実際の結果」のペアを全開催回から集める。"""
    import json

    with _conn() as con:
        rows = con.execute(
            "SELECT m.payload AS payload, r.actual AS actual"
            " FROM matches m JOIN results r"
            " ON m.round_no=r.round_no AND m.match_no=r.match_no"
        ).fetchall()
    return [(json.loads(r["payload"]), r["actual"]) for r in rows]


def save_payouts(round_no: str, payouts: dict) -> None:
    """公式の1〜3等配当金（100円あたり）を保存する。"""
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO payouts(round_no, grade1, grade2, grade3)"
            " VALUES(?,?,?,?)",
            (round_no, payouts.get("1"), payouts.get("2"), payouts.get("3")),
        )


def get_payouts(round_no: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT grade1, grade2, grade3 FROM payouts WHERE round_no=?", (round_no,)
        ).fetchone()
    if not row:
        return None
    return {"1": row["grade1"], "2": row["grade2"], "3": row["grade3"]}


def list_payouts() -> dict[str, dict]:
    with _conn() as con:
        rows = con.execute("SELECT round_no, grade1, grade2, grade3 FROM payouts").fetchall()
    return {r["round_no"]: {"1": r["grade1"], "2": r["grade2"], "3": r["grade3"]}
            for r in rows}


def has_any_match() -> bool:
    with _conn() as con:
        return con.execute("SELECT COUNT(1) AS c FROM matches").fetchone()["c"] > 0
