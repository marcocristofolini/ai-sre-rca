import { NextRequest, NextResponse } from 'next/server';
import { forwardedAuthHeaders } from '../../../../../lib/api';

export async function POST(request: NextRequest, { params }: { params: Promise<{ incidentId: string }> }) {
  const { incidentId } = await params;
  if (!/^[a-zA-Z0-9_-]{1,128}$/.test(incidentId)) return NextResponse.json({ detail: 'invalid incident id' }, { status: 400 });
  const body = await request.json().catch(() => null);
  const upstream = await fetch(`${process.env.API_URL ?? 'http://localhost:8080'}/api/v1/incidents/${encodeURIComponent(incidentId)}/copilot`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...await forwardedAuthHeaders() }, body: JSON.stringify(body), cache: 'no-store', signal: request.signal,
  });
  return new NextResponse(upstream.body, { status: upstream.status, headers: { 'Content-Type': upstream.headers.get('Content-Type') ?? 'application/json' } });
}
