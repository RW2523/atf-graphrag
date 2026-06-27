"""Amazon Bedrock Data Automation (BDA) parser — AWS-native multimodal ingestion.

BDA turns unstructured documents/images into structured insights (text, tables,
figures) as managed output. We expose it as a `parser.provider = "bda"` option so
the same ingestion pipeline can use AWS's managed extraction instead of local
Docling/pdfplumber. Returns the standard parser contract
    load(path, vision_provider=None) -> [(page_no, text-with-markdown-tables)]
so it is a drop-in swap.

BDA is asynchronous + S3-based: upload → invoke_data_automation_async → poll
get_data_automation_status → read the JSON "standard output" from S3. BDA's
document output already contains per-page Markdown WITH tables, which flows
straight into our table_data parser.

Defensive: any missing config / boto3 / error falls back to DoclingParser (then
AdvancedParser), so a profile swap never breaks ingestion. Validated here with
MOCKED clients only — needs live AWS + a BDA project to run for real.
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Tuple

from .parser import Parser, AdvancedParser


def _client(service: str, cfg: Dict):
    import boto3  # lazy
    return boto3.client(service, region_name=cfg.get("region", "us-east-1"))


class BedrockDataAutomationParser(Parser):
    name = "bda"

    def __init__(self, cfg: Optional[Dict] = None):
        self.cfg = cfg or {}
        self.region = self.cfg.get("region", "us-east-1")
        self.bucket = self.cfg.get("bucket", "")            # S3 working bucket
        self.prefix = self.cfg.get("prefix", "bda/").rstrip("/") + "/"
        self.project_arn = self.cfg.get("project_arn", "")  # BDA project (blueprint)
        self.profile_arn = self.cfg.get("profile_arn", "")  # data-automation profile
        self.poll_secs = float(self.cfg.get("poll_secs", 5))
        self.timeout_secs = float(self.cfg.get("timeout_secs", 600))
        self._fallback = None

    def _fallback_parser(self):
        if self._fallback is None:
            try:
                from .docling_parser import DoclingParser
                self._fallback = DoclingParser(self.cfg)
            except Exception:  # noqa: BLE001
                self._fallback = AdvancedParser(self.cfg)
        return self._fallback

    def load(self, path: str, vision_provider=None) -> List[Tuple[int, str]]:
        if not self.bucket:
            return self._fallback_parser().load(path, vision_provider=vision_provider)
        try:
            pages = self._run_bda(path)
        except Exception as exc:  # noqa: BLE001
            print(f"[bda] failed for {path} ({exc}); falling back")
            return self._fallback_parser().load(path, vision_provider=vision_provider)
        return pages or self._fallback_parser().load(path, vision_provider=vision_provider)

    # ── BDA pipeline ───────────────────────────────────────────────────────
    def _run_bda(self, path: str) -> List[Tuple[int, str]]:
        s3 = _client("s3", self.cfg)
        rt = _client("bedrock-data-automation-runtime", self.cfg)
        base = os.path.basename(path)
        in_key = f"{self.prefix}input/{base}"
        out_prefix = f"{self.prefix}output/{base}/"
        with open(path, "rb") as f:
            s3.put_object(Bucket=self.bucket, Key=in_key, Body=f.read())
        in_uri = f"s3://{self.bucket}/{in_key}"
        out_uri = f"s3://{self.bucket}/{out_prefix}"

        params: Dict = {
            "inputConfiguration": {"s3Uri": in_uri},
            "outputConfiguration": {"s3Uri": out_uri},
        }
        if self.project_arn:
            params["dataAutomationConfiguration"] = {
                "dataAutomationProjectArn": self.project_arn}
        if self.profile_arn:
            params["dataAutomationProfileArn"] = self.profile_arn
        resp = rt.invoke_data_automation_async(**params)
        inv_arn = resp.get("invocationArn", "")

        # poll until done
        deadline = self._now() + self.timeout_secs
        status, result_uri = "Created", ""
        while self._now() < deadline:
            st = rt.get_data_automation_status(invocationArn=inv_arn)
            status = st.get("status", st.get("Status", ""))
            if status in ("Success", "ServiceError", "ClientError"):
                result_uri = (st.get("outputConfiguration", {}) or {}).get("s3Uri", "")
                break
            self._sleep(self.poll_secs)
        if status != "Success":
            raise RuntimeError(f"BDA status={status}")

        return self._read_output(s3, result_uri or out_uri)

    def _read_output(self, s3, result_uri: str) -> List[Tuple[int, str]]:
        bucket, key = self._split_s3(result_uri)
        # The job metadata points to the standard-output JSON; if result_uri is a
        # folder, find the result json under it.
        if key.endswith("/") or not key.endswith(".json"):
            listed = s3.list_objects_v2(Bucket=bucket, Prefix=key).get("Contents", [])
            jsons = [o["Key"] for o in listed if o["Key"].endswith(".json")]
            if not jsons:
                return []
            key = sorted(jsons)[-1]
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        data = json.loads(body)
        return self._parse_standard_output(data)

    @staticmethod
    def _parse_standard_output(data: Dict) -> List[Tuple[int, str]]:
        """Extract per-page Markdown (with tables) from BDA standard output.
        Schema-tolerant across BDA versions."""
        pages: List[Tuple[int, str]] = []
        doc = data.get("document", data)
        page_items = doc.get("pages") or data.get("pages") or []
        for i, pg in enumerate(page_items, 1):
            pno = pg.get("page_index", pg.get("pageIndex", i))
            try:
                pno = int(pno) + (1 if pno == i - 1 else 0)
            except Exception:  # noqa: BLE001
                pno = i
            rep = pg.get("representation", {}) or {}
            text = (rep.get("markdown") or rep.get("text")
                    or pg.get("markdown") or pg.get("text") or "")
            if text.strip():
                pages.append((pno, text.strip()))
        # fallback: a single flat markdown field
        if not pages:
            md = (doc.get("representation", {}) or {}).get("markdown") \
                or data.get("markdown") or ""
            if md.strip():
                pages = [(1, md.strip())]
        return pages

    # ── small seams so tests don't actually sleep/clock ───────────────────
    def _now(self) -> float:
        return time.time()

    def _sleep(self, s: float) -> None:
        time.sleep(s)

    @staticmethod
    def _split_s3(uri: str) -> Tuple[str, str]:
        u = uri.replace("s3://", "")
        b, _, k = u.partition("/")
        return b, k
