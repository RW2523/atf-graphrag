# AWS-Native GraphRAG — Setup & Teardown (first → last)

This guide takes you from a clean AWS account to a working AWS-native GraphRAG
stack driven entirely from the app console, and — importantly — back down to
**zero running cost** with a single teardown. Read it once before you start.

> **Cost reality (read first).** Two resources dominate the bill and run 24/7
> while they exist: **Neptune Analytics (~$350/mo)** and **OpenSearch Serverless
> (~$700/mo for the 2-OCU minimum, search+index)**. Everything else (S3,
> DynamoDB, SSM, Guardrail, IAM) is ~free at rest. **Always tear down when you
> stop testing.** The console's "Delete ALL AWS resources" button exists for
> exactly this.

> **Validation boundary (honest note).** The provisioning/teardown code is real
> boto3, validated here with **mocked clients + dry-run plans only** — not against
> live AWS. The first time you run it against your account, use **Plan**, then
> provision one component at a time, and verify in the AWS console. Treat the
> first live run as a supervised dry-run.

---

## 0. What gets created

| Component | Service | Purpose | ~Cost while live |
|---|---|---|---|
| `atf-graphrag-raw/processed/vectors-<acct>` | S3 | landing + processed + vector data | ~$1/mo |
| `atf-graphrag-catalog` | DynamoDB | extended metadata catalog | ~$1/mo (on-demand) |
| `/atf-graphrag/*` | SSM Parameter Store | config | $0 |
| `atf-graphrag-guardrail` | Bedrock Guardrails | content safety | $0 at rest |
| `atf-graphrag-vectors` | OpenSearch Serverless | hybrid vector index | **~$700/mo** |
| `atf-graphrag-graph` | Neptune Analytics | GraphRAG knowledge graph | **~$350/mo** |

All tagged `Project=atf-graphrag` so teardown finds the whole stack.

> Bedrock **Knowledge Base**, **Data Automation**, **AgentCore**, **Step
> Functions**, and the **Rerank API** are part of the target architecture
> (`aws_native_graphrag_platform_architecture`). The KB ties the above together;
> it is created *after* the vector store + graph + IAM role exist. This release
> ships the resource control plane (storage, graph, vector, guardrail) and the
> connect/validate/apply flow; the KB/Step-Functions wiring is the documented
> next phase (Section 7).

---

## 1. Prerequisites

1. An AWS account with access to **Amazon Bedrock**, **Neptune Analytics**, and
   **OpenSearch Serverless** in your region (default `us-east-1`).
2. **Enable Bedrock model access** (console → Bedrock → Model access): Claude 3.5
   Sonnet (generation/vision), Titan Embeddings v2 (or Cohere), Cohere Rerank.
3. An IAM principal (user/role) whose keys you'll paste into the console, with
   permissions to create the resources above. A broad managed-policy starting
   point: `AdministratorAccess` for a sandbox; for least-privilege see Section 6.
4. `pip install boto3` in the app's environment (the only extra dependency).

## 2. Open the console

```bash
export OPENROUTER_API_KEY=...        # only for the local engine; not AWS
python -m atf_graphrag serve         # http://127.0.0.1:8077
```

Go to the **AWS Native** tab. It has four numbered cards:
1. Credentials → 2. Component config → 3. **Provision / tear down** → 4. Validate & activate.

## 3. Enter credentials (card 1)

Paste **Region**, **Access key ID**, **Secret access key** (and session token if
using temporary creds). Click **Save credentials**. The keys go into the process
environment (the standard boto3 chain) and are **never written to disk**.

## 4. Provision the stack (card 3) — in order

1. **Plan provision** — shows every resource, the action, and the estimated
   monthly cost. No changes are made. Confirm the account id + region are right.
2. **Provision all** — creates the stack. Neptune Analytics and OpenSearch
   Serverless are **asynchronous** (a few minutes to become ACTIVE); the call
   returns immediately after requesting them.
3. **Inventory & cost** — re-scan: shows which components are live and the
   running monthly cost. Wait until Neptune + OpenSearch show **live** before
   activating.

