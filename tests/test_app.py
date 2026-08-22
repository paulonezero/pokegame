from __future__ import annotations

import csv
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from server.app import create_app


@dataclass
class FakeClock:
    value: float = 1_000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def build_data(
    data_dir: Path,
    pokemon_ids: range | None = None,
    generation: int = 1,
) -> None:
    pokemon_ids = range(1, 5) if pokemon_ids is None else pokemon_ids
    pokemon_dir = data_dir / "pokemon"
    artwork_dir = pokemon_dir / "artwork"
    silhouettes_dir = data_dir / "silhouettes"
    similarity_dir = data_dir / "similarity"
    for directory in (artwork_dir, silhouettes_dir, similarity_dir):
        directory.mkdir(parents=True, exist_ok=True)

    metadata: list[dict[str, object]] = []
    for pokemon_id in pokemon_ids:
        artwork_path = artwork_dir / f"{pokemon_id}.png"
        red = 40 + pokemon_id % 5 * 40
        Image.new("RGBA", (64, 64), (red, 100, 140, 255)).save(artwork_path)

        silhouette = Image.new("L", (256, 256), 0)
        draw = ImageDraw.Draw(silhouette)
        draw.rectangle((48 + pokemon_id, 40, 206, 216), fill=255)
        silhouette.save(silhouettes_dir / f"{pokemon_id:03d}.png")

        metadata.append(
            {
                "id": pokemon_id,
                "name": f"fixture-{pokemon_id}",
                "generation": generation,
                "artwork_path": str(artwork_path),
            }
        )
    (pokemon_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    columns = [
        "target_id",
        "target_name",
        "similar_id",
        "similar_name",
        "overall_score",
        "contour_score",
        "iou_score",
        "radial_score",
        "geometric_score",
    ]
    with (similarity_dir / "similarity.csv").open(
        "w", encoding="utf-8", newline=""
    ) as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for target_id in pokemon_ids:
            for similar_id in pokemon_ids:
                if target_id == similar_id:
                    continue
                score = 1.0 - abs(target_id - similar_id) / 10
                writer.writerow(
                    {
                        "target_id": target_id,
                        "target_name": f"fixture-{target_id}",
                        "similar_id": similar_id,
                        "similar_name": f"fixture-{similar_id}",
                        "overall_score": score,
                        "contour_score": score,
                        "iou_score": score,
                        "radial_score": score,
                        "geometric_score": score,
                    }
                )


class FastAPIRoundFlowTests(unittest.TestCase):
    def test_round_flow_timeout_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            build_data(data_dir)
            clock = FakeClock()

            with TestClient(create_app(data_dir, clock)) as client:
                initial = client.get("/api/state")
                self.assertEqual(initial.status_code, 200)
                self.assertEqual(initial.json(), {"screen": "start", "best": 0})
                self.assertIn("HttpOnly", initial.headers["set-cookie"])
                self.assertIn("SameSite=lax", initial.headers["set-cookie"])

                started = client.post("/api/round/start").json()
                self.assertEqual(started["screen"], "play")
                self.assertEqual(len(started["question"]["answers"]), 4)
                self.assertEqual(
                    len({answer["id"] for answer in started["question"]["answers"]}), 4
                )
                self.assertEqual(started["attempt"], 1)
                self.assertEqual(started["points_available"], 3)
                original_deadline = started["deadline_ms"]
                original_answers = started["question"]["answers"]
                target_id = started["question"]["target_id"]
                wrong_id = next(
                    answer["id"] for answer in original_answers if answer["id"] != target_id
                )

                wrong = client.post(
                    "/api/round/guess", json={"answer_id": wrong_id}
                ).json()
                self.assertEqual(wrong["event"]["kind"], "wrong")
                self.assertEqual(wrong["event"]["points_left"], 2)
                self.assertEqual(wrong["question"]["removed_ids"], [wrong_id])
                self.assertNotIn(target_id, wrong["question"]["removed_ids"])
                self.assertEqual(wrong["question"]["answers"], original_answers)
                self.assertEqual(wrong["attempt"], 2)
                self.assertEqual(wrong["points_available"], 2)
                self.assertEqual(wrong["deadline_ms"], original_deadline)
                self.assertEqual(
                    wrong["feedback"]["text"],
                    f"Not Fixture {wrong_id} — removed. 2 pts left.",
                )

                correct = client.post(
                    "/api/round/guess", json={"answer_id": target_id}
                ).json()
                self.assertEqual(correct["event"]["kind"], "correct")
                self.assertEqual(correct["event"]["points"], 2)
                self.assertTrue(correct["question"]["revealed"])
                self.assertEqual(
                    correct["question"]["artwork_url"],
                    f"/api/assets/artwork/{target_id}.png",
                )
                artwork = client.get(correct["question"]["artwork_url"])
                self.assertEqual(artwork.status_code, 200)
                self.assertEqual(artwork.headers["content-type"], "image/png")
                self.assertEqual(correct["score"], 2)
                self.assertEqual(correct["found"], 1)
                self.assertEqual(correct["deadline_ms"], original_deadline)
                self.assertEqual(
                    correct["feedback"]["text"],
                    f"Correct — Fixture {target_id} · +2",
                )

                locked = client.post(
                    "/api/round/guess", json={"answer_id": target_id}
                ).json()
                self.assertNotIn("event", locked)
                self.assertEqual(locked["score"], 2)

                advanced = client.post("/api/round/advance").json()
                self.assertEqual(advanced["screen"], "play")
                self.assertNotEqual(advanced["question"]["target_id"], target_id)
                self.assertEqual(advanced["deadline_ms"], original_deadline)
                self.assertEqual(advanced["score"], 2)
                self.assertEqual(advanced["found"], 1)
                self.assertEqual(advanced["q_num"], 2)
                self.assertEqual(advanced["attempt"], 1)
                self.assertEqual(len(advanced["question"]["answers"]), 4)

                clock.advance(31)
                late_target = advanced["question"]["target_id"]
                result = client.post(
                    "/api/round/guess", json={"answer_id": late_target}
                ).json()
                self.assertEqual(result["screen"], "result")
                self.assertEqual(result["score"], 2)
                self.assertEqual(result["found"], 1)
                self.assertEqual(result["best"], 2)

                unavailable = client.post(
                    "/api/round/guess", json={"answer_id": late_target}
                ).json()
                self.assertEqual(unavailable, result)
                self.assertEqual(client.post("/api/round/advance").json(), result)

                replay = client.post("/api/round/start").json()
                self.assertEqual(replay["screen"], "play")
                self.assertEqual(replay["score"], 0)
                self.assertEqual(replay["found"], 0)
                self.assertEqual(replay["attempt"], 1)
                self.assertEqual(replay["best"], 2)
                self.assertEqual(replay["deadline_ms"], int((clock.value + 30) * 1000))
                self.assertEqual(len(replay["question"]["answers"]), 4)

    def test_target_pool_has_no_repeat_until_refill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            build_data(data_dir)
            with TestClient(create_app(data_dir, FakeClock())) as client:
                state = client.post("/api/round/start").json()
                targets: list[int] = []
                for _ in range(4):
                    target_id = state["question"]["target_id"]
                    targets.append(target_id)
                    client.post("/api/round/guess", json={"answer_id": target_id})
                    state = client.post("/api/round/advance").json()

                self.assertEqual(len(set(targets)), 4)
                self.assertNotEqual(state["question"]["target_id"], targets[-1])

    def test_later_generations_are_included_in_the_target_pool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            expected_ids = set(range(152, 156))
            build_data(data_dir, range(152, 156), generation=2)

            with TestClient(create_app(data_dir, FakeClock())) as client:
                state = client.post("/api/round/start").json()
                targets: set[int] = set()
                for _ in expected_ids:
                    target_id = state["question"]["target_id"]
                    targets.add(target_id)
                    client.post("/api/round/guess", json={"answer_id": target_id})
                    state = client.post("/api/round/advance").json()

                self.assertEqual(targets, expected_ids)

    def test_setup_error_is_actionable_and_retry_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            with TestClient(create_app(data_dir, FakeClock())) as client:
                error = client.get("/api/state").json()
                self.assertEqual(error["screen"], "error")
                self.assertEqual(error["error"]["code"], "metadata_missing")
                self.assertEqual(
                    error["error"]["path"], str(data_dir / "pokemon" / "metadata.json")
                )
                self.assertEqual(
                    error["error"]["fix_command"], "python scripts/download_images.py"
                )
                self.assertTrue(error["error"]["consequence"])

                build_data(data_dir)
                recovered = client.post("/api/setup/retry").json()
                self.assertEqual(recovered, {"screen": "start", "best": 0})
                self.assertEqual(client.post("/api/round/start").json()["screen"], "play")


if __name__ == "__main__":
    unittest.main()
