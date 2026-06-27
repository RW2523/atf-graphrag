# Deployment & AWS-native

IntelliGraphRAG runs three ways, and you choose between them with a single
environment variable. The core is stdlib-only Python, so the simplest deployment
needs nothing but an interpreter. From there, the same application scales — by
configuration only — onto a fully managed, serverless AWS-native stack. Nothing in
the code path changes; the provider-factory pattern swaps every component behind
the config.

This page covers the three profiles and what each swaps, Docker / Compose, the
AWS-native path in depth, the one-click control plane in the UI, the matching
`/api/aws/*` endpoints, and how to tear everything down so an idle stack costs
nothing.

---

## 1. The three profiles

Configuration is layered: built-in `DEFAULTS` → `config/settings.json` →
`config/settings.<profile>.json` → environment. The active profile is selected by
`ATF_PROFILE` (default `local`). Profiles are: `local`, `hybrid`, `aws` (a fourth,
`oss`, swaps in open-source models). Each profile is just a JSON overlay that
repoints providers.

| Concern | `local` | `hybrid` | `aws` |
|---|---|---|---|
| LLM | OpenRouter | OpenRouter | **Bedrock** (Claude 3.5 Sonnet) |
| Vision | OpenRouter | OpenRouter | **Bedrock** (Claude 3.5 Sonnet) |
| Embeddings | sentence-transformer / local | OpenRouter | **Bedrock** (Titan Embed v2, dim 1024) |
| Reranker | local | LLM | **Bedrock** (Cohere Rerank v3.5) |
| Vector store | local (file) | local (file) | **OpenSearch Serverless** (or Qdrant) |
| Graph store | local (file) | **Neo4j** | **Neptune Analytics** (or Neo4j) |
| Blob store | local (file) | local | **S3** |
| OCR / parsing | docling / advanced | tesseract | **Textract / Bedrock FM / BDA** |
| Catalog | local | local | **DynamoDB** |
| Config store | files / env | files / env | **SSM Parameter Store** |
| Guardrails | none / local | none / local | **Bedrock Guardrails + Automated Reasoning** |

The overlays live in `config/`:

- `config/settings.local.json` — everything local; pair with `OPENROUTER_API_KEY`.
- `config/settings.hybrid.json` — OpenRouter models + a Neo4j graph (see Compose below).
- `config/settings.aws.json` — the AWS-native overlay shown above.

> The platform is domain-agnostic. It was built and validated on a large U.S.
> government (ATF firearms/explosives) document corpus, but nothing in the
> deployment is tied to that data — it is the example/validation dataset only.

### Selecting a profile

```bash
export ATF_PROFILE=local        # or hybrid | aws | oss
python -m atf_graphrag serve    # HTTP API + web UI at http://localhost:8077
```

Relevant environment variables across profiles: `ATF_PROFILE`, `ATF_PARSER`,
`ATF_DATA_DIR`, `ATF_API_TOKEN` (Bearer auth, required off-local), `PREVIEW_ROOTS`,
`OPENROUTER_API_KEY`, `TAVILY_API_KEY`, and the standard `AWS_*` chain.

---

## 2. Docker & Compose

The image is a thin wrapper around the stdlib-only core.

### Dockerfile

`Dockerfile` builds from `python:3.11-slim`, installs `requirements.txt` (core
only — the optional accelerators), copies the app, and serves on `8077`:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV ATF_PROFILE=local
ENV ATF_PORT=8077
EXPOSE 8077
CMD ["python", "-m", "atf_graphrag", "serve"]
```

```bash
docker build -t intelligraphrag .
docker run -p 8077:8077 -e OPENROUTER_API_KEY=... intelligraphrag
```

### docker-compose.yml

`docker-compose.yml` brings up the app plus a **Neo4j 5** graph for the `hybrid`
profile. The `local` profile needs none of this — `python -m atf_graphrag serve`
alone is enough.

```bash
ATF_PROFILE=hybrid OPENROUTER_API_KEY=... docker compose up
```

The `app` service publishes `8077`; the `neo4j` service publishes `7474` (browser)
and `7687` (Bolt), with credentials `neo4j/testpassword` and a persistent
`neo4j_data` volume. The app reaches it via `NEO4J_URI=bolt://neo4j:7687`.

