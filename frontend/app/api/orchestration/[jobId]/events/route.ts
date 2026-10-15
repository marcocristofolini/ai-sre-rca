import { forwardedAuthHeaders } from '../../../../../lib/api';

export async function GET(request: Request, { params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = await params;
  if (!/^[a-zA-Z0-9_-]{1,128}$/.test(jobId)) return Response.json({ detail: 'Not found' }, { status: 404 });
  const base = process.env.API_URL ?? 'http://localhost:8080';
  const url = new URL(`${base}/api/v1/planner/jobs/${encodeURIComponent(jobId)}/events`);
  const after = new URL(request.url).searchParams.get('after');
  if (after) url.searchParams.set('after', after);
  const headers = await forwardedAuthHeaders();
  const lastEventId = request.headers.get('last-event-id');
  if (lastEventId) headers['Last-Event-ID'] = lastEventId;
  const upstream = await fetch(url, { headers, cache: 'no-store', signal: request.signal });
  return new Response(upstream.body, { status: upstream.status, headers: {
    'Content-Type': upstream.headers.get('content-type') ?? 'text/event-stream',
    'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no',
  } });
}
