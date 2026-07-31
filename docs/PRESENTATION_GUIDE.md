# CACE — Presentation Guide & System Walkthrough

This document is written for you to present from and to build your own mental
model of the system. It covers, in order: whether the project is ready to
present, the order to present it in, what to say about each part, the
limitations to state up front, and a from-scratch explanation of LangGraph and
RAG using this project as the example — with ASCII diagrams throughout.

---

## 0. Readiness verdict

**Yes — ready to present, with stated limitations.**

Evidence, checked immediately before writing this document:

| Check | Result |
|---|---|
| Backend test suite | **951 passed, 0 failed** |
| Type checking (`mypy`) | **0 errors across 219 files** |
| Frontend build (`tsc` + `vite`) | **succeeds** |
| Git status | clean, in sync with `origin/main` (commit `c232e6c`) |

Nothing is broken. The verdict below is "ready with limitations," not "ready,
full stop" — the limitations section (§2) is not optional to mention. A
capstone reviewer will trust you more for stating them yourself than for
having them discovered.

### Cleanup performed just now

You asked to remove unnecessary files. Removed, none of it tracked by git:

- **3 stale verification worktrees** (`backend/scratch/cace-clean-check{,2,3}`
  and a matching one under `/tmp`) — full duplicate checkouts left over from
  earlier isolated test runs, ~740 MB total, already superseded by the current
  `main`.
- **6 deployment debug scripts** at the repo root (`scratch/railway_db.py`,
  `remote_db.py`, `run_migration.py`, `verify_migration.py`, `query_db.py`,
  `test_production_api.py`) — one-off scripts used while debugging the Railway
  deployment, reading credentials from environment variables at runtime (none
  hardcoded), no longer needed now that migrations run automatically before
  startup.

Nothing that was part of the actual application, its tests, its migrations, or
its documentation was touched. `.venv/`, `node_modules/`, and the cache
directories are normal, gitignored build artifacts — left alone because you
need them to run the project.

---

## 1. Presentation order

Present in this order. Each step says what to show and what to say.

```
1. Problem statement           →  what customs export compliance requires,
                                   why it is currently manual and error-prone

2. Three-experience overview   →  show the sidebar: Prepare an Export,
                                   Ask CACE, Overview/Search
                                   (§3 below has the ASCII map)

3. Deterministic engine demo   →  Prepare an Export, live: pick a product,
                                   see required/conditional documents with
                                   cited sources (§4)

4. Document upload + audit     →  New Review: upload an invoice + packing
                                   list, start the agent audit, show the
                                   LangGraph run reach a verdict (§5 — this
                                   is your LangGraph story)

5. Human-in-the-loop           →  trigger or show a manual-review case,
                                   the interrupt, the correction, the
                                   recheck (§5.3)

6. Ask CACE demo               →  live queries showing the four answer
                                   modes: a checklist question, an
                                   ambiguous product question, a "what is
                                   Form-E" explanation, an explicit search
                                   (§6 — this is your RAG story)

7. Under the hood              →  hybrid retrieval, the evidence gate, the
                                   persistent index (§6.4–6.6) — as much
                                   depth as your audience wants

8. Limitations                 →  state them yourself (§2)

9. Q&A
```

The reasoning for this order: a live demo of the deterministic checklist
first establishes "this actually works and is grounded," before you show the
more impressive but probabilistic-feeling multi-agent audit and RAG chat.
Trust, then sophistication.

---

## 2. Limitations — state these yourself

Say these before anyone asks. Each one is a scope decision you can defend, not
a bug you're hiding.

1. **Deterministic compliance covers 17 textile PCT codes**, not the full
   Pakistan Customs Tariff. Every code was individually verified against the
   official tariff PDF and the Export Policy Order before being added — not
   guessed. Outside those 17, Ask CACE can discuss regulation informationally
   but the system will not issue a pass/fail verdict.
2. **Single-user prototype.** No authentication, no multi-tenant document
   isolation. Stated in the UI itself.
3. **Not legal advice.** Every guidance response says so. The corpus is a
   dated snapshot (see `regulatory_data/README.md`), not a live feed of
   Pakistani customs law.
