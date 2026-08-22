from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.leaderboard import LeaderboardStore
from tests.test_app import FakeClock, build_data


class LeaderboardStoreTests(unittest.TestCase):
    def test_personal_bests_ties_and_top_ten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            store = LeaderboardStore(Path(temporary) / "scores.sqlite3", clock)
            player_ids = [str(uuid.uuid4()) for _ in range(12)]

            first = store.record_best(player_ids[0], "Same Name", 9)
            clock.advance(1)
            second = store.record_best(player_ids[1], "Same Name", 9)
            self.assertEqual(first["rank"], 1)
            self.assertEqual(second["rank"], 2)

            for index, player_id in enumerate(player_ids[2:], start=2):
                clock.advance(1)
                store.record_best(player_id, f"Player {index}", index)

            entries = store.top(player_ids[-1])
            self.assertEqual(len(entries), 10)
            self.assertEqual(entries[0]["score"], 11)
            self.assertTrue(entries[0]["is_current"])
            self.assertNotIn("player_id", entries[0])

            unchanged = store.record_best(player_ids[-1], "Renamed", 3)
            self.assertFalse(unchanged["new_best"])
            self.assertEqual(unchanged["personal_best"], 11)
            self.assertEqual(store.top(player_ids[-1])[0]["username"], "Renamed")

    def test_zero_score_is_not_ranked_and_data_survives_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scores.sqlite3"
            player_id = str(uuid.uuid4())
            store = LeaderboardStore(path)
            self.assertFalse(store.record_best(player_id, "Zero Hero", 0)["eligible"])
            self.assertEqual(store.top(), [])
            store.record_best(player_id, "Zero Hero", 4)
            self.assertEqual(LeaderboardStore(path).top()[0]["score"], 4)


class LeaderboardApiTests(unittest.TestCase):
    def test_profile_round_submission_rename_and_logout(self) -> None:
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
                self.assertTrue(result["leaderboard"]["new_best"])
                self.assertTrue(result["leaderboard"]["auto_show"])
                self.assertEqual(result["leaderboard"]["rank"], 1)

                entries = client.get("/api/leaderboard").json()["entries"]
                self.assertEqual(entries[0]["username"], "Ash K")
                self.assertTrue(entries[0]["is_current"])

                client.put(
                    "/api/player",
                    json={"player_id": player_id, "username": "Ash-Prime"},
                )
                self.assertEqual(
                    client.get("/api/leaderboard").json()["entries"][0]["username"],
                    "Ash-Prime",
                )
                client.post("/api/player/logout")
                remaining = client.get("/api/leaderboard").json()["entries"][0]
                self.assertEqual(remaining["username"], "Ash-Prime")
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
