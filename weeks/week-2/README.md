# Week 2 — Embeddings, Chunking Strategies, and Hybrid Search

**Goal:** Understand how embedding model choice, chunk size, and search
strategy (dense vs hybrid) affect retrieval quality on real data. After this
week you should be able to answer "Why is your retrieval quality poor?" without
guessing.

**Time:** 4–5 days  
**Prerequisites:** Week 1 complete

---

## Embedding model selection — what to know

| Model | Dims | Context | Speed | Notes |
|-------|------|---------|-------|-------|
| `nomic-embed-text` | 768 | 8192 | fast | Current baseline, good for docs |
| `BAAI/bge-base-en-v1.5` | 768 | 512 | fast | Strong for short passages |
| `BAAI/bge-large-en-v1.5` | 1024 | 512 | slow | Better quality, more RAM |
| `BAAI/bge-m3` | 1024 | 8192 | slow | Multilingual, dense+sparse in one |
| `sentence-transformers/all-MiniLM-L6-v2` | 384 | 128 | very fast | Good for prototypes |

**Interview traps:**
1. "Can I swap embedding models without re-indexing?" No. The vector space
   changes completely with model change. You must re-embed + re-index everything.
   This is called **embedding drift** — the worst-case operational risk for
   production RAG systems.
2. "Does a larger model always mean better retrieval?" Not for your use case.
   BGE-large outperforms BGE-base on BEIR benchmarks, but on domain-specific
   technical docs (your case: Vault policies, Consul configs), a smaller model
   fine-tuned on tech docs can outperform.
3. "What's the cost of higher dimensionality?" Memory scales linearly.
   768 dims vs 1024 dims = 33% more storage. At 10M points, that's 40GB vs 30GB.

## Chunking — why it matters more than people think

Poor chunking is the most common root cause of bad retrieval quality. Symptoms:
- High cosine scores but LLM gives wrong answers → chunks semantically similar
  but not informationally aligned
- Low cosine scores on clearly relevant queries → chunks too large (diluted
  signal) or split mid-sentence (broken semantic units)
- Repetitive answers → chunks overlap too aggressively

**Chunking strategies:**
| Strategy | Size | Overlap | Best for |
|----------|------|---------|----------|
| Fixed-size | 300–600 chars | 50–100 | Simple baseline |
| Sentence-aware | by sentence | none | Short docs, FAQs |
| Heading-aware | by section | none | Structured docs (Markdown) |
| Semantic | by topic shift | none | Long-form text, requires LLM |
| Hierarchical | parent+child | parent context | Complex retrieval with re-ranking |

## Hybrid search — dense + sparse

Dense vectors (what you have now) capture **semantic meaning**.
Sparse vectors (BM25 / SPLADE) capture **keyword matches**.

Hybrid = combine both via **Reciprocal Rank Fusion (RRF)**:
```
score_hybrid = 1/(k + rank_dense) + 1/(k + rank_sparse)
```
Where k=60 is a smoothing constant (Qdrant default).

When hybrid beats dense-only:
- Exact product names, version numbers, config keys ("VAULT_ADDR", "spiffe://")
- Proper nouns not well-covered by embedding training data
- Short queries (1-2 words) where semantic context is insufficient

When hybrid adds no value:
- Long, natural-language queries where semantics dominate
- Domain where all vocabulary appears in embedding training data

---

## Labs

```bash
task w2:embed    # Compare nomic vs BGE-small via fastembed, measure recall
task w2:chunk    # Compare chunk sizes 300/600/1200 on same corpus
task w2:hybrid   # Add sparse vectors, compare hybrid vs dense retrieval
```