### Dependencies

- `requirements.txt` — core accelerators (numpy, pypdf, requests, bs4, etc.). All
  optional; the core falls back to stdlib without them.
- `requirements-aws.txt` — installs the above plus `boto3>=1.34` (Bedrock LLM /
  embeddings / vision, Textract, S3), `opensearch-py>=2.4` (k-NN vector store), and
  `neo4j>=5.0` (Neo4j and Neptune via openCypher/Bolt). Qdrant is available by
  uncommenting `qdrant-client`.

```bash
pip install -r requirements-aws.txt   # for ATF_PROFILE=aws
```

---

## 3. The AWS-native path

The `aws` profile maps every IntelliGraphRAG component onto a managed AWS service.
The providers live under `atf_graphrag/providers/` and `atf_graphrag/stores/`, and
the factory in `providers/__init__.py` selects them by config with graceful
fallback.

| Concern | Service | Provider |
|---|---|---|
| LLM | Bedrock FM (Converse) | `providers/bedrock.py` `BedrockLLM` |
| Vision / multimodal | Bedrock FM | `BedrockVision` |
| Embeddings | Bedrock (Titan / Cohere) | `BedrockEmbedder` |
| Reranker | Bedrock (Cohere / Amazon Rerank) | `BedrockReranker` |
| Vectors | OpenSearch Serverless (or Qdrant) | `stores/opensearch_store.py` |
| Graph | Neptune Analytics (or Neo4j) | `providers/neptune.py` |
| Blobs | S3 | `providers/blob.py` |
| Catalog | DynamoDB | extended metadata catalog |
| Config | SSM Parameter Store | control plane |
| OCR | Textract | `TextractOCR` |
| NER / PII | Comprehend | `ComprehendEntities` |
| Document parsing | Bedrock FM / BDA | `aws_parsers.py`, `providers/bda.py` |
| Safety | Bedrock Guardrails (+ Automated Reasoning) | `providers/guardrail.py` |
| Quality | Managed RAG Evaluation | `eval/bedrock_eval.py` |

### Bedrock LLM / vision / embeddings

The `aws` overlay points `llm`, `vision`, and `embeddings` at Bedrock with
`anthropic.claude-3-5-sonnet-20240620-v1:0` for generation/vision and
`amazon.titan-embed-text-v2:0` (dim 1024) for embeddings. The reranker uses
`cohere.rerank-v3-5:0`. You must **enable model access** in the Bedrock console
(Model access) for each of these before they will respond.

### Vector & graph stores

`vector_store.provider = opensearch` targets an OpenSearch Serverless collection
for hybrid (vector + lexical) search. `graph_store.provider = neptune` targets a
Neptune Analytics graph for the GraphRAG knowledge graph. Both are also available
as `qdrant` / `neo4j` for a hybrid topology — for example Bedrock models with a
self-hosted Neo4j.

### S3 blobs, DynamoDB catalog, SSM config

S3 holds raw landing, processed, and vector data (three buckets). DynamoDB holds
the extended metadata catalog (`document_id` / `chunk_id` keyed, on-demand
billing). SSM Parameter Store holds runtime config under `/<project>/*`.

### Bedrock Guardrails + Automated Reasoning

`guardrails.provider = bedrock` runs answers through `ApplyGuardrail` (and inline
Converse) for content filtering and PII redaction. Adding an
`automated_reasoning_policy` ARN layers **formal, factual-accuracy checks** on top
of the topic/PII filters — Bedrock runs logic checks against the policy's encoded
domain rules so answers are validated as factually consistent.

```json
{ "guardrails": {
    "provider": "bedrock", "enabled": true,
    "guardrail_id": "...", "guardrail_version": "1",
    "automated_reasoning_policy": "arn:aws:bedrock:...:automated-reasoning-policy/..." } }
```

### Bedrock Data Automation (BDA) parsing

Set `ingestion.parser.provider = bda` to use BDA's managed multimodal extraction
(`providers/bda.py` `BedrockDataAutomationParser`). The flow is: upload →
`invoke_data_automation_async` → poll → read structured per-page Markdown (with
tables) from S3 → straight into the `table_data` parser. It falls back to Docling /
advanced if not configured.

