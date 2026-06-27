# Evaluation

IntelliGraphRAG ships with an **end-to-end evaluation harness** that exercises the
whole platform — not a single retriever, but the entire pipeline from query
understanding through every retrieval lane to grounded, cited generation. The
flagship harness is the **50-question suite** in
[`scripts/eval_50.py`](../../scripts/eval_50.py), with a faster 18-question
variant in [`scripts/eval_full.py`](../../scripts/eval_full.py).

The goal is honest, lane-by-lane proof: each question is tagged with the
behaviour it is meant to test, scored for correctness, and tagged with **which
lane actually fired**. A passing run shows not just a high overall score but that
*every* retrieval lane engaged at least once and that out-of-corpus questions are
correctly refused.

> The harnesses were built and validated on a large U.S. government document
> corpus (the ATF firearms/explosives dataset). The questions below are drawn
> from that validation corpus — they are examples, not a fixed product
> benchmark. Bring your own domain question set (see
> [Adding your own question sets](#adding-your-own-question-sets)).

---

## What the harness covers

Each question in `scripts/eval_50.py` is a 4-tuple
`(question, [expected any-of], kind, must_refuse)`. The `kind` tag maps to a
distinct platform capability and, usually, a distinct retrieval lane:

| Kind | What it tests | Lane(s) it should drive |
| --- | --- | --- |
| `cell` | Cell-level lookup of a specific value from a real indexed table row | `table_row` deterministic lookup |
| `aggregate` | Counts / totals / rankings over the table store | `sql` (text-to-SQL) or `numeric` rescue |
| `crossyear` | Comparing the same metric across report years | `sql` / multi-hop |
| `comparison` | Comparing two quantities (imports vs. exports, lost vs. stolen) | `sql` / multi-hop |
| `fact` | Definitions and policy facts | vector + BM25 hybrid |
| `relationship` | How two entities connect | graph (`bfs` / `ppr`) |
| `pattern` | Recurring structures across the corpus | graph (`ppr`) / global communities |
| `timeline` | Trends over time | multi-hop / global |
| `multi` | Multi-document synthesis | global / community summaries |
| `visual` | Questions answered from chart/figure/table content | vector (with `visual_boost`) |
| `refusal` | Out-of-corpus questions that **must** be declined | refusal detection |

The 50 questions intentionally include `cell` questions whose ground-truth values
were re-sampled from the **current parse's actual rows** (e.g. the city for a
specific manufacturer in the AFMER listings), so a correct answer proves the cell
made it intact all the way from PDF table to deterministic lookup.

---

## Running the harness

The harness builds a real `Engine` and `Retriever` against your indexed corpus,
so you need a populated knowledge base and an LLM provider configured. With the
default `local` profile that means an OpenRouter key.

```bash
# 1. Make sure a corpus is indexed (see the Quickstart / ingestion docs)
python -m atf_graphrag ingest ./your-docs

# 2. Configure the environment
export ATF_PROFILE=local            # local | hybrid | aws | oss
export OPENROUTER_API_KEY=sk-or-...  # required for the local profile's LLM

# 3. Run the 50-question harness
python scripts/eval_50.py

# (faster smoke variant — 18 questions)
python scripts/eval_full.py
```

> The harness calls `os.environ.setdefault("ATF_PROFILE", "local")`, so if you do
> not set `ATF_PROFILE` it defaults to the `local` profile. Set it explicitly to
> evaluate against `hybrid`, `aws`, or `oss`.

Each question prints a live line as it runs:

```text
OK [       cell] hit=True lane=table_row (1.2s) :: WILSONS GUN SHOP INC is located in Berryville...
OK [  aggregate] hit=True lane=sql (2.8s) :: A total of 3,939,131 firearms were manufactured...
OK [    refusal] hit=None lane=graph:bfs (0.9s) :: The provided documents do not contain any...
```

`OK`/`XX` is the per-question verdict, `kind` is the category, `hit` is whether
an expected substring was found (`None` when no ground truth is pinned), `lane`
is the lane that fired, and the tail is the first ~80 characters of the answer.

A machine-readable report is written next to the script:
`scripts/eval_50_report.json` (and `scripts/eval_full_report.json` for the
smaller suite).

---

## Reading the scorecard

At the end of a run the harness prints — and writes to the report's `summary`
block — a compact scorecard:

```text
================================================================
               n: 50
      overall_ok: 0.86
   answerable_ok: 0.851
         cell_ok: 1.0
      refusal_ok: 1.0
         by_kind: {'cell': '8/8', 'aggregate': '6/6', 'fact': '11/11', ...}
     lanes_fired: {'table_row': 8, 'sql': 6, 'numeric': 1, 'graph:bfs': ...}
       elapsed_s: 92.4
================================================================
  misses: ['...the question text of any failures...']
```

| Field | Meaning |
| --- | --- |
| `n` | Total questions in the suite |
| `overall_ok` | Fraction correct across **all** questions, refusals included (~**0.86**) |
| `answerable_ok` | Correctness on non-refusal questions only |
| `cell_ok` | Correctness on the cell-level lookups (the strictest, most grounded slice) |
| `refusal_ok` | Fraction of out-of-corpus questions correctly refused (target **1.0 / 100%**) |
| `by_kind` | Per-category `correct/total`, e.g. `'aggregate': '6/6'` |
| `lanes_fired` | How many questions each lane handled — your **lane-coverage** check |
| `elapsed_s` | Wall-clock time for the run |
| `misses` | The exact text of every question that failed, for triage |

### How a question is scored

For each question the harness inspects the answer and its trace:

- **`refused`** — true when the answer is empty or its opening reads as a genuine
  refusal (see [Refusal detection](#how-refusal-detection-works)).
- **`answered`** — true when the answer is *not* a refusal **and** carries at
  least one citation. Citations are mandatory; an uncited answer never counts as
  answered.
- **`hit`** — true when any expected substring appears in the answer (`None` when
  the question has no pinned ground truth — common for open-ended `fact`,
  `relationship`, and `multi` questions).
- **Verdict (`ok`)** — for `must_refuse` questions, `ok == refused`. Otherwise
  `ok == answered and hit is not False` (so an answered, cited response with no
  pinned ground truth still passes as long as it does not contradict a known
  expected value).

### Lane coverage

`lanes_fired` is the proof that the platform is exercising its whole retrieval
surface, not leaning on one lane. The lane label is derived from the trace:

- `sql` — the text-to-SQL lane fired (`trace["3d_sql"]`)
- `numeric` — the headline-total numeric rescue fired (`trace["3e_numeric"]`)
- `table_row` — deterministic cell lookup returned matches
  (`trace["3_retrieval"]["table_row_matches"]`)
- `global` — a corpus-wide community-summary answer (`mode == "global"`)
- `graph:bfs` / `graph:ppr` — the graph lane, tagged with its traversal mode

A healthy run shows every one of these with a non-zero count.

---

## How refusal detection works

Refusals are scored by a small, deliberately conservative heuristic. The
50-question harness anchors refusal cues to the **opening** of the answer
(`_is_refusal` in `scripts/eval_50.py`):

```python
def _is_refusal(a):
    al = (a or "").strip().lower()
    if not al:
        return True
    head = al[:240]
    cues = ["does not contain", "do not contain", "does not include any inf",
            "no information", "not available in the", "is not available in",
            "not provided in the", "cannot provide", "could not find",
            "i cannot answer", "not found in the", "no relevant",
            "insufficient context", "outside the scope", ...]
    return any(c in head for c in cues)
```

The key design choice: only the **first 240 characters** are scanned. A genuine
refusal states its inability up front. Earlier versions matched broad substrings
anywhere in the answer and produced **false positives** — a correct answer that
merely mentioned a limitation mid-body (for example "…PMFs *do not include*
firearms registered in the NFRTR" or "…a person *unable to* legally possess…")
was wrongly flagged as a refusal. Anchoring to the head fixes that: those phrases
are legitimate *content*, not a decline.

> `scripts/eval_full.py` uses a simpler, broader cue list that is not
> head-anchored. It is a quick smoke test; treat `scripts/eval_50.py` as the
> authoritative harness.

For the three out-of-corpus refusal questions (stock price, a future World Cup,
a cake recipe), the target is a clean 100% refusal rate — the system must decline
rather than hallucinate.

---

## Honest caveats

The scorecard is intentionally not gamed to 1.0. A couple of known sources of
legitimate, non-bug variance:

- **Multi-instance company gold ambiguity.** Some entities appear in more than one
  table row across years or reports (the same manufacturer with slightly
  different addresses, or two records sharing a name). A cell question can have
  more than one defensible "correct" value, so the pinned ground truth is a
  best-effort any-of list. An answer that is genuinely correct for a *different*
  valid instance may score as a miss.
- **Ranking / aggregate non-determinism.** The `aggregate` and ranking questions
  route through the LLM-backed SQL and synthesis path. Tie-breaks, rounding, and
  phrasing can vary run to run, so a borderline ranking question may flip between
  `OK` and `XX` across runs even with no code change. Look at `overall_ok` as a
  band (~0.86) rather than a single fixed number, and use `misses` to confirm a
  drop is a real regression, not noise.

These are documented on purpose: a harness that always reports a perfect score is
usually measuring the wrong thing.

---

## Adding your own question sets

IntelliGraphRAG is domain-agnostic, and so is the harness. The fastest way to
validate the platform on **your** corpus is to write a question set in the same
4-tuple shape and point it at your index:

1. Copy `scripts/eval_50.py` to e.g. `scripts/eval_mydomain.py`.
2. Replace the `Q` list with your own
   `(question, [expected any-of], kind, must_refuse)` tuples. Re-sample the
   `cell` ground-truth values from your *actual* indexed rows so they survive a
   re-parse.
3. Keep at least a few `refusal` questions (clearly out-of-corpus) so you keep
   measuring refusal accuracy, not just recall.
4. Cover every `kind` you care about so `lanes_fired` confirms the relevant lanes
   engage on your data.
5. Run it exactly like the built-in harness:
   ```bash
   export ATF_PROFILE=local
   export OPENROUTER_API_KEY=sk-or-...
   python scripts/eval_mydomain.py
   ```

Other bundled evaluation scripts you can use as templates:
`scripts/eval_15_structured.py` and `scripts/eval_atf_25.py`.

> Tip: a good domain question set mixes a handful of grounded `cell` lookups
> (strict, deterministic), a few `aggregate`/`comparison` questions (SQL lane),
> some open-ended `fact`/`relationship`/`multi` questions (vector + graph), and a
> couple of refusals. That single mix exercises essentially the whole pipeline.

---
📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Architecture](Architecture.md)
