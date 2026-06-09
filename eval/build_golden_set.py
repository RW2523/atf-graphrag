"""Reproducibly (re)generate eval/golden_set.jsonl.

The golden set is DOC-LEVEL: relevance is expressed as the source filenames that
contain the answer. Chunk ids are random uuids regenerated on every reindex, so
pinning them is fragile — document_id = md5(filename)[:12] is deterministic and
stable, which makes recall@k / NDCG / MRR reproducible across reindexing.

Each record:
  id                    : stable question id
  question              : the natural-language query
  intent                : fact|table|relationship|pattern|timeline|visual|multi
  corpus                : which corpus the answer lives in (pdf|web|...)
  relevant_doc_files    : source filenames containing the answer (source of truth)
  relevant_chunk_ids    : optional explicit chunk ids (usually [])
  expected_answer_points: key facts a faithful answer should include
  expected_refusal      : true when the answer is NOT in the corpus (the system
                          should correctly say so); excluded from retrieval metrics

Run: python -m eval.build_golden_set
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "golden_set.jsonl"

# Seeded from the 20-question evaluation, mapped to the documents that actually
# contain each answer within the indexed corpus (data/selected_30.json).
# Grow this list to 50+ over time, spanning every intent.
GOLDEN = [
    # ── AI / ML papers ──────────────────────────────────────────────────────
    {"id": "q01", "intent": "fact", "corpus": "pdf",
     "question": "What does the attention mechanism do in the Transformer architecture?",
     "relevant_doc_files": ["arxiv_attention_2017.pdf"],
     "expected_answer_points": ["relates positions in a sequence", "self-attention", "weights tokens"]},
    {"id": "q02", "intent": "fact", "corpus": "pdf",
     "question": "How many parameters does GPT-3 have, and how does that compare to earlier language models?",
     "relevant_doc_files": ["arxiv_gpt3_2020.pdf"],
     "expected_answer_points": ["175 billion parameters", "larger than prior models"]},
    {"id": "q03", "intent": "fact", "corpus": "pdf",
     "question": "What training approach does RLHF use and what problem does it solve?",
     "relevant_doc_files": ["arxiv_llm_survey_2023.pdf"],
     "expected_answer_points": ["reinforcement learning from human feedback", "aligns model to human preferences"]},
    {"id": "q04", "intent": "fact", "corpus": "pdf",
     "question": "Which neural scaling laws govern how model performance improves with compute and data?",
     "relevant_doc_files": ["arxiv_gpt3_2020.pdf", "arxiv_llm_survey_2023.pdf"],
     "expected_answer_points": ["performance scales with compute/data/params", "power-law"]},
    {"id": "q05", "intent": "fact", "corpus": "pdf",
     "question": "What datasets were used to train BERT, and what tasks does it excel at?",
     "relevant_doc_files": ["arxiv_bert_2018.pdf"],
     "expected_answer_points": ["BooksCorpus", "Wikipedia", "GLUE / QA tasks"]},
    {"id": "q06", "intent": "fact", "corpus": "pdf",
     "question": "What is the core idea behind diffusion models for image generation?",
     "relevant_doc_files": ["arxiv_diffusion_2020.pdf"],
     "expected_answer_points": ["gradually denoise", "reverse a noising process"]},
    {"id": "q07", "intent": "fact", "corpus": "pdf",
     "question": "How do RAG (Retrieval-Augmented Generation) systems work and what problem do they address?",
     "relevant_doc_files": ["arxiv_rag_survey_2023.pdf"],
     "expected_answer_points": ["retrieve external knowledge", "ground LLM outputs", "reduce hallucination"]},
    {"id": "q08", "intent": "fact", "corpus": "pdf",
     "question": "What is the CLIP model and how does it connect vision and language?",
     "relevant_doc_files": [], "expected_refusal": True,
     "expected_answer_points": ["not in corpus"]},
    {"id": "q09", "intent": "fact", "corpus": "pdf",
     "question": "What benchmarks did LLaMA achieve, and how does it compare to GPT models?",
     "relevant_doc_files": [], "expected_refusal": True,
     "expected_answer_points": ["not in corpus"]},
    {"id": "q10", "intent": "fact", "corpus": "pdf",
     "question": "What is chain-of-thought prompting and why does it improve reasoning?",
     "relevant_doc_files": ["arxiv_llm_survey_2023.pdf", "arxiv_flan_2021.pdf"],
     "expected_answer_points": ["intermediate reasoning steps", "improves complex reasoning"]},

    # ── ATF / firearms (table, trend) ───────────────────────────────────────
    {"id": "q11", "intent": "table", "corpus": "pdf",
     "question": "How many total firearms were manufactured in the United States in 2022 and 2024?",
     "relevant_doc_files": ["afmer_2022.pdf", "nfcta_manufacturing.pdf", "firearms_commerce_2024.pdf"],
     "expected_answer_points": ["6,183,507 in 2022", "2024 not in context"]},
    {"id": "q12", "intent": "table", "corpus": "pdf",
     "question": "What were the top 5 source states for crime guns traced in Texas in 2023?",
     "relevant_doc_files": ["trace_tx_2023.pdf"],
     "expected_answer_points": ["Texas", "Louisiana", "Oklahoma", "Mississippi", "Florida"]},
    {"id": "q13", "intent": "timeline", "corpus": "pdf",
     "question": "How did the number of privately made firearms (ghost guns) change over recent years?",
     "relevant_doc_files": ["nfcta_manufacturing.pdf", "firearms_commerce_2024.pdf"],
     "expected_answer_points": ["increased", "recovered at crime scenes"]},
    {"id": "q14", "intent": "table", "corpus": "pdf",
     "question": "How many explosive incidents occurred in 2022 versus 2024?",
     "relevant_doc_files": ["explosives_2022.pdf"],
     "expected_answer_points": ["2022 incident count", "2024 not in context"]},
    {"id": "q15", "intent": "table", "corpus": "pdf",
     "question": "What are the most common firearm types recovered and traced in California?",
     "relevant_doc_files": ["trace_ca_2023.pdf"],
     "expected_answer_points": ["Pistols", "Rifles", "Revolvers"]},

    # ── Cross-domain / policy ───────────────────────────────────────────────
    {"id": "q16", "intent": "fact", "corpus": "pdf",
     "question": "What risks does the NIST AI Risk Management Framework identify for AI systems?",
     "relevant_doc_files": ["nist_ai_framework_2023.pdf"],
     "expected_answer_points": ["civil liberties", "bias", "security", "privacy"]},
    {"id": "q17", "intent": "fact", "corpus": "pdf",
     "question": "How does the FLAN paper improve language model instruction-following?",
     "relevant_doc_files": ["arxiv_flan_2021.pdf"],
     "expected_answer_points": ["instruction tuning", "fine-tune on instructions"]},

    # ── Statistical / table extraction ──────────────────────────────────────
    {"id": "q18", "intent": "table", "corpus": "pdf",
     "question": "What were the leading causes of death in the United States in 2022?",
     "relevant_doc_files": ["cdc_mortality_2022.pdf"],
     "expected_answer_points": ["heart disease", "cancer", "accidents"]},
    {"id": "q19", "intent": "table", "corpus": "pdf",
     "question": "What are the main firearm calibers reported in multiple sale transactions?",
     "relevant_doc_files": ["nfcta_selling.pdf"],
     "expected_answer_points": ["9mm", ".223", "calibers"]},

    # ── Conceptual / multi-doc ──────────────────────────────────────────────
    {"id": "q20", "intent": "multi", "corpus": "pdf",
     "question": "What common themes appear across recent research on large language models?",
     "relevant_doc_files": ["arxiv_llm_survey_2023.pdf", "arxiv_gpt3_2020.pdf", "arxiv_rag_survey_2023.pdf"],
     "expected_answer_points": ["scaling", "instruction tuning", "retrieval augmentation"]},
]


def main() -> None:
    with OUT.open("w") as f:
        for rec in GOLDEN:
            rec.setdefault("relevant_chunk_ids", [])
            rec.setdefault("expected_refusal", False)
            f.write(json.dumps(rec) + "\n")
    print(f"wrote {len(GOLDEN)} golden records -> {OUT}")


if __name__ == "__main__":
    main()
