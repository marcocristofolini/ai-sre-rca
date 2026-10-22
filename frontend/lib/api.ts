import { headers } from 'next/headers';

export type GraphEdge = {
  source: string;
  target: string;
  relationship: string;
  metadata: Record<string, unknown>;
  evidence_ids: string[];
};

export type AgenticDiagnosis = {
  schema_version: 'ai-sre-rca.agentic-diagnosis.v1';
  plan_id: string;
  planner_origin: 'deterministic' | 'model-assisted';
  execution_status: 'completed' | 'partial' | 'failed';
  evidence_integrity_ok: boolean;
  started_at: string;
  finished_at: string;
  specialists: Array<{
    task_id: string; specialist: string; status: string;
    query_count: number; query_limit: number; integrity_ok: boolean;
    reason: string; error_type?: string | null;
    started_at: string; finished_at: string;
  }>;
  decisions: Array<{
    hypothesis_id: string; specialist: string; decision: string; reasons: string[];
  }>;
  explanation_graph: {
    nodes: Array<{ id: string; kind: string; label: string; status: string; metadata: Record<string, unknown> }>;
    edges: Array<{ source: string; target: string; relationship: string }>;
  };
};

export type Investigation = {
  agentic?: AgenticDiagnosis | null;
  incident_id: string;
  status: string;
  incident: {
    title: string;
    tenant_id: string;
    cluster: string;
    namespace?: string;
    pod?: string;
    node?: string;
    service?: string;
    severity: string;
    alert_name: string;
    starts_at?: string;
    ends_at?: string;
  };
  entities: Array<{
    id: string;
    kind: string;
    name: string;
    namespace?: string;
    relationship?: string;
    metadata: Record<string, unknown>;
  }>;
  graph_edges: GraphEdge[];
  evidence: Array<{
    id: string;
    source: string;
    category: string;
    summary: string;
    strength: string;
    facts: Record<string, unknown>;
    observed_at?: string;
    query?: string | null;
    raw_reference?: string | null;
    integrity_sha256?: string | null;
  }>;
  hypotheses: Array<{
    id: string;
    statement: string;
    status: string;
    origin: 'playbook' | 'llm';
    score: number;
    rationale: string;
    supporting_evidence: string[];
    contradicting_evidence: string[];
    required_checks: string[];
  }>;
  similar_incidents: Array<{
    incident_id: string;
    title: string;
    root_cause: string;
    similarity: number;
    occurred_at: string;
    cluster: string;
    namespace?: string;
  }>;
  timeline: Array<{ timestamp: string; source: string; message: string }>;
  root_cause: {
    hypothesis_id?: string | null;
    title: string;
    summary: string;
    evidence_strength: string;
    supporting_signals: number;
    contradicting_signals: number;
  };
  root_causes?: Array<{
    hypothesis_id?: string | null;
    title: string;
    summary: string;
    evidence_strength: string;
    supporting_signals: number;
    contradicting_signals: number;
  }>;
  remediation: Array<{ priority: string; action: string; reason: string; requires_approval?: boolean }>;
  created_at?: string;
  trace_analyses: Array<{
    trace_id: string;
    root_service: string;
    root_operation: string;
    duration_ms: number;
    error_count: number;
    services: string[];
    candidate_service?: string;
    candidate_operation?: string;
    candidate_instance?: string;
    candidate_reason?: string;
    candidate_strength: string;
    source_query?: string;
    causal_chain: Array<{
      service: string;
      operation: string;
      span_id: string;
      parent_span_id?: string;
      kind: string;
      status: string;
      duration_ms: number;
      role: 'origin' | 'propagated' | 'context';
      reason: string;
      metadata?: Record<string, unknown>;
    }>;
  }>;
  stateful_dependencies: Array<{
    dependency_id: string;
    kind: 'postgresql' | 'kafka' | 'redis';
    name: string;
    target?: string;
    health: 'healthy' | 'degraded' | 'critical' | 'unknown';
    causal_role: 'origin' | 'supporting' | 'context';
    cause?: string;
    cause_strength: 'weak' | 'medium' | 'strong';
    hypothesis_id?: string;
    evidence_ids: string[];
    signals: Array<{
      name: string;
      value?: number;
      unit?: string;
      status: 'normal' | 'warning' | 'critical' | 'unknown';
      threshold?: number;
      query?: string;
      description: string;
    }>;
    metadata: Record<string, unknown>;
  }>;
  query_budget: {
    enabled?: boolean;
    total_calls?: number;
    per_source?: Record<string, number>;
    denied?: Array<Record<string, unknown>>;
    elapsed_seconds?: number;
    limits?: Record<string, number>;
  };
};

export type CopilotAnswer = {
  answer: string;
  insufficient_evidence: boolean;
  evidence_ids: string[];
  mode: 'model' | 'deterministic';
};

