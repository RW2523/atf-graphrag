"""Structured table store + SQL retrieval lane (Stage 1 of the table layer).

Every extracted table (the chunk-level `table_data` grids) is promoted into a
queryable SQLite store with FULL provenance — document, page, year, title,
source chunk — so tabular questions can be answered by SQL over ALL rows
instead of hoping semantic retrieval surfaced the right fragment. Cross-
document combination happens AT QUERY TIME (year/doc are columns to filter and
GROUP BY), which delivers "combine same-category tables" with zero risk of a
silent wrong merge.

Flow:  build(engine)        scan vector-store payloads -> tables + rows (SQLite)
       find_tables(q)       rank candidate tables by token overlap with the
                            question (title + doc + columns + sample cells)
       query(q, engine)     materialize the best candidates as temp tables,
                            ask the LLM for ONE SQLite SELECT, validate
                            (SELECT-only, parses, executes, non-empty),
                            return computed rows + provenance + the SQL.

Anything that fails at any step returns None — the caller falls back to the
normal RAG lane, so the worst case equals today's behavior. No data is ever
removed or merged; the store is an additional index over existing chunks.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional

_TOKEN = re.compile(r"[a-z0-9]{2,}")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_SELECT_ONLY = re.compile(r"^\s*select\b", re.I)
_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|attach|pragma|create)\b", re.I)
_STOP = {"the", "and", "for", "what", "how", "many", "which", "was", "were",
         "according", "report", "data", "total", "number", "per", "did", "does"}


def _toks(text: str) -> set:
    return {t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP}


class TableStore:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS tables ("
            "id INTEGER PRIMARY KEY, doc TEXT, page INTEGER, year TEXT, "
            "title TEXT, columns TEXT, n_rows INTEGER, chunk_id TEXT, "
            "search_blob TEXT)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS rows ("
            "table_id INTEGER, idx INTEGER, cells TEXT)")
        self.db.execute("CREATE INDEX IF NOT EXISTS rows_tid ON rows(table_id)")
        self.db.commit()

    # ── build ────────────────────────────────────────────────────────────────
    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM tables").fetchone()[0]

    def build(self, engine, corpora: Optional[List[str]] = None) -> Dict[str, int]:
        """(Re)build the store from every chunk carrying table_data."""
        self.db.execute("DELETE FROM tables")
        self.db.execute("DELETE FROM rows")
        n_tables = n_rows = 0
        for corpus in (corpora or engine.corpora):
            vs = engine.vstore(corpus)
            for cid, p in getattr(vs, "_payloads", {}).items():
                td = p.get("table_data")
                if not td or not isinstance(td, dict) or not td.get("rows"):
                    continue
                cols = [str(c) for c in (td.get("columns") or [])]
                rows = [r for r in td["rows"] if isinstance(r, (list, tuple))]
                if not rows:
                    continue
                doc = p.get("source_name") or p.get("document_title") or ""
                year = (p.get("document_date") or "")[:4]
                if not year:
                    m = _YEAR.search(doc)
                    year = m.group(1) if m else ""
                title = p.get("table_title") or p.get("section_heading") or ""
                sample = " ".join(str(c) for r in rows[:3] for c in r)[:400]
                blob = " ".join((title, doc, " ".join(cols), sample)).lower()
                cur = self.db.execute(
                    "INSERT INTO tables (doc,page,year,title,columns,n_rows,"
                    "chunk_id,search_blob) VALUES (?,?,?,?,?,?,?,?)",
                    (doc, p.get("page_number") or 0, year, title,
                     json.dumps(cols), len(rows), cid, blob))
                tid = cur.lastrowid
                self.db.executemany(
                    "INSERT INTO rows (table_id, idx, cells) VALUES (?,?,?)",
                    [(tid, i, json.dumps([str(c) for c in r]))
                     for i, r in enumerate(rows)])
                n_tables += 1
                n_rows += len(rows)
        self.db.commit()
        return {"tables": n_tables, "rows": n_rows}

    # ── candidate selection ──────────────────────────────────────────────────
    def find_tables(self, question: str, limit: int = 4) -> List[Dict[str, Any]]:
        qt = _toks(question)
        if not qt:
            return []
        qyear = None
        m = _YEAR.search(question)
        if m:
            qyear = m.group(1)
        scored = []
        for tid, doc, page, year, title, cols, n, cid, blob in self.db.execute(
                "SELECT id,doc,page,year,title,columns,n_rows,chunk_id,"
                "search_blob FROM tables"):
            bt = set(_TOKEN.findall(blob))
            ov = len(qt & bt)
            if not ov:
                continue
            score = ov + (2 if (qyear and qyear == year) else 0) \
                - (1 if (qyear and year and qyear != year) else 0)
            scored.append((score, {"id": tid, "doc": doc, "page": page,
                                   "year": year, "title": title,
                                   "columns": json.loads(cols), "n_rows": n,
                                   "chunk_id": cid}))
        scored.sort(key=lambda x: -x[0])
        return [t for _, t in scored[:limit]]

    def rows_for(self, table_id: int, limit: int = 100000) -> List[List[str]]:
        return [json.loads(c) for (c,) in self.db.execute(
            "SELECT cells FROM rows WHERE table_id=? ORDER BY idx LIMIT ?",
            (table_id, limit))]

    # ── SQL lane ─────────────────────────────────────────────────────────────
    def query(self, question: str, engine,
              max_result_rows: int = 30) -> Optional[Dict[str, Any]]:
        """Text-to-SQL over the best candidate tables. None on any failure."""
        llm = getattr(engine, "llm", None)
        if llm is None or getattr(llm, "name", "offline") == "offline":
            return None
        cands = self.find_tables(question)
        if not cands:
            return None
        # Materialize candidates as temp tables t1..tN with c1..cM (+meta cols).
        mem = sqlite3.connect(":memory:")
        schema_desc = []
        for i, t in enumerate(cands, 1):
            rows = self.rows_for(t["id"])
            if not rows:
                continue
            width = max(len(r) for r in rows)
            colnames = [f"c{j+1}" for j in range(width)]
            mem.execute(f"CREATE TABLE t{i} ({', '.join(c + ' TEXT' for c in colnames)}, "
                        "doc TEXT, page INTEGER, year TEXT)")
            mem.executemany(
                f"INSERT INTO t{i} VALUES ({','.join('?' * (width + 3))})",
                [list(r) + [""] * (width - len(r)) + [t["doc"], t["page"], t["year"]]
                 for r in rows])
            head = " | ".join(t["columns"][:width]) if t["columns"] else ""
            sample = "; ".join(" , ".join(r[:6]) for r in rows[:3])
            schema_desc.append(
                f"t{i} (cols c1..c{width} + doc,page,year) — doc:{t['doc'][:60]} "
                f"p{t['page']} year:{t['year']} title:{t['title'][:60]} "
                f"headers:[{head[:120]}] sample rows: {sample[:300]}")
        if not schema_desc:
            return None
        mem.commit()
        sys = ("Write ONE SQLite SELECT statement answering the question from "
               "these tables. Cell values are TEXT — CAST(REPLACE(cx,',','') AS "
               "INTEGER) for numeric comparisons/sums; exclude obvious total/"
               "subtotal rows (e.g. WHERE UPPER(c1) NOT LIKE '%TOTAL%') when "
               "aggregating. Use LIKE for name matching. Respond ONLY with SQL.")
        try:
            sql = llm.complete("QUESTION: " + question + "\nTABLES:\n" +
                               "\n".join(schema_desc), system=sys,
                               temperature=0.0, max_tokens=220)
            sql = re.sub(r"^```(?:sql)?|```$", "", (sql or "").strip(),
                         flags=re.M).strip().rstrip(";")
        except Exception:  # noqa: BLE001
            return None
        if not _SELECT_ONLY.match(sql) or _FORBIDDEN.search(sql):
            return None
        try:
            cur = mem.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(max_result_rows)
        except Exception:  # noqa: BLE001
            return None
        if not rows:
            return None
        return {"sql": sql, "result_columns": cols,
                "result_rows": [list(r) for r in rows],
                "tables": [{"doc": t["doc"], "page": t["page"],
                            "year": t["year"], "title": t["title"],
                            "chunk_id": t["chunk_id"]} for t in cands]}


_STORES: Dict[str, TableStore] = {}


def get_store(engine) -> TableStore:
    """Singleton store per storage root; built lazily on first use and rebuilt
    when the corpus table count changes."""
    root = os.path.dirname(engine.settings["vector_store"]["path"])
    path = os.path.join(root, "tables.db")
    st = _STORES.get(path)
    if st is None:
        st = TableStore(path)
        _STORES[path] = st
    n_payload_tables = sum(
        1 for c in engine.corpora
        for p in getattr(engine.vstore(c), "_payloads", {}).values()
        if p.get("table_data") and (p["table_data"].get("rows") or []))
    if st.count() != n_payload_tables:
        st.build(engine)
    return st
