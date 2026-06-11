# Bedrock-native capabilities — what's wired, what's new, and the platform vision

Honest, current status of every Amazon Bedrock service for this platform.

## Capability matrix (as of this release)

| Bedrock service | Status | Where |
|---|---|---|
| Foundation models (LLM) | ✅ implemented | `providers/bedrock.py` `BedrockLLM` (Converse) |
| Embeddings (Titan/Cohere) | ✅ implemented | `BedrockEmbedder` |
| Vision / multimodal FM | ✅ implemented | `BedrockVision` |
| Rerank (Cohere/Amazon) | ✅ implemented | `BedrockReranker` |
| **Guardrails** | ✅ implemented | `BedrockGuardrail` (ApplyGuardrail + inline Converse) |
| **Guardrails — Automated Reasoning** | ✅ **new** | guardrail accepts `automated_reasoning_policy` ARN → factual-accuracy checks |
| OCR | ✅ implemented | `TextractOCR` |
| NER / PII | ✅ implemented | `ComprehendEntities` |
| FM document parsing | ✅ implemented | `aws_parsers.BedrockDocumentParser` |
| **Data Automation (BDA)** | ✅ **new** | `providers/bda.py` `BedrockDataAutomationParser` (parser.provider=`bda`) |
| Vector store (OpenSearch Serverless) | ✅ implemented | `stores/opensearch_store.py` + control plane |
| Graph (Neptune Analytics) | ✅ implemented | `providers/neptune.py` + control plane |
| **RAG Evaluation (managed)** | ✅ **new** | `eval/bedrock_eval.py` `submit_rag_evaluation` (create_evaluation_job) |
| BDA project (provisioning) | ✅ **new** | control plane component `bda_project` |
| **Knowledge Bases** | ⚠️ documented | control plane next phase (needs role+vector+graph first) |
| **Prompt Flows** | ⚠️ documented | next phase (`bedrock-agent create_flow`) |
| **Model Evaluation (non-RAG)** | ⚠️ documented | `bedrock create_evaluation_job applicationType=ModelEvaluation` |

> **Before this release we did NOT use:** Bedrock Data Automation, Automated
> Reasoning, managed RAG Evaluation, Flows, or Knowledge Bases. This release adds
> BDA, Automated Reasoning, and managed RAG Evaluation as real (mocked-tested)
> integrations, plus a provisionable BDA project. Flows + Knowledge Bases remain
> the documented next phase. All AWS code is validated with **mocked clients** —
> it needs live AWS to run for real.

## How to use the new pieces

### 1. Bedrock Data Automation (BDA) for ingestion
```json
// config (aws profile): use BDA's managed multimodal extraction
{ "ingestion": {
    "parser": { "provider": "bda" },
    "bda": { "region": "us-east-1", "bucket": "my-bda-bucket",
             "project_arn": "arn:aws:bedrock:...:data-automation-project/...",
             "profile_arn": "arn:aws:bedrock:...:data-automation-profile/..." } } }
```
Flow: upload → `invoke_data_automation_async` → poll → read structured output
(per-page Markdown **with tables**) from S3 → straight into our `table_data`
parser. Falls back to Docling/advanced if not configured. Provision the project
from the console: AWS Native tab → it's the `bda_project` control-plane component.

### 2. Guardrails with Automated Reasoning (factual checks)
```json
{ "guardrails": { "provider": "bedrock", "enabled": true,
                  "guardrail_id": "...", "guardrail_version": "1",
                  "automated_reasoning_policy": "arn:aws:bedrock:...:automated-reasoning-policy/..." } }
```
The policy encodes domain rules; Bedrock runs formal logic checks so answers are
validated as factually consistent — on top of PII/denied-topic filtering.

### 3. Managed RAG Evaluation
```python
from eval.bedrock_eval import submit_rag_evaluation
submit_rag_evaluation(region="us-east-1", role_arn="arn:...:role/bedrock-eval",
                      output_s3="s3://b/eval-out/", dataset_s3="s3://b/qa.jsonl",
                      metrics=["Builtin.Correctness","Builtin.Completeness",
                               "Builtin.Faithfulness","Builtin.ContextRelevance"])
```
The AWS-managed counterpart to our local harness — LLM-as-judge scoring of
correctness/completeness/faithfulness/context-relevance, results on S3.

## The powerful platform you can build (vision)

Composing all of the above gives a fully managed, serverless ATF GraphRAG:

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

Why this is powerful:
- **Zero servers** — every layer is a managed service; pay per use, tear down to $0.
- **Managed extraction** (BDA) replaces fragile local parsing for any modality.
- **Managed GraphRAG** (KB + Neptune) — entity/relationship graph with no graph-DB ops.
- **Safe + provably factual** — Guardrails + Automated Reasoning on every answer.
- **Continuously evaluated** — managed RAG eval tracks quality over time.
- **One control plane** — provision the whole stack and **delete it in one click**
  (tag-based teardown) so an idle stack costs nothing. See `AWS_NATIVE_SETUP.md`.

Everything stays config-swappable: the same application runs local (Docling +
local stores) → hybrid → fully Bedrock-native, by configuration only.
