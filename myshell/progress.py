"""Pip's progress record: which lessons you finished and how your quizzes went.
It lives in a small JSON file (state.progress_file, by default ~/.myshell_progress.json).
A missing or damaged file just means a fresh start."""
import json
import os
import time
from typing import Dict, List, Tuple

KEEP_RUNS = 20


def empty() -> dict:
    return {"lessons": {}, "quizzes": {"runs": [], "best": {}}}


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return empty()
    base = empty()
    if isinstance(data, dict):
        if isinstance(data.get("lessons"), dict):
            base["lessons"] = data["lessons"]
        quizzes = data.get("quizzes")
        if isinstance(quizzes, dict):
            if isinstance(quizzes.get("runs"), list):
                base["quizzes"]["runs"] = quizzes["runs"]
            if isinstance(quizzes.get("best"), dict):
                base["quizzes"]["best"] = quizzes["best"]
    return base


def save(path: str, data: dict) -> bool:
    """Write the file in one step. Returns False when the disk refuses."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def mark_lesson(path: str, key: str) -> None:
    data = load(path)
    entry = data["lessons"].get(key, {})
    entry = entry if isinstance(entry, dict) else {}
    entry["times"] = int(entry.get("times", 0)) + 1
    entry["last"] = stamp()
    data["lessons"][key] = entry
    save(path, data)


def record_quiz(path: str, topic: str, score: int, total: int) -> bool:
    """Save a finished quiz. Returns True when this is a new best for the topic."""
    data = load(path)
    runs = data["quizzes"]["runs"]
    runs.append({"topic": topic, "score": score, "total": total, "when": stamp()})
    del runs[:-KEEP_RUNS]
    best = data["quizzes"]["best"]
    old = best.get(topic)
    ratio = score / total if total else 0
    is_best = not (isinstance(old, list) and len(old) == 2 and old[1]
                   and old[0] / old[1] >= ratio)
    if is_best:
        best[topic] = [score, total]
    save(path, data)
    return is_best


def done_lessons(path: str) -> List[str]:
    return sorted(load(path)["lessons"])


def best_scores(path: str) -> Dict[str, Tuple[int, int]]:
    out = {}
    for topic, value in load(path)["quizzes"]["best"].items():
        if isinstance(value, list) and len(value) == 2:
            out[topic] = (int(value[0]), int(value[1]))
    return out


def reset(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
