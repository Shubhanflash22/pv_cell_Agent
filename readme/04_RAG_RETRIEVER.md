# 04 — RAG Retriever (`rag_retriever.py`)

## Purpose

The RAG (Retrieval-Augmented Generation) retriever provides the LLM with **domain-specific knowledge** about the San Diego solar market that is not present in the computed features. While features give the LLM numeric data (e.g., "annual consumption = 8,234 kWh"), the RAG passages give it qualitative context (e.g., "NEM 3.0 reduced export credits to $0.05–$0.08/kWh" or "SDG&E rates are $0.33–$0.38/kWh").

This prevents the LLM from **hallucinating market-specific details** and grounds its recommendations in real policy and pricing data.

---

## How It Works (High Level)

```
┌───────────────────────────────────────────────────────────┐
│  1. LOAD: Read all .txt and .md files from knowledge_dir  │
│     → data/rag_knowledge/san_diego_pv_market.md           │
└──────────────┬────────────────────────────────────────────┘
               │
               ▼
┌───────────────────────────────────────────────────────────┐
│  2. CHUNK: Split each document into overlapping chunks    │
│     512 chars per chunk, 64 char overlap                  │
│     → ~20–30 chunks from the market knowledge file        │
└──────────────┬────────────────────────────────────────────┘
               │
               ▼
┌───────────────────────────────────────────────────────────┐
│  3. EMBED: Encode all chunks using sentence-transformers  │
│     Model: all-MiniLM-L6-v2 (384-dim vectors)           │
│     Cached in .model_cache/ directory                     │
└──────────────┬────────────────────────────────────────────┘
               │
               ▼
┌───────────────────────────────────────────────────────────┐
│  4. QUERY: For each location, encode the query            │
│     "solar PV sizing San Diego Alpine net metering..."    │
│     Compute cosine similarity with all chunk embeddings   │
│     Return top-5 most relevant chunks                     │
└──────────────┬────────────────────────────────────────────┘
               │
               ▼
┌───────────────────────────────────────────────────────────┐
│  5. FORMAT: Wrap passages in a structured block           │
│     === RAG PASSAGES ===                                  │
│     --- Passage 1 ---                                     │
│     <chunk text>                                          │
│     --- Passage 2 ---                                     │
│     ...                                                   │
│     === END RAG ===                                       │
└───────────────────────────────────────────────────────────┘
```

---

## File: `rag_retriever.py`

### Class: `RAGRetriever`

```python
class RAGRetriever:
    def __init__(self, cfg: RAGConfig) -> None:
    def build(self) -> None:
    def retrieve(self, query: str, top_k: int = None) -> List[str]:
    def retrieve_block(self, query: str, top_k: int = None) -> str:
```

### Constructor

```python
def __init__(self, cfg: RAGConfig) -> None:
```

Takes a `RAGConfig` dataclass with:
- `knowledge_dir` — path to the folder with knowledge documents.
- `chunk_size` — characters per chunk (default 512).
- `chunk_overlap` — overlap between consecutive chunks (default 64).
- `top_k` — number of passages to retrieve (default 5).
- `embedding_model` — sentence-transformers model name (default `all-MiniLM-L6-v2`).

Initialises empty internal state — no computation happens until `build()` is called.

### `build()` — Index Construction

```python
def build(self) -> None:
```

1. **Load documents**: Scans `knowledge_dir` for `.txt` and `.md` files, reads their content.
2. **Chunk**: Splits each document into overlapping character-based chunks using `_chunk_text()`.
3. **Embed**: Loads the sentence-transformers model and encodes all chunks into dense vectors.
4. **Cache**: The model is downloaded once and stored in `.model_cache/` at the project root.

#### Document Chunking

```python
def _chunk_text(text: str, chunk_size: int, overlap: int) -> List[str]:
```

Sliding window approach:
- Start at position 0.
- Take `chunk_size` characters.
- Advance by `chunk_size - overlap` characters.
- Repeat until end of text.
- Strip whitespace, discard empty chunks.

