"""
RAG retriever for PV-sizing knowledge.

Chunks documents from a knowledge directory, builds a simple in-memory
vector store using sentence-transformers, and retrieves the top-k most
relevant passages for a query.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

from config import RAGConfig

logger = logging.getLogger(__name__)


# ── Document chunker ─────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    """Split *text* into overlapping chunks by character count."""
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return [c.strip() for c in chunks if c.strip()]


def _load_documents(knowledge_dir: str) -> List[str]:
    """Read all .txt and .md files from *knowledge_dir* and return raw texts."""
    docs: List[str] = []
    kdir = Path(knowledge_dir)
    if not kdir.exists():
        logger.warning("RAG knowledge directory does not exist: %s", kdir)
        return docs
    for ext in ("*.txt", "*.md"):
        for fp in sorted(kdir.glob(ext)):
            try:
                docs.append(fp.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Could not read %s: %s", fp, exc)
    return docs


# ── Vector store (simple numpy / sentence-transformers) ──────

class RAGRetriever:
    """In-memory vector-similarity retriever.

    Uses ``sentence-transformers`` for embeddings and cosine similarity.
    Falls back to keyword matching if the library is unavailable.
    """

    def __init__(self, cfg: RAGConfig) -> None:
        self.cfg = cfg
        self._chunks: List[str] = []
        self._embeddings = None          # np.ndarray or None
        self._model = None               # SentenceTransformer or None
        self._ready = False

    # ── Build index ──────────────────────────────────────────

    def build(self) -> None:
        """Load documents, chunk them, and compute embeddings."""
        raw_docs = _load_documents(self.cfg.knowledge_dir)
        if not raw_docs:
            logger.info("No RAG documents found – retriever will return empty.")
            self._ready = True
            return

        for doc in raw_docs:
            self._chunks.extend(
                _chunk_text(doc, self.cfg.chunk_size, self.cfg.chunk_overlap)
            )
        logger.info("RAG: %d chunks from %d documents.", len(self._chunks), len(raw_docs))

        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np

            # cache_folder persists the model to disk so it is only
            # downloaded once instead of on every run
            _cache_dir = Path(__file__).resolve().parent / ".model_cache"
            _cache_dir.mkdir(exist_ok=True)
            self._model = SentenceTransformer(
                self.cfg.embedding_model,
                cache_folder=str(_cache_dir),
            )
            self._embeddings = self._model.encode(
                self._chunks, show_progress_bar=False, convert_to_numpy=True
            )
            logger.info("RAG: embeddings computed (%s).", self.cfg.embedding_model)
        except ImportError:
            logger.warning(
                "sentence-transformers not installed – "
                "falling back to keyword retrieval."
            )
        self._ready = True

    # ── Retrieve ─────────────────────────────────────────────

    def retrieve(self, query: str, top_k: Optional[int] = None) -> List[str]:
        """Return the *top_k* most relevant chunks for *query*."""
        if not self._ready:
            self.build()

        k = top_k or self.cfg.top_k
        if not self._chunks:
            return []

        # Vector path
        if self._model is not None and self._embeddings is not None:
            return self._vector_retrieve(query, k)

        # Keyword fallback
        return self._keyword_retrieve(query, k)

    def _vector_retrieve(self, query: str, k: int) -> List[str]:
        import numpy as np

        q_emb = self._model.encode([query], convert_to_numpy=True)  # (1, dim)
        # Cosine similarity
        norms_c = np.linalg.norm(self._embeddings, axis=1, keepdims=True)
        norms_q = np.linalg.norm(q_emb, axis=1, keepdims=True)
        sim = (self._embeddings @ q_emb.T) / (norms_c * norms_q + 1e-10)
        sim = sim.squeeze()
        top_idx = sim.argsort()[::-1][:k]
        return [self._chunks[i] for i in top_idx]

    def _keyword_retrieve(self, query: str, k: int) -> List[str]:
        """Simple keyword overlap scoring."""
        query_tokens = set(query.lower().split())
        scored: List[Tuple[float, int]] = []
        for idx, chunk in enumerate(self._chunks):
            chunk_tokens = set(chunk.lower().split())
            overlap = len(query_tokens & chunk_tokens)
            scored.append((overlap, idx))
        scored.sort(reverse=True)
        return [self._chunks[idx] for _, idx in scored[:k]]

    # ── Formatted block for prompt injection ─────────────────

    def retrieve_block(self, query: str, top_k: Optional[int] = None) -> str:
        """Return a formatted RAG block ready for prompt injection."""
        passages = self.retrieve(query, top_k)
        if not passages:
            return "=== RAG PASSAGES ===\n(no relevant documents found)\n=== END RAG ==="
        lines = ["=== RAG PASSAGES ==="]
        for i, p in enumerate(passages, 1):
            lines.append(f"\n--- Passage {i} ---")
            lines.append(p)
        lines.append("\n=== END RAG ===")
        return "\n".join(lines)
