export type Investigation = {
  incident_id: string;
  status: string;
  incident: {
    title: string;
    cluster: string;
    namespace?: string;
    pod?: string;
    node?: string;
    severity: string;
    alert_name: string;
  };
  entities: Array<{ id: string; kind: string; name: string; namespace?: string; relationship?: string }>;
  evidence: Array<{ id: string; source: string; category: string; summary: string; strength: string; facts: Record<string, unknown> }>;
  hypotheses: Array<{ id: string; statement: string; status: string; score: number; rationale: string; supporting_evidence: string[]; contradicting_evidence: string[] }>;
  timeline: Array<{ timestamp: string; source: string; message: string }>;
  root_cause: { title: string; summary: string; evidence_strength: string; supporting_signals: number; contradicting_signals: number };
  remediation: Array<{ priority: string; action: string; reason: string }>;
};

export async function fetchDemo(): Promise<Investigation> {
  const base = process.env.API_URL ?? 'http://localhost:8080';
  const res = await fetch(`${base}/api/v1/incidents/demo`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`API returned ${res.status}`);
  return res.json();
}