**Example** with chunk_size=512, overlap=64:
```
Characters 0-511     → Chunk 1
Characters 448-959   → Chunk 2  (overlaps 64 chars with Chunk 1)
Characters 896-1407  → Chunk 3  (overlaps 64 chars with Chunk 2)
...
```

The overlap ensures that information split across chunk boundaries is captured in at least one chunk.

#### Embedding Model

| Property | Value |
|----------|-------|
| **Model** | `all-MiniLM-L6-v2` |
| **Dimensions** | 384 |
| **Parameters** | 22.7M |
| **Speed** | ~14,000 sentences/sec on CPU |
| **Quality** | Good balance of speed and accuracy |
| **Size** | ~80 MB |
| **Cache Location** | `.model_cache/` (persisted, downloaded once) |

The model is loaded via:
```python
_cache_dir = Path(__file__).resolve().parent / ".model_cache"
_cache_dir.mkdir(exist_ok=True)
self._model = SentenceTransformer(
    self.cfg.embedding_model,
    cache_folder=str(_cache_dir),
)
```

This `cache_folder` parameter ensures the model files are stored locally and not re-downloaded on every pipeline run.

### `retrieve()` — Vector Search

```python
def retrieve(self, query: str, top_k: int = None) -> List[str]:
```

1. Encodes the query string into a 384-dimensional vector.
2. Computes **cosine similarity** between the query vector and all chunk vectors:

$$\text{sim}(q, c) = \frac{q \cdot c}{\|q\| \times \|c\|}$$

3. Sorts chunks by similarity (descending).
4. Returns the top-k chunk texts.

#### Vector Retrieval Implementation

```python
def _vector_retrieve(self, query: str, k: int) -> List[str]:
    q_emb = self._model.encode([query], convert_to_numpy=True)  # (1, 384)
    norms_c = np.linalg.norm(self._embeddings, axis=1, keepdims=True)
    norms_q = np.linalg.norm(q_emb, axis=1, keepdims=True)
    sim = (self._embeddings @ q_emb.T) / (norms_c * norms_q + 1e-10)
    sim = sim.squeeze()
    top_idx = sim.argsort()[::-1][:k]
    return [self._chunks[i] for i in top_idx]
```

### Keyword Fallback

If `sentence-transformers` is not installed, the retriever gracefully falls back to **keyword overlap scoring**:

```python
def _keyword_retrieve(self, query: str, k: int) -> List[str]:
    query_tokens = set(query.lower().split())
    scored = []
    for idx, chunk in enumerate(self._chunks):
        chunk_tokens = set(chunk.lower().split())
        overlap = len(query_tokens & chunk_tokens)
        scored.append((overlap, idx))
    scored.sort(reverse=True)
    return [self._chunks[idx] for _, idx in scored[:k]]
```

This is simpler and less accurate but requires no external dependencies beyond standard Python.

### `retrieve_block()` — Formatted Output

```python
def retrieve_block(self, query: str, top_k: int = None) -> str:
```

Wraps the retrieved passages in a formatted block ready for prompt injection:

```
=== RAG PASSAGES ===

--- Passage 1 ---
San Diego falls under SDG&E (San Diego Gas & Electric) territory. As of 2024,
new residential solar installations are subject to NEM 3.0 (Net Billing Tariff).
Key changes under NEM 3.0: Export credits are significantly reduced compared
to NEM 2.0...

--- Passage 2 ---
SDG&E has some of the highest electricity rates in the US: Residential average:
$0.33–$0.38/kWh (2024). Time-of-Use (TOU) rates: Off-peak: ~$0.25/kWh,
On-peak (4–9 PM): ~$0.55/kWh...

--- Passage 3 ---
...

=== END RAG ===
```

If no documents are found, it returns:
```
=== RAG PASSAGES ===
(no relevant documents found)
=== END RAG ===
```

---

## Knowledge Base: `data/rag_knowledge/`

