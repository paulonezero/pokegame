from __future__ import annotations

import hashlib
import random
import time
from math import ceil

import streamlit as st

from src.distractors import get_distractor_ids
from src.game import points_for_attempt, record_guess

from src.ui_data import (
    SetupError,
    find_similarity_csv,
    generation_one,
    load_art,
    load_metadata,
    load_rendered_silhouette,
    load_similarity,
    rows_for_target,
)

ROUND_SECONDS = 30
DIFFICULTY = "expert"


def state_int(value: object, default: int = 0) -> int:
    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        try:
            return int(value)
        except ValueError:
            pass
    return default


def state_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        try:
            return float(value)
        except ValueError:
            pass
    return default


st.set_page_config(page_title="Who's That Pokémon?", page_icon="❓", layout="centered")
st.markdown(
    """
    <style>
    [data-testid="stHeader"], [data-testid="stToolbar"], #MainMenu, footer {
        display: none;
    }
    .block-container {
        max-width: 820px;
        padding: 0.45rem 0.9rem 0.35rem;
    }
    [data-testid="stVerticalBlock"] {
        gap: 0.42rem;
    }
    h1 {
        font-size: 1.75rem !important;
        line-height: 1.1 !important;
        margin: 0 0 0.1rem !important;
    }
    h2, h3 {
        margin: 0.15rem 0 !important;
    }
    p {
        margin-bottom: 0.15rem;
    }
    hr {
        margin: 0.2rem 0 !important;
    }
    [data-testid="stMetric"] {
        padding: 0.2rem 0.45rem;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.82rem;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.45rem;
    }
    [data-testid="stImage"] {
        display: flex;
        justify-content: center;
    }
    [data-testid="stImage"] img {
        max-height: 245px;
        width: auto;
        object-fit: contain;
    }
    div.stButton > button {
        min-height: 3rem;
        font-size: 1rem;
        font-weight: 600;
        border-radius: 0.7rem;
        touch-action: manipulation;
    }
    [data-testid="stAlert"] {
        padding: 0.45rem 0.7rem;
    }
    @media (orientation: landscape) and (max-height: 850px) {
        .block-container {
            padding-top: 0.25rem;
        }
        [data-testid="stVerticalBlock"] {
            gap: 0.28rem;
        }
        [data-testid="stImage"] img {
            max-height: 205px;
        }
        div.stButton > button {
            min-height: 2.8rem;
        }
    }
    @media (max-width: 700px) {
        .block-container {
            padding-left: 0.65rem;
            padding-right: 0.65rem;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Who's That Pokémon?")
st.caption("Identify as many random Gen I silhouettes as you can in 30 seconds.")

try:
    pokemon = generation_one(load_metadata())
except SetupError as exc:
    st.error(str(exc))
    st.info(
        "Generate `data/pokemon/metadata.json` before starting the app. Records may be a JSON list "
        "or a `pokemon` list and should include `id`, `name`, and a local `artwork_path`."
    )
    st.stop()

if len(pokemon) < 4:
    st.error("At least four Gen I metadata records are required to build a guessing round.")
    st.stop()

try:
    similarity_path = find_similarity_csv()
    similarity = load_similarity(str(similarity_path))
except SetupError as exc:
    st.error(str(exc))
    st.info("Place the generated pair rows in a CSV under `data/similarity/`, then rerun the app.")
    st.stop()

pokemon_by_id = {record["id"]: record for record in pokemon}
pokemon_ids = list(pokemon_by_id)
available_similarity = similarity.loc[
    similarity["target_id"].isin(pokemon_by_id)
    & similarity["similar_id"].isin(pokemon_by_id)
]


def build_answers(
    target_id: int,
    round_number: int,
    question_number: int,
) -> tuple[list[int], str | None]:
    if len(rows_for_target(available_similarity, target_id)) < 3:
        return [], "This Pokémon needs at least three Gen I similarity rows before it can be used."

    seed_text = f"{target_id}:{DIFFICULTY}:{round_number}:{question_number}:pokegame-v3"
    seed = int.from_bytes(hashlib.sha256(seed_text.encode("utf-8")).digest()[:8], "big")
    try:
        distractors = get_distractor_ids(
            target_id,
            difficulty=DIFFICULTY,
            count=3,
            similarity_data=available_similarity,
            random_state=seed,
        )
    except ValueError as exc:
        return [], str(exc)

    answers = [target_id, *distractors]
    random.Random(seed).shuffle(answers)
    return answers, None


def create_question(
    target_id: int,
    round_number: int,
    question_number: int,
) -> dict[str, object]:
    answers, answer_error = build_answers(target_id, round_number, question_number)
    return {
        "target_id": target_id,
        "question_number": question_number,
        "answers": answers,
        "removed_ids": [],
        "attempt_count": 0,
        "selected_id": None,
        "last_wrong_id": None,
        "completed": False,
        "revealed": False,
        "outcome": None,
        "points": 0,
        "error": answer_error,
    }


def shuffled_targets() -> list[int]:
    targets = pokemon_ids.copy()
    random.SystemRandom().shuffle(targets)
    return targets


def create_round(round_number: int) -> dict[str, object]:
    target_order = shuffled_targets()
    target_id = target_order.pop()
    return {
        "signature": f"round:{round_number}",
        "round_number": round_number,
        "deadline": time.time() + ROUND_SECONDS,
        "completed": False,
        "score": 0,
        "correct_count": 0,
        "question_number": 1,
        "remaining_targets": target_order,
        "last_result": None,
        "question": create_question(target_id, round_number, 1),
    }


def start_new_round() -> dict[str, object]:
    round_number = state_int(st.session_state.get("home_round_number"), 0) + 1
    st.session_state.home_round_number = round_number
    round_state = create_round(round_number)
    st.session_state.home_round = round_state
    return round_state


def advance_question(round_state: dict[str, object]) -> dict[str, object]:
    updated = dict(round_state)
    remaining_value = updated.get("remaining_targets", [])
    remaining = [int(value) for value in remaining_value] if isinstance(remaining_value, list) else []
    if not remaining:
        remaining = shuffled_targets()
        current_question = updated.get("question")
        if isinstance(current_question, dict):
            current_target = state_int(current_question.get("target_id"), -1)
            remaining = [target for target in remaining if target != current_target]

    target_id = remaining.pop()
    question_number = state_int(updated.get("question_number"), 0) + 1
    round_number = state_int(updated.get("round_number"), 1)
    updated["remaining_targets"] = remaining
    updated["question_number"] = question_number
    updated["question"] = create_question(target_id, round_number, question_number)
    return updated


def complete_round(round_state: dict[str, object]) -> dict[str, object]:
    updated = dict(round_state)
    updated["completed"] = True
    st.session_state.home_best_score = max(
        state_int(st.session_state.get("home_best_score"), 0),
        state_int(updated.get("score"), 0),
    )
    return updated


st.session_state.setdefault("home_best_score", 0)
stored_round = st.session_state.get("home_round")
valid_round = (
    isinstance(stored_round, dict)
    and isinstance(stored_round.get("deadline"), (int, float))
    and isinstance(stored_round.get("question"), dict)
)
if not valid_round:
    stored_round = start_new_round()

if not isinstance(stored_round, dict):
    st.error("The round could not be initialized. Reload the page to start again.")
    st.stop()
round_state: dict[str, object] = dict(stored_round)
round_signature = str(round_state.get("signature", "round"))
round_completed = bool(round_state.get("completed", False))
question_value = round_state.get("question")
if not isinstance(question_value, dict):
    st.error("The current question was invalid. Start a new round to continue.")
    st.stop()
question: dict[str, object] = dict(question_value)

question_error = question.get("error")
if question_error:
    st.error(str(question_error))
    st.stop()


def finish_if_expired(current: dict[str, object]) -> dict[str, object]:
    if not bool(current.get("completed", False)) and time.time() >= state_float(current["deadline"]):
        return complete_round(current)
    return current


@st.fragment(run_every="1s")
def render_countdown(signature: str) -> None:
    current = st.session_state.get("home_round")
    if not isinstance(current, dict) or current.get("signature") != signature:
        st.metric("Time left", "—")
        return

    remaining = max(0, ceil(state_float(current["deadline"]) - time.time()))
    st.metric("Time left", f"{remaining}s")
    if remaining == 0 and not bool(current.get("completed", False)):
        st.session_state.home_round = complete_round(current)
        st.rerun()


status_columns = st.columns(3)
with status_columns[0]:
    render_countdown(round_signature)
with status_columns[1]:
    st.metric("Score", state_int(round_state.get("score"), 0))
with status_columns[2]:
    st.metric("Pokémon caught", state_int(round_state.get("correct_count"), 0))

if round_completed:
    target_id = state_int(question.get("target_id"), 0)
    st.divider()
    st.error("Time's up!")
    art_path = pokemon_by_id.get(target_id, {}).get("art_path")
    if art_path:
        try:
            st.image(load_art(art_path), width=245)
        except (OSError, ValueError):
            pass
    if target_id in pokemon_by_id:
        st.subheader(f"The final Pokémon was {pokemon_by_id[target_id]['name']}")

    score = state_int(round_state.get("score"), 0)
    correct_count = state_int(round_state.get("correct_count"), 0)
    st.success(
        f"Round complete: **{score} points** from **{correct_count} Pokémon**."
    )
    st.caption(f"Best score this session: {state_int(st.session_state.home_best_score)}")
    if st.button("Play again", type="primary", use_container_width=True):
        start_new_round()
        st.rerun()
else:
    target_id = state_int(question.get("target_id"), 0)
    stored_answers = question.get("answers")
    removed_values = question.get("removed_ids", [])
    if not isinstance(stored_answers, list) or not isinstance(removed_values, list):
        st.error("The current question data was invalid. Start a new round to continue.")
        st.stop()
    answer_ids = [int(answer_id) for answer_id in stored_answers]
    removed_ids = {int(answer_id) for answer_id in removed_values}
    attempt_count = state_int(question.get("attempt_count"), 0)
    question_number = state_int(question.get("question_number"), 1)

    st.divider()
    try:
        st.image(load_rendered_silhouette(target_id), width=245)
    except SetupError as exc:
        st.error(str(exc))
        st.info("Generate masks as `data/silhouettes/{id:03d}.png` and rerun the app.")
        st.stop()

    last_result = round_state.get("last_result")
    last_wrong_value = question.get("last_wrong_id")
    if isinstance(last_result, dict):
        awarded = state_int(last_result.get("points"), 0)
        name = str(last_result.get("name", "Pokémon"))
        st.success(f"Correct — {name}! +{awarded} point{'s' if awarded != 1 else ''}. Next Pokémon:")
    elif isinstance(last_wrong_value, int):
        wrong_name = pokemon_by_id.get(last_wrong_value, {"name": "That answer"})["name"]
        st.warning(f"Not quite — {wrong_name} has been removed. Try again.")

    active_answer_ids = [answer_id for answer_id in answer_ids if answer_id not in removed_ids]
    available_points = points_for_attempt(attempt_count + 1)
    st.write(
        f"**Pokémon {question_number} · Guess {attempt_count + 1} · "
        f"{available_points} point{'s' if available_points != 1 else ''} available**"
    )
    column_count = 1 if len(active_answer_ids) == 1 else 2
    answer_columns = st.columns(column_count)
    for index, answer_id in enumerate(active_answer_ids):
        record = pokemon_by_id[answer_id]
        with answer_columns[index % column_count]:
            if st.button(
                record["name"],
                key=f"answer_{round_signature}_{question_number}_{answer_id}",
                use_container_width=True,
            ):
                current_value = st.session_state.home_round
                if not isinstance(current_value, dict):
                    st.stop()
                current_round = finish_if_expired(dict(current_value))
                if bool(current_round.get("completed", False)):
                    st.session_state.home_round = current_round
                    st.rerun()

                current_question_value = current_round.get("question")
                if not isinstance(current_question_value, dict):
                    st.stop()
                updated_question = record_guess(
                    current_question_value,
                    target_id=target_id,
                    answer_id=answer_id,
                )
                if updated_question.get("outcome") == "correct":
                    awarded = state_int(updated_question.get("points"), 0)
                    current_round["score"] = state_int(current_round.get("score"), 0) + awarded
                    current_round["correct_count"] = (
                        state_int(current_round.get("correct_count"), 0) + 1
                    )
                    current_round["last_result"] = {
                        "name": pokemon_by_id[target_id]["name"],
                        "points": awarded,
                    }
                    current_round["question"] = updated_question
                    current_round = advance_question(current_round)
                else:
                    current_round["question"] = updated_question
                    current_round["last_result"] = None
                st.session_state.home_round = current_round
                st.rerun()


