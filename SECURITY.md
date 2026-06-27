# Security Policy

## Supported versions

The latest `1.x` release on the default branch receives security fixes.

| Version | Supported |
|---------|-----------|
| 1.x     | ✅        |
| < 1.0   | ❌        |

## Reporting a vulnerability

Please report security issues **privately** to **info@ajace.com** with details
and reproduction steps. Do **not** open a public GitHub issue for
security-sensitive reports. We aim to acknowledge within 3 business days and to
provide a remediation timeline after triage.

## Secrets handling

- **No API keys or credentials are stored in the repository.** Provide them via
  environment variables (see [`.env.example`](.env.example)) or the in-app key
  entry (Configuration tab → `POST /api/key`, kept in memory only).
- `.env`, `storage/`, and local data directories are git-ignored.
- In non-local profiles (`hybrid`, `aws`) the HTTP API **requires** a bearer
  token (`ATF_API_TOKEN`); the server refuses to start without it when CORS is
  open.

## Data handling

- Original source files are processed locally; in the document-preview feature
  they never leave the host.
- Model inference calls go to the configured provider (OpenRouter or AWS
  Bedrock). Review your provider's data-handling terms before sending sensitive
  corpora, and prefer the AWS-native (Bedrock) profile with Guardrails for
  regulated data.
- Optional content guardrails (PII redaction, denied terms, Bedrock Guardrails +
  Automated Reasoning) and a grounding-verification gate are available via
  configuration.
