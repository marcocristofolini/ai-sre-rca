import { forwardedAuthHeaders } from '../../../../../../lib/api';

export async function POST(request: Request, { params }: { params: Promise<{ incidentId: string; action: string }> }) {
  const { incidentId, action } = await params;
  if (!/^[a-zA-Z0-9_-]{1,128}$/.test(incidentId) || !['proposals', 'confirm'].includes(action)) return Response.json({ detail: 'Not found' }, { status: 404 });
  const body = await request.json().catch(() => null);
  try {
    const upstream = await fetch(`${process.env.API_URL ?? 'http://localhost:8080'}/api/v1/incidents/${encodeURIComponent(incidentId)}/copilot/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...await forwardedAuthHeaders() }, body: JSON.stringify(body), cache: 'no-store', signal: request.signal,
    });
    return new Response(upstream.body, { status: upstream.status, headers: { 'Content-Type': 'application/json' } });
  } catch { return Response.json({ detail: 'Investigation service unavailable' }, { status: 503 }); }
}

export async function GET(request: Request, { params }: { params: Promise<{ incidentId: string; action: string }> }) {
  const { incidentId, action } = await params;
  if (!/^[a-zA-Z0-9_-]{1,128}$/.test(incidentId) || action !== 'scope-options') return Response.json({ detail: 'Not found' }, { status: 404 });
  const url = new URL(`${process.env.API_URL ?? 'http://localhost:8080'}/api/v1/incidents/${encodeURIComponent(incidentId)}/copilot/scope-options`);
  const cluster = new URL(request.url).searchParams.get('cluster');
  if (cluster) url.searchParams.set('cluster', cluster);
  try {
    const upstream = await fetch(url, { headers: await forwardedAuthHeaders(), cache: 'no-store', signal: request.signal });
    return new Response(upstream.body, { status: upstream.status, headers: { 'Content-Type': 'application/json' } });
  } catch { return Response.json({ detail: 'Inventory service unavailable' }, { status: 503 }); }
}