```json
{ "ingestion": {
    "parser": { "provider": "bda" },
    "bda": { "region": "us-east-1", "bucket": "my-bda-bucket",
             "project_arn": "arn:aws:bedrock:...:data-automation-project/...",
             "profile_arn": "arn:aws:bedrock:...:data-automation-profile/..." } } }
```

The BDA project itself is provisionable from the console (the `bda_project`
control-plane component).

### Managed RAG Evaluation

`eval/bedrock_eval.py` `submit_rag_evaluation` wraps Bedrock's
`create_evaluation_job` — the AWS-managed counterpart to the local 50-question
harness. It runs LLM-as-judge scoring (correctness / completeness / faithfulness /
context-relevance) and writes results to S3.

```python
from eval.bedrock_eval import submit_rag_evaluation
submit_rag_evaluation(
    region="us-east-1", role_arn="arn:...:role/bedrock-eval",
    output_s3="s3://b/eval-out/", dataset_s3="s3://b/qa.jsonl")
```

It is also exposed as `POST /api/aws/rag-eval` (`role_arn`, `output_s3`,
`dataset_s3`).

> **Validation boundary (honest note).** All AWS code is real boto3 but validated
> with **mocked clients + dry-run plans only** — not against live AWS. On your
> first real run, use **Plan**, then provision one component at a time and verify
> in the AWS console. Treat the first live run as a supervised dry-run.

---

## 4. The one-click control plane

The **AWS Native** tab in the web UI drives the whole stack from
`atf_graphrag/aws/provision.py`. Every resource is tagged `Project=<project>` so a
single teardown can find and remove the entire stack. The lifecycle is
**Plan → Provision → Smoke → Teardown**.

### What gets created

The control plane manages these components, provisioned in this order (teardown
runs in reverse):

| Component | Key | Service | ~Cost while live |
|---|---|---|---|
| S3 buckets (raw / processed / vectors) | `s3` | S3 | ~$1/mo |
| Metadata catalog | `dynamodb` | DynamoDB (on-demand) | ~$1/mo |
| Config parameters | `ssm` | SSM Parameter Store | $0 |
| Content guardrail | `guardrail` | Bedrock Guardrails | $0 at rest |
| Data Automation project | `bda_project` | Bedrock Data Automation | $0 (pay per page) |
| Vector collection | `opensearch_serverless` | OpenSearch Serverless | **~$350/mo** (2-OCU min) |
| Knowledge graph | `neptune_analytics` | Neptune Analytics | **~$350/mo** (min capacity) |

Each component is a small object with `create()` / `delete()` / `status()`, all
**idempotent** (create checks-exists, delete ignores-absent) and **defensive** — a
missing `boto3` or missing credentials degrades to a clear "unavailable" status
instead of raising. Resources carry tags `Project=<project>` and
`ManagedBy=graphrag-console`. The default project name is `atf-graphrag`.

> Bedrock Knowledge Bases and Prompt Flows are the documented next phase — they
> need the IAM role + vector store + graph to exist first. The current control
> plane ships storage, graph, vector, guardrail, and BDA-project, plus the
> connect / validate / apply flow.

### The workflow (UI cards)

The tab has four numbered cards:

1. **Credentials** — paste Region, Access key ID, Secret access key (and session
   token for temporary creds). Keys go into the process environment (the standard
   boto3 chain) and are **never written to disk**.
2. **Component config** — review what each component will create.
3. **Provision / tear down**:
   - **Plan provision** — dry-run: every resource, the action, and estimated
     monthly cost. No changes made. Confirm account id + region.
   - **Provision all** — creates the stack. OpenSearch Serverless and Neptune
     Analytics are **asynchronous** (a few minutes to become ACTIVE); the call
     returns immediately after requesting them.
   - **Inventory & cost** — re-scan: which components are live and the running
     monthly cost. Wait until OpenSearch + Neptune show **live** before activating.
   - **Delete ALL AWS resources** — tag-based teardown (see Section 6).
