import { forwardedAuthHeaders } from '../../../../lib/api';
import type { Investigation } from '../../../../lib/api';

// Return only the authorized citation context needed by the conversation.
export async function GET(request: Request, { params }: { params: Promise<{ incidentId: string }> }) {
  const { incidentId } = await params;
  if (!/^[a-zA-Z0-9_-]{1,128}$/.test(incidentId)) return Response.json({ detail: 'Not found' }, { status: 404 });
  try {
    const upstream = await fetch(`${process.env.API_URL ?? 'http://localhost:8080'}/api/v1/incidents/${encodeURIComponent(incidentId)}`, { headers: await forwardedAuthHeaders(), cache: 'no-store', signal: request.signal });
    if (!upstream.ok) return Response.json({ detail: 'Investigation unavailable' }, { status: upstream.status });
    const result = await upstream.json() as Investigation;
    return Response.json({ incident_id: result.incident_id, evidence_ids: result.evidence.map((item) => item.id) });
  } catch { return Response.json({ detail: 'Investigation service unavailable' }, { status: 503 }); }
}
