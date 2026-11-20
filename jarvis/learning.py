"""Learned lessons: what worked before, reused next time.

This is Jarvis's procedural memory. It is not model training - it is a small
store of short lessons (a command that worked, a fix, a way of doing a task)
that is injected into the prompt when it looks relevant to what is being asked.
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Dict, List

MAX_LEARNINGS = 400
MAX_LENGTH = 400

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "then", "than",
    "your", "you", "its", "was", "are", "not", "but", "use", "using", "when",
    "what", "how", "all", "any", "can", "have", "has", "were", "will", "would",
    "should", "could", "about", "over", "out", "one", "two", "them", "they",
    "there", "here", "just", "also", "some", "more", "most", "been", "being",
    "make", "made", "get", "got", "does", "did", "his", "her", "she", "him",
}


def _tokens(text: str) -> set:
    return {
        word
        for word in re.findall(r"[a-z0-9_]+", str(text).lower())
        if len(word) > 2 and word not in STOPWORDS
    }


class Learnings:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._items: List[Dict[str, Any]] = []
        self._load()

    # ------------------------------------------------------------------ storage

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            data = data.get("lessons")
        if not isinstance(data, list):
            return
        for entry in data:
            if isinstance(entry, dict) and str(entry.get("text") or "").strip():
                self._items.append(
                    {
                        "text": str(entry["text"]).strip()[:MAX_LENGTH],
                        "at": float(entry.get("at") or 0),
                    }
                )
            elif isinstance(entry, str) and entry.strip():
                self._items.append({"text": entry.strip()[:MAX_LENGTH], "at": 0.0})

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"lessons": self._items}, indent=2), encoding="utf-8")
        temp.replace(self.path)

    # -------------------------------------------------------------------- write

    def add(self, text: str) -> bool:
        text = " ".join(str(text or "").split()).strip()[:MAX_LENGTH]
        if len(text) < 8:
            return False
        if any(existing["text"].lower() == text.lower() for existing in self._items):
            return False
        self._items.append({"text": text, "at": time.time()})
        if len(self._items) > MAX_LEARNINGS:
            self._items = self._items[-MAX_LEARNINGS:]
        self._save()
        return True

    def remove(self, needle: str) -> int:
        needle = str(needle or "").strip().lower()
        if not needle:
            return 0
        before = len(self._items)
        self._items = [item for item in self._items if needle not in item["text"].lower()]
        removed = before - len(self._items)
        if removed:
            self._save()
        return removed

    # -------------------------------------------------------------------- read

    def texts(self) -> List[str]:
        return [item["text"] for item in self._items]

    def relevant(self, query: str, limit: int = 4) -> List[str]:
        """Lessons whose words overlap the query, best first."""
        wanted = _tokens(query)
        if not wanted or not self._items:
            return []
        scored = []
        for position, item in enumerate(self._items):
            words = _tokens(item["text"])
            if not words:
                continue
            overlap = len(wanted & words)
            if not overlap:
                continue
            score = overlap / math.sqrt(len(words)) + position / (len(self._items) * 50.0)
            scored.append((score, item["text"]))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [text for _, text in scored[:limit]]

    def block(self, query: str = "", limit: int = 4) -> str:
        picked = self.relevant(query, limit) if query else self.texts()[-limit:]
        if not picked:
            return "(nothing learned yet)"
        return "\n".join("- " + text for text in picked)
