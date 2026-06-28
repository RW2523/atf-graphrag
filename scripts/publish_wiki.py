"""Publish docs/ into the GitHub Wiki tab (and keep it in sync).

The GitHub Wiki is a separate git repo (`<repo>.wiki.git`) of flat Markdown
pages linked by page-name. This script regenerates that wiki from the canonical
docs/ — adapting links to wiki format — and pushes it, so the Wiki tab always
mirrors docs/wiki/ + the user manual.

ONE-TIME PREREQUISITE: the wiki git repo only exists after the first page is
created in the browser. If you see "Repository not found", open
  https://github.com/<owner>/<repo>/wiki  ->  "Create the first page" -> Save,
then re-run this script. After that it works unattended.

Usage:  python scripts/publish_wiki.py
"""
import os
import re
import shutil
import subprocess
import tempfile
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC_WIKI = REPO / "docs" / "wiki"
SRC_MANUAL = REPO / "docs" / "USER_MANUAL.md"


def _origin() -> str:
    url = subprocess.check_output(
        ["git", "-C", str(REPO), "remote", "get-url", "origin"], text=True).strip()
    if url.endswith(".git"):
        url = url[:-4]
    return url


def _transform(text: str, repo_url: str) -> str:
    text = re.sub(r'\]\(\.\./USER_MANUAL\.md\)', '](User-Manual)', text)
    text = re.sub(r'\]\(USER_MANUAL\.md\)', '](User-Manual)', text)
    text = re.sub(r'\]\(wiki/Home\.md\)', '](Home)', text)
    text = re.sub(r'\]\(wiki/([A-Za-z0-9_-]+)\.md\)', r'](\1)', text)
    text = re.sub(r'\]\((?:\.\./)*README\.md\)', '](Home)', text)
    text = re.sub(r'\]\((intelligraphrag/[^)]+|scripts/[^)]+|[A-Za-z_]+\.py)\)',
                  rf']({repo_url}/blob/main/\1)', text)
    text = re.sub(r'\]\(([A-Za-z0-9_-]+)\.md\)', r'](\1)', text)
    return text


def _build(dst: pathlib.Path, repo_url: str) -> int:
    for f in sorted(SRC_WIKI.glob("*.md")):
        (dst / f.name).write_text(_transform(f.read_text(), repo_url))
    if SRC_MANUAL.exists():
        (dst / "User-Manual.md").write_text(_transform(SRC_MANUAL.read_text(), repo_url))
    order = ["Installation-and-Quickstart", "Architecture", "Configuration-Reference",
             "Ingestion-and-Parsing", "Retrieval-Lanes", "Knowledge-Graph",
             "Tables-and-SQL", "Web-Crawling", "API-Reference", "CLI-and-Scripts",
             "Deployment-and-AWS", "Evaluation", "Troubleshooting-and-FAQ", "Glossary"]
    sb = ["**IntelliGraphRAG**", "", "- [Home](Home)", "- [User Manual](User-Manual)",
          "", "**Reference**"]
    for p in order:
        if (dst / f"{p}.md").exists():
            sb.append(f"- [{p.replace('-', ' ')}]({p})")
    (dst / "_Sidebar.md").write_text("\n".join(sb) + "\n")
    return len(list(dst.glob("*.md")))


def main() -> int:
    repo_url = _origin()
    wiki_remote = repo_url + ".wiki.git"
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="igr_wiki_"))
    n = _build(tmp, repo_url)
    print(f"[publish_wiki] built {n} pages -> pushing to {wiki_remote}")
    run = lambda *a: subprocess.run(["git", "-C", str(tmp), *a], check=True)
    try:
        run("init", "-q")
        run("add", "-A")
        run("-c", "user.name=IntelliGraphRAG docs",
            "-c", "user.email=info@ajace.com", "commit", "-q",
            "-m", "Publish IntelliGraphRAG wiki from docs/")
        run("branch", "-M", "master")
        run("remote", "add", "origin", wiki_remote)
        res = subprocess.run(["git", "-C", str(tmp), "push", "-f", "-u", "origin", "master"],
                             capture_output=True, text=True)
        if res.returncode != 0:
            print(res.stderr.strip())
            if "not found" in res.stderr.lower():
                print("\n[publish_wiki] The wiki repo isn't initialized yet. Open\n"
                      f"  {repo_url}/wiki  ->  Create the first page -> Save,\n"
                      "  then re-run:  python scripts/publish_wiki.py")
            return 1
        print(f"[publish_wiki] done -> {repo_url}/wiki")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
