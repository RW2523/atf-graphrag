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
        # Stage 2/3: category consolidation + enrichment.
        for ddl in ("ALTER TABLE tables ADD COLUMN category TEXT",
                    "ALTER TABLE tables ADD COLUMN cat_conf REAL"):
            try:
                self.db.execute(ddl)
            except Exception:  # noqa: BLE001  column already exists
                pass
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS categories ("
            "category TEXT PRIMARY KEY, n_tables INTEGER, years TEXT, "
            "confidence REAL, name TEXT, reason TEXT, summary TEXT)")
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
        cons = self.consolidate()          # Stage 2 runs on every (re)build
        return {"tables": n_tables, "rows": n_rows, **cons}

    # ── Stage 2: confidence-gated category consolidation ─────────────────────
    @staticmethod
    def _signature(title: str, doc: str, columns: List[str], width: int) -> set:
        """Tokens describing what KIND of table this is (year/numbers removed,
        so the 2025 and 2026 editions of the same table share a signature)."""
        base = " ".join([title or "", os.path.basename(doc or "")] +
                        [c for c in columns if not c.startswith("col")])
        toks = {t for t in _TOKEN.findall(base.lower())
                if t not in _STOP and not t.isdigit() and not _YEAR.match(t)}
        toks.add(f"w{width}")          # same family ⇒ same column count
        return toks

    def consolidate(self) -> Dict[str, Any]:
        """Group same-category tables across documents/years. CONFIDENCE-GATED:
        a table joins a category only when its signature overlaps the category
        seed >= 0.55 (Jaccard) AND the column count matches — below that it
        stays standalone. No rows are ever physically merged; the category is a
        label that lets retrieval pull ALL years of a family and lets SQL
        GROUP BY year across them."""
        rows = self.db.execute(
            "SELECT id,doc,title,columns,n_rows,year FROM tables").fetchall()
        cats: List[Dict[str, Any]] = []    # {key, sig, members, years, confs}
        for tid, doc, title, cols_json, n, year in rows:
            cols = json.loads(cols_json)
            width = len(cols) if cols else 0
            sig = self._signature(title, doc, cols, width)
            best, best_j = None, 0.0
            for c in cats:
                if f"w{width}" not in c["sig"]:
                    continue
                j = len(sig & c["sig"]) / max(len(sig | c["sig"]), 1)
                if j > best_j:
                    best, best_j = c, j
            if best is not None and best_j >= 0.55:
                best["members"].append(tid)
                best["years"].add(year or "")
                best["confs"].append(best_j)
                best["sig"] |= sig
            else:
                key = "cat_" + "_".join(sorted(sig - {f'w{width}'}))[:60] \
                    + f"_w{width}"
                cats.append({"key": key, "sig": set(sig), "members": [tid],
                             "years": {year or ""}, "confs": [1.0]})
        merged = skipped = 0
        self.db.execute("DELETE FROM categories")
        for c in cats:
            conf = round(sum(c["confs"]) / len(c["confs"]), 3)
            if len(c["members"]) >= 2:
                merged += 1
            else:
                skipped += 1
            for tid in c["members"]:
                self.db.execute(
                    "UPDATE tables SET category=?, cat_conf=? WHERE id=?",
                    (c["key"], conf, tid))
            self.db.execute(
                "INSERT OR REPLACE INTO categories (category,n_tables,years,"
                "confidence) VALUES (?,?,?,?)",
                (c["key"], len(c["members"]),
                 ",".join(sorted(y for y in c["years"] if y)), conf))
        self.db.commit()
        return {"categories": len(cats), "multi_table": merged,
                "standalone": skipped}

    # ── Stage 3: meta + reason + summary per category ─────────────────────────
    def summarize_categories(self, engine, top: int = 40) -> int:
        """LLM name/purpose/summary for the biggest categories (skips ones
        already summarized; offline → no-op). Summaries feed the SQL prompt
        and the /api/tables/categories inspection endpoint."""
        llm = getattr(engine, "llm", None)
        if llm is None or getattr(llm, "name", "offline") == "offline":
            return 0
        done = 0
        for cat, n, years, conf in self.db.execute(
                "SELECT category,n_tables,years,confidence FROM categories "
                "WHERE name IS NULL ORDER BY n_tables DESC LIMIT ?", (top,)):
            t = self.db.execute(
                "SELECT id,doc,title,columns FROM tables WHERE category=? "
                "LIMIT 1", (cat,)).fetchone()
            if not t:
                continue
            sample = self.rows_for(t[0], limit=3)
            try:
                out = llm.complete(
                    f"Table family: title={t[2]!r} doc={t[1]!r} "
                    f"columns={t[3]} years=[{years}] sample={sample}",
                    system=('Describe this table family for a data catalog. '
                            'Respond ONLY JSON: {"name": "<short name>", '
                            '"reason": "<what it is used for, <=15 words>", '
                            '"summary": "<what the data contains, <=30 words>"}'),
                    temperature=0.0, max_tokens=140)
                m = re.search(r"\{.*\}", out or "", re.S)
                j = json.loads(m.group(0)) if m else {}
            except Exception:  # noqa: BLE001
                continue
            self.db.execute(
                "UPDATE categories SET name=?, reason=?, summary=? WHERE category=?",
                (j.get("name", "")[:80], j.get("reason", "")[:160],
                 j.get("summary", "")[:300], cat))
            done += 1
        self.db.commit()
        return done

    def categories(self) -> List[Dict[str, Any]]:
        return [{"category": c, "n_tables": n, "years": y, "confidence": f,
                 "name": nm or "", "reason": r or "", "summary": s or ""}
                for c, n, y, f, nm, r, s in self.db.execute(
                    "SELECT category,n_tables,years,confidence,name,reason,"
                    "summary FROM categories ORDER BY n_tables DESC")]

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
        out = [t for _, t in scored[:limit]]
        # Stage-2 expansion: when the best match belongs to a multi-year
        # category, pull its siblings from OTHER years so cross-year questions
        # ("compare 2025 vs 2026") see every edition of the family.
        if out:
            row = self.db.execute("SELECT category FROM tables WHERE id=?",
                                  (out[0]["id"],)).fetchone()
            cat = row[0] if row else None
            if cat:
                have = {t["id"] for t in out}
                years_have = {t["year"] for t in out}
                for (tid, doc, page, year, title, cols, n, cid) in self.db.execute(
                        "SELECT id,doc,page,year,title,columns,n_rows,chunk_id "
                        "FROM tables WHERE category=? AND id NOT IN "
                        f"({','.join('?' * len(have))}) ORDER BY n_rows DESC "
                        "LIMIT 6", (cat, *have)):
                    if year and year not in years_have:
                        years_have.add(year)
                        out.append({"id": tid, "doc": doc, "page": page,
                                    "year": year, "title": title,
                                    "columns": json.loads(cols), "n_rows": n,
                                    "chunk_id": cid})
                    if len(out) >= limit + 3:
                        break
        return out

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
        # Lazy Stage-3 catalog fill: if a candidate's category has no catalog
        # entry yet, summarize the biggest uncataloged families now (<=3 LLM
        # calls, idempotent) so the SQL prompt gets the catalog context.
        try:
            ids = ",".join("?" * len(cands))
            missing = self.db.execute(
                f"SELECT COUNT(*) FROM tables t LEFT JOIN categories c ON "
                f"t.category=c.category WHERE t.id IN ({ids}) AND "
                "(c.name IS NULL OR c.name='')",
                [t["id"] for t in cands]).fetchone()[0]
            if missing:
                self.summarize_categories(engine, top=3)
        except Exception:  # noqa: BLE001
            pass
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
            # Stage-3 enrichment: the category's catalog summary helps the LLM
            # pick the right table and columns.
            csum = ""
            crow = self.db.execute(
                "SELECT c.name, c.summary FROM tables t JOIN categories c "
                "ON t.category=c.category WHERE t.id=?", (t["id"],)).fetchone()
            if crow and (crow[0] or crow[1]):
                csum = f" catalog:[{(crow[0] or '')} — {(crow[1] or '')[:120]}]"
            schema_desc.append(
                f"t{i} (cols c1..c{width} + doc,page,year) — doc:{t['doc'][:60]} "
                f"p{t['page']} year:{t['year']} title:{t['title'][:60]} "
                f"headers:[{head[:120]}]{csum} sample rows: {sample[:300]}")
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
