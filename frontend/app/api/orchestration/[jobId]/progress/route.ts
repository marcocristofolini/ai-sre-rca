import { forwardedAuthHeaders } from '../../../../../lib/api';

export async function GET(request: Request, { params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = await params;
  if (!/^[a-zA-Z0-9_-]{1,128}$/.test(jobId)) return Response.json({ detail: 'Not found' }, { status: 404 });
  const incoming = new URL(request.url);
  const url = new URL(`${process.env.API_URL ?? 'http://localhost:8080'}/api/v1/planner/jobs/${encodeURIComponent(jobId)}/progress`);
  const after = incoming.searchParams.get('after');
  if (after) url.searchParams.set('after', after);
  const upstream = await fetch(url, { headers: await forwardedAuthHeaders(), cache: 'no-store', signal: request.signal });
  return Response.json(await upstream.json(), { status: upstream.status });
}
