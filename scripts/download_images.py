#!/usr/bin/env python3
"""Download Pokémon metadata and official artwork for local data setup.

Run from the project root with:
    python scripts/download_images.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_API_URL = "https://pokeapi.co/api/v2/pokemon/{id}"
DEFAULT_ARTWORK_URL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/"
    "sprites/pokemon/other/official-artwork/{id}.png"
)
USER_AGENT = "pokegame-data-setup/1.0 (+https://pokeapi.co/)"
ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}


class DownloadError(RuntimeError):
    """A network resource could not be downloaded after retries."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download PokéAPI metadata and official transparent artwork."
    )
    parser.add_argument("--start-id", type=int, default=1, help="first Pokémon ID (default: 1)")
    parser.add_argument("--end-id", type=int, default=151, help="last Pokémon ID (default: 151)")
    parser.add_argument("--force", action="store_true", help="replace existing metadata and artwork")
    parser.add_argument("--timeout", type=float, default=20.0, help="request timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="retries after the initial request")
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=1.0,
        help="initial retry delay in seconds (exponential backoff)",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=Path("data/pokemon/metadata.json"),
        help="metadata path, relative to project root by default",
    )
    parser.add_argument(
        "--artwork-dir",
        type=Path,
        default=Path("data/pokemon/artwork"),
        help="artwork directory, relative to project root by default",
    )
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help=argparse.SUPPRESS)
    parser.add_argument("--artwork-url", default=DEFAULT_ARTWORK_URL, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.start_id < 1:
        parser.error("--start-id must be at least 1")
    if args.end_id < args.start_id:
        parser.error("--end-id must be greater than or equal to --start-id")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.retries < 0:
        parser.error("--retries cannot be negative")
    if args.retry_delay < 0:
        parser.error("--retry-delay cannot be negative")
    return args


def project_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def request_bytes(
    url: str,
    *,
    timeout: float,
    retries: int,
    retry_delay: float,
    label: str,
) -> bytes:
    """Fetch bytes with bounded exponential retries and actionable errors."""
    last_error: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            last_error = exc
            # Retrying permanent client errors only delays the useful fallback/error.
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or attempt == retries:
                break
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == retries:
                break

        delay = retry_delay * (2**attempt)
        print(
            f"    retry {attempt + 1}/{retries} for {label} in {delay:.1f}s: {last_error}",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(delay)

    raise DownloadError(f"could not fetch {label} ({url}): {last_error}")


def request_json(
    url: str,
    *,
    timeout: float,
    retries: int,
    retry_delay: float,
    label: str,
) -> dict[str, Any]:
    payload = request_bytes(
        url,
        timeout=timeout,
        retries=retries,
        retry_delay=retry_delay,
        label=label,
    )
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DownloadError(f"{label} returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise DownloadError(f"{label} returned JSON of type {type(value).__name__}, expected object")
    return value


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    try:
        with temporary.open("wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    payload = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    atomic_write_bytes(path, payload)


def load_existing_metadata(path: Path) -> dict[int, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read existing metadata at {path}: {exc}") from exc

    if isinstance(value, dict) and isinstance(value.get("pokemon"), list):
        value = value["pokemon"]
    if not isinstance(value, list):
        raise SystemExit(f"Existing metadata at {path} must be a JSON list")

    records: dict[int, dict[str, Any]] = {}
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            raise SystemExit(f"Invalid metadata record at index {index} in {path}")
        pokemon_id = item["id"]
        if pokemon_id in records:
            raise SystemExit(f"Duplicate Pokémon ID {pokemon_id} in {path}")
        records[pokemon_id] = item
    return records


def metadata_is_complete(record: dict[str, Any] | None, artwork_path: Path) -> bool:
    return bool(
        record
        and isinstance(record.get("name"), str)
        and record.get("name")
        and isinstance(record.get("generation"), int)
        and isinstance(record.get("types"), list)
        and record.get("types")
        and isinstance(record.get("artwork_path"), str)
        and artwork_path.is_file()
        and artwork_path.stat().st_size > 0
    )


def roman_to_int(value: str) -> int:
    text = value.lower()
    if not text or any(character not in ROMAN_VALUES for character in text):
        raise ValueError(f"invalid Roman numeral {value!r}")
    result = 0
    previous = 0
    for character in reversed(text):
        current = ROMAN_VALUES[character]
        if current < previous:
            result -= current
        else:
            result += current
            previous = current
    return result


def generation_number(species: dict[str, Any]) -> int:
    generation = species.get("generation")
    name = generation.get("name") if isinstance(generation, dict) else None
    match = re.fullmatch(r"generation-([ivxlc]+)", name or "", flags=re.IGNORECASE)
    if not match:
        raise DownloadError(f"species response has an unrecognized generation: {name!r}")
    return roman_to_int(match.group(1))


def fallback_artwork_url(pokemon: dict[str, Any]) -> str | None:
    sprites = pokemon.get("sprites")
    if not isinstance(sprites, dict):
        return None
    other = sprites.get("other")
    if not isinstance(other, dict):
        return None
    official = other.get("official-artwork")
    if not isinstance(official, dict):
        return None
    url = official.get("front_default")
    return url if isinstance(url, str) and url.startswith(("http://", "https://")) else None


def relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def download_one(
    pokemon_id: int,
    artwork_path: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    pokemon = request_json(
        args.api_url.format(id=pokemon_id),
        timeout=args.timeout,
        retries=args.retries,
        retry_delay=args.retry_delay,
        label=f"Pokémon {pokemon_id} metadata",
    )

    species = pokemon.get("species")
    species_url = species.get("url") if isinstance(species, dict) else None
    if not isinstance(species_url, str):
        raise DownloadError(f"Pokémon {pokemon_id} metadata does not contain a species URL")
    species_data = request_json(
        species_url,
        timeout=args.timeout,
        retries=args.retries,
        retry_delay=args.retry_delay,
        label=f"Pokémon {pokemon_id} species metadata",
    )

    name = pokemon.get("name")
    if not isinstance(name, str) or not name:
        raise DownloadError(f"Pokémon {pokemon_id} metadata has no valid name")

    raw_types = pokemon.get("types")
    if not isinstance(raw_types, list):
        raise DownloadError(f"Pokémon {pokemon_id} metadata has no valid types list")
    ordered_types: list[tuple[int, str]] = []
    for item in raw_types:
        type_data = item.get("type") if isinstance(item, dict) else None
        type_name = type_data.get("name") if isinstance(type_data, dict) else None
        slot = item.get("slot") if isinstance(item, dict) else None
        if not isinstance(type_name, str) or not isinstance(slot, int):
            raise DownloadError(f"Pokémon {pokemon_id} metadata contains an invalid type entry")
        ordered_types.append((slot, type_name))
    if not ordered_types:
        raise DownloadError(f"Pokémon {pokemon_id} metadata contains no types")

    if args.force or not artwork_path.is_file() or artwork_path.stat().st_size == 0:
        primary_url = args.artwork_url.format(id=pokemon_id)
        fallback_url = fallback_artwork_url(pokemon)
        try:
            artwork = request_bytes(
                primary_url,
                timeout=args.timeout,
                retries=args.retries,
                retry_delay=args.retry_delay,
                label=f"Pokémon {pokemon_id} official artwork",
            )
        except DownloadError:
            if not fallback_url or fallback_url == primary_url:
                raise
            print("    primary artwork unavailable; trying PokéAPI fallback", flush=True)
            artwork = request_bytes(
                fallback_url,
                timeout=args.timeout,
                retries=args.retries,
                retry_delay=args.retry_delay,
                label=f"Pokémon {pokemon_id} fallback artwork",
            )
        if not artwork.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DownloadError(f"Pokémon {pokemon_id} artwork response is not a PNG")
        atomic_write_bytes(artwork_path, artwork)

    return {
        "id": pokemon_id,
        "name": name,
        "generation": generation_number(species_data),
        "types": [name for _, name in sorted(ordered_types)],
        "artwork_path": relative_to_root(artwork_path),
    }


def main() -> int:
    args = parse_args()
    metadata_path = project_path(args.metadata)
    artwork_dir = project_path(args.artwork_dir)
    artwork_dir.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)

    records = load_existing_metadata(metadata_path)
    requested_ids = list(range(args.start_id, args.end_id + 1))
    failures: list[tuple[int, str]] = []
    changed = False

    print(
        f"Preparing Pokémon {args.start_id}-{args.end_id} "
        f"({len(requested_ids)} total) in {relative_to_root(artwork_dir)}",
        flush=True,
    )
    for position, pokemon_id in enumerate(requested_ids, start=1):
        artwork_path = artwork_dir / f"{pokemon_id}.png"
        existing = records.get(pokemon_id)
        prefix = f"[{position:>3}/{len(requested_ids)}] #{pokemon_id:>4}"
        if (
            not args.force
            and existing is not None
            and metadata_is_complete(existing, artwork_path)
        ):
            print(f"{prefix} {existing['name']}: already present", flush=True)
            continue

        print(f"{prefix}: downloading...", flush=True)
        try:
            record = download_one(pokemon_id, artwork_path, args)
        except (DownloadError, OSError, ValueError) as exc:
            failures.append((pokemon_id, str(exc)))
            print(f"{prefix}: ERROR: {exc}", file=sys.stderr, flush=True)
            continue

        records[pokemon_id] = record
        changed = True
        # Persist each completed item so an interrupted long run can resume cleanly.
        atomic_write_json(metadata_path, [records[key] for key in sorted(records)])
        print(f"{prefix} {record['name']}: saved", flush=True)

    if not metadata_path.exists() or changed:
        atomic_write_json(metadata_path, [records[key] for key in sorted(records)])

    completed = len(requested_ids) - len(failures)
    print(f"Finished: {completed}/{len(requested_ids)} requested Pokémon are ready.", flush=True)
    if failures:
        print(f"{len(failures)} Pokémon failed:", file=sys.stderr)
        for pokemon_id, message in failures:
            print(f"  #{pokemon_id}: {message}", file=sys.stderr)
        print("Rerun the same command to retry failed items.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
