# Deployment & AWS-native

IntelliGraphRAG runs three ways, and you choose between them with a single
environment variable. The core is stdlib-only Python, so the simplest deployment
needs nothing but an interpreter. From there the *same* application scales — by
configuration only — onto a fully managed, serverless AWS-native stack. No code
path changes when you switch: a provider-factory pattern swaps every component
behind the config, so `local` → `hybrid` → `aws` is a JSON/env decision, not a
rewrite.

This page covers:

- the deployment **profiles** and exactly what each one swaps,
- **Docker / Compose** for the local and hybrid stacks,
- the **AWS-native path** in depth (Bedrock LLM / vision / embeddings, Qdrant or
  OpenSearch, Neptune or Neo4j, S3, DynamoDB, SSM, Guardrails + Automated
  Reasoning, Bedrock Data Automation, managed RAG evaluation),
- the **one-click control plane** (Plan → Provision → Smoke → Teardown, tagged by
  `Project`) and the matching **`/api/aws/*` endpoints**,
- and how to tear everything down so an idle stack costs nothing.

> Repository: <https://github.com/RW2523/intelligraphrag>

---

## 1. Deployment profiles

Configuration is layered, lowest precedence first:

1. built-in `DEFAULTS` (in `intelligraphrag/config.py`) — the `local` profile,
2. `config/settings.json` (optional, applies to every profile),
3. `config/settings.<profile>.json` (the active profile's overlay),
4. environment overrides.

The active profile is selected by the `IGR_PROFILE` env var (default `local`).
Each profile is just a JSON overlay that repoints providers — nothing else
changes.

```bash
# pick one
export IGR_PROFILE=local     # default, fully offline-capable
export IGR_PROFILE=hybrid    # OpenRouter models + Neo4j graph
export IGR_PROFILE=aws       # fully managed AWS-native (Bedrock + OpenSearch + Neptune)
```

> Additional overlays ship in `config/` for narrower setups, e.g.
> `settings.oss.json` (open-source models), `settings.bedrock-hybrid.json`,
> `settings.ec2.json`, and `settings.aws-ingest.json`. They are selected the same
> way (`IGR_PROFILE=<name>`).

### What each profile swaps

| Component | `local` | `hybrid` | `aws` |
|---|---|---|---|
| LLM | OpenRouter (`openai/gpt-4o-mini`) | OpenRouter (`anthropic/claude-3.5-sonnet`) | **Bedrock** Claude 3.5 Sonnet (Converse) |
| Vision / multimodal | OpenRouter | OpenRouter (Claude 3.5 Sonnet) | **Bedrock** Claude 3.5 Sonnet |
| Embeddings | `sentence_transformer` (`all-MiniLM-L6-v2`, 384-dim) | OpenRouter (`text-embedding-3-small`, 1536-dim) | **Bedrock** Titan Embed v2 (`amazon.titan-embed-text-v2:0`, 1024-dim) |
| Reranker | local (cross-feature) | LLM (`openai/gpt-4o-mini`) | **Bedrock** Cohere Rerank (`cohere.rerank-v3-5:0`) |
| Vector store | local (file-backed) | local | **OpenSearch Serverless** (or Qdrant) |
| Graph store | local (file-backed) | **Neo4j** (Bolt) | **Neptune Analytics** (or Neo4j) |
| Blob store | local filesystem | local | **S3** |
| OCR | `auto` (Tesseract if present) | Tesseract | **Textract** |
| Parser | Docling (DocLayNet + TableFormer) | Docling | Bedrock FM parser (swap to **BDA** in config) |
| Entity / PII extraction | LLM | LLM | LLM (swap to **Comprehend** in config) |
| Guardrails | none | none | **Bedrock Guardrails** (off until configured) |

> The `aws` profile defaults to the Bedrock foundation-model document parser
> (`ingestion.parser.provider = "bedrock"`). Bedrock **Data Automation** (BDA) is
> opt-in — set `ingestion.parser.provider = "bda"` and supply a project ARN (see
> [§6](#6-advanced-bedrock-bda-guardrails--automated-reasoning-managed-rag-eval)).

### Configuration layering in code

`Settings(profile=...)` deep-merges the overlays in order, so you only specify
the deltas in a profile file. For example the entire `aws` overlay
(`config/settings.aws.json`) is:

```json
{
  "profile": "aws",
  "llm": {"provider": "bedrock", "model": "anthropic.claude-3-5-sonnet-20240620-v1:0", "region": "us-east-1"},
  "vision": {"provider": "bedrock", "model": "anthropic.claude-3-5-sonnet-20240620-v1:0"},
  "embeddings": {"provider": "bedrock", "model": "amazon.titan-embed-text-v2:0", "dim": 1024, "region": "us-east-1"},
  "reranker": {"provider": "bedrock", "model": "cohere.rerank-v3-5:0"},
  "vector_store": {"provider": "opensearch"},
  "graph_store": {"provider": "neptune"},
  "blob_store": {"provider": "s3"},
  "guardrails": {"provider": "bedrock", "enabled": false, "guardrail_id": "", "guardrail_version": "DRAFT", "trace": false},
  "ingestion": {
    "ocr": {"provider": "textract", "region": "us-east-1"},
    "parser": {"provider": "bedrock", "model": "anthropic.claude-3-5-sonnet-20240620-v1:0", "region": "us-east-1"},
    "extraction": {"provider": "llm"}
  }
}
```

### Useful environment overrides

These are read in `config.py` and applied on top of the active profile:

| Variable | Effect |
|---|---|
| `IGR_PROFILE` | select `local` / `hybrid` / `aws` (or another overlay name) |
| `OPENROUTER_API_KEY` | API key for the `local` / `hybrid` model engine |
| `IGR_PORT` | HTTP port the server binds (default `8077`) |
| `IGR_DATA_DIR` | storage root for local stores |
| `IGR_EMBED_PROVIDER` | force the embeddings provider (`local` / `openrouter` / `bedrock`) |
| `IGR_PARSER` | force the document parser (`docling` / `advanced` / `textract` / `bedrock` / `bda`) |
| `IGR_API_TOKEN` | bearer token for API auth — **required** in non-local profiles |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | Neo4j connection (when `graph_store.provider = neo4j`) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` / `AWS_SESSION_TOKEN` | standard boto3 credentials for the `aws` profile |
| `TAVILY_API_KEY` | enable Tavily web-research augmentation |

> **Auth.** In `local` the API is open (dev convenience). In `hybrid` / `aws`,
> set `IGR_API_TOKEN` — every `POST` then requires a matching
> `Authorization: Bearer <token>` header.

---

## 2. Local profile (no dependencies)

The local profile needs nothing but a Python interpreter. Models go through
OpenRouter when a key is present; without one the engine degrades gracefully
(deterministic offline fallbacks for embeddings and generation).

```bash
pip install -r requirements.txt          # base install
export OPENROUTER_API_KEY=...             # optional but recommended
python -m intelligraphrag serve              # http://127.0.0.1:8077
```

Or use the convenience launcher, which loads `.env` first:

```bash
./run.sh
```

Copy `.env.example` to `.env` to set `OPENROUTER_API_KEY`, `IGR_PROFILE`, and the
optional overrides above.

---

## 3. Docker & Compose

### Single-container (local profile)

The `Dockerfile` builds a slim Python 3.11 image that defaults to the local
profile on port `8077`:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV IGR_PROFILE=local
ENV IGR_PORT=8077
EXPOSE 8077
CMD ["python", "-m", "intelligraphrag", "serve"]
```

```bash
docker build -t intelligraphrag .
docker run --rm -p 8077:8077 \
  -e OPENROUTER_API_KEY=$OPENROUTER_API_KEY \
  intelligraphrag
```

### Compose (hybrid profile: app + Neo4j)

`docker-compose.yml` brings up the app plus a Neo4j graph store for the hybrid
profile. The local profile needs none of this — `python -m intelligraphrag serve`
is enough.

```yaml
services:
  app:
    build: .
    ports:
      - "8077:8077"
    environment:
      - IGR_PROFILE=${IGR_PROFILE:-local}
      - OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}
      - NEO4J_URI=bolt://neo4j:7687
      - NEO4J_USER=neo4j
      - NEO4J_PASSWORD=testpassword
    depends_on:
      - neo4j

  neo4j:
    image: neo4j:5
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      - NEO4J_AUTH=neo4j/testpassword
    volumes:
      - neo4j_data:/data

volumes:
  neo4j_data:
```

```bash
# hybrid: app uses OpenRouter models + the Compose Neo4j graph
IGR_PROFILE=hybrid OPENROUTER_API_KEY=$OPENROUTER_API_KEY docker compose up --build
```

Neo4j Browser is on <http://localhost:7474>; the app is on
<http://localhost:8077>. Change the default `testpassword` before any real use.

> The AWS-native profile is not driven by Compose — its backends are managed AWS
> services, provisioned and torn down from the in-app control plane
> ([§5](#5-the-one-click-control-plane)).

---

## 4. The AWS-native path

In the `aws` profile every component is a managed AWS service. The application is
identical; only the providers behind the factory change.

### 4.1 Install the AWS extras

```bash
pip install -r requirements-aws.txt
```

`requirements-aws.txt` layers the cloud SDKs on top of the base install:

```text
-r requirements.txt
boto3>=1.34          # Bedrock LLM/embeddings/vision, Textract, S3
opensearch-py>=2.4   # OpenSearch k-NN vector store
neo4j>=5.0           # graph store (Neo4j and Neptune via openCypher/Bolt)

# Alternative managed vector store
# qdrant-client>=1.7
```

> `boto3` is the only hard requirement for the control plane. `opensearch-py`
> and `neo4j` are needed when you point the engine at those stores;
> `qdrant-client` is optional (uncomment if you use Qdrant instead of OpenSearch).

### 4.2 The AWS-native components

| Concern | Service | Notes |
|---|---|---|
| LLM / generation | **Bedrock** Claude 3.5 Sonnet via the Converse API | `providers/bedrock.py` `BedrockLLM` |
| Vision / multimodal | **Bedrock** Claude 3.5 Sonnet | `BedrockVision` |
| Embeddings | **Bedrock** Titan Embed v2 (1024-dim) or Cohere | `BedrockEmbedder` |
| Reranker | **Bedrock** Cohere / Amazon Rerank | `BedrockReranker` |
| Vector store | **OpenSearch Serverless** k-NN (or **Qdrant**) | `stores/opensearch_store.py` |
| Graph store | **Neptune Analytics** (GraphRAG) or **Neo4j** via Bolt / openCypher | `providers/neptune.py` |
| Blob store | **S3** (raw / processed / vectors buckets) | `S3BlobStore` |
| Metadata catalog | **DynamoDB** (on-demand) | extended chunk metadata |
| Config store | **SSM Parameter Store** | `/<project>/*` parameters |
| OCR | **Textract** `detect_document_text` | `TextractOCR` |
| Document parsing | **Bedrock** FM parser or **Bedrock Data Automation** | `aws_parsers.py`, `providers/bda.py` |
| Entity / PII | LLM or **Comprehend** NER + PII | `extraction.provider` |
| Content safety | **Bedrock Guardrails** (+ **Automated Reasoning**) | `BedrockGuardrail` |
| Managed RAG eval | **Bedrock** `create_evaluation_job` (RagEvaluation) | `eval/bedrock_eval.py` |

> The vector store and graph store are independently swappable: set
> `vector_store.provider` to `opensearch` or `qdrant`, and `graph_store.provider`
> to `neptune` or `neo4j`. The UI form and `build_aws_settings` accept either.

### 4.3 Prerequisites

1. An AWS account with access to **Amazon Bedrock**, **Neptune Analytics**, and
   **OpenSearch Serverless** in your region (default `us-east-1`).
2. **Enable Bedrock model access** (console → Bedrock → Model access): Claude 3.5
   Sonnet (generation + vision), Titan Embeddings v2 (or Cohere), Cohere Rerank.
3. An IAM principal whose keys you'll use, able to create the resources below.
   For a sandbox, `AdministratorAccess` is the quick path; see
   [§7](#7-least-privilege-iam) for least-privilege.
4. `pip install boto3` (covered by `requirements-aws.txt`).

> **Credentials are never written to disk.** When you paste keys into the AWS
> Native tab, they go into the process environment (the standard boto3 credential
> chain) and are only ever echoed back masked. Nothing is written to
> `settings.*.json` or any file.

---

## 5. The one-click control plane

The **AWS Native** tab in the UI is a control plane over the managed stack:
**Plan → Provision → (Validate / Apply) → Smoke → Teardown**. Every resource it
creates is tagged `Project=<project>` (default `atf-graphrag`) so a single
teardown can find and remove the whole stack — spin it up to demo or test, then
tear it down to stop paying for it.

The backend is `intelligraphrag/aws/provision.py` (`ControlPlane` + per-resource
`Component` objects) and `intelligraphrag/api/aws_setup.py` (credentials, settings
build, connectivity probes, engine rebind).

### 5.1 What gets provisioned

`ControlPlane.ORDER` defines provision order (teardown runs it in reverse):

```
s3 → dynamodb → ssm → guardrail → bda_project → opensearch_serverless → neptune_analytics
```

| Key | Resource(s) | Service | ~Cost while live |
|---|---|---|---|
| `s3` | `<project>-raw/processed/vectors-<acct>` | S3 | ~$1/mo |
| `dynamodb` | `<project>-catalog` (PAY_PER_REQUEST) | DynamoDB | ~$1/mo |
| `ssm` | `/<project>/region`, `/<project>/project` | SSM Parameter Store | $0 |
| `guardrail` | `<project>-guardrail` (content filters) | Bedrock Guardrails | $0 at rest |
| `bda_project` | `<project>-bda` (page + element extraction) | Bedrock Data Automation | $0 at rest (pay per page) |
| `opensearch_serverless` | `<project>-vectors` (VECTORSEARCH collection + enc/net policies) | OpenSearch Serverless | **~$350/mo** (2-OCU min) |
| `neptune_analytics` | `<project>-graph` (16 GiB provisioned memory) | Neptune Analytics | **~$350/mo** |

Everything is tagged `Project=<project>` and `ManagedBy=graphrag-console`. Each
`Component` is idempotent — `create()` checks-exists, `delete()` ignores absent
— and degrades to a clear "unavailable" status if boto3 or credentials are
missing rather than raising.

> **Async resources.** OpenSearch Serverless and Neptune Analytics provision
> **asynchronously** (a few minutes to ACTIVE). The provision call returns
> immediately after requesting them; re-run **Inventory** until both show live
> before you Apply.

### 5.2 The workflow

1. **Enter credentials** (card 1) — Region, Access key ID, Secret access key (+
   session token for temporary creds). They go into the process env only.
2. **Plan provision** (card 3) — dry-run: every resource, its action, and the
   estimated monthly cost. Confirms account id + region. No changes are made.
3. **Provision all** — creates the stack in `ORDER`. (Or provision one component
   at a time with `only`, e.g. `{"only": ["s3"]}`, verifying in the console
   between steps — recommended on a first live run.)
4. **Inventory & cost** — re-scans which components are live and the running
   monthly cost. Wait until OpenSearch + Neptune show live.
5. **Validate connectivity** (card 4) — live probes of each component (STS,
   Bedrock LLM, embeddings, vision, Textract OCR, vector store, graph store, S3),
   each timed and returning ok/fail + detail.
6. **Apply & switch engine** — rebinds the *running* engine onto the AWS
   backends, with no restart and no code change (`build_aws_settings` →
   `_rebind`). The provider-factory pattern makes this config-only.
7. **Run end-to-end smoke** — ingests a tiny self-contained document and answers
   a question through the full AWS pipeline (ingest → index → retrieve →
   generate), returning the cited answer plus per-stage timings.
8. **Revert to local** — switches the engine back to the local profile any time.
9. **Tear down** (card 3) — delete every `Project`-tagged resource in reverse
   order (see [§8](#8-cost--teardown)).

### 5.3 `/api/aws/*` endpoints

Everything the UI does maps to an HTTP endpoint. In non-local profiles these
require the `Authorization: Bearer <IGR_API_TOKEN>` header.

| Method | Endpoint | Body | Does |
|---|---|---|---|
| `GET` | `/api/aws/status` | — | live engine wiring (concrete class per component) + which credentials are present |
| `POST` | `/api/aws/credentials` | `{region, access_key_id, secret_access_key, session_token?, neo4j_uri?, neo4j_user?, neo4j_password?, neptune_endpoint?}` | place creds into the process env (never persisted); returns a masked summary |
| `POST` | `/api/aws/plan` | `{action, project, region, only?}` | dry-run plan + estimated monthly cost |
| `POST` | `/api/aws/provision` | `{project, region, only?}` | create resources (in `ORDER`) |
| `POST` | `/api/aws/inventory` | `{project, region}` | what's live + running monthly cost + boto3 availability |
| `POST` | `/api/aws/teardown` | `{project, region, only?}` | delete `Project`-tagged resources in reverse order |
| `POST` | `/api/aws/validate` | component form | live connectivity probe per component |
| `POST` | `/api/aws/apply` | component form | rebind the running engine onto AWS backends |
| `POST` | `/api/aws/smoke` | — | end-to-end ingest → index → query smoke on the live engine |
| `POST` | `/api/aws/revert` | — | rebind back to the local profile |
| `POST` | `/api/aws/rag-eval` | `{region?, role_arn, output_s3, dataset_s3}` | submit a managed Bedrock RAG evaluation job (see [§6](#63-managed-rag-evaluation)) |

`only` is an optional list of component keys for one-at-a-time control:
`s3, dynamodb, ssm, guardrail, bda_project, opensearch_serverless,
neptune_analytics`.

```bash
TOKEN=...   # IGR_API_TOKEN, required in the aws profile
BASE=http://127.0.0.1:8077

# 1) credentials (env-only)
curl -s -X POST $BASE/api/aws/credentials -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"region":"us-east-1","access_key_id":"AKIA...","secret_access_key":"..."}'

# 2) dry-run plan + cost
curl -s -X POST $BASE/api/aws/plan -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"action":"provision","project":"atf-graphrag","region":"us-east-1"}'

# 3) provision one component first, then verify in the console
curl -s -X POST $BASE/api/aws/provision -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"project":"atf-graphrag","region":"us-east-1","only":["s3"]}'

# 4) what is live, and what is it costing?
curl -s -X POST $BASE/api/aws/inventory -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"project":"atf-graphrag","region":"us-east-1"}'

# 5) tear it ALL down
curl -s -X POST $BASE/api/aws/teardown -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"project":"atf-graphrag","region":"us-east-1"}'
```

> **Validation boundary (honest note).** The provision / teardown code is real
> boto3, but it is validated in CI with **mocked clients and dry-run plans only**
> — not against live AWS. Treat your first live run as a supervised dry-run: use
> **Plan**, then provision one component at a time and verify each in the AWS
> console before proceeding.

---

## 6. Advanced Bedrock: BDA, Guardrails + Automated Reasoning, managed RAG eval

These are config-swappable extensions on top of the `aws` profile.

### 6.1 Bedrock Data Automation (BDA) for ingestion

BDA is AWS's managed multimodal extraction. Point the parser at it to replace
local parsing for any modality:

```json
{
  "ingestion": {
    "parser": { "provider": "bda" },
    "bda": {
      "region": "us-east-1",
      "bucket": "my-bda-bucket",
      "project_arn": "arn:aws:bedrock:...:data-automation-project/...",
      "profile_arn": "arn:aws:bedrock:...:data-automation-profile/..."
    }
  }
}
```

Flow: upload → `invoke_data_automation_async` → poll → read the structured
output (per-page Markdown **with tables**) from S3 → straight into the table-data
parser. It falls back to Docling / advanced parsing if not configured. Provision
the project from the **AWS Native** tab — it is the `bda_project` control-plane
component (`<project>-bda`), or `POST /api/aws/provision {"only":["bda_project"]}`.

### 6.2 Guardrails with Automated Reasoning (factual checks)

Bedrock Guardrails filter PII and denied topics. Adding an Automated Reasoning
policy ARN layers formal, logical factual-consistency checks on top:

```json
{
  "guardrails": {
    "provider": "bedrock",
    "enabled": true,
    "guardrail_id": "...",
    "guardrail_version": "1",
    "automated_reasoning_policy": "arn:aws:bedrock:...:automated-reasoning-policy/..."
  }
}
```

The policy encodes domain rules; Bedrock runs formal logic checks so answers are
validated as factually consistent, on top of the PII / denied-topic filtering.
The control plane's `guardrail` component creates a baseline content-filter
guardrail (`<project>-guardrail`) you can then attach a policy to.

### 6.3 Managed RAG evaluation

The AWS-managed counterpart to the local evaluation harness — LLM-as-judge
scoring of correctness, completeness, faithfulness, and context relevance, with
results written to S3. Submit a job via the endpoint or directly:

```python
from eval.bedrock_eval import submit_rag_evaluation

submit_rag_evaluation(
    region="us-east-1",
    role_arn="arn:aws:iam::...:role/bedrock-eval",
    output_s3="s3://my-bucket/eval-out/",
    dataset_s3="s3://my-bucket/qa.jsonl",
    metrics=["Builtin.Correctness", "Builtin.Completeness",
             "Builtin.Faithfulness", "Builtin.ContextRelevance"],
)
```

This calls Bedrock `create_evaluation_job` with `applicationType="RagEvaluation"`
and returns the `jobArn`. The same is reachable over HTTP at
`POST /api/aws/rag-eval`.

### 6.4 The fully managed platform (target architecture)

Composed end to end, these services give a serverless GraphRAG with no servers to
run:

```
 S3 (raw) ─► EventBridge ─► Step Functions ─► Bedrock DATA AUTOMATION
                                              (docs/images/tables → structured)
                                                   │
                                                   ▼
                        Bedrock KNOWLEDGE BASE  (chunk + embed + GraphRAG)
                        ├─ vector: OpenSearch Serverless
                        └─ graph:  Neptune Analytics
                                                   │
            user ─► API ─► Bedrock PROMPT FLOWS / AgentCore (orchestration)
                          ├─ retrieve (KB) → RERANK → generate (FM)
                          ├─ GUARDRAILS + AUTOMATED REASONING (safe + factual)
                          └─ cite sources / Neptune node ids
                                                   │
                        Bedrock RAG EVALUATION  (continuous quality scoring)
                        CloudWatch + AgentCore Observability  (traces, cost)
```

> **What ships now vs. next.** This release implements the resource control plane
> (S3, DynamoDB, SSM, Guardrails, BDA project, OpenSearch, Neptune) plus the
> connect / validate / apply / smoke flow, and the BDA parser, Automated
> Reasoning, and managed RAG evaluation integrations (all validated with mocked
> clients). Bedrock **Knowledge Bases**, **Prompt Flows**, **AgentCore**, and the
> **S3 → EventBridge → Step Functions** event-driven ingestion are the documented
> next phase. `aws/provision.py` is structured so each is added as one more
> `Component` with `create()` / `delete()` / `status()`.

---

## 7. Least-privilege IAM

For production, grant only what the control plane and engine touch, scoped by the
`Project` tag with a condition key where the service supports it:

- `s3:*` on the project buckets,
- `dynamodb:*` on the catalog table,
- `ssm:*Parameter*` on `/<project>/*`,
- `aoss:*` on the OpenSearch collection,
- `neptune-graph:*` on the graph,
- `bedrock:*Guardrail*` and `bedrock:InvokeModel*`,
- `bedrock-data-automation:*` (only if using BDA),
- `sts:GetCallerIdentity`,
- `tag:GetResources`.

---

## 8. Cost & teardown

> **Cost reality (read first).** Two resources dominate the bill and run 24/7
> while they exist: **Neptune Analytics (~$350/mo)** and **OpenSearch Serverless
> (~$350/mo for the 2-OCU minimum)**. Everything else (S3, DynamoDB, SSM,
> Guardrail, BDA project, IAM) is ~free at rest. **Always tear down when you stop
> testing.**

The intended cost-saving cycle is **provision → demo / test → teardown**, with
nothing left running between sessions.

**Tear down** from card 3 (two confirmations) or via the endpoint. It deletes
every `Project`-tagged resource in **reverse** dependency order:

```
neptune_analytics → opensearch_serverless → bda_project → guardrail → ssm → dynamodb → s3
```

S3 buckets are emptied before deletion. Re-run **Inventory** afterward to confirm
`0 live · running cost ~$0/month`.

```bash
curl -s -X POST http://127.0.0.1:8077/api/aws/teardown \
  -H "Authorization: Bearer $IGR_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"project":"atf-graphrag","region":"us-east-1"}'
```

### Teardown gotchas

- **Bill still showing cost after teardown** → run Inventory; anything still
  `live` wasn't deleted (often a resource that was still *creating* and ignored
  the delete). Delete it from the console.
- **Teardown leaves a bucket** → a non-empty, versioned bucket: empty the object
  versions in the console, then re-run teardown.
- **Collection / graph stuck "creating"** → both are async; re-run Inventory in a
  few minutes. Don't Apply until both are live.
- **`boto3 MISSING` in Plan** → `pip install -r requirements-aws.txt` in the
  server's environment.
- **AccessDenied** → the IAM principal lacks a permission; read the action in the
  error and add it ([§7](#7-least-privilege-iam)).

---

## See also

- **Configuration-Reference** — every config key and its provider options.
- **Architecture** — the provider-factory pattern and the engine internals.
- **Evaluation** — the local evaluation harness behind the managed RAG eval.
- `docs/AWS_NATIVE_SETUP.md` and `docs/BEDROCK_NATIVE.md` — the source playbooks
  this page summarizes.
