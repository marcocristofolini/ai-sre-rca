# Tracecroft AI — Visual and product identity

Tracecroft AI is the public product identity for the open-source AI SRE RCA engineering project. The GitHub repository retains `ai-sre-rca` for clone URLs, package compatibility, commit history and existing documentation.

## Wordmark and symbol
The circuit-inspired T joins several branching infrastructure traces into one evidenced finding. It is not a detective face, fedora or copy of another vendor's mark.

- `frontend/public/tracecroft-mark.svg` — primary scalable logo symbol
- `frontend/app/icon.svg` — Next.js favicon
- `website/assets/tracecroft-mark.svg` — self-hosted static website identity
- `website/favicon.svg` — website favicon

The SVGs are authored as editable vector graphics. A generated exploratory raster concept was used for art direction, not shipped as production source. Brand text uses `Tracecroft AI`; lowercase `tracecroft` is appropriate for a logotype.

## Design tokens
| Token | Hex | Purpose |
| --- | --- | --- |
| Canvas | `#070C17` | Near-black, avoids flat pure black |
| Surface | `#101827` | Cards and investigation panels |
| Raised | `#151F31` | Toolbars, modal and hovered controls |
| Border | `#263247` | Quiet panel separation |
| Text | `#F1F7FF` | Titles and primary text |
| Muted | `#9BACBF` | Captions and secondary text |
| Trace cyan | `#3EE4D7` | Verified evidence and interaction focus |
| Violet | `#A492FC` | Agentic and trace accents |
| Green | `#83E6BD` | Confirmed safe status |
| Warning | `#F3C876` | Caution, not confirmation |
| Danger | `#FF7E93` | Failed checks |

Use system fonts and robust monospace styling for evidence IDs, queries, UIDs and hashes. Accessibility, keyboard focus, readable contrast and reduced-motion behavior take precedence over decoration. Use concise source-grounded copy.

## Brand message
**Follow the evidence. Find the cause.**

Supporting line: Evidence-first root cause investigation for Kubernetes and distributed systems.

Do not promise autonomous production remediation, general production accuracy, zero downtime, SOC 2 certification or guaranteed MTTR reductions. This is an engineering release candidate with reproducible controlled tests.

## Architecture and product
The static [public website](../website/) is a self-contained bilingual presentation with real browser screenshots and the original Archify diagram. The [application](../frontend/) retains its deterministic APIs, validation and authorization model. Branding changes may not alter causal authority, tenant isolation, telemetry provenance, job budgets or human approval.

Reference inspiration: the information architecture of modern SRE sites, including sherlocks.ai, without reproducing another company's graphics, layout source, copy, customer references or product claims.
