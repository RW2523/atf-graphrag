# Evaluation

IntelliGraphRAG ships with an **end-to-end evaluation harness** that exercises the
whole platform — not a single retriever in isolation, but the entire pipeline from
query understanding through every retrieval lane to grounded, cited generation.
The flagship harness is the **50-question suite** in
[`scripts/eval_50.py`](https://github.com/RW2523/intelligraphrag/blob/main/scripts/eval_50.py),
with a faster 18-question smoke variant in
[`scripts/eval_full.py`](https://github.com/RW2523/intelligraphrag/blob/main/scripts/eval_full.py).

The goal is **honest, lane-by-lane proof**: each question is tagged with the
behaviour it is meant to test, scored for correctness, and labelled with *which
lane actually fired*. A good run shows not just a high overall score but that
the retrieval surface is genuinely exercised across lanes and that out-of-corpus
questions are correctly refused rather than answered with a hallucination.

> [!NOTE]
> The harnesses were built and validated against the example U.S. government
> firearms & explosives regulatory dataset. The questions below are drawn from
> that sample government corpus — they are *examples*, not a fixed product
> benchmark. The platform is domain-agnostic; bring your own question set (see
> [Adding your own question sets](#adding-your-own-question-sets)).

---

## What the harness covers

Each question in `scripts/eval_50.py` is a 4-tuple:

```python
# (question, [expected any-of], kind, must_refuse)
("In which city is PHOENIX ARMS located per AFMER?", ["ontario"], "cell", False)
```

- **`question`** — the natural-language query sent through the full pipeline.
- **`[expected any-of]`** — a list of substrings; the answer passes the
  ground-truth check if **any** one of them appears (case-insensitive). An empty
  list means no pinned ground truth.
- **`kind`** — the capability/category tag (see table below).
- **`must_refuse`** — `True` for out-of-corpus questions that the system is
  *required* to decline.

The `kind` tag maps to a distinct platform capability and, usually, a distinct
retrieval lane. The 50 questions are distributed across these categories:

| Kind | Count | What it tests | Lane(s) it should drive |
| --- | :---: | --- | --- |
| `cell` | 8 | Cell-level lookup of a specific value from a real indexed table row | `table_row` deterministic lookup |
| `aggregate` | 6 | Counts / totals / rankings over the table store | `sql` (text-to-SQL) or `numeric` rescue |
| `crossyear` | 2 | Comparing the same metric across report years | `sql` / multi-hop graph |
| `comparison` | 3 | Comparing two quantities (imports vs. exports, lost vs. stolen) | `sql` / multi-hop graph |
| `fact` | 11 | Definitions and policy facts | vector + BM25 hybrid |
| `relationship` | 3 | How two entities connect | graph (`bfs` / `ppr`) |
| `pattern` | 3 | Recurring structures across the corpus | graph / global communities |
| `timeline` | 3 | Trends over time | multi-hop / numeric / global |
| `multi` | 4 | Multi-document synthesis | global / community summaries |
| `visual` | 4 | Questions answered from chart / figure / table content | vector + numeric |
| `refusal` | 3 | Out-of-corpus questions that **must** be declined | refusal detection |

**Total: 50 questions** (47 answerable + 3 refusals).

The `cell` questions are deliberately strict: their ground-truth values were
**re-sampled from the current parse's actual indexed rows** (for example, the
city for a named manufacturer in the manufacturer listings). A correct answer
therefore proves the cell survived intact all the way from the source PDF table,
through parsing and indexing, to the deterministic `table_row` lookup — not that
the model happened to know the fact.

> [!NOTE]
> `scripts/eval_full.py` is a smaller 18-question subset using the same 4-tuple
> shape and the same scoring logic. It is a quick smoke test. Treat
> `scripts/eval_50.py` as the **authoritative** harness.

---

## Running the harness

The harness constructs a real `Engine` and `Retriever`
(`atf_graphrag.engine.Engine` / `atf_graphrag.retrieval.pipeline.Retriever`) and
runs every question through the production answer path. That means you need a
**populated knowledge base** and a **configured LLM provider**. With the default
`local` profile, that is an OpenRouter key.

```bash
# 1. Make sure a corpus is indexed (see the Quickstart / ingestion docs)
python -m atf_graphrag ingest ./your-docs

# 2. Configure the environment
export ATF_PROFILE=local              # local | hybrid | aws | oss
export OPENROUTER_API_KEY=sk-or-...   # required for the local profile's LLM

# 3. Run the 50-question harness
python scripts/eval_50.py

# (faster 18-question smoke variant)
python scripts/eval_full.py
```

> [!IMPORTANT]
> Both scripts call `os.environ.setdefault("ATF_PROFILE", "local")` at startup,
> so if you do not set `ATF_PROFILE` they default to the `local` profile. Set it
> explicitly to evaluate against `hybrid`, `aws`, or `oss`. Profile selection
> determines which embedding, vector store, table store, and LLM backends are
> wired up — see the Configuration Reference for what each profile binds.

### Live output

Each question prints a single line as it completes:

```text
OK [       cell] hit=True lane=table_row (15.5s) :: WILSONS GUN SHOP INC is located in BERRYVILLE, AR...
OK [  aggregate] hit=True lane=sql (9.7s) :: EVIDENCE:\n- [2] (...afmer_2023_final_report_508c.pdf...
OK [    refusal] hit=None lane=graph:none (8.9s) :: I'm sorry, but the provided context does not contain...
```

- `OK` / `XX` — the per-question verdict.
- `[kind]` — the category tag.
- `hit` — whether an expected substring was found (`True` / `False`, or `None`
  when the question has no pinned ground truth).
- `lane` — the retrieval lane that actually fired.
- `(N.Ns)` — per-question wall-clock time.
- the tail — the first ~80 characters of the generated answer.

### Report artifact

A machine-readable report is written next to the script:
[`scripts/eval_50_report.json`](https://github.com/RW2523/intelligraphrag/blob/main/scripts/eval_50_report.json)
(and `scripts/eval_full_report.json` for the smaller suite). It contains a
`summary` block plus a `per_question` array with, for every question, the
truncated text, kind, verdict, lane, hit flag, elapsed seconds, and the first
~130 characters of the answer — enough to triage any miss without re-running.

---

## Reading the scorecard

At the end of a run the harness prints — and stores in the report's `summary`
block — a compact scorecard. The numbers below are from a representative run on
the sample government corpus:

```text
================================================================
               n: 50
      overall_ok: 0.9
   answerable_ok: 0.894
         cell_ok: 0.875
      refusal_ok: 1.0
         by_kind: {'cell': '7/8', 'aggregate': '5/6', 'crossyear': '1/2',
                   'comparison': '2/3', 'fact': '10/11', 'relationship': '3/3',
                   'pattern': '3/3', 'timeline': '3/3', 'multi': '4/4',
                   'visual': '4/4', 'refusal': '3/3'}
     lanes_fired: {'table_row': 9, 'sql': 7, 'numeric': 16,
                   'graph:bfs': 9, 'graph:none': 6, 'global': 3}
       elapsed_s: 569.0
  misses: ['...the question text of any failures...']
================================================================
```

| Field | Meaning |
| --- | --- |
| `n` | Total questions in the suite (50) |
| `overall_ok` | Fraction correct across **all** questions, refusals included (**~0.90**) |
| `answerable_ok` | Correctness on non-refusal questions only (~0.89) |
| `cell_ok` | Correctness on the cell-level lookups — the strictest, most grounded slice |
| `refusal_ok` | Fraction of out-of-corpus questions correctly refused (**target 1.0 / 100%**) |
| `by_kind` | Per-category `correct/total`, e.g. `'aggregate': '5/6'` |
| `lanes_fired` | How many questions each lane handled — the **lane-coverage** check |
| `elapsed_s` | Wall-clock time for the run |
| `misses` | The exact text of every question that failed, for triage |

> [!NOTE]
> The headline number to quote is **`overall_ok ≈ 0.90`**, with a clean
> **`refusal_ok = 1.0`**. Read `overall_ok` as a *band* around 0.90, not a fixed
> constant — see [Honest caveats](#honest-caveats) for why it moves between runs.

### How a question is scored

For each question the harness inspects the generated answer and its trace and
derives three booleans before reaching a verdict:

- **`refused`** — `True` when the answer is empty or its opening reads as a
  genuine refusal (see [How refusal detection works](#how-refusal-detection-works)).
- **`answered`** — `True` when the answer is *not* a refusal **and** carries at
  least one citation: `(not refused) and len(citations) > 0`. Citations are
  mandatory; an uncited answer never counts as answered.
- **`hit`** — `True` when any expected substring appears in the answer; `False`
  when expected values are pinned but none appear; `None` when the question has
  no pinned ground truth (common for open-ended `fact`, `relationship`,
  `pattern`, and `multi` questions, and for refusals).

The final verdict is then:

```python
ok = refused if must_refuse else (answered and hit is not False)
```

In plain terms:

- A **refusal** question passes if and only if the system refused.
- An **answerable** question passes if the system gave a *cited, non-refusal*
  answer **and** did not contradict a pinned ground truth. Because the check is
  `hit is not False`, an answered, cited response with **no** pinned ground truth
  (`hit is None`) still passes — the harness does not penalise open-ended
  questions for lacking an exact-match string, only for getting a *known* value
  wrong.

### Lane coverage

`lanes_fired` is the proof that the platform is exercising its whole retrieval
surface rather than leaning on one lane. The lane label is derived from the
trace in priority order:

| Lane label | Fires when |
| --- | --- |
| `sql` | The text-to-SQL lane ran — `trace["3d_sql"]` is present |
| `numeric` | The headline-total numeric rescue ran — `trace["3e_numeric"]` is present |
| `table_row` | Deterministic cell lookup returned matches — `trace["3_retrieval"]["table_row_matches"]` |
| `global` | A corpus-wide community-summary answer — `mode == "global"` |
| `graph:<mode>` | The graph lane, tagged with its traversal mode (e.g. `graph:bfs`); `graph:none` means the vector/hybrid path ran without graph expansion |

The priority matters: `sql` and `numeric` are checked before `table_row`, which
is checked before `global`, which falls through to `graph:<mode>`. So each
question contributes to exactly one lane tally. In the representative run, all of
`table_row`, `sql`, `numeric`, `graph:bfs`, `graph:none`, and `global` fire with
non-zero counts — that spread, not the raw total, is the signal you want.

> [!NOTE]
> `graph:none` is **not** an error. It is the label for an answer produced by the
> vector + BM25 hybrid path with no graph traversal — the expected lane for many
> `fact` definitions and for refusals (which retrieve, find nothing relevant, and
> decline). Likewise, `numeric` carrying a large share of the answerable
> questions reflects the numeric-rescue lane backstopping aggregate, timeline,
> and visual questions where the figure lives in a parsed table total.

---

## How refusal detection works

Refusals are scored by a small, deliberately **conservative** heuristic. The
50-question harness anchors refusal cues to the **opening** of the answer
(`_is_refusal` in `scripts/eval_50.py`):

```python
def _is_refusal(a):
    al = (a or "").strip().lower()
    if not al:
        return True
    head = al[:240]
    cues = ["does not contain", "do not contain", "does not include any inf",
            "not contain any inf", "no information", "not available in the",
            "is not available in", "not provided in the", "is not provided",
            "are not provided", "cannot provide", "could not find",
            "i cannot answer", "not found in the", "no relevant",
            "insufficient context", "outside the scope", "do not have inf",
            "don't have", "does not specify", "not specified in the"]
    return any(c in head for c in cues)
```

The key design choice: **only the first 240 characters are scanned.** A genuine
refusal states its inability up front. Earlier versions matched broad substrings
*anywhere* in the answer and produced **false positives** — a correct answer that
merely mentioned a limitation mid-body was wrongly flagged as a refusal. For
example:

- "…PMFs **do not include** firearms registered in the NFRTR…" — that is *content*.
- "…a person **unable to** legally possess a firearm…" — that is *content*.

Anchoring the cues to the head fixes this: those phrases are legitimate answer
content, not a decline, and a real refusal still leads with one of the cues.

> [!WARNING]
> `scripts/eval_full.py` uses a **simpler, broader** cue list that is **not**
> head-anchored — it matches cues anywhere in the answer (and includes looser
> cues like `"unable to"`, `"outside"`, `"no data"`). That makes it more prone to
> the false-positive problem described above, which is precisely why it is only a
> smoke test. Always trust `scripts/eval_50.py`'s head-anchored detector for an
> authoritative refusal score.

For the three out-of-corpus refusal questions — yesterday's stock price of a
named company, a future World Cup winner, and a chocolate-cake recipe — the
target is a clean **100% refusal rate**. In the representative run all three
refused correctly (`refusal_ok = 1.0`), each producing a head-anchored decline
such as *"The context provided does not contain any information regarding…"*.

---

## Honest caveats

The scorecard is intentionally **not** gamed to 1.0. A couple of known sources of
legitimate, non-bug variance keep it honest:

- **Multi-instance gold ambiguity.** Some entities appear in more than one table
  row across years or reports — the same manufacturer with slightly different
  addresses, or two records sharing a name. A `cell` question can therefore have
  more than one defensible "correct" value, so the pinned ground truth is a
  best-effort *any-of* list sampled from one instance. An answer that is
  genuinely correct for a *different* valid instance can still score as a miss.
  (In the representative run, the single `cell` miss is exactly this kind of
  case: the model returned a real, cited row for the named entity that did not
  match the one address pinned as gold.)

- **Ranking / aggregate non-determinism.** The `aggregate`, `crossyear`, and
  `comparison` questions route through the LLM-backed SQL and synthesis path.
  Tie-breaks, rounding, year selection, and phrasing can vary run to run, so a
  borderline ranking or comparison question may flip between `OK` and `XX` across
  runs **even with no code change**. This is why `overall_ok` should be read as a
  band (~0.90) rather than a single fixed number.

> [!TIP]
> When a run dips, use the `misses` list and the `per_question` array in
> `scripts/eval_50_report.json` to confirm whether a drop is a *real regression*
> or just run-to-run noise on one of the non-deterministic categories above. A
> harness that always reports a perfect score is usually measuring the wrong
> thing.

---

## Adding your own question sets

IntelliGraphRAG is domain-agnostic, and so is the harness. The fastest way to
validate the platform on **your** corpus is to write a question set in the same
4-tuple shape and point it at your index:

1. Copy `scripts/eval_50.py` to, e.g., `scripts/eval_mydomain.py`.
2. Replace the `Q` list with your own
   `(question, [expected any-of], kind, must_refuse)` tuples. **Re-sample the
   `cell` ground-truth values from your *actual* indexed rows** so they survive a
   re-parse — that is what makes the `cell` slice a real grounding test rather
   than a knowledge test.
3. Keep at least a few `refusal` questions (clearly out-of-corpus) so you keep
   measuring refusal accuracy, not just recall.
4. Cover every `kind` you care about so `lanes_fired` confirms the relevant lanes
   actually engage on your data.
5. Run it exactly like the built-in harness:

   ```bash
   export ATF_PROFILE=local
   export OPENROUTER_API_KEY=sk-or-...
   python scripts/eval_mydomain.py
   ```

Other bundled evaluation scripts you can use as templates:
[`scripts/eval_15_structured.py`](https://github.com/RW2523/intelligraphrag/blob/main/scripts/eval_15_structured.py)
and `scripts/eval_atf_25.py`.

> [!TIP]
> A good domain question set mixes a handful of grounded `cell` lookups (strict,
> deterministic), a few `aggregate` / `comparison` questions (SQL + numeric
> lanes), some open-ended `fact` / `relationship` / `multi` questions (vector +
> graph), and a couple of refusals. That single mix exercises essentially the
> whole pipeline in one run.

---
📖 [Docs Home](Home.md) · [Architecture](Architecture.md) · [Retrieval Lanes](Retrieval-Lanes.md) · [Configuration Reference](Configuration-Reference.md)