4. **Some official amendments are excluded on purpose.** Seven Export Policy
   Order amendments are held back because their own OCR-validation record
   flags unresolved code conflicts — the system chose not to index law it
   can't verify, rather than index it anyway and hope. That refusal is a
   feature, not a gap you stumbled into.
5. **No `pgvector`.** Dense retrieval runs as an in-process cached matrix
   multiply rather than a database-native vector index, because this
   PostgreSQL instance doesn't have the extension. It works and is fast at
   this corpus size (~6,700 chunks); it would need revisiting at a much
   larger scale.
6. **RAG answers are generated by Groq from gated evidence.** Retrieval and
   the evidence gate run first; Groq then writes the prose from the passages
   the gate accepted. It cannot invent a document requirement — the checklist
   is decided by Python and rendered verbatim beneath the answer — and
   generated text that names a document outside that checklist, or claims the
   list is complete, is discarded for a written template (§6.7).
7. **The multi-agent audit's "agents" are deterministic by default**, not
   live LLM calls, in the test/demo environment — this is intentional (§5.4)
   but worth being upfront about if asked "is this really AI."

---

## 3. The three-experience map

This is the first diagram to show. It's the whole product on one screen.

```
                              ┌─────────────┐
                              │   Exporter  │
                              └──────┬──────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
              ▼                      ▼                      ▼
   ┌─────────────────────┐ ┌──────────────────┐ ┌────────────────────────┐
   │  A. Prepare an       │ │  B. Ask CACE      │ │  C. Ask About This     │
   │     Export           │ │                   │ │     Shipment           │
   │                      │ │                   │ │                        │
   │  deterministic       │ │  informational,   │ │  read-only, answers    │
   │   17-PCT checklist    │ │  full corpus,     │ │  from THIS shipment's  │
   │  engine               │ │  no verdicts      │ │  frozen audit result   │
   │  (§4)                 │ │  outside the 17   │ │  (§5)                  │
   │                      │ │  (§6)             │ │                        │
   └──────────┬───────────┘ └─────────┬─────────┘ └───────────┬────────────┘
              │                       │                       │
              └───────────┬───────────┴───────────┬───────────┘
                          ▼                        ▼
              ┌────────────────────────────────────────────┐
              │   Shared regulatory corpus (PostgreSQL)     │
              │   6 756 chunks · 9 sources · hybrid search  │
              └────────────────────────────────────────────┘
```

**The one sentence that matters:** these are three *different scopes*, kept
deliberately separate — the knowledge corpus (what Ask CACE can discuss), the
compliance catalog (what the deterministic engine can decide), and the
shipment (what's true about one specific upload). Early in this project a
single "five supported codes" list was used as all three boundaries at once,
which meant Ask CACE refused to even *talk about* Form-E unless you typed one
of five exact tariff codes. Separating the three scopes was the single
biggest architecture correction in the whole project — worth a sentence in
your talk.

---

## 4. The deterministic compliance engine

This is **not** an AI component. Say that plainly — it's a strength, not an
admission. Customs compliance decisions (required / conditional / failed /
manual review) must be reproducible and auditable, so they come from
configuration, not from a model.

```
   Product + PCT code + destination
                 │
                 ▼
   ┌─────────────────────────────────┐
   │  textile_mvp_pct_codes.json      │   ← 17 codes, each with its
   │  (the catalog)                   │      tariff page verified by hand
   └────────────────┬────────────────┘
                     ▼
   ┌─────────────────────────────────┐
   │  textile_mvp_executable_rules    │   ← 79 rules: "PCT X requires
   │  .json  (the rule set)           │      document Y, unless Z"
   └────────────────┬────────────────┘
                     ▼
   ┌─────────────────────────────────┐
   │  DeterministicComplianceRule     │   ← pure function: same input,
   │  Engine.check()                  │      same output, every time
   └────────────────┬────────────────┘
                     ▼
        required / conditional / manual_review
        + which rule, which law, which page
```

**Why this design:** every requirement traces to a `rule_id`, a source
document, and (where known) a page number. When you show a document
requirement on screen, you can click through to *why* — that traceability is
what makes it auditable rather than a black box.

**Concrete numbers to say out loud:** 17 PCT codes across 6 textile
categories (raw cotton, yarn, woven fabric, knitted garments, woven garments,
made-ups), 79 configured rules, all cross-checked against the Export Policy
Order 2022 and the current tariff before being trusted.

---

## 5. LangGraph — the multi-agent audit workflow

### 5.1 What LangGraph actually is (concept, not this project)

If your audience doesn't know LangGraph: it's a library for building an
application as a **state machine of steps ("nodes")** connected by **edges**,
where the whole machine's job is to read and update one shared object (the
"state") as it moves node to node. The two things it gives you that a plain
function call chain doesn't:

