# DD-070: The semantic encoder runs on onnxruntime, not torch

**Status:** Implemented (v0.62.14)
**Extends:** DD-048

## Context

Every Brain MCP server is a long-lived Python process, one per client session,
and semantic search encodes the query inside that process. Until v0.62.13 the
encoder was `sentence-transformers` on top of `torch`, loaded with no `device`
argument. On Apple silicon sentence-transformers therefore placed the model on
Metal.

Measured on 2026-09-11 in the managed runtime, fresh interpreter, real pinned
`all-MiniLM-L6-v2` snapshot (`footprint`, physical footprint):

| Step | torch, default device (MPS) | torch, `device="cpu"` | onnxruntime + tokenizers |
|---|---|---|---|
| After imports | 301 MB | 302 MB | 26 MB |
| After model load | 1,365 MB | 321 MB | 182 MB |
| After one query | 1,387 MB | 327 MB | 183 MB |
| Warm query latency | 93 ms | 10 ms | 36 ms |
| Cosine against the torch vectors | 1.0 | 1.0 | 1.0 |

A server that had run one hybrid search sat at roughly 1.4 GB for the rest of
its life; nine to twelve such servers on one machine were observed. The cost
was a process-topology multiplier (one server per session) times a per-process
cost dominated by GPU-owned memory that the process never released.

## Decision

The local encoder loads the snapshot's `onnx/model.onnx` with
`onnxruntime.InferenceSession` pinned to `CPUExecutionProvider`, tokenises with
the Hugging Face `tokenizers` library from `tokenizer.json`, mean-pools over the
attention mask and L2-normalises. That reproduces the shipped
sentence-transformers pipeline (Transformer → mean Pooling → Normalize) exactly,
so the model pin, the persisted embeddings sidecars and every recorded vector
stay valid; nothing needs rebuilding.

The semantic runtime pins become `onnxruntime`, `tokenizers`, `numpy` and
`huggingface-hub`. `torch`, `transformers` and `sentence-transformers` are no
longer installed. Model provisioning downloads only the files the encoder
reads, named once in `SNAPSHOT_FILES`, instead of the whole repository.

The loader refuses snapshots whose pooling config is not mean pooling and reads
`max_seq_length` from the snapshot rather than assuming it, so a future model
pin that needs a different pipeline fails at load time instead of producing
silently different vectors.

## Alternatives considered

**Pass `device="cpu"` and keep torch.** One line, 1,365 MB → 321 MB. Still
triples a server's baseline just by importing torch, and still leaves the
heaviest dependency in the stack for a 90 MB model. Kept only as the stopgap
this decision replaces.

**A shared embedding daemon that all MCP servers call.** Turns N × cost into
1 × cost, but adds a machine-global process tier, socket lifecycle, spawn races,
model-pin coordination across runtimes and a new failure mode inside the search
path. With the per-server increment at roughly 80 MB after this decision, the
numbers do not justify distributing. The encoder stays behind one seam
(`get_query_encoder`) so a daemon can be placed there later if a heavier model
ever ships.

**The quantised int8 ONNX export.** 88 MB loaded and about twice the batch
throughput, but its vectors drift to cosine 0.992 against the fp32 pipeline.
That makes the swap a model change rather than a runtime change and would force
a sidecar rebuild in every vault. Available later as a deliberate pin change.

**Release the model after idle.** torch does not reliably return its arena or
Metal buffers to the operating system, and after this decision there is little
left to release.

## Consequences

- A session server that has answered semantic queries now costs roughly 80 MB
  over the 104 MB baseline instead of 1.3 GB, and touches no GPU.
- Batch encoding is slower on CPU: about 33 s for 2,000 worst-case documents
  versus 4.7 s on Metal. That work runs in the detached warm-up worker that no
  caller waits on.
- The Intel macOS exclusion existed only because of torch wheel coverage and is
  lifted; `semantic_runtime_supported_platform` remains as the seam for any
  future exclusion.
- The semantic runtime shrinks from roughly 900 MB of wheels to tens of
  megabytes, and a fresh model snapshot from 932 MB to about 90 MB. Existing
  full snapshots keep working because they contain the ONNX export.
- `EMBEDDING_DIM` remains a hardcoded 384; the encoder exposes `dimension` from
  the model so a later pin change has a source of truth to derive it from.
- The MCP server still hosts the query encoder in-process. Corpus encoding in
  response to explicit MCP rebuild commands is addressed separately.
