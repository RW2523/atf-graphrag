"""Export the PARSED corpus to a portable JSONL — the expensive-to-produce part.

Parsing (Docling / Bedrock Data Automation / Textract) is the costly step. Once
done, this dumps every chunk (clean text + ~30 metadata fields + structured
table_data + entities/relationships) to a single portable file. You can then
import it into ANY deployment's stores (local / Qdrant / OpenSearch / Neo4j /
Neptune) and re-embed with that deployment's free embedder — so you pay to parse
ONCE and serve cheaply anywhere.

  python scripts/export_corpus.py  [out.jsonl]
"""
import json
import os
import sys


def main():
    os.environ.setdefault("ATF_PROFILE", "local")
    out = sys.argv[1] if len(sys.argv) > 1 else "corpus_export.jsonl"
    from atf_graphrag.engine import Engine
    e = Engine()
    n = 0
    with open(out, "w") as f:
        for corpus in e.corpora:
            vs = e.vstore(corpus)
            for rec in vs.all_chunks():
                d = rec.to_dict()
                d["corpus"] = corpus
                f.write(json.dumps(d, default=str) + "\n")
                n += 1
    size_mb = round(os.path.getsize(out) / 1024 / 1024, 1)
    print(f"[export] {n} chunks -> {out} ({size_mb} MB)")
    print("[export] portable: import into any deployment with import_corpus.py")
    print("[export] (re-embeds with the target's embedder; graph rebuilt from chunks)")


if __name__ == "__main__":
    sys.exit(main())
