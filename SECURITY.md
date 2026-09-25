# Security Policy

## Supported versions

Security fixes target the latest released minor version.

| Version | Supported |
| --- | --- |
| 0.2.x | Yes |
| 0.1.x | No |

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Use GitHub's private vulnerability reporting feature when it is enabled for the repository.

## Security model

The investigator is designed to be read-only. Production credentials should only allow narrow query operations such as Kubernetes `get/list/watch`, Prometheus/Loki queries and read-only metadata retrieval from deployment systems.

The LLM layer must never receive infrastructure credentials or generic shell access. LLM-produced hypotheses remain unverified until deterministic tools collect supporting evidence.