export async function forwardedAuthHeaders(): Promise<Record<string, string>> {
  const incoming = await headers();
  const authorization = incoming.get('authorization');
  if (authorization) return { Authorization: authorization };

  const proxyHeader = process.env.AUTH_FORWARD_HEADER ?? 'x-auth-request-access-token';
  const proxiedToken =
    incoming.get(proxyHeader) ??
    incoming.get('x-forwarded-access-token');

  if (!proxiedToken) return {};
  return {
    Authorization: proxiedToken.toLowerCase().startsWith('bearer ')
      ? proxiedToken
      : `Bearer ${proxiedToken}`,
  };
}

export class IncidentFetchError extends Error {
  constructor(public readonly status: number) {
    super(`Incident API returned ${status}`);
  }
}

export async function fetchIncident(incidentId: string): Promise<Investigation> {
  const base = process.env.API_URL ?? 'http://localhost:8080';
  const res = await fetch(`${base}/api/v1/incidents/${encodeURIComponent(incidentId)}`, {
    cache: 'no-store',
    headers: await forwardedAuthHeaders(),
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) throw new IncidentFetchError(res.status);
  return res.json();
}

export async function fetchDemo(): Promise<Investigation> {
  const base = process.env.API_URL ?? 'http://localhost:8080';
  const res = await fetch(`${base}/api/v1/incidents/demo`, {
    cache: 'no-store',
    headers: await forwardedAuthHeaders(),
  });
  if (!res.ok) throw new Error(`API returned ${res.status}`);
  return res.json();
}


export async function fetchTraceDemo(): Promise<Investigation> {
  const base = process.env.API_URL ?? 'http://localhost:8080';
  const res = await fetch(`${base}/api/v1/incidents/demo/trace`, {
    cache: 'no-store',
    headers: await forwardedAuthHeaders(),
  });
  if (!res.ok) throw new Error(`API returned ${res.status}`);
  return res.json();
}


export type StatefulDependencyKind = 'postgresql' | 'kafka' | 'redis';

export async function fetchStatefulDemo(dependency: StatefulDependencyKind = 'postgresql'): Promise<Investigation> {
  const base = process.env.API_URL ?? 'http://localhost:8080';
  const res = await fetch(`${base}/api/v1/incidents/demo/stateful/${dependency}`, {
    cache: 'no-store',
    headers: await forwardedAuthHeaders(),
  });
  if (!res.ok) throw new Error(`API returned ${res.status}`);
  return res.json();
}


export type SystemIdentity = {
  subject: string;
  tenant_id: string;
  roles: string[];
  clusters: string[];
  auth_enabled: boolean;
  can_investigate?: boolean;
  agentic_execution_enabled?: boolean;
  demo_mode?: boolean;
};

export type CapabilityReport = {
  cluster: string;
  tenant_id: string;
  auth_enabled: boolean;
  notifications_enabled: boolean;
  persistence: string;
  generated_at: string;
  sources: Array<{
    source: string;
    status: 'healthy' | 'degraded' | 'unavailable' | 'disabled';
    detail: string;
    cluster?: string;
    latency_ms?: number;
  }>;
};

export async function fetchIdentity(): Promise<SystemIdentity> {
  const base = process.env.API_URL ?? 'http://localhost:8080';
  const res = await fetch(`${base}/api/v1/system/me`, {
    cache: 'no-store',
    signal: AbortSignal.timeout(15_000),
    headers: await forwardedAuthHeaders(),
  });
  if (!res.ok) throw new IncidentFetchError(res.status);
  return res.json();
}

export async function fetchCapabilities(cluster: string): Promise<CapabilityReport> {
  const base = process.env.API_URL ?? 'http://localhost:8080';
  const query = new URLSearchParams({ cluster });
  const res = await fetch(`${base}/api/v1/system/capabilities?${query.toString()}`, {
    cache: 'no-store',
    headers: await forwardedAuthHeaders(),
  });
  if (!res.ok) throw new Error(`API returned ${res.status}`);
  return res.json();
}

export type AwarenessFreshness = 'fresh' | 'stale' | 'historical' | 'tombstoned';

export type AwarenessEntity = {
  id: string;
  tenant_id: string;
  cluster: string;
  kind: string;
  name: string;
  namespace?: string | null;
  metadata: Record<string, unknown>;
  first_seen: string;
  last_seen: string;
  last_incident_id: string;
  observation_count: number;
  tombstoned_at?: string | null;
  tombstone_snapshot_id?: string | null;
  tombstone_source?: string | null;
  freshness: AwarenessFreshness;
  age_seconds: number;
  stale_since?: string | null;
  depth?: number | null;
};

export type AwarenessRelationship = {
  id: string;
  tenant_id: string;
  cluster: string;
  source_id: string;
  target_id: string;
  relationship: string;
  metadata: Record<string, unknown>;
  evidence_ids: string[];
  first_seen: string;
  last_seen: string;
  last_incident_id: string;
  observation_count: number;
  tombstoned_at?: string | null;
  tombstone_snapshot_id?: string | null;
  tombstone_source?: string | null;
  freshness: AwarenessFreshness;
  age_seconds: number;
  stale_since?: string | null;
};

export type AwarenessTopology = {
  tenant_id: string;
  cluster: string;
  entities: AwarenessEntity[];
  relationships: AwarenessRelationship[];
  stale_after_minutes?: number;
  generated_at: string;
  service_name?: string;
  namespace?: string | null;
  root?: AwarenessEntity;
  requested_depth?: number;
  effective_depth?: number;
  max_nodes?: number;
  max_edges?: number;
  truncated?: boolean;
};

export type AwarenessChangeEvent = {
  entity_id: string;
  tenant_id: string;
  cluster: string;
  namespace?: string | null;
  application: string;
  revision?: string | null;
  repository?: string | null;
  service_name?: string | null;
  workload_kind?: string | null;
  workload_name?: string | null;
  change_type: string;
  occurred_at: string;
  first_observed_at: string;
  last_observed_at: string;
  observation_count: number;
  last_incident_id: string;
  evidence_ids: string[];
  metadata: Record<string, unknown>;
};

export type AwarenessQuery = {
  cluster: string;
  namespace?: string;
  service?: string;
  tenantId?: string;
  depth?: number;
  limit?: number;
};

function awarenessQueryParams(query: AwarenessQuery): URLSearchParams {
  const params = new URLSearchParams({ cluster: query.cluster });
  if (query.namespace) params.set('namespace', query.namespace);
  if (query.tenantId) params.set('tenant_id', query.tenantId);
  return params;
}

export async function fetchAwarenessTopology(
  query: AwarenessQuery,
): Promise<AwarenessTopology> {
  const base = process.env.API_URL ?? 'http://localhost:8080';
  const params = awarenessQueryParams(query);
  let path = '/api/v1/awareness/topology';

  if (query.service) {
    path = `/api/v1/awareness/services/${encodeURIComponent(query.service)}`;
    params.set('depth', String(query.depth ?? 2));
  } else {
    params.set('limit', String(query.limit ?? 500));
  }

  const res = await fetch(`${base}${path}?${params.toString()}`, {
    cache: 'no-store',
    headers: await forwardedAuthHeaders(),
  });
  if (!res.ok) throw new Error(`Awareness API returned ${res.status}`);
  return res.json();
}

export async function fetchAwarenessChanges(
  query: AwarenessQuery,
): Promise<AwarenessChangeEvent[]> {
  const base = process.env.API_URL ?? 'http://localhost:8080';
  const params = awarenessQueryParams(query);
  if (query.service) params.set('service', query.service);
  params.set('limit', String(Math.min(query.limit ?? 100, 200)));

  const res = await fetch(
    `${base}/api/v1/awareness/changes?${params.toString()}`,
    {
      cache: 'no-store',
      headers: await forwardedAuthHeaders(),
    },
  );
  if (!res.ok) throw new Error(`Awareness changes API returned ${res.status}`);
  return res.json();
}



export type IncidentSummary = {
  incident_id: string; title: string; tenant_id: string; cluster: string;
  namespace?: string | null; status: string; severity: string; root_cause: string;
  investigation_mode: 'classic' | 'agentic'; created_at: string;
};
export type IncidentList = {
  items: IncidentSummary[]; limit: number; offset: number; next_offset?: number | null;
};

export async function fetchIncidents(params: URLSearchParams): Promise<IncidentList> {
  const base = process.env.API_URL ?? 'http://localhost:8080';
  const response = await fetch(`${base}/api/v1/incidents?${params}`, {
    cache: 'no-store', headers: await forwardedAuthHeaders(), signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new IncidentFetchError(response.status);
  return response.json();
}

export async function startInvestigation(input: Record<string, string>, mode: 'classic' | 'agentic'): Promise<{ id: string; kind: 'job' | 'incident' }> {
  const base = process.env.API_URL ?? 'http://localhost:8080';
  const path = mode === 'agentic' ? '/api/v1/planner/jobs' : '/api/v1/incidents/investigate';
  const response = await fetch(`${base}${path}`, {
    method: 'POST', cache: 'no-store',
    headers: { ...await forwardedAuthHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(input), signal: AbortSignal.timeout(180_000),
  });
  if (!response.ok) throw new IncidentFetchError(response.status);
  const result = await response.json();
  const id = mode === 'agentic' ? result.id : result.incident_id;
  if (typeof id !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(id)) {
    throw new Error('Invalid investigation identifier returned');
  }
  return { id, kind: mode === 'agentic' ? 'job' : 'incident' };
}
