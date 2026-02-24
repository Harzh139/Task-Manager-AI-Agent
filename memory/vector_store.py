"""
vector_store.py - FAISS-backed persistent memory for the productivity system.

Uses sentence-transformers (all-MiniLM-L6-v2) for LOCAL embeddings — no API
key required. This keeps memory fully offline and free.

Responsibilities:
 - Store user preferences, past plans, reflections as vector embeddings
 - Provide semantic similarity search for context retrieval
 - Persist index to disk so state survives restarts
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# all-MiniLM-L6-v2 produces 384-dim embeddings
DIMENSION = 384


class VectorStore:
    """FAISS vector store with metadata side-car for semantic memory."""

    def __init__(self) -> None:
        self.settings = settings
        self.index_dir = Path(settings.FAISS_INDEX_PATH)
        self.index_path = self.index_dir / "index.faiss"
        self.meta_path = self.index_dir / "metadata.json"

        # Load embedding model once (cached on disk by sentence-transformers)
        logger.info("Loading sentence-transformers model: %s", settings.EMBEDDING_MODEL)
        self._model = SentenceTransformer(settings.EMBEDDING_MODEL)

        self.index: faiss.IndexFlatL2 = faiss.IndexFlatL2(DIMENSION)
        self.metadata: list[dict[str, Any]] = []

        self._load()

    # ── Persistence ─────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load existing FAISS index and metadata from disk if they exist."""
        self.index_dir.mkdir(parents=True, exist_ok=True)
        if self.index_path.exists() and self.meta_path.exists():
            try:
                self.index = faiss.read_index(str(self.index_path))
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    self.metadata = json.load(f)
                logger.info(
                    "Loaded FAISS index with %d vectors from %s",
                    self.index.ntotal,
                    self.index_dir,
                )
            except Exception as exc:
                logger.warning("Could not load FAISS index (%s). Starting fresh.", exc)
                self.index = faiss.IndexFlatL2(DIMENSION)
                self.metadata = []
        else:
            logger.info("No existing FAISS index found; initialising fresh store.")

    def _save(self) -> None:
        """Persist FAISS index and metadata to disk."""
        self.index_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(self.index_path))
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(self.metadata, f, indent=2, default=str)
        logger.debug("Saved FAISS index (%d vectors).", self.index.ntotal)

    # ── Core Operations ──────────────────────────────────────────────────────

    def _embed(self, text: str) -> np.ndarray:
        """
        Create a 384-dim embedding vector using a local SentenceTransformer model.
        Fully synchronous — no API call needed.
        """
        vector = self._model.encode([text], convert_to_numpy=True, normalize_embeddings=True)
        return vector.astype(np.float32)  # shape (1, 384)

    async def add(self, text: str, meta: dict[str, Any]) -> int:
        """
        Add *text* with associated *meta* to the vector store.
        Returns the assigned vector ID.
        """
        vector = self._embed(text)
        self.index.add(vector)
        idx = len(self.metadata)
        self.metadata.append({"id": idx, "text": text, **meta})
        self._save()
        logger.debug("Added vector id=%d  meta=%s", idx, meta)
        return idx

    async def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        """
        Return the *top_k* most similar documents to *query*.
        Each result dict contains the original metadata plus a 'score' (L2 distance).
        """
        k = top_k or settings.FAISS_TOP_K
        if self.index.ntotal == 0:
            logger.debug("VectorStore is empty; returning [].")
            return []

        k = min(k, self.index.ntotal)
        vector = self._embed(query)
        distances, indices = self.index.search(vector, k)

        results: list[dict[str, Any]] = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1:
                continue
            entry = dict(self.metadata[idx])
            entry["score"] = float(dist)
            results.append(entry)

        return results

    async def update_preference(self, key: str, value: Any) -> None:
        """Upsert a user preference into the vector store."""
        text = f"User preference: {key} = {value}"
        await self.add(text, {"type": "preference", "key": key, "value": value})

    async def store_reflection(self, reflection_text: str, week: str) -> None:
        """Persist a weekly reflection to the vector store."""
        await self.add(reflection_text, {"type": "reflection", "week": week})

    async def store_plan(self, goal: str, plan: dict[str, Any]) -> None:
        """Persist a goal-plan pair to the vector store."""
        text = f"Goal: {goal}\nPlan: {json.dumps(plan)}"
        await self.add(text, {"type": "plan", "goal": goal})

    async def get_relevant_context(self, query: str) -> str:
        """
        Retrieve relevant memory items and format them as a context string for
        injection into LLM prompts.
        """
        results = await self.search(query)
        if not results:
            return "No relevant past context found."

        lines: list[str] = ["Relevant past context:"]
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
