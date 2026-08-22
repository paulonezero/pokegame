from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


class LeaderboardStore:
    """Small SQLite store for public player names and personal-best scores."""

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

    def record_best(self, player_id: str, username: str, score: int) -> dict[str, Any]:
        self._ensure_schema()
        score = int(score)
        if score <= 0:
            return {"saved": True, "eligible": False, "new_best": False, "rank": None}

        now = float(self.clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT best_score FROM players WHERE player_id = ?", (player_id,)
            ).fetchone()
            old_score = existing["best_score"] if existing else None
            new_best = old_score is None or score > int(old_score)

            if existing is None:
                connection.execute(
                    """
                    INSERT INTO players(
                        player_id, username, best_score, achieved_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (player_id, username, score, now, now),
                )
            elif new_best:
                connection.execute(
                    """
                    UPDATE players
                    SET username = ?, best_score = ?, achieved_at = ?, updated_at = ?
                    WHERE player_id = ?
                    """,
                    (username, score, now, now, player_id),
                )
            else:
                connection.execute(
                    "UPDATE players SET username = ?, updated_at = ? WHERE player_id = ?",
                    (username, now, player_id),
                )

            player = connection.execute(
                "SELECT best_score, achieved_at FROM players WHERE player_id = ?",
                (player_id,),
            ).fetchone()
            rank = connection.execute(
                """
                SELECT 1 + COUNT(*) AS rank
                FROM players
                WHERE best_score IS NOT NULL AND best_score > 0 AND (
                    best_score > ? OR
                    (best_score = ? AND achieved_at < ?) OR
                    (best_score = ? AND achieved_at = ? AND player_id < ?)
                )
                """,
                (
                    player["best_score"],
                    player["best_score"],
                    player["achieved_at"],
                    player["best_score"],
                    player["achieved_at"],
                    player_id,
                ),
            ).fetchone()["rank"]

        return {
            "saved": True,
            "eligible": True,
            "new_best": new_best,
            "personal_best": int(player["best_score"]),
            "rank": int(rank),
        }

    def top(self, player_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        self._ensure_schema()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT player_id, username, best_score, achieved_at
                FROM players
                WHERE best_score IS NOT NULL AND best_score > 0
                ORDER BY best_score DESC, achieved_at ASC, player_id ASC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [
            {
                "rank": index,
                "username": row["username"],
                "score": int(row["best_score"]),
                "achieved_at": row["achieved_at"],
                "is_current": bool(player_id and row["player_id"] == player_id),
            }
            for index, row in enumerate(rows, start=1)
        ]
