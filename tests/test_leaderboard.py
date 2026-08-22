from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.leaderboard import LeaderboardStore
from tests.test_app import FakeClock, build_data


class LeaderboardStoreTests(unittest.TestCase):
    def test_equal_scores_use_performance_tiebreakers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            store = LeaderboardStore(Path(temporary) / "scores.sqlite3", clock)
            players = [str(uuid.uuid4()) for _ in range(5)]
            performances = [
                ("Fewer Named", 4, 0, 5),
                ("More Misses", 5, 2, 5),
                ("Shorter Streak", 5, 1, 2),
                ("Earlier Best", 5, 1, 3),
                ("Later Best", 5, 1, 3),
            ]
            for player_id, (name, found, wrong_count, best_streak) in zip(
                players, performances, strict=True
            ):
                store.record_score(
                    player_id,
                    name,
                    20,
                    found=found,
                    wrong_count=wrong_count,
                    best_streak=best_streak,
                )
                clock.advance(1)

            self.assertEqual(
                [entry["username"] for entry in store.top()],
                [
                    "Earlier Best",
                    "Later Best",
                    "Shorter Streak",
                    "More Misses",
                    "Fewer Named",
                ],
            )

    def test_round_scores_ties_and_top_ten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            store = LeaderboardStore(Path(temporary) / "scores.sqlite3", clock)
            player_ids = [str(uuid.uuid4()) for _ in range(12)]

            first = store.record_score(player_ids[0], "Same Name", 9)
            clock.advance(1)
            second = store.record_score(player_ids[1], "Same Name", 9)
            self.assertEqual(first["rank"], 1)
            self.assertEqual(second["rank"], 2)

            for index, player_id in enumerate(player_ids[2:], start=2):
                clock.advance(1)
                store.record_score(player_id, f"Player {index}", index)

            entries = store.top(player_ids[-1])
            self.assertEqual(len(entries), 10)
            self.assertEqual(entries[0]["score"], 11)
            self.assertTrue(entries[0]["is_current"])
            self.assertNotIn("player_id", entries[0])

            another = store.record_score(player_ids[-1], "Renamed", 10)
            self.assertEqual(another["rank"], 3)
            current_rows = [row for row in store.top(player_ids[-1]) if row["is_current"]]
            self.assertEqual([row["score"] for row in current_rows], [11, 10])
            self.assertTrue(all(row["username"] == "Renamed" for row in current_rows))

    def test_zero_score_is_not_ranked_and_data_survives_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scores.sqlite3"
            player_id = str(uuid.uuid4())
            store = LeaderboardStore(path)
            self.assertFalse(store.record_score(player_id, "Zero Hero", 0)["eligible"])
            self.assertEqual(store.top(), [])
            store.record_score(player_id, "Zero Hero", 4)
            self.assertEqual(LeaderboardStore(path).top()[0]["score"], 4)

    def test_existing_personal_bests_are_migrated_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scores.sqlite3"
            player_id = str(uuid.uuid4())
            with sqlite3.connect(path) as connection:
                connection.execute(
                    """
                    CREATE TABLE players (
                        player_id TEXT PRIMARY KEY,
                        username TEXT NOT NULL,
                        best_score INTEGER,
                        achieved_at REAL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO players VALUES (?, ?, ?, ?, ?)",
                    (player_id, "Legacy", 7, 100.0, 100.0),
                )

            self.assertEqual(LeaderboardStore(path).top()[0]["score"], 7)
            self.assertEqual(len(LeaderboardStore(path).top()), 1)

    def test_existing_score_table_gains_tiebreak_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scores.sqlite3"
            player_id = str(uuid.uuid4())
            with sqlite3.connect(path) as connection:
                connection.executescript(
                    """
                    CREATE TABLE players (
                        player_id TEXT PRIMARY KEY,
                        username TEXT NOT NULL,
                        best_score INTEGER,
                        achieved_at REAL,
                        updated_at REAL NOT NULL
                    );
                    CREATE TABLE scores (
                        score_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        player_id TEXT NOT NULL,
                        score INTEGER NOT NULL,
                        achieved_at REAL NOT NULL
                    );
                    CREATE TABLE leaderboard_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    INSERT INTO leaderboard_meta VALUES ('best_scores_migrated', '1');
                    """
                )
                connection.execute(
                    "INSERT INTO players VALUES (?, ?, NULL, NULL, ?)",
                    (player_id, "Existing", 100.0),
                )
                connection.execute(
                    "INSERT INTO scores(player_id, score, achieved_at) VALUES (?, 12, 100)",
                    (player_id,),
                )

            entry = LeaderboardStore(path).top()[0]
            self.assertEqual(
                (entry["found"], entry["wrong_count"], entry["best_streak"]),
                (0, 0, 0),
            )


class LeaderboardApiTests(unittest.TestCase):
    def test_profile_multiple_round_submissions_and_logout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_data(root)
            clock = FakeClock()
            player_id = str(uuid.uuid4())
            app = create_app(root, clock, root / "leaderboard.sqlite3")

            with TestClient(app) as client:
                profile = client.put(
                    "/api/player",
                    json={"player_id": player_id, "username": "Ash K"},
                ).json()
                self.assertTrue(profile["persisted"])

                state = client.post("/api/round/start").json()
                target_id = state["question"]["target_id"]
                client.post("/api/round/guess", json={"answer_id": target_id})
                clock.advance(31)
                result = client.post("/api/round/expire").json()
                self.assertTrue(result["leaderboard"]["saved"])
                self.assertTrue(result["leaderboard"]["auto_show"])
                self.assertEqual(result["leaderboard"]["rank"], 1)

                state = client.post("/api/round/start").json()
                target_id = state["question"]["target_id"]
                client.post("/api/round/guess", json={"answer_id": target_id})
                clock.advance(31)
                second_result = client.post("/api/round/expire").json()
                self.assertTrue(second_result["leaderboard"]["auto_show"])
                self.assertEqual(second_result["leaderboard"]["rank"], 2)

                entries = client.get("/api/leaderboard").json()["entries"]
                self.assertEqual(entries[0]["username"], "Ash K")
                self.assertTrue(entries[0]["is_current"])
                self.assertEqual([entry["score"] for entry in entries], [3, 3])
                self.assertEqual(entries[0]["found"], 1)
                self.assertEqual(entries[0]["wrong_count"], 0)
                self.assertEqual(entries[0]["best_streak"], 1)

                client.post("/api/player/logout")
                remaining = client.get("/api/leaderboard").json()["entries"][0]
                self.assertEqual(remaining["username"], "Ash K")
                self.assertFalse(remaining["is_current"])

    def test_invalid_profile_and_database_outage_do_not_block_gameplay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_data(root)
            blocked_parent = root / "not-a-directory"
            blocked_parent.write_text("blocked", encoding="utf-8")
            app = create_app(root, FakeClock(), blocked_parent / "scores.sqlite3")

            with TestClient(app) as client:
                invalid = client.put(
                    "/api/player",
                    json={"player_id": "nope", "username": "??"},
                )
                self.assertEqual(invalid.status_code, 422)

                profile = client.put(
                    "/api/player",
                    json={"player_id": str(uuid.uuid4()), "username": "Misty 2"},
                ).json()
                self.assertFalse(profile["persisted"])
                self.assertEqual(client.post("/api/round/start").json()["screen"], "play")
                self.assertEqual(client.get("/api/leaderboard").status_code, 503)


if __name__ == "__main__":
    unittest.main()
