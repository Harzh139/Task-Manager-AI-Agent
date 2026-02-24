"""
vector_store.py - Lightweight in-memory semantic memory for production.

Replaced FAISS + sentence-transformers with a simple TF-IDF-style keyword
search to avoid the 512MB RAM limit on Render's free tier. All functionality
is preserved — storage, retrieval, preferences, reflections, plans.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Persistence paths ─────────────────────────────────────────────────────────
_MEMORY_FILE = Path("memory/vector_store.json")


class VectorStore:
    """Lightweight keyword-based vector store (no FAISS / sentence-transformers)."""

    def __init__(self) -> None:
        self.metadata: list[dict[str, Any]] = []
        self._load()

    # ── Persistence ─────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            _MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            if _MEMORY_FILE.exists():
                self.metadata = json.loads(_MEMORY_FILE.read_text(encoding="utf-8"))
                logger.info("Loaded %d memory entries from %s", len(self.metadata), _MEMORY_FILE)
            else:
                logger.info("No existing memory found; starting fresh.")
        except Exception as exc:
            logger.warning("Could not load memory (%s). Starting fresh.", exc)
            self.metadata = []

    def _save(self) -> None:
        try:
            _MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            _MEMORY_FILE.write_text(json.dumps(self.metadata, indent=2, default=str), encoding="utf-8")
        except Exception as exc:
            logger.error("Could not save memory: %s", exc)

    # ── Keyword similarity ────────────────────────────────────────────────────

    @staticmethod
    def _tokenize(text: str) -> Counter:
        words = re.findall(r"[a-z0-9]+", text.lower())
        return Counter(words)

    def _score(self, query: str, doc_text: str) -> float:
        q = self._tokenize(query)
        d = self._tokenize(doc_text)
        common = sum((q & d).values())
        denom = (sum(q.values()) + sum(d.values()))
        return common / denom if denom else 0.0

    # ── Core Operations ──────────────────────────────────────────────────────

    async def add(self, text: str, meta: dict[str, Any]) -> int:
        idx = len(self.metadata)
        self.metadata.append({"id": idx, "text": text, **meta})
        self._save()
        logger.debug("Added memory id=%d", idx)
        return idx

    async def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        k = top_k or settings.FAISS_TOP_K
        if not self.metadata:
            return []
        scored = [(self._score(query, e["text"]), e) for e in self.metadata]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"score": s, **e} for s, e in scored[:k] if s > 0]

    async def update_preference(self, key: str, value: Any) -> None:
        text = f"User preference: {key} = {value}"
        await self.add(text, {"type": "preference", "key": key, "value": value})

    async def store_reflection(self, reflection_text: str, week: str) -> None:
        await self.add(reflection_text, {"type": "reflection", "week": week})

    async def store_plan(self, goal: str, plan: dict[str, Any]) -> None:
        text = f"Goal: {goal}\nPlan: {json.dumps(plan)}"
        await self.add(text, {"type": "plan", "goal": goal})

    async def get_relevant_context(self, query: str) -> str:
        results = await self.search(query)
        if not results:
            return "No relevant past context found."
        lines = ["Relevant past context:"]
        for r in results:
            lines.append(f"  - [{r.get('type', 'unknown')}] {r['text'][:200]}")
        return "\n".join(lines)


# ── Module-level singleton ────────────────────────────────────────────────────

_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    """Return the module-level VectorStore singleton (lazy init)."""
    global _store
    if _store is None:
        _store = VectorStore()
    return _store
