"""AWS control plane — provision/teardown/inventory/plan (mocked boto3)."""
import types

import pytest


def _fake_clients(monkeypatch, calls):
    """Patch boto3.client so every service returns a recording fake."""
    import boto3

    class _Fake:
        def __init__(self, svc):
            self._svc = svc
            self.exceptions = types.SimpleNamespace(
                BucketAlreadyOwnedByYou=type("E", (Exception,), {}),
                ResourceInUseException=type("E", (Exception,), {}))

        def __getattr__(self, name):
            def _call(**kw):
                calls.append((self._svc, name))
                if name == "get_caller_identity":
                    return {"Account": "111122223333"}
                if name == "list_collections":
                    return {"collectionSummaries": []}
                if name == "list_graphs":
                    return {"graphs": []}
                if name == "list_guardrails":
                    return {"guardrails": []}
                if name == "get_parameters_by_path":
                    return {"Parameters": []}
                if name == "list_objects_v2":
                    return {"Contents": []}
                if name == "create_graph":
                    return {"id": "g-abc"}
                if name == "create_guardrail":
                    return {"guardrailId": "gr-abc"}
                return {}
            return _call
    monkeypatch.setattr(boto3, "client", lambda svc, **kw: _Fake(svc))


def test_plan_is_offline_and_orders_correctly():
    # plan() does not need boto3 to work for the step list
    from atf_graphrag.aws.provision import ControlPlane
    cp = ControlPlane(region="us-east-1", project="atf-test")
    prov = cp.plan("provision")
    tear = cp.plan("teardown")
    pkeys = [s["component"] for s in prov["steps"]]
    tkeys = [s["component"] for s in tear["steps"]]
    assert pkeys[0] == "s3" and pkeys[-1] == "neptune_analytics"
    assert tkeys == list(reversed(pkeys))           # teardown reverses order
    assert prov["est_cost_month"] >= 700            # neptune + opensearch dominate


def test_provision_creates_every_component(monkeypatch):
    calls = []
    _fake_clients(monkeypatch, calls)
    from atf_graphrag.aws.provision import ControlPlane
    cp = ControlPlane(project="atf-test")
    out = cp.provision()
    assert out["ok"] is True
    comps = {r["component"] for r in out["results"]}
    assert {"s3", "dynamodb", "ssm", "guardrail",
            "opensearch_serverless", "neptune_analytics"} <= comps
    # the expensive resources were actually requested
    assert ("neptune-graph", "create_graph") in calls
    assert ("opensearchserverless", "create_collection") in calls


def test_teardown_deletes_in_reverse(monkeypatch):
    calls = []
    _fake_clients(monkeypatch, calls)
    from atf_graphrag.aws.provision import ControlPlane
    cp = ControlPlane(project="atf-test")
    out = cp.teardown()
    assert out["ok"] is True
    order = [r["component"] for r in out["results"]]
    assert order[0] == "neptune_analytics" and order[-1] == "s3"


def test_inventory_reports_costs(monkeypatch):
    calls = []
    _fake_clients(monkeypatch, calls)
    from atf_graphrag.aws.provision import ControlPlane
    inv = ControlPlane(project="atf-test").inventory()
    assert "components" in inv and inv["account_id"] == "111122223333"
    # the fakes make head_bucket/describe_table succeed (s3 + dynamodb exist),
    # while list_* return empty (no oss/neptune/guardrail/ssm).
    assert isinstance(inv["running_cost_month"], int)
    live = {c["component"] for c in inv["components"] if c.get("exists")}
    assert "neptune_analytics" not in live and "opensearch_serverless" not in live


def test_teardown_only_subset(monkeypatch):
    calls = []
    _fake_clients(monkeypatch, calls)
    from atf_graphrag.aws.provision import ControlPlane
    out = ControlPlane(project="atf-test").teardown(only=["neptune_analytics"])
    assert [r["component"] for r in out["results"]] == ["neptune_analytics"]


def test_graceful_without_boto3(monkeypatch):
    # simulate boto3 absent: _account_id + status degrade, no crash
    import atf_graphrag.aws.provision as P
    monkeypatch.setattr(P, "_client", lambda *a, **k: (_ for _ in ()).throw(
        ImportError("no boto3")))
    cp = P.ControlPlane(project="atf-test")
    inv = cp.inventory()
    assert inv["account_id"] == ""          # sts failed gracefully
    for c in inv["components"]:
        assert c["exists"] in (None, False)
