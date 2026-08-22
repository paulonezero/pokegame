from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


class LeaderboardStore:
    """Small SQLite store for public player names and ranked round scores."""

    def __init__(self, path: Path, clock: Callable[[], float] | None = None) -> None:
        self.path = Path(path).expanduser().resolve()
        self.clock = clock or time.time
        self._schema_lock = threading.Lock()
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _ensure_schema(self) -> None:
        if self._ready:
            return
        with self._schema_lock:
            if self._ready:
                return
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS players (
                        player_id TEXT PRIMARY KEY,
                        username TEXT NOT NULL,
                        best_score INTEGER,
                        achieved_at REAL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS players_ranking
                    ON players(best_score DESC, achieved_at ASC, player_id ASC)
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS scores (
                        score_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        player_id TEXT NOT NULL,
                        score INTEGER NOT NULL,
                        achieved_at REAL NOT NULL,
                        FOREIGN KEY(player_id) REFERENCES players(player_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS scores_ranking
                    ON scores(score DESC, achieved_at ASC, score_id ASC)
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS leaderboard_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
                migrated = connection.execute(
                    "SELECT 1 FROM leaderboard_meta WHERE key = 'best_scores_migrated'"
                ).fetchone()
                if migrated is None:
                    connection.execute(
                        """
                        INSERT INTO scores(player_id, score, achieved_at)
                        SELECT player_id, best_score, achieved_at
                        FROM players
                        WHERE best_score IS NOT NULL AND best_score > 0
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO leaderboard_meta(key, value)
                        VALUES ('best_scores_migrated', '1')
                        """
                    )
            self._ready = True

    def upsert_player(self, player_id: str, username: str) -> None:
        self._ensure_schema()
        now = float(self.clock())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO players(player_id, username, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    username = excluded.username,
                    updated_at = excluded.updated_at
                """,
                (player_id, username, now),
            )

    def record_score(self, player_id: str, username: str, score: int) -> dict[str, Any]:
        self._ensure_schema()
        score = int(score)
        if score <= 0:
            return {"saved": True, "eligible": False, "rank": None}

        now = float(self.clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO players(player_id, username, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    username = excluded.username,
                    updated_at = excluded.updated_at
                """,
                (player_id, username, now),
            )
            cursor = connection.execute(
                "INSERT INTO scores(player_id, score, achieved_at) VALUES (?, ?, ?)",
                (player_id, score, now),
            )
            score_id = int(cursor.lastrowid)
            rank = connection.execute(
                """
                SELECT 1 + COUNT(*) AS rank
                FROM scores
                WHERE score > ? OR
                    (score = ? AND achieved_at < ?) OR
                    (score = ? AND achieved_at = ? AND score_id < ?)
                """,
                (
                    score,
                    score,
                    now,
                    score,
                    now,
                    score_id,
                ),
            ).fetchone()["rank"]

        return {
            "saved": True,
            "eligible": True,
            "score": score,
            "rank": int(rank),
        }

    def top(self, player_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        self._ensure_schema()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT scores.player_id, players.username, scores.score, scores.achieved_at
                FROM scores
                JOIN players ON players.player_id = scores.player_id
                WHERE scores.score > 0
                ORDER BY scores.score DESC, scores.achieved_at ASC, scores.score_id ASC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [
            {
                "rank": index,
                "username": row["username"],
                "score": int(row["score"]),
                "achieved_at": row["achieved_at"],
                "is_current": bool(player_id and row["player_id"] == player_id),
            }
            for index, row in enumerate(rows, start=1)
        ]