> Prefer one-at-a-time on your first run: use the API with `only` to provision a
> single component, e.g. `POST /api/aws/provision {"only":["s3"]}`, verify in the
> console, then proceed.

## 5. Validate & activate (card 4)

1. **Validate connectivity** — probes each component (STS, Bedrock, embeddings,
   vision, OCR, vector store, graph store, S3).
2. **Apply & switch engine** — repoints the running engine onto the AWS backends
   (Bedrock LLM/embeddings/vision, OpenSearch/Neptune, Textract) with no code
   change — the provider-factory pattern makes this config-only.
3. **Run end-to-end smoke test** — ingests a tiny document and answers a question
   through the AWS pipeline to prove ingest → index → retrieve → generate works.
4. **Revert to local** — switches back to the local engine any time.

## 6. Tear down (stop paying) — card 3

When you're done: **Delete ALL AWS resources** (two confirmations). It deletes
every `Project=atf-graphrag` resource in **reverse** dependency order (Neptune →
OpenSearch → Guardrail → SSM → DynamoDB → S3, emptying buckets first). Re-run
**Inventory** to confirm `0 live · running cost ~$0/month`.

This is the cost-saving workflow: **provision → demo/test → teardown**, repeat.
Nothing is left running between sessions.

### Least-privilege IAM (for production)
Grant only: `s3:*` on the project buckets, `dynamodb:*` on the catalog table,
`ssm:*Parameter*` on `/atf-graphrag/*`, `aoss:*` on the collection,
`neptune-graph:*` on the graph, `bedrock:*Guardrail*`, `bedrock:InvokeModel*`,
`sts:GetCallerIdentity`, and `tag:GetResources`. Scope by the `Project` tag with
a condition key where the service supports it.

## 7. The Knowledge Base / AgentCore phase (next)

To complete the managed GraphRAG core per the architecture doc:
1. Create an **IAM role** the Knowledge Base assumes (trust `bedrock.amazonaws.com`;
   permissions to read the processed S3 bucket, write the OpenSearch collection,
   and the Neptune graph).
2. Create a **Bedrock Knowledge Base** per corpus (`pdf`, `web`, `connected`,
   `visual`, `news`) with the OpenSearch collection as the vector store **and**
   Neptune Analytics for GraphRAG; choose the embedding model + chunking.
3. Wire **S3 → EventBridge → Step Functions** for event-driven ingestion, with
   **Textract / Bedrock Data Automation** as the parse step.
4. Build the **AgentCore** multi-agent retrieval (the same six agents this app
   already implements locally map 1:1).

These are documented as the next control-plane components; the current `aws/
provision.py` is structured so each is added as one more `Component` with
`create()/delete()/status()`.

## 8. API reference (everything the console does)

| Endpoint | Body | Does |
|---|---|---|
| `POST /api/aws/credentials` | `{region, access_key_id, ...}` | set creds in env |
| `POST /api/aws/plan` | `{action, project, region}` | dry-run plan + cost |
| `POST /api/aws/provision` | `{project, region, only?}` | create resources |
| `POST /api/aws/inventory` | `{project, region}` | what's live + cost |
| `POST /api/aws/teardown` | `{project, region, only?}` | delete by tag |
| `POST /api/aws/validate` | component form | probe connectivity |
| `POST /api/aws/apply` | component form | switch engine to AWS |
| `POST /api/aws/smoke` | — | end-to-end AWS smoke |
| `POST /api/aws/revert` | — | back to local engine |

`only` is an optional list of component keys: `s3, dynamodb, ssm, guardrail,
opensearch_serverless, neptune_analytics` — for one-at-a-time control.

## 9. Troubleshooting
- **`boto3 MISSING` in Plan** → `pip install boto3` in the server's env.
- **AccessDenied** → the IAM principal lacks a permission; check the action in
  the error and add it (Section 6).
- **Collection/graph stuck "creating"** → these are async; re-run Inventory in a
  few minutes. Don't Apply until both are live.
- **Teardown leaves a bucket** → a non-empty bucket with versioning; empty
  versions in the console, then re-run teardown.
- **Bill still showing cost after teardown** → run Inventory; anything `live`
  wasn't deleted (often a still-creating resource that ignored delete) — delete
  it from the console.
