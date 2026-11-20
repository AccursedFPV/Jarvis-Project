"""Long-term memory: durable facts Jarvis keeps about the user."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

MAX_FACTS = 100
MAX_FACT_LENGTH = 500


class Memory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._facts: List[str] = []
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            data = data.get("facts")
        if isinstance(data, list):
            self._facts = [str(item).strip() for item in data if str(item).strip()]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"facts": self._facts}, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def add(self, fact: str) -> bool:
        fact = " ".join(str(fact).split()).strip()
        if not fact:
            return False
        fact = fact[:MAX_FACT_LENGTH]
        if any(existing.lower() == fact.lower() for existing in self._facts):
            return False
        self._facts.append(fact)
        if len(self._facts) > MAX_FACTS:
            self._facts = self._facts[-MAX_FACTS:]
        self._save()
        return True

    def remove(self, needle: str) -> int:
        needle = str(needle).strip().lower()
        if not needle:
            return 0
        before = len(self._facts)
        self._facts = [fact for fact in self._facts if needle not in fact.lower()]
        removed = before - len(self._facts)
        if removed:
            self._save()
        return removed

    def facts(self) -> List[str]:
        return list(self._facts)

    def block(self) -> str:
        if not self._facts:
            return "(nothing yet)"
        return "\n".join("- " + fact for fact in self._facts)
