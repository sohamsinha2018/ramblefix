from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CORPUS_PATH = Path("eval_corpus/ramblefix_corpus.json")


def load_corpus(path: str | Path = DEFAULT_CORPUS_PATH) -> list[dict[str, Any]]:
    corpus_path = Path(path)
    if not corpus_path.exists():
        return []
    return json.loads(corpus_path.read_text(encoding="utf-8"))


def save_corpus(items: list[dict[str, Any]], path: str | Path = DEFAULT_CORPUS_PATH) -> None:
    corpus_path = Path(path)
    corpus_path.parent.mkdir(parents=True, exist_ok=True)
    corpus_path.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_corpus_item(
    *,
    item_id: str,
    audio_path: str | Path,
    source: str,
    workflow: str,
    notes: str = "",
    path: str | Path = DEFAULT_CORPUS_PATH,
) -> dict[str, Any]:
    corpus_path = Path(path)
    audio = Path(audio_path)
    try:
        stored_audio = str(audio.relative_to(corpus_path.parent.parent))
    except ValueError:
        stored_audio = str(audio)

    items = load_corpus(corpus_path)
    existing = next((item for item in items if item.get("id") == item_id), None)
    if existing:
        return existing

    item = {
        "id": item_id,
        "audio": stored_audio,
        "gold": "",
        "source": source,
        "workflow": workflow,
        "notes": notes,
    }
    items.append(item)
    save_corpus(items, corpus_path)
    return item


def set_corpus_gold(item_id: str, gold: str, path: str | Path = DEFAULT_CORPUS_PATH) -> dict[str, Any]:
    items = load_corpus(path)
    for item in items:
        if item.get("id") == item_id:
            item["gold"] = gold
            save_corpus(items, path)
            return item
    raise KeyError(f"Corpus item not found: {item_id}")


def set_corpus_benchmark(
    item_id: str,
    name: str,
    text: str,
    path: str | Path = DEFAULT_CORPUS_PATH,
) -> dict[str, Any]:
    items = load_corpus(path)
    for item in items:
        if item.get("id") == item_id:
            benchmarks = item.setdefault("benchmarks", {})
            if not isinstance(benchmarks, dict):
                benchmarks = {}
                item["benchmarks"] = benchmarks
            benchmarks[name] = text
            save_corpus(items, path)
            return item
    raise KeyError(f"Corpus item not found: {item_id}")
