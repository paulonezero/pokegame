"""Core library for the Pokémon silhouette similarity prototype."""

from .config import (
    DEFAULT_CANVAS_SIZE,
    DEFAULT_PADDING,
    DEFAULT_RADIAL_SAMPLES,
    DEFAULT_WEIGHTS,
    GEN_I_IDS,
    PROJECT_ROOT,
)
from .distractors import DIFFICULTY_RANKS, get_distractor_ids, get_distractors
from .game import end_round, points_for_attempt, record_guess
from .features import (
    deserialize_features,
    extract_features,
    load_features,
    save_features,
    serialize_features,
)
from .image_processing import (
    alpha_to_binary_mask,
    load_silhouette,
    process_and_save_silhouette,
    process_silhouette,
    save_silhouette,
)
from .pokemon_data import (
    get_artwork_path,
    get_features_path,
    get_pokemon_record,
    get_silhouette_path,
    load_pokemon_metadata,
    pokemon_ids,
)
from .similarity import (
    SIMILARITY_COLUMNS,
    best_shifted_iou,
    compare_silhouettes,
    load_similarity_csv,
    normalize_weights,
    reweight_score_rows,
    score_pair,
)

__all__ = [
    "DEFAULT_CANVAS_SIZE",
    "DEFAULT_PADDING",
    "DEFAULT_RADIAL_SAMPLES",
    "DEFAULT_WEIGHTS",
    "DIFFICULTY_RANKS",
    "GEN_I_IDS",
    "PROJECT_ROOT",
    "SIMILARITY_COLUMNS",
    "alpha_to_binary_mask",
    "best_shifted_iou",
    "compare_silhouettes",
    "deserialize_features",
    "end_round",
    "extract_features",
    "get_artwork_path",
    "get_distractor_ids",
    "get_distractors",
    "get_features_path",
    "get_pokemon_record",
    "get_silhouette_path",
    "load_features",
    "load_pokemon_metadata",
    "load_silhouette",
    "load_similarity_csv",
    "normalize_weights",
    "points_for_attempt",
    "pokemon_ids",
    "process_and_save_silhouette",
    "process_silhouette",
    "record_guess",
    "reweight_score_rows",
    "save_features",
    "save_silhouette",
    "score_pair",
    "serialize_features",
]
