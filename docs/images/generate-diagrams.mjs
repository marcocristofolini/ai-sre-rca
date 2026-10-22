// Editable SVG layout source. No dependencies, network access or embedded assets.
// Run from the repository root: node docs/images/generate-diagrams.mjs
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const directory = fileURLToPath(new URL('.', import.meta.url));
const escape = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
function diagram(name, title, description, height, draw) {
  let items = [];
  const text = (x, y, value, cls = '') => items.push(`<text x="${x}" y="${y}" class="${cls}">${escape(value)}</text>`);
  const box = (x, y, width, heading, lines, tone = 'blue', height = 100) => {
    items.push(`<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="10" class="${tone}"/>`);
    text(x + 18, y + 28, heading, 'heading');
    lines.forEach((line, index) => text(x + 18, y + 53 + index * 22, line));
  };
  const arrow = (points, dashed = false) => items.push(`<path d="${points}" class="arrow${dashed ? ' dashed' : ''}" marker-end="url(#arrow)"/>`);
  const rule = (y, label) => {
    items.push(`<path d="M 40 ${y} H 1080" class="divider"/>`);
    text(60, y + 28, label, 'label');
  };
  text(60, 42, title, 'title');
  draw({ box, arrow, text, rule });
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1120 ${height}" width="1120" role="img" aria-labelledby="title desc" lang="en">
<title id="title">${escape(title)}</title>
<desc id="desc">${escape(description)}</desc>
<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#475569"/></marker>
<style>svg{max-width:100%;height:auto;background:#f8fafc}text{font-family:Arial,Helvetica,sans-serif;font-size:16px;fill:#1e293b}.title{font-size:26px;font-weight:700}.heading{font-size:18px;font-weight:700}.label{font-size:15px;fill:#475569}.blue{fill:#eff6ff;stroke:#3b6795}.green{fill:#ecfdf5;stroke:#37775f}.amber{fill:#fffbeb;stroke:#92713c}.gray{fill:#f1f5f9;stroke:#64748b}rect{stroke-width:1.5}.arrow{fill:none;stroke:#475569;stroke-width:2}.dashed{stroke-dasharray:6 5}.divider{stroke:#94a3b8;stroke-dasharray:4 5}</style></defs>
<rect width="1120" height="${height}" fill="#f8fafc"/>
${items.join('\n')}
</svg>\n`;
  writeFileSync(directory + name + '.svg', svg);
}
diagram('ai-sre-rca-architecture', 'AI SRE RCA · System architecture',
  'Next.js sends authenticated requests to scoped FastAPI APIs. Persistent jobs run bounded read-only specialists against Kubernetes and telemetry. Verified evidence feeds deterministic evaluation and PostgreSQL persistence. Graph context informs jobs. Remediation and disposable live validation are separate boundaries.', 1010,
  ({ box, arrow, text, rule }) => {
    box(60, 80, 1000, 'Next.js · Investigation Workspace + Copilot', ['Saved incidents • scope selection • explicit confirmation • live SSE/replay • evidence citations'], 'blue', 80);
    box(60, 210, 1000, 'FastAPI · Authentication → RBAC → tenant / cluster authorization', ['Typed incident APIs • trusted inventory resolver • proposal compiler • bounded Q&A'], 'blue', 80);
    box(60, 350, 300, 'Persistent Orchestrator', ['Jobs • idempotency • leases', 'Worker in API lifespan', 'Progress persistence + SSE'], 'blue', 125);
    box(410, 350, 300, 'Read-only specialists', ['Kubernetes • metrics • logs', 'Traces → database • changes', 'Server-owned budgets'], 'blue', 125);
    box(760, 350, 300, 'Evidence → RCA', ['Hash / reference checks', 'Deterministic causal gates', 'Confirmed or inconclusive'], 'green', 125);
    box(410, 535, 300, 'Production source adapters', ['Kubernetes • Prometheus', 'Loki • Tempo / OTel', 'Argo CD • GitHub'], 'gray', 125);
    box(60, 720, 650, 'PostgreSQL / pgvector · scoped persistence', ['Diagnoses • evidence • jobs / progress • audit', 'Awareness Graph / inventory / history • incident memory'], 'green', 100);
    box(760, 720, 300, 'Separate remediation', ['Human approval + policy', 'Distinct role / credentials', 'Allow-listed scaling only'], 'amber', 125);
    arrow('M 560 160 V 210');
    arrow('M 210 290 V 350');
    arrow('M 360 410 H 410');
    arrow('M 710 410 H 760');
    arrow('M 560 475 V 535');
    text(575, 510, 'bounded reads', 'label');
    arrow('M 910 475 V 690 H 385 V 720');
    arrow('M 210 475 V 720');
    arrow('M 60 765 H 30 V 410 H 60', true);
    text(65, 620, 'Graph context', 'label');
    text(65, 644, 'feeds plans', 'label');
    rule(875, 'DISPOSABLE LOCAL VALIDATION · separate from production runtime');
    box(60, 920, 1000, 'Kind + instrumented Deployment / StatefulSet + existing telemetry stack', ['Controlled faults / recovery → production read adapters → local reports; no production-cluster access'], 'gray', 75);
  });
diagram('investigation-pipeline', 'AI SRE RCA · Investigation and evidence lifecycle',
  'Authorize an incident, resolve verified inventory and review a typed proposal. Explicit confirmation precedes persistent job creation. Bounded read-only specialists collect evidence; integrity and deterministic gates decide the diagnosis. Saved results support authorized Q&A. Conflicts or unavailable required telemetry reject submission; missing causal evidence remains inconclusive.', 1000,
  ({ box, arrow, text, rule }) => {
    box(60, 95, 300, '1 · Authorize', ['Saved incident + actor', 'Tenant / cluster / role']);
    box(410, 95, 300, '2 · Resolve scope', ['Inventory UIDs + ownership', 'Telemetry identity + window']);
    box(760, 95, 300, '3 · Review proposal', ['Typed scope + query limits', 'No job created yet']);
    box(760, 280, 300, '4 · Explicit confirmation', ['Reauthorize + recompile', 'Reject changed proposal'], 'amber');
    box(410, 280, 300, '5 · Persist job', ['Idempotency + queue / lease', 'Worker revalidates scope']);
    box(60, 280, 300, '6 · Read-only collection', ['Specialists + global limits', 'Live SSE / replay to UI']);
    box(60, 465, 300, '7 · Verify evidence', ['Hashes + cited references', 'UID / trace identity checks'], 'green');
    box(410, 465, 300, '8 · Evaluate RCA', ['Playbooks + causal gates', 'Context is not causal proof'], 'green');
    box(760, 465, 300, '9 · Save diagnosis', ['Cause or inconclusive', 'Evidence + graph + audit'], 'green');
    arrow('M 360 145 H 410'); arrow('M 710 145 H 760');
    arrow('M 910 195 V 280'); arrow('M 760 330 H 710'); arrow('M 410 330 H 360');
    arrow('M 210 380 V 465'); arrow('M 360 515 H 410'); arrow('M 710 515 H 760');
    box(60, 650, 1000, 'Authorized follow-up in the Copilot conversation', ['Completed investigation only • verified citation IDs • explicit insufficient_evidence state', 'Optional bounded LLM response or deterministic fallback; never changes the saved verdict'], 'blue', 100);
    arrow('M 910 565 V 650');
    rule(795, 'FAILURE BEHAVIOR');
    box(60, 845, 1000, 'Reject unsafe scope; preserve uncertainty', ['Stale / ambiguous / changed inventory → 409; invalid time bounds → 422', 'Required telemetry / budget unavailable → 503 before job; incomplete causal support → no confirmed cause', 'Recovery / zero replicas never manufacture missing Pod telemetry or a root cause'], 'amber', 130);
  });
diagram('security-boundaries', 'AI SRE RCA · Authorization, trust and remediation',
  'Untrusted users, logs and model text cannot grant permissions. FastAPI validates identity and tenant/cluster scope. Read-only source credentials and bounded evidence collection feed deterministic RCA and scoped persistence. Remediation uses a separate service, exact role, credentials, approval and policy. Disposable validation alone injects faults.', 1090,
  ({ box, arrow, text, rule }) => {
    box(60, 90, 470, 'Untrusted requests + model output', ['Questions / identifiers are not permissions', 'Model prose cannot confirm a cause'], 'gray');
    box(590, 90, 470, 'Untrusted source content', ['Logs / traces may contain injected instructions', 'Hashes protect integrity, not source truth'], 'gray');
    box(60, 250, 1000, 'Trusted server boundary · OIDC + RBAC + tenant / cluster authorization', ['Authorize saved incident and job access • resolve inventory identity • compile only allow-listed schemas', 'Bound query cost and context • delimit untrusted data • verify every returned citation'], 'blue', 110);
    arrow('M 295 190 V 250'); arrow('M 825 190 V 250');
    box(60, 425, 300, 'Read-only credentials', ['Cluster-specific adapters', 'UID / telemetry identity', 'No shell or mutation'], 'blue', 125);
    box(410, 425, 300, 'Deterministic authority', ['Hash / reference verification', 'Independent causal gates', 'Missing support → uncertain'], 'green', 125);
    box(760, 425, 300, 'Scoped persistence', ['PostgreSQL / pgvector', 'Graph + memory are context', 'Audit + jobs + evidence'], 'green', 125);
    arrow('M 210 360 V 425'); arrow('M 560 360 V 425'); arrow('M 910 360 V 425');
    rule(580, 'SEPARATE MUTATION BOUNDARY · disabled by default; no Copilot execution path');
    box(60, 635, 300, 'Human review + approval', ['Typed remediation plan', 'Distinct approver by default'], 'amber', 120);
    box(410, 635, 300, 'Remediation service', ['Exact remediator role', 'Policy + dry-run + TTL', 'Drift checks + audit'], 'amber', 120);
    box(760, 635, 300, 'Separate write credentials', ['Allow-listed scale_deployment', 'Explicit rollback', 'No arbitrary exec / patch'], 'amber', 120);
    arrow('M 360 695 H 410'); arrow('M 710 695 H 760');
    rule(820, 'VALIDATION BOUNDARY · disposable resources only');
    box(60, 875, 1000, 'Local Kind harness · controlled failure injection and evidence collection', ['Reuses existing instrumented workloads and Prometheus / Loki / Tempo stack', 'Creates scoped test credentials and reports; cleanup only owns created resources', 'Local results are not production reliability or release certification'], 'gray', 125);
    text(60, 1045, 'Source access and trustworthy telemetry labels remain operational prerequisites.', 'label');
  });
