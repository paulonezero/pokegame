from __future__ import annotations

import io
import random
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from src import ui_data
from src.distractors import get_distractor_ids
from src.game import points_for_attempt, record_guess

SESSION_COOKIE = "pokegame_session"
ROUND_SECONDS = 30


@dataclass(frozen=True, slots=True)
class Pokemon:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class ValidatedData:
    pokemon: dict[int, Pokemon]
    target_ids: tuple[int, ...]
    similarity: pd.DataFrame
    silhouettes: dict[int, bytes]
    artwork: dict[int, bytes]


@dataclass(frozen=True, slots=True)
class SetupIssue:
    code: str
    message: str
    path: str
    fix_command: str
    consequence: str


class SetupFailure(RuntimeError):
    def __init__(self, issue: SetupIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


@dataclass(slots=True)
class Question:
    target_id: int
    answers: tuple[int, ...]
    guess_state: dict[str, Any] = field(
        default_factory=lambda: {
            "attempt_count": 0,
            "removed_ids": [],
            "completed": False,
            "revealed": False,
        }
    )


@dataclass(slots=True)
class Round:
    deadline: float
    question: Question
    score: int = 0
    found: int = 0
    q_num: int = 1
    streak: int = 0
    best_streak: int = 0
    feedback_kind: Literal["idle", "wrong", "correct"] = "idle"
    feedback_text: str = ""


@dataclass(slots=True)
class Session:
    screen: Literal["start", "play", "result"] = "start"
    best: int = 0
    round: Round | None = None
    target_pool: list[int] = field(default_factory=list)
    last_target: int | None = None


class GuessBody(BaseModel):
    answer_id: int


def _failure(
    code: str,
    message: str,
    consequence: str,
    *,
    path: str | None = None,
) -> SetupFailure:
    if path is None:
        path = (
            "data/pokemon/metadata.json"
            if code.startswith("metadata_")
            else "data/similarity/similarity.csv"
        )
    fix_command = (
        "python scripts/download_images.py"
        if code.startswith("metadata_")
        else "python scripts/build_similarity.py"
    )
    return SetupFailure(
        SetupIssue(
            code=code,
            message=message,
            path=path,
            fix_command=fix_command,
            consequence=consequence,
        )
    )


def _clear_loader_caches() -> None:
    for loader in (ui_data.load_metadata, ui_data.load_similarity, ui_data.load_mask, ui_data.load_art):
        clear = getattr(loader, "clear", None)
        if callable(clear):
            clear()


def _similarity_candidates(data_dir: Path) -> list[Path]:
    similarity_dir = data_dir / "similarity"
    preferred = [
        similarity_dir / "similarity.csv",
        similarity_dir / "similarity_scores.csv",
        similarity_dir / "pokemon_similarity.csv",
        similarity_dir / "scores.csv",
        data_dir / "similarity.csv",
    ]
    discovered = sorted(similarity_dir.glob("*.csv")) if similarity_dir.is_dir() else []
    result: list[Path] = []
    for path in [*preferred, *discovered]:
        if path.is_file() and path not in result:
            result.append(path)
    return result


def _load_similarity(data_dir: Path) -> pd.DataFrame:
    candidates = _similarity_candidates(data_dir)
    if not candidates:
        expected_path = data_dir / "similarity" / "similarity.csv"
        raise _failure(
            "similarity_missing",
            f"The packaged similarity CSV is missing at {expected_path}.",
            "The round cannot start until the similarity index is available.",
            path=str(expected_path),
        )

    failures: list[str] = []
    for path in candidates:
        try:
            return ui_data.load_similarity(str(path))
        except ui_data.SetupError as exc:
            failures.append(str(exc))
        except Exception as exc:
            failures.append(f"Could not read similarity data: {exc}")

    detail = failures[0] if failures else "The similarity CSV could not be read."
    if any("missing required columns" in message.lower() for message in failures):
        raise _failure(
            "similarity_missing_columns",
            detail,
            "Rebuild the similarity CSV with all required score and Pokémon columns, then retry setup.",
        )
    raise _failure(
        "similarity_invalid",
        detail,
        "Replace or rebuild the malformed similarity CSV, then retry setup.",
    )


def _mask_path(data_dir: Path, pokemon_id: int) -> Path:
    masks_dir = data_dir / "silhouettes"
    padded = masks_dir / f"{pokemon_id:03d}.png"
    legacy = masks_dir / f"{pokemon_id}.png"
    return padded if padded.is_file() or not legacy.is_file() else legacy


def _png_bytes(image: Any) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _transparent_silhouette(mask: Any) -> Image.Image:
    height, width = mask.shape
    image = Image.new("RGBA", (width, height), (32, 30, 29, 0))
    alpha = Image.fromarray(mask.astype("uint8") * 255, mode="L")
    image.putalpha(alpha)
    return image


def _load_validated_data(data_dir: Path, *, clear_caches: bool = False) -> ValidatedData:
    if clear_caches:
        _clear_loader_caches()

    metadata_path = data_dir / "pokemon" / "metadata.json"
    if not metadata_path.is_file():
        raise _failure(
            "metadata_missing",
            f"Pokémon metadata was not found at {metadata_path}.",
            "The round cannot start until packaged Generation I metadata is available.",
            path=str(metadata_path),
        )
    try:
        records = ui_data.load_metadata(str(metadata_path))
    except ui_data.SetupError as exc:
        raise _failure(
            "metadata_invalid",
            str(exc),
            "The round cannot start until the metadata is valid JSON with usable records.",
            path=str(metadata_path),
        ) from exc
    except Exception as exc:
        raise _failure(
            "metadata_invalid",
            f"Could not load Pokémon metadata at {metadata_path}: {exc}",
            "The round cannot start until the metadata is valid JSON with usable records.",
            path=str(metadata_path),
        ) from exc

    generation_one = [
        record
        for record in ui_data.generation_one(records)
        if 1 <= int(record["id"]) <= 151
    ]
    pokemon = {
        int(record["id"]): Pokemon(id=int(record["id"]), name=str(record["name"]))
        for record in generation_one
    }
    if len(pokemon) < 4:
        raise _failure(
            "metadata_insufficient",
            f"At least 4 usable Generation I Pokémon records are required in {metadata_path}; found {len(pokemon)}.",
            "The round needs one target and three answer choices before it can start.",
            path=str(metadata_path),
        )

    similarity = _load_similarity(data_dir)
    available_ids = tuple(pokemon)
    similarity = similarity.loc[
        similarity["target_id"].isin(available_ids)
        & similarity["similar_id"].isin(available_ids)
    ].copy()

    silhouettes: dict[int, bytes] = {}
    artwork: dict[int, bytes] = {}
    records_by_id = {int(record["id"]): record for record in generation_one}
    for pokemon_id in sorted(pokemon):
        path = _mask_path(data_dir, pokemon_id)
        if not path.is_file():
            raise _failure(
                "mask_missing",
                f"No silhouette mask was found for Pokémon {pokemon_id}. Expected a 256×256 grayscale PNG at {path}.",
                "The round cannot start until every playable target has a valid mask.",
                path=str(path),
            )
        try:
            mask = ui_data.load_mask(str(path))
            if mask.shape != (256, 256) or bool(mask.all()):
                raise ValueError("expected a non-full 256×256 mask")
            silhouettes[pokemon_id] = _png_bytes(_transparent_silhouette(mask))
        except ui_data.SetupError as exc:
            raise _failure(
                "mask_empty",
                f"The silhouette mask at {path} contains no foreground pixels.",
                "The round cannot start until every playable target has a non-empty mask.",
                path=str(path),
            ) from exc
        except Exception as exc:
            raise _failure(
                "mask_invalid",
                f"The silhouette at {path} is not a valid non-full 256×256 grayscale mask: {exc}",
                "The round cannot start until every playable target has a valid mask.",
                path=str(path),
            ) from exc

        art_path_value = records_by_id[pokemon_id].get("art_path")
        if art_path_value:
            try:
                artwork[pokemon_id] = _png_bytes(ui_data.load_art(str(art_path_value)))
            except Exception:
                # Result artwork is optional; a bad or missing file must not disable the game.
                pass

    for pokemon_id in sorted(pokemon):
        try:
            distractors = get_distractor_ids(
                pokemon_id,
                difficulty="expert",
                count=3,
                similarity_data=similarity,
                random_state=0,
            )
        except ValueError as exc:
            raise _failure(
                "distractors_insufficient",
                f"Pokémon {pokemon_id} does not have three expert distractors: {exc}",
                "Rebuild similarity data with at least three available rank 1–5 neighbors per Pokémon, then retry setup.",
            ) from exc
        if len(set(distractors)) != 3 or any(item not in pokemon for item in distractors):
            raise _failure(
                "distractors_insufficient",
                f"Pokémon {pokemon_id} does not have three unique available expert distractors.",
                "Rebuild similarity data with at least three available rank 1–5 neighbors per Pokémon, then retry setup.",
            )

    return ValidatedData(
        pokemon=pokemon,
        target_ids=tuple(sorted(pokemon)),
        similarity=similarity,
        silhouettes=silhouettes,
        artwork=artwork,
    )


class GameService:
    def __init__(self, data_dir: Path, clock: Callable[[], float]) -> None:
        self.data_dir = data_dir
        self.clock = clock
        self.lock = threading.RLock()
        self.sessions: dict[str, Session] = {}
        self.rng = random.Random(secrets.randbits(128))
        self.data: ValidatedData | None = None
        self.setup_issue: SetupIssue | None = None
        self._reload(clear_caches=False)

    def _reload(self, *, clear_caches: bool) -> None:
        try:
            self.data = _load_validated_data(self.data_dir, clear_caches=clear_caches)
            self.setup_issue = None
        except SetupFailure as exc:
            self.data = None
            self.setup_issue = exc.issue

    def retry_setup(self) -> None:
        self._reload(clear_caches=True)
        for session in self.sessions.values():
            session.screen = "start"
            session.round = None
            session.target_pool.clear()
            session.last_target = None

    def get_session(self, request: Request) -> tuple[str, Session, bool]:
        supplied_id = request.cookies.get(SESSION_COOKIE)
        if supplied_id and supplied_id in self.sessions:
            return supplied_id, self.sessions[supplied_id], False
        session_id = secrets.token_urlsafe(32)
        session = Session()
        self.sessions[session_id] = session
        return session_id, session, True

    def _draw_target(self, session: Session) -> int:
        assert self.data is not None
        if not session.target_pool:
            session.target_pool = list(self.data.target_ids)
            self.rng.shuffle(session.target_pool)
            if (
                len(session.target_pool) > 1
                and session.last_target is not None
                and session.target_pool[-1] == session.last_target
            ):
                session.target_pool[0], session.target_pool[-1] = (
                    session.target_pool[-1],
                    session.target_pool[0],
                )
        target_id = session.target_pool.pop()
        session.last_target = target_id
        return target_id

    def _new_question(self, session: Session) -> Question:
        assert self.data is not None
        target_id = self._draw_target(session)
        distractors = get_distractor_ids(
            target_id,
            difficulty="expert",
            count=3,
            similarity_data=self.data.similarity,
            random_state=self.rng.randrange(0, 2**32),
        )
        answers = [target_id, *distractors]
        self.rng.shuffle(answers)
        return Question(target_id=target_id, answers=tuple(answers))

    def start_round(self, session: Session) -> None:
        session.target_pool.clear()
        session.last_target = None
        session.round = Round(
            deadline=float(self.clock()) + ROUND_SECONDS,
            question=self._new_question(session),
        )
        session.screen = "play"

    def expire(self, session: Session) -> None:
        if session.screen != "play" or session.round is None:
            return
        session.best = max(session.best, session.round.score)
        session.screen = "result"

    def expire_if_due(self, session: Session) -> None:
        if (
            session.screen == "play"
            and session.round is not None
            and float(self.clock()) >= session.round.deadline
        ):
            self.expire(session)

    def state(self, session: Session, *, event: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.setup_issue is not None:
            issue = self.setup_issue
            return {
                "screen": "error",
                "best": session.best,
                "error": {
                    "code": issue.code,
                    "message": issue.message,
                    "path": issue.path,
                    "fix_command": issue.fix_command,
                    "consequence": issue.consequence,
                },
            }
        assert self.data is not None
        if session.screen == "start" or session.round is None:
            return {"screen": "start", "best": session.best}
        if session.screen == "result":
            round_state = session.round
            target_id = round_state.question.target_id
            target = self.data.pokemon[target_id]
            artwork_url = (
                f"/api/assets/artwork/{target_id}.png"
                if target_id in self.data.artwork
                else None
            )
            return {
                "screen": "result",
                "score": round_state.score,
                "found": round_state.found,
                "best": session.best,
                "best_streak": round_state.best_streak,
                "target_id": target.id,
                "target_name": target.name,
                "artwork_url": artwork_url,
                "final_target": {
                    "id": target.id,
                    "name": target.name,
                    "artwork_url": artwork_url,
                },
            }

        round_state = session.round
        question = round_state.question
        target = self.data.pokemon[question.target_id]
        guess_state = question.guess_state
        payload: dict[str, Any] = {
            "screen": "play",
            "deadline_ms": int(round_state.deadline * 1000),
            "total_seconds": ROUND_SECONDS,
            "score": round_state.score,
            "found": round_state.found,
            "q_num": round_state.q_num,
            "attempt": int(guess_state.get("attempt_count", 0)) + 1,
            "points_available": points_for_attempt(
                int(guess_state.get("attempt_count", 0)) + 1
            ),
            "streak": round_state.streak,
            "best_streak": round_state.best_streak,
            "best": session.best,
            "feedback": {
                "kind": round_state.feedback_kind,
                "text": round_state.feedback_text,
            },
            "question": {
                "target_id": target.id,
                "silhouette_url": f"/api/assets/silhouettes/{target.id}.png",
                "answers": [
                    {"id": answer_id, "name": self.data.pokemon[answer_id].name}
                    for answer_id in question.answers
                ],
                "removed_ids": list(guess_state.get("removed_ids", [])),
                "revealed": bool(guess_state.get("revealed", False)),
                "target_name": target.name,
            },
        }
        if event is not None:
            payload["event"] = event
        return payload

    def guess(self, session: Session, answer_id: int) -> dict[str, Any]:
        self.expire_if_due(session)
        if session.screen != "play" or session.round is None:
            return self.state(session)

        round_state = session.round
        question = round_state.question
        guess_state = question.guess_state
        removed_ids = {int(value) for value in guess_state.get("removed_ids", [])}
        if (
            bool(guess_state.get("completed", False))
            or answer_id in removed_ids
            or answer_id not in question.answers
        ):
            return self.state(session)

        updated = record_guess(
            guess_state,
            target_id=question.target_id,
            answer_id=answer_id,
        )
        question.guess_state = updated
        assert self.data is not None
        answer = self.data.pokemon[answer_id]

        if answer_id != question.target_id:
            round_state.streak = 0
            points_left = points_for_attempt(int(updated["attempt_count"]) + 1)
            round_state.feedback_kind = "wrong"
            round_state.feedback_text = (
                f"Not {answer.name} — removed. {points_left} pts left."
            )
            return self.state(
                session,
                event={
                    "kind": "wrong",
                    "answer_id": answer.id,
                    "name": answer.name,
                    "points_left": points_left,
                },
            )

        points = points_for_attempt(int(updated["attempt_count"]))
        round_state.score += points
        round_state.found += 1
        if int(updated["attempt_count"]) == 1:
            round_state.streak += 1
            round_state.best_streak = max(round_state.best_streak, round_state.streak)
        round_state.feedback_kind = "correct"
        round_state.feedback_text = (
            f"Correct — {answer.name} · +{points}"
            if points
            else f"Correct — {answer.name} · no points left"
        )
        return self.state(
            session,
            event={
                "kind": "correct",
                "answer_id": answer.id,
                "name": answer.name,
                "points": points,
            },
        )

    def advance(self, session: Session) -> dict[str, Any]:
        self.expire_if_due(session)
        if session.screen != "play" or session.round is None:
            return self.state(session)
        round_state = session.round
        if not bool(round_state.question.guess_state.get("completed", False)):
            return self.state(session)
        round_state.question = self._new_question(session)
        round_state.q_num += 1
        round_state.feedback_kind = "idle"
        round_state.feedback_text = ""
        return self.state(session)


def _set_session_cookie(response: Response, session_id: str, is_new: bool) -> None:
    if is_new:
        response.set_cookie(
            key=SESSION_COOKIE,
            value=session_id,
            httponly=True,
            samesite="lax",
        )


def create_app(
    data_dir: Path | None = None,
    clock: Callable[[], float] | None = None,
) -> FastAPI:
    """Create an isolated game API with in-memory browser sessions."""
    resolved_data_dir = Path(data_dir or ui_data.DATA_DIR).expanduser().resolve()
    service = GameService(resolved_data_dir, clock or time.time)
    api = FastAPI(title="Pokégame API")
    api.state.game_service = service

    @api.get("/api/state")
    def get_state(request: Request, response: Response) -> dict[str, Any]:
        with service.lock:
            session_id, session, is_new = service.get_session(request)
            service.expire_if_due(session)
            payload = service.state(session)
            _set_session_cookie(response, session_id, is_new)
            return payload

    @api.post("/api/round/start")
    def start_round(request: Request, response: Response) -> dict[str, Any]:
        with service.lock:
            session_id, session, is_new = service.get_session(request)
            if service.setup_issue is None:
                service.start_round(session)
            payload = service.state(session)
            _set_session_cookie(response, session_id, is_new)
            return payload

    @api.post("/api/round/guess")
    def guess(body: GuessBody, request: Request, response: Response) -> dict[str, Any]:
        with service.lock:
            session_id, session, is_new = service.get_session(request)
            payload = service.guess(session, int(body.answer_id))
            _set_session_cookie(response, session_id, is_new)
            return payload

    @api.post("/api/round/advance")
    def advance(request: Request, response: Response) -> dict[str, Any]:
        with service.lock:
            session_id, session, is_new = service.get_session(request)
            payload = service.advance(session)
            _set_session_cookie(response, session_id, is_new)
            return payload

    @api.post("/api/round/expire")
    def expire(request: Request, response: Response) -> dict[str, Any]:
        with service.lock:
            session_id, session, is_new = service.get_session(request)
            service.expire(session)
            payload = service.state(session)
            _set_session_cookie(response, session_id, is_new)
            return payload

    @api.post("/api/back")
    def back(request: Request, response: Response) -> dict[str, Any]:
        with service.lock:
            session_id, session, is_new = service.get_session(request)
            session.screen = "start"
            session.round = None
            payload = service.state(session)
            _set_session_cookie(response, session_id, is_new)
            return payload

    @api.post("/api/setup/retry")
    def retry_setup(request: Request, response: Response) -> dict[str, Any]:
        with service.lock:
            session_id, session, is_new = service.get_session(request)
            service.retry_setup()
            payload = service.state(session)
            _set_session_cookie(response, session_id, is_new)
            return payload

    @api.get("/api/assets/silhouettes/{pokemon_id}.png")
    def silhouette(pokemon_id: int) -> Response:
        with service.lock:
            content = service.data.silhouettes.get(pokemon_id) if service.data else None
        if content is None:
            raise HTTPException(status_code=404, detail="Silhouette not found")
        return Response(content=content, media_type="image/png")

    @api.get("/api/assets/artwork/{pokemon_id}.png")
    def artwork(pokemon_id: int) -> Response:
        with service.lock:
            content = service.data.artwork.get(pokemon_id) if service.data else None
        if content is None:
            raise HTTPException(status_code=404, detail="Artwork not found")
        return Response(content=content, media_type="image/png")

    frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if frontend_dist.is_dir():
        api.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return api


app = create_app()
