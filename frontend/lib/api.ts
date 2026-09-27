import { headers } from 'next/headers';

export type GraphEdge = {
  source: string;
  target: string;
  relationship: string;
  metadata: Record<string, unknown>;
  evidence_ids: string[];
};

export type Investigation = {
  incident_id: string;
  status: string;
  incident: {
    title: string;
    cluster: string;
    namespace?: string;
    pod?: string;
    node?: string;
    service?: string;
    severity: string;
    alert_name: string;
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
    title: string;
    summary: string;
    evidence_strength: string;
    supporting_signals: number;
    contradicting_signals: number;
  };
  remediation: Array<{ priority: string; action: string; reason: string }>;
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
};

async function forwardedAuthHeaders(): Promise<Record<string, string>> {
  const incoming = await headers();
  const authorization = incoming.get('authorization');
  return authorization ? { Authorization: authorization } : {};
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
