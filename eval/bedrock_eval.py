"""Amazon Bedrock managed RAG evaluation.

Submits a Bedrock `create_evaluation_job` for retrieval-augmented generation,
scoring metrics like Correctness, Completeness and Context Relevance with an
LLM-as-judge — the managed AWS counterpart to our local eval harness
(eval/run_eval.py + eval/ragas_metrics.py). Use it to grade either a Bedrock
Knowledge Base or your own RAG outputs supplied as a JSONL dataset on S3.

Real boto3 (validated with mocked clients). Needs a Bedrock service role with
eval + S3 permissions to run for real.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

DEFAULT_METRICS = ["Builtin.Correctness", "Builtin.Completeness",
                   "Builtin.Faithfulness", "Builtin.ContextRelevance"]


def submit_rag_evaluation(region: str, role_arn: str, output_s3: str,
                          dataset_s3: str,
                          eval_model: str = "anthropic.claude-3-5-sonnet-20240620-v1:0",
                          metrics: Optional[List[str]] = None,
                          job_name: str = "atf-rag-eval") -> Dict[str, Any]:
    """Kick off a managed Bedrock RAG evaluation job over a JSONL dataset on S3.
    Returns the create_evaluation_job response (jobArn)."""
    import boto3  # lazy
    br = boto3.client("bedrock", region_name=region)
    metric_names = metrics or DEFAULT_METRICS
    resp = br.create_evaluation_job(
        jobName=job_name,
        roleArn=role_arn,
        applicationType="RagEvaluation",
        evaluationConfig={
            "automated": {
                "datasetMetricConfigs": [{
                    "taskType": "QuestionAndAnswer",
                    "dataset": {"name": "atf",
                                "datasetLocation": {"s3Uri": dataset_s3}},
                    "metricNames": metric_names,
                }],
                "evaluatorModelConfig": {
                    "bedrockEvaluatorModels": [{"modelIdentifier": eval_model}]},
            }
        },
        inferenceConfig={"ragConfigs": []},
        outputDataConfig={"s3Uri": output_s3},
    )
    return resp