1. **Conditional edges** — after a node runs, a small routing function looks
   at the state and decides *which* node runs next. This is how the flow
   branches ("if the two agents disagree enough → go to human review; if they
   agree → skip straight to the final report").
2. **Checkpointing** — after every node, the entire state is saved to durable
   storage (here, PostgreSQL). This means the workflow can **stop and wait**
   — for a human to review something — and **resume days later** exactly
   where it left off, state intact.

```
   ┌───────────┐   node runs,   ┌──────────────┐  routing fn   ┌───────────┐
   │  State    │──updates state→│  Node        │─decides next─→│  State    │
   │ (shared   │                │  function    │   node from   │  (again)  │
   │  object)  │←───────────────│              │   the state   │           │
   └───────────┘                └──────────────┘                └───────────┘
        │
        ▼ after every node
   ┌────────────────────────────┐
   │  Checkpoint saved to        │   ← this is what makes
   │  PostgreSQL                 │      "pause for a human,
   └────────────────────────────┘      resume later" possible
```

### 5.2 This project's actual graph

This is the real wiring from `graph.py` — every node and edge below exists in
the code, nothing simplified for the diagram.

```
 START
   │
   ▼
 load_shipment_documents         (pull the uploaded invoice + packing list)
   │
   ▼
 broker_agent                    (extracts a structured report: what the
   │                              documents SAY)
   ▼
 deterministic_compliance        (the engine from §4 — the compliance
   │                              VERDICT, not opinion)
   ▼
 auditor_agent                   (independently checks the broker's report
   │                              against the documents — a second opinion)
   ▼
 compare_agent_reports           (do broker and auditor agree?)
   │
   ├─ requires_human_review? ──yes──▶ prepare_human_review
   │                                    (builds + checkpoints the task,
   │                                     so the audit trail records the
   │                                     pause *before* it happens)
   │                                          │
   │                                          ▼
   │                                  interrupt_for_human_review
   │                                    (pauses; computes nothing, because
   │                                     LangGraph re-runs this body on resume)
   │                                          │
   │                                          ▼
   │                                  human_decision_received
   │                                          │
   │                            ┌─────────────┴─────────────┐
   │                     confirm/correct                 anything else
   │                            │                             │
   │                            ▼                             ▼
   │                  apply_human_correction            resume_workflow
   │                            │                             │
   │                  ┌─────────┴─────────┐                   │
   │            validation failed    correction applied        │
   │                  │                   │                   │
   │                  ▼                   ▼                   │
   │           build_final_report  freeze_corrected_revision   │
   │                  ▲                   │                   │
   │                  │                   ▼                   │
   │                  │          auditor_recheck_revision      │
   │                  │                   │                   │
   │                  │                   ▼                   │
   │                  │      recompute_consensus_after         │
   │                  │            _correction                 │
   │                  │                   │                   │
   │                  │        still uncertain, round < cap?   │
   │                  │            │              │            │
   │                  │           yes             no           │
   │                  │            │              │            │
   │                  │            ▼              ▼            │
   │                  │   (loop back to    build_final_report  │
   │                  │    interrupt_for_          ▲           │
   │                  │    human_review)            │           │
   │                  └─────────────────────────────┘◀──────────┘
   │
   └─ no, agree ──────────────────────────────────▶ build_final_report
                                                              │
                                                              ▼
                                                    generate_explanation
                                                              │
                                                              ▼
                                                    persist_audit_record
                                                              │
                                                              ▼
                                                             END
```

**How to narrate this in one breath:** "Two independent agents look at the
same documents — a broker that extracts what they say, and an auditor that
checks the broker's work. If they agree, the shipment finalizes
automatically. If they don't, the graph *pauses* — genuinely pauses, saved to
Postgres — until a human resolves it. Corrections get rechecked by the
auditor again before the graph is allowed to finalize, and it will loop back
for another review round rather than finalize on a correction nobody
verified, up to a capped number of rounds so it can't loop forever."

### 5.3 Human-in-the-loop, concretely

This is the part worth a live demo if you have a fixture ready:

```
   broker says X  ──┐
                     ├──► disagreement ──► PAUSE, checkpoint saved
   auditor says Y  ──┘         (this is a LangGraph "interrupt", not
                                 a crash — the process can restart and
                                 the workflow is still sitting here,
                                 waiting, in Postgres)
                                       │
                          human reviews the disputed field
                                       │
                          confirms X, corrects to Z, or takes
                          another action (reject/reprocess/etc.)
                                       │
                          ▼
                 workflow resumes from the checkpoint —
                 not from the beginning
```

### 5.4 A note on "agents"

Say this explicitly if asked: in the default/test configuration the broker
and auditor are **deterministic** implementations (`DeterministicBrokerAgent`,
`DeterministicAuditorAgent`) — they extract and cross-check using rules, not
an LLM call, which is why the whole audit test suite runs with zero real API
calls and zero flakiness. The graph is written against an `Agent` **protocol**
(an interface), so a live-LLM agent could be swapped in without touching the
graph — but the shipped, tested default is deterministic. This is a defensible
engineering choice for a compliance system: you generally do *not* want a
probabilistic model deciding whether a customs shipment is compliant.

---

## 6. RAG and Ask CACE — the advanced techniques

### 6.1 What RAG is, from zero

RAG = **Retrieval-Augmented Generation**. Instead of asking a language model a
question and trusting whatever it recalls from training (which can be wrong,
outdated, or fabricated — "hallucination"), you:

```
   1. Search your own trusted documents for passages relevant to
      the question           →  RETRIEVAL

   2. Only build an answer from the passages you actually found  →  AUGMENTED
      (the answer is "grounded" in real, cited text)

   3. Produce the final answer, ideally with citations           →  GENERATION
```

The whole point is **grounding**: every claim should be traceable back to a
real passage, in a real document, at a real page. This project does use a
language model for step 3, but constrains what that model is allowed to
decide: the compliance facts are fixed by Python before the call, and the
generated prose is validated against them afterwards (§6.7).

### 6.2 The retrieval pipeline — hybrid search

A single search strategy has a known weakness. **Keyword search (BM25)** is
precise but misses synonyms ("paperwork" won't match "documentation").
**Dense/semantic search (embeddings)** catches meaning but can drift toward
vaguely-related passages. This project runs **both**, then fuses the results.

```
   User's question, normalized
              │
    ┌─────────┴──────────┐
    ▼                     ▼
┌─────────────┐   ┌──────────────────┐
│  LEXICAL     │   │  DENSE            │
│  PostgreSQL  │   │  cached embedding │
│  tsvector +  │   │  matrix, cosine   │
│  GIN index   │   │  similarity       │
│  (§6.5)      │   │  (§6.5)           │
└──────┬───────┘   └────────┬─────────┘
       │  ~200 candidates    │  ~200 candidates
       └──────────┬──────────┘
                   ▼
        ┌────────────────────┐
        │  RRF                │   Reciprocal Rank Fusion:
        │  (Reciprocal Rank   │   combines both rankings into one,
        │   Fusion)           │   rewarding a passage that scored
        └──────────┬──────────┘   well on EITHER signal
                   ▼
        ┌────────────────────┐
        │  cross-encoder      │   a small model that reads the
        │  reranker           │   (question, passage) PAIR together —
        └──────────┬──────────┘   more accurate than either signal alone,
                   ▼               but too slow to run on the whole corpus,
        ┌────────────────────┐   which is why it only reranks the
        │  parent-chunk       │   ~25 survivors from RRF
        │  expansion          │
        └──────────┬──────────┘   the SEARCH unit is a small child chunk
                   ▼               (precise matching); the RETURNED unit
        ┌────────────────────┐    is its larger parent chunk (enough
        │  evidence gate      │    context for a human to read)
        │  (§6.6)             │
        └──────────┬──────────┘   a hard, deterministic relevance floor —
                   ▼               "did we actually find something," not
             accepted evidence     left to the reranker's opinion
```

**Why RRF instead of just averaging scores:** BM25 scores and cosine
similarities live on completely different numeric scales and aren't
comparable. RRF sidesteps that by fusing on *rank position* (1st, 2nd, 3rd...)
rather than raw score, which needs no calibration between the two systems.

### 6.3 Parent–child chunking

```
   PARENT chunk (a full clause/section — maybe 300 words)
   ┌────────────────────────────────────────────────┐
   │  "Export of raw cotton is conditional. A        │
   │  security deposit of 1% ... an irrevocable      │
   │  letter of credit shall be opened ..."          │
   │  ┌──────────┐ ┌──────────┐ ┌──────────┐         │
   │  │ CHILD 1  │ │ CHILD 2  │ │ CHILD 3  │  ← search
   │  │ "1%      │ │ "letter  │ │ "180     │    happens
   │  │ deposit" │ │ of       │ │ days"    │    HERE
   │  │          │ │ credit"  │ │          │
   │  └──────────┘ └──────────┘ └──────────┘         │
   └────────────────────────────────────────────────┘
```

Searching on small, focused children keeps matching precise (a question about
"letter of credit" shouldn't match on an unrelated sentence three paragraphs
away just because they're in the same chunk). But *returning* the small
child alone would lose context, so the system expands back out to the parent
before presenting it as evidence.

### 6.4 The evidence gate — why it exists

This is a **deterministic, reranker-independent relevance floor**, and it's
one of the more carefully engineered pieces in the project. Two real
incidents drove its exact shape, worth telling as a story:

**Incident 1 — the metadata trap.** A page in the base Export Policy Order
document was tagged at ingestion with *every one of the five original PCT
codes*, even though the page itself was actually about an unrelated Schedule
III negative list (tobacco, medical devices, seeds). A naive "boost if the
metadata tag matches" rule let that irrelevant page pass the gate for a
cotton-T-shirt query — its real lexical overlap with the query was 0.083 (they
share only the word "PCT"). The fix: a chunk's own declared metadata may only
ever **boost an already-relevant passage**, never manufacture relevance out of
almost nothing. There's a numeric floor (`MIN_BASE_LEXICAL_OVERLAP`) that has
to be cleared by real word-overlap *before* any metadata bonus applies.

**Incident 2 — the reranker-scale trap.** A different evidence gate elsewhere
in the code compared the reranker's raw score against a hardcoded threshold of
`-2.0`. That was fine for a reranker whose scores live in `[0, 1]` — but the
*real* cross-encoder model returns unbounded logits, measured on this
project's own test fixtures at values from `-9.09` to `+4.79`. The same
threshold made the gate a complete no-op under one reranker and a blanket
rejection under the other — so whether an answer worked at all *silently
depended on which reranker happened to be configured*. The fix: stop
comparing a reranker score to a fixed number at all. The gate now asks a
model-independent question instead — "do the question and the passage share
at least one real content word?" — which can never drift out of calibration
because there's no calibrated constant left to drift.

The lesson worth stating in your talk: **a relevance gate must not depend on
which scoring model happens to be plugged in.** Both incidents were exactly
this mistake in two different forms, and both fixes were the same fix: make
the accept/reject decision deterministic and reranker-independent, and let the
reranker only affect *ordering*, never *whether something counts as evidence
at all*.

### 6.5 Making it fast — the persistent index

Early in the project, both retrieval stages rebuilt themselves from scratch
**on every single question**:

```
   BEFORE                                     AFTER
   ───────                                    ─────
   every query:                               every query:
     load all chunks from DB                    PostgreSQL GIN index
     tokenize all of them                        lookup (already built,
     build a BM25 index in Python                 already indexed)
     load ALL stored embeddings                 in-process cached
     from DB as JSON                             embedding matrix
     (this alone: 296ms at only                  (loaded ONCE, reused
      374 chunks)                                 until it changes)

   374 chunks:  ~590ms per query              374 chunks:   ~30ms
   6 756 chunks: 553ms of index-build           6 756 chunks: ~30ms
                 alone, before search even
                 started
```

This is the difference between an architecture that scales with the corpus
and one that doesn't. It's also what made it *affordable* to add the full
Customs Act (279 pages) and Customs Rules (634 pages) to the corpus — on the
old design, that would have pushed every question into multi-second latency.

**The stored index itself:** a PostgreSQL `tsvector` column, declared
`GENERATED ALWAYS AS (...) STORED`. This is the key detail — it's not a
trigger someone could forget to fire, it's a database-enforced invariant: the
search vector *cannot* drift out of sync with the text, because Postgres
recomputes it as part of every write.

### 6.6 Ask CACE's routing intelligence

This is the newest and most "product-thinking" layer, added after a real
failure: the question *"What documents to prepare for Cotton paints export to
usa from pakistan"* (a typo for "pants") was answered with five raw regulatory
passages about cotton seeds and vegetable ghee — technically "relevant" by
keyword overlap, useless to a human. Tracing it found the fix wasn't one
patch, it was a whole missing layer: **routing and presentation**, sitting
in front of retrieval.

```
   Free-text question
          │
          ▼
   ┌─────────────────────┐
   │  Domain guard         │  in-domain? (customs/trade/textile topic,
   │                       │   not injected instructions)
   └──────────┬────────────┘
              ▼
   ┌─────────────────────┐
   │  Product resolver     │  "cotton pants" → curated alias table →
   │  (typo-tolerant,      │   {men's trousers, women's trousers}
   │   conservative)       │   "paints"→"pants": edit-distance-1 repair,
   └──────────┬────────────┘   ONLY within a fixed vocabulary, ONLY
              │                 when a textile signal is also present
              ▼
   ┌─────────────────────┐
   │  Destination          │  "usa"/"U.S.A."/"United States"/"America"
   │  normalizer           │   → one canonical value; "from Pakistan"
   └──────────┬────────────┘   correctly ignored as origin, not destination
              ▼
   ┌─────────────────────┐
   │  Intent classifier     │  checklist? explanation? evidence-lookup?
   │                       │   explicit document-search?
   └──────────┬────────────┘
              ▼
      ┌───────┴────────┬─────────────┬──────────────┐
      ▼                ▼             ▼              ▼
  CHECKLIST      CLARIFICATION  EXPLANATION   DOCUMENT SEARCH
  (ambiguous      (2+ product    (short,        (explicit "find
   resolved,       matches:       cited          every document
   deterministic   "men's or      answer)        mentioning X" —
   engine answers) women's?")                    the only mode
                                                  that shows raw
                                                  passages)
```

**Why the alias/typo matching is deliberately narrow, not "smart":** an
unrestricted fuzzy matcher over 17 product names would happily map "cotton
seed" onto a garment code and issue a confident-looking checklist for the
wrong product — worse than admitting it doesn't know. So the repair rules are:
(1) aliases come from a small curated table, never derived by fuzzy-matching
catalog names directly; (2) a spelling repair can only rewrite a word into
something *already in that table*, at edit-distance exactly 1, and only for
words long enough that "differs by one letter" is meaningful; (3) a match
still needs an independent textile signal elsewhere in the question. When two
codes fit equally (men's vs. women's trousers), the system **asks**, and shows
what both options already share while it waits for an answer, rather than
guessing.

### 6.7 Where the language model is allowed to act

Ask CACE **does** use Groq to write its answers — the project owner asked for
that explicitly. The safety property is not "no model runs"; it is that the
model runs *after* every decision has already been made, and is checked
afterwards.

```
   What Python decides (before the call)   What Groq is asked to do
   ─────────────────────────────────────   ────────────────────────
   which product / PCT code                write 2-3 short paragraphs
   which documents are required            in plain English, explaining
   which are conditional                   the facts it was given
   passed / failed / manual_review
   which passages cleared the gate         (prose only - no decisions)
```

Four constraints hold around the call:

1. **Only gated evidence reaches the prompt.** Retrieval, reranking and the
   deterministic evidence gate all run first, so the model never sees a
   passage the gate rejected.
2. **The checklist is rendered verbatim underneath.** The document list comes
   from the rule engine, not the model, and the structured fields the UI reads
   are untouched by it.
3. **Generated prose is validated.** It passes the same forbidden-claim checks
   as CACE's own framing ("...is compliant", "...will clear customs",
   "...is authentic"), plus two more: it may not name a document outside the
   deterministic checklist, and it may not claim the list is complete. Failing
   any of these, it is discarded and the written template is shown instead.
4. **Passages are data, not instructions.** The system prompt says so, and
   nothing in this path executes passage text.

If Groq is unavailable, rate limited, or returns something that fails
validation, the answer says so plainly and shows the deterministic content
underneath — a template answer is never silently passed off as a generated one.

### 6.8 Unsupported-code honesty

One more piece worth a slide: when a question names a PCT code outside the
17 supported ones, the system runs **staged retrieval** rather than either
refusing outright or quietly answering as if the code were supported:

```
   Stage 1: search WITH the exact code
            └─ passage text actually contains that code? → "exact_pct"
                     evidence, shown normally

   Stage 2 (only if stage 1 found nothing):
            search the BROADER product category, code removed
            └─ found something? → shown, but labelled:
               "I did not find evidence specifically mentioning PCT
                {code}. The following passages concern the broader
                product category and should not be treated as a
                compliance determination for that code."

   Either way: NEVER a pass/fail verdict for an unsupported code.
```

This distinguishes three honestly different situations — "the corpus talks
about exactly this," "the corpus talks about the general category, not this
specific thing," and "the corpus has nothing" — instead of collapsing them
into one answer that oversells its own confidence.

---

## 7. Glossary (for your own reference, not necessarily to present)

| Term | One-line meaning |
|---|---|
| **RAG** | Search real documents first, then answer only from what was found |
| **BM25** | Classic keyword-ranking algorithm; good at exact terms |
| **Embedding** | A vector representation of text; similar meaning → similar vector |
| **Dense retrieval** | Search by embedding similarity (semantic, not keyword) |
| **RRF** | Reciprocal Rank Fusion — merges two rankings by position, not raw score |
| **Cross-encoder** | A model that scores a (question, passage) pair together — more accurate, more expensive |
| **Chunk** | A slice of a document small enough to search/embed |
| **Parent/child chunking** | Search small, return the larger surrounding context |
| **Evidence gate** | A hard pass/fail relevance check before anything is called "evidence" |
| **Grounding** | Every claim traceable to a specific retrieved passage |
| **LangGraph** | A framework for building an app as nodes + edges over a shared, checkpointed state |
| **Node** | One step in a LangGraph workflow (a function that reads/writes the state) |
| **Conditional edge** | A routing function that picks the next node based on current state |
| **Checkpoint** | A saved snapshot of the whole workflow state, enabling pause/resume |
| **Human-in-the-loop** | The workflow pauses and waits for a person before continuing |
| **Deterministic** | Same input always produces the same output — no randomness, no model call |
| **tsvector / GIN index** | PostgreSQL's built-in full-text search machinery |

---

## 8. If you get a hard question

- **"Is this in production?"** No — say so plainly. It's a working capstone
  prototype: single-user, a curated 17-code catalog, a snapshot corpus. The
  architecture is the deliverable, not a production deployment claim.
- **"Why not let the LLM just answer directly?"** §6.7 — grounding and
  citation-checking are impossible to guarantee once an LLM is writing the
  prose from its own training data. Extractive composition makes the
  hallucination risk structurally zero for that path, at the cost of less
  fluent phrasing.
- **"Why only 17 codes?"** Because every one of them was verified by hand
  against the actual Export Policy Order and tariff, and a wrong compliance
  verdict is worse than no verdict. Widening the list is a research task, not
  a config edit.
- **"What would you do with more time?"** `pgvector` for native dense search
  at scale, broader PCT coverage with the same verification rigor, and a real
  authentication/multi-tenant layer.