### Current Content: `san_diego_pv_market.md` (76 lines)

This file contains expert-curated knowledge about:

| Section | Key Facts |
|---------|-----------|
| **NEM 3.0** | Export credits reduced to $0.05–$0.08/kWh (vs $0.30+ under NEM 2.0). Self-consumption is now more valuable than exporting. |
| **Solar Resource** | 5.3–5.7 peak sun hours/day. Coastal: 5.2–5.4 (marine layer). Inland: 5.5–5.8. Annual: 1,900–2,100 kWh/kWp. |
| **Costs** | $2.80–$3.50/W installed. Average ~$3.00/W. 30% Federal ITC → effective $2.00–$2.45/W. |
| **Panel Specs** | 380–420 Wp standard. Most common: 400 Wp. 1.7m × 1.0m. 20–22% efficiency. |
| **SDG&E Rates** | $0.33–$0.38/kWh average. TOU: off-peak $0.25, on-peak $0.55, super off-peak $0.20. |
| **Sizing Guidance** | 70% offset is the NEM 3.0 sweet spot. 100% offset leads to low-value exports. Typical system: 4–7 kW DC. |
| **Payback** | Without battery: 6–9 years. With battery: 10–14 years. 25-year ROI: 150–300%. |
| **Degradation** | 0.5%/year. Year 25: ~88% of Year 1. Warranties: 85–87% at 25 years. |

### Adding New Knowledge

Create any `.txt` or `.md` file in `data/rag_knowledge/`:

```bash
cat > data/rag_knowledge/battery_storage.md << 'EOF'
# Battery Storage for San Diego Solar

Tesla Powerwall 3: 13.5 kWh, ~$9,500 installed.
Enphase IQ Battery 5P: 5 kWh per unit, stackable.
...
EOF
```

The RAG system will automatically load and index new files on the next pipeline run — no code changes needed.

---

## How the Pipeline Uses RAG

In `pipeline.py`, Step 3:

```python
rag = self._get_rag()          # Lazily build the index
rag_query = (
    f"solar PV sizing San Diego {name} "
    f"net metering NEM export rate cost per watt residential"
)
rag_block = rag.retrieve_block(rag_query)
```

The query is constructed to be **location-specific** (includes the location name) and **domain-targeted** (includes key solar terms). This ensures the most relevant passages are retrieved.

The `rag_block` is then passed to `prompt_builder.py` where it becomes part of the assembled prompt.

---

## Lazy Initialisation

The RAG retriever is **lazily initialised** by the pipeline:

```python
def _get_rag(self) -> RAGRetriever:
    if self._rag is not None:
        return self._rag       # Return cached instance
    self._rag = RAGRetriever(self.cfg.rag)
    self._rag.build()          # Load, chunk, embed — only once
    return self._rag
```

When processing multiple locations, the index is built **once** and reused for all locations. This avoids the ~2-second embedding computation on every location.

---

## Configuration

All RAG behaviour is controlled via `config.yaml`:

```yaml
rag:
  knowledge_dir: data/rag_knowledge   # Where to find knowledge docs
  chunk_size: 512                     # Characters per chunk
  chunk_overlap: 64                   # Overlap between chunks
  top_k: 5                           # Passages to retrieve
  embedding_model: all-MiniLM-L6-v2  # Embedding model
```

| Parameter | Effect of Increasing | Effect of Decreasing |
|-----------|---------------------|---------------------|
| `chunk_size` | Longer passages, more context per chunk | Shorter, more focused chunks |
| `chunk_overlap` | Better coverage of split information | Less redundancy, fewer chunks |
| `top_k` | More context for the LLM (but longer prompt) | Less context (but faster, shorter prompt) |

---

## Error Handling

- **No knowledge directory**: Logs a warning, returns empty passages.
- **No documents found**: Index is empty, retrieval returns `[]`.
- **sentence-transformers not installed**: Falls back to keyword search (logs a warning).
- **Unreadable file**: Logs a warning per file, continues with other files.
