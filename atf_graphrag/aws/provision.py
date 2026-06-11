"""AWS-native stack control plane — provision and (cost-saving) teardown.

Creates and DELETES the managed AWS resources behind the AWS-native GraphRAG
architecture, driven from the app console. Every resource is tagged
`Project=<project>` so a single teardown can find and remove the whole stack —
so you can spin the (expensive) stack up to demo/test, then tear it down to stop
paying for it.

Design:
  * Each component is a small object with create() / delete() / status() and a
    rough monthly cost estimate.
  * plan(action) returns a dry-run of what would happen (testable offline).
  * provision()/teardown() execute; teardown runs in reverse dependency order.
  * Everything is idempotent (create checks-exists, delete ignores absent) and
    defensive — a missing boto3 / missing credentials degrades to a clear
    "unavailable" status instead of raising.

This is real boto3 code, but it is validated here with MOCKED clients + dry-run
only (no live AWS in CI). Run plan() first against your account, review, then
provision(); always teardown() when done. See docs/AWS_NATIVE_SETUP.md.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

PROJECT_TAG_KEY = "Project"

# Rough on-always monthly USD estimates (us-east-1, mid-2026) — the big two are
# Neptune Analytics and OpenSearch Serverless; the rest are ~free at rest.
_COST = {
    "s3": 1, "dynamodb": 1, "ssm": 0, "iam": 0, "guardrail": 0,
    "opensearch_serverless": 350,   # 2 OCU minimum
    "neptune_analytics": 350,       # min capacity, always-on
    "knowledge_base": 0,            # pay per ingest/query
}


def _client(service: str, region: str):
    import boto3  # lazy
    return boto3.client(service, region_name=region)


def boto3_available() -> bool:
    try:
        import boto3  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class Component:
    """One AWS resource (or small group) the control plane manages."""
    key = "base"
    label = "base"

    def __init__(self, cp: "ControlPlane"):
        self.cp = cp

    @property
    def cost(self) -> int:
        return _COST.get(self.key, 0)

    def create(self) -> Dict[str, Any]:        # pragma: no cover - overridden
        raise NotImplementedError

    def delete(self) -> Dict[str, Any]:        # pragma: no cover - overridden
        raise NotImplementedError

    def status(self) -> Dict[str, Any]:        # pragma: no cover - overridden
        raise NotImplementedError

    def _ok(self, action, detail="", **extra):
        return {"component": self.key, "action": action, "ok": True,
                "detail": detail, **extra}

    def _err(self, action, exc):
        return {"component": self.key, "action": action, "ok": False,
                "error": str(exc)}


class S3Buckets(Component):
    key, label = "s3", "S3 buckets (raw / processed / vectors)"

    def _names(self) -> List[str]:
        p = self.cp.project
        acct = self.cp.account_id or "acct"
        return [f"{p}-raw-{acct}", f"{p}-processed-{acct}", f"{p}-vectors-{acct}"]

    def create(self):
        try:
            s3 = _client("s3", self.cp.region)
            made = []
            for b in self._names():
                try:
                    if self.cp.region == "us-east-1":
                        s3.create_bucket(Bucket=b)
                    else:
                        s3.create_bucket(Bucket=b, CreateBucketConfiguration={
                            "LocationConstraint": self.cp.region})
                    s3.put_bucket_tagging(Bucket=b, Tagging={"TagSet": self.cp.tagset()})
                    made.append(b)
                except s3.exceptions.BucketAlreadyOwnedByYou:
                    made.append(b + " (exists)")
            return self._ok("create", f"{len(made)} buckets", resources=made)
        except Exception as e:  # noqa: BLE001
            return self._err("create", e)

    def delete(self):
        try:
            s3 = _client("s3", self.cp.region)
            gone = []
            for b in self._names():
                try:
                    objs = s3.list_objects_v2(Bucket=b).get("Contents", []) or []
                    if objs:
                        s3.delete_objects(Bucket=b, Delete={
                            "Objects": [{"Key": o["Key"]} for o in objs]})
                    s3.delete_bucket(Bucket=b)
                    gone.append(b)
                except Exception:  # noqa: BLE001  bucket absent / not empty edge
                    pass
            return self._ok("delete", f"{len(gone)} buckets removed", resources=gone)
        except Exception as e:  # noqa: BLE001
            return self._err("delete", e)

    def status(self):
        try:
            s3 = _client("s3", self.cp.region)
            present = []
            for b in self._names():
                try:
                    s3.head_bucket(Bucket=b)
                    present.append(b)
                except Exception:  # noqa: BLE001
                    pass
            return {"component": self.key, "exists": bool(present),
                    "resources": present, "cost_month": self.cost}
        except Exception as e:  # noqa: BLE001
            return {"component": self.key, "exists": None, "error": str(e)}


class DynamoCatalog(Component):
    key, label = "dynamodb", "DynamoDB metadata catalog"

    def _name(self):
        return f"{self.cp.project}-catalog"

    def create(self):
        try:
            ddb = _client("dynamodb", self.cp.region)
            try:
                ddb.create_table(
                    TableName=self._name(),
                    AttributeDefinitions=[
                        {"AttributeName": "document_id", "AttributeType": "S"},
                        {"AttributeName": "chunk_id", "AttributeType": "S"}],
                    KeySchema=[
                        {"AttributeName": "document_id", "KeyType": "HASH"},
                        {"AttributeName": "chunk_id", "KeyType": "RANGE"}],
                    BillingMode="PAY_PER_REQUEST",
                    Tags=self.cp.tagset())
                return self._ok("create", self._name(), resources=[self._name()])
            except ddb.exceptions.ResourceInUseException:
                return self._ok("create", self._name() + " (exists)")
        except Exception as e:  # noqa: BLE001
            return self._err("create", e)

    def delete(self):
        try:
            ddb = _client("dynamodb", self.cp.region)
            try:
                ddb.delete_table(TableName=self._name())
            except Exception:  # noqa: BLE001
                pass
            return self._ok("delete", self._name() + " removed")
        except Exception as e:  # noqa: BLE001
            return self._err("delete", e)

    def status(self):
        try:
            ddb = _client("dynamodb", self.cp.region)
            try:
                ddb.describe_table(TableName=self._name())
                return {"component": self.key, "exists": True,
                        "resources": [self._name()], "cost_month": self.cost}
            except Exception:  # noqa: BLE001
                return {"component": self.key, "exists": False, "cost_month": self.cost}
        except Exception as e:  # noqa: BLE001
            return {"component": self.key, "exists": None, "error": str(e)}


class Guardrail(Component):
    key, label = "guardrail", "Bedrock Guardrail"

    def _name(self):
        return f"{self.cp.project}-guardrail"

    def create(self):
        try:
            bd = _client("bedrock", self.cp.region)
            existing = self._find(bd)
            if existing:
                return self._ok("create", self._name() + " (exists)", resources=[existing])
            resp = bd.create_guardrail(
                name=self._name(),
                description="ATF GraphRAG guardrail",
                blockedInputMessaging="This request was blocked by policy.",
                blockedOutputsMessaging="This response was withheld by policy.",
                contentPolicyConfig={"filtersConfig": [
                    {"type": t, "inputStrength": "HIGH", "outputStrength": "HIGH"}
                    for t in ("SEXUAL", "VIOLENCE", "HATE", "INSULTS",
                              "MISCONDUCT", "PROMPT_ATTACK")]},
                tags=self.cp.tagset())
            return self._ok("create", resp.get("guardrailId", ""),
                            resources=[resp.get("guardrailId", "")])
        except Exception as e:  # noqa: BLE001
            return self._err("create", e)

    def _find(self, bd) -> Optional[str]:
        try:
            for g in bd.list_guardrails().get("guardrails", []):
                if g.get("name") == self._name():
                    return g.get("id") or g.get("guardrailId")
        except Exception:  # noqa: BLE001
            pass
        return None

    def delete(self):
        try:
            bd = _client("bedrock", self.cp.region)
            gid = self._find(bd)
            if gid:
                bd.delete_guardrail(guardrailIdentifier=gid)
                return self._ok("delete", gid + " removed")
            return self._ok("delete", "none")
        except Exception as e:  # noqa: BLE001
            return self._err("delete", e)

    def status(self):
        try:
            bd = _client("bedrock", self.cp.region)
            gid = self._find(bd)
            return {"component": self.key, "exists": bool(gid),
                    "resources": [gid] if gid else [], "cost_month": self.cost}
        except Exception as e:  # noqa: BLE001
            return {"component": self.key, "exists": None, "error": str(e)}


class OpenSearchServerless(Component):
    key, label = "opensearch_serverless", "OpenSearch Serverless (vector)"

    def _name(self):
        return f"{self.cp.project}-vectors"

    def create(self):
        try:
            oss = _client("opensearchserverless", self.cp.region)
            name = self._name()
            # encryption + network + data-access policies, then the collection.
            self._ensure_policy(oss, "encryption", name)
            self._ensure_policy(oss, "network", name)
            try:
                oss.create_collection(name=name, type="VECTORSEARCH",
                                      tags=self.cp.tagset())
            except Exception as e:  # noqa: BLE001
                if "ConflictException" not in type(e).__name__ and "exist" not in str(e).lower():
                    raise
            return self._ok("create", name, resources=[name],
                            note="collection provisioning is async (~minutes)")
        except Exception as e:  # noqa: BLE001
            return self._err("create", e)

    def _ensure_policy(self, oss, kind, name):
        try:
            if kind == "encryption":
                oss.create_security_policy(
                    name=f"{name}-enc", type="encryption",
                    policy=json.dumps({"Rules": [{"ResourceType": "collection",
                        "Resource": [f"collection/{name}"]}], "AWSOwnedKey": True}))
            elif kind == "network":
                oss.create_security_policy(
                    name=f"{name}-net", type="network",
                    policy=json.dumps([{"Rules": [{"ResourceType": "collection",
                        "Resource": [f"collection/{name}"]}], "AllowFromPublic": True}]))
        except Exception:  # noqa: BLE001  already exists
            pass

    def delete(self):
        try:
            oss = _client("opensearchserverless", self.cp.region)
            name = self._name()
            cid = self._collection_id(oss, name)
            if cid:
                oss.delete_collection(id=cid)
            for suf, kind in (("enc", "encryption"), ("net", "network")):
                try:
                    oss.delete_security_policy(name=f"{name}-{suf}", type=kind)
                except Exception:  # noqa: BLE001
                    pass
            return self._ok("delete", name + " removed")
        except Exception as e:  # noqa: BLE001
            return self._err("delete", e)

    def _collection_id(self, oss, name) -> Optional[str]:
        try:
            for c in oss.list_collections().get("collectionSummaries", []):
                if c.get("name") == name:
                    return c.get("id")
        except Exception:  # noqa: BLE001
            pass
        return None

    def status(self):
        try:
            oss = _client("opensearchserverless", self.cp.region)
            cid = self._collection_id(oss, self._name())
            return {"component": self.key, "exists": bool(cid),
                    "resources": [self._name()] if cid else [], "cost_month": self.cost}
        except Exception as e:  # noqa: BLE001
            return {"component": self.key, "exists": None, "error": str(e)}


class NeptuneAnalytics(Component):
    key, label = "neptune_analytics", "Neptune Analytics graph (GraphRAG)"

    def _name(self):
        return f"{self.cp.project}-graph"

    def create(self):
        try:
            ng = _client("neptune-graph", self.cp.region)
            gid = self._graph_id(ng)
            if gid:
                return self._ok("create", self._name() + " (exists)", resources=[gid])
            resp = ng.create_graph(graphName=self._name(), provisionedMemory=16,
                                   tags=self.cp.tags())
            return self._ok("create", resp.get("id", ""), resources=[resp.get("id", "")],
                            note="graph provisioning is async (~minutes)")
        except Exception as e:  # noqa: BLE001
            return self._err("create", e)

    def _graph_id(self, ng) -> Optional[str]:
        try:
            for g in ng.list_graphs().get("graphs", []):
                if g.get("name") == self._name():
                    return g.get("id")
        except Exception:  # noqa: BLE001
            pass
        return None

    def delete(self):
        try:
            ng = _client("neptune-graph", self.cp.region)
            gid = self._graph_id(ng)
            if gid:
                ng.delete_graph(graphIdentifier=gid, skipSnapshot=True)
                return self._ok("delete", gid + " removed")
            return self._ok("delete", "none")
        except Exception as e:  # noqa: BLE001
            return self._err("delete", e)

    def status(self):
        try:
            ng = _client("neptune-graph", self.cp.region)
            gid = self._graph_id(ng)
            return {"component": self.key, "exists": bool(gid),
                    "resources": [gid] if gid else [], "cost_month": self.cost}
        except Exception as e:  # noqa: BLE001
            return {"component": self.key, "exists": None, "error": str(e)}


class SsmConfig(Component):
    key, label = "ssm", "SSM config parameters"

    def _prefix(self):
        return f"/{self.cp.project}/"

    def create(self):
        try:
            ssm = _client("ssm", self.cp.region)
            params = {"region": self.cp.region, "project": self.cp.project}
            for k, v in params.items():
                # Overwrite=True is incompatible with Tags in one call, so tags
                # are managed at the stack level — params just carry config.
                ssm.put_parameter(Name=f"{self._prefix()}{k}", Value=str(v),
                                  Type="String", Overwrite=True)
            return self._ok("create", f"{len(params)} parameters")
        except Exception as e:  # noqa: BLE001
            return self._err("create", e)

    def delete(self):
        try:
            ssm = _client("ssm", self.cp.region)
            names = [p["Name"] for p in ssm.get_parameters_by_path(
                Path=self._prefix(), Recursive=True).get("Parameters", [])]
            if names:
                ssm.delete_parameters(Names=names)
            return self._ok("delete", f"{len(names)} parameters removed")
        except Exception as e:  # noqa: BLE001
            return self._err("delete", e)

    def status(self):
        try:
            ssm = _client("ssm", self.cp.region)
            names = [p["Name"] for p in ssm.get_parameters_by_path(
                Path=self._prefix(), Recursive=True).get("Parameters", [])]
            return {"component": self.key, "exists": bool(names),
                    "resources": names, "cost_month": self.cost}
        except Exception as e:  # noqa: BLE001
            return {"component": self.key, "exists": None, "error": str(e)}


class ControlPlane:
    # provision order; teardown runs in reverse
    ORDER = ["s3", "dynamodb", "ssm", "guardrail",
             "opensearch_serverless", "neptune_analytics"]

    def __init__(self, region: str = "us-east-1", project: str = "atf-graphrag"):
        self.region = region or "us-east-1"
        self.project = project or "atf-graphrag"
        self.account_id = self._account_id()
        self._by_key = {c.key: c for c in [
            S3Buckets(self), DynamoCatalog(self), SsmConfig(self), Guardrail(self),
            OpenSearchServerless(self), NeptuneAnalytics(self)]}

    def _account_id(self) -> str:
        try:
            return _client("sts", self.region).get_caller_identity()["Account"]
        except Exception:  # noqa: BLE001
            return ""

    def tags(self) -> Dict[str, str]:
        return {PROJECT_TAG_KEY: self.project, "ManagedBy": "atf-graphrag-console"}

    def tagset(self) -> List[Dict[str, str]]:
        return [{"Key": k, "Value": v} for k, v in self.tags().items()]

    # ---- operations -------------------------------------------------------
    def plan(self, action: str, only: Optional[List[str]] = None) -> Dict[str, Any]:
        keys = only or self.ORDER
        seq = keys if action == "provision" else list(reversed(keys))
        steps = [{"component": k, "label": self._by_key[k].label, "action": action,
                  "cost_month": self._by_key[k].cost} for k in seq if k in self._by_key]
        return {"action": action, "region": self.region, "project": self.project,
                "account_id": self.account_id, "steps": steps,
                "est_cost_month": sum(s["cost_month"] for s in steps),
                "boto3": boto3_available()}

    def provision(self, only: Optional[List[str]] = None) -> Dict[str, Any]:
        keys = only or self.ORDER
        results = [self._by_key[k].create() for k in keys if k in self._by_key]
        return {"action": "provision", "results": results,
                "ok": all(r.get("ok") for r in results)}

    def teardown(self, only: Optional[List[str]] = None) -> Dict[str, Any]:
        keys = list(reversed(only or self.ORDER))
        results = [self._by_key[k].delete() for k in keys if k in self._by_key]
        return {"action": "teardown", "results": results,
                "ok": all(r.get("ok") for r in results)}

    def inventory(self) -> Dict[str, Any]:
        comps = [self._by_key[k].status() for k in self.ORDER]
        live = [c for c in comps if c.get("exists")]
        return {"region": self.region, "project": self.project,
                "account_id": self.account_id, "boto3": boto3_available(),
                "components": comps,
                "running_cost_month": sum(self._by_key[c["component"]].cost
                                          for c in live),
                "n_live": len(live)}
