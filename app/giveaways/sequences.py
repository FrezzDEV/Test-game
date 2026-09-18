import json
import os
from functools import lru_cache
from pathlib import Path


SEQUENCES_PATH = Path(
    os.getenv("ACTION_SEQUENCES_PATH", "./config/action_sequences.json")
)


@lru_cache(maxsize=1)
def load_sequences() -> dict:
    if not SEQUENCES_PATH.exists():
        return {}
    with SEQUENCES_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def available_sequence_ids() -> list[str]:
    return sorted(load_sequences().keys())


def get_sequence(sequence_id: str) -> dict | None:
    sequence = load_sequences().get(sequence_id)
    return sequence if isinstance(sequence, dict) else None