4. **Validate & activate**:
   - **Validate connectivity** — probes each component (STS, Bedrock, embeddings,
     vision, OCR, vector store, graph store, S3).
   - **Apply & switch engine** — repoints the running engine onto the AWS backends
     with no code change (config-only, via the provider factory).
   - **Run end-to-end smoke test** — ingests a tiny document and answers a
     question through the AWS pipeline to prove ingest → index → retrieve →
     generate works.
   - **Revert to local** — switches back to the local engine any time.

> Prefer one-at-a-time on your first run: provision a single component, verify in
> the console, then proceed (use the `only` field on the API below).

---

## 5. `/api/aws/*` endpoints

Everything the console does is also a JSON endpoint on the API server
(`atf_graphrag/api/server.py`, port `8077`; Bearer `ATF_API_TOKEN` required
off-local).

| Endpoint | Body | Does |
|---|---|---|
| `POST /api/aws/credentials` | `{region, access_key_id, secret_access_key, session_token?}` | set creds in process env |
| `POST /api/aws/plan` | `{action, project, region, only?}` | dry-run plan + cost estimate |
| `POST /api/aws/provision` | `{project, region, only?}` | create resources |
| `POST /api/aws/inventory` | `{project, region}` | what's live + running cost |
| `POST /api/aws/teardown` | `{project, region, only?}` | delete by tag, reverse order |
| `POST /api/aws/validate` | component form | probe connectivity |
| `POST /api/aws/apply` | component form | switch engine to AWS |
| `POST /api/aws/smoke` | — | end-to-end AWS smoke test |
| `POST /api/aws/revert` | — | switch back to local engine |
| `POST /api/aws/rag-eval` | `{region?, role_arn, output_s3, dataset_s3}` | submit managed RAG eval |
| `GET /api/aws/status` | — | current AWS engine status |

`only` is an optional list of component keys for one-at-a-time control:
`s3, dynamodb, ssm, guardrail, bda_project, opensearch_serverless,
neptune_analytics`.

```bash
# Provision just the S3 buckets first, verify, then continue
curl -X POST http://localhost:8077/api/aws/provision \
  -H 'Authorization: Bearer '"$ATF_API_TOKEN" \
  -d '{"region":"us-east-1","only":["s3"]}'
```

The `plan` response includes `est_cost_month` (sum across the planned steps), the
resolved `account_id`, and a `boto3` availability flag. `inventory` returns
`running_cost_month` and `n_live`.

---

## 6. Cost control & teardown

> **Cost reality.** Two resources dominate the bill and run 24/7 while they exist:
> **Neptune Analytics (~$350/mo)** and **OpenSearch Serverless (~$350/mo for the
> 2-OCU minimum)**. Everything else (S3, DynamoDB, SSM, Guardrail, BDA project) is
> ~free at rest. **Always tear down when you stop testing.**

The intended loop is **provision → demo/test → teardown**, repeating. Nothing is
left running between sessions.

**Delete ALL AWS resources** (two confirmations in the UI, or
`POST /api/aws/teardown`) deletes every `Project=<project>` resource in **reverse**
dependency order: Neptune → OpenSearch → BDA project → Guardrail → SSM → DynamoDB →
S3 (emptying buckets first). Re-run **Inventory** to confirm
`0 live · running cost ~$0/month`.

Troubleshooting teardown:

- **`boto3 MISSING` in Plan** → `pip install boto3` (or `requirements-aws.txt`) in
  the server's environment.
- **AccessDenied** → the IAM principal lacks a permission; check the failing action
  in the error and grant it.
- **Collection / graph stuck "creating"** → these are async; re-run Inventory in a
  few minutes. Don't Apply until both are live.
- **Teardown leaves a bucket** → a non-empty versioned bucket; empty versions in
  the console, then re-run teardown.
- **Bill still showing cost after teardown** → run Inventory; anything still `live`
  (often a resource that was still creating when delete fired) must be removed from
  the console.

For production, scope an IAM policy to the project: `s3:*` on the project buckets,
`dynamodb:*` on the catalog, `ssm:*Parameter*` on `/<project>/*`, `aoss:*` on the
collection, `neptune-graph:*` on the graph, `bedrock:*Guardrail*`,
`bedrock:InvokeModel*`, `sts:GetCallerIdentity`, and `tag:GetResources` — scoped by
the `Project` tag where the service supports a condition key.

---

📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Architecture](Architecture.md)
