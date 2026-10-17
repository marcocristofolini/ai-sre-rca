'use client';

import { useEffect, useRef, useState } from 'react';

type ProgressItem = { id: number; type: string; payload: Record<string, unknown> };
const eventTypes = ['queued', 'started', 'plan_ready', 'specialist_update', 'evaluation', 'completed', 'failed', 'replay_gap'];

export default function OrchestrationProgress({ jobId, onCompleted, onFailed, verifiedEvidenceOnly = false }: { jobId: string; onCompleted?: (incidentId: string) => void; onFailed?: () => void; verifiedEvidenceOnly?: boolean }) {
  const [events, setEvents] = useState<ProgressItem[]>([]);
  const [status, setStatus] = useState('queued');
  const [connected, setConnected] = useState(false);
  const [fallback, setFallback] = useState(false);
  const [sourceMode, setSourceMode] = useState<'demo' | 'live'>('live');
  const cursor = useRef(0);
  const connectedRef = useRef(false);

  useEffect(() => {
    let alive = true;
    const accept = (type: string, id: number, payload: Record<string, unknown>) => {
      if (!alive) return;
      if (id > 0) cursor.current = Math.max(cursor.current, id);
      if (type === 'completed' || type === 'failed') {
        setStatus(type);
        source.close();
      } else if (type === 'started') setStatus('running');
      setEvents((previous) => id > 0 && previous.some((item) => item.id === id)
        ? previous : [...previous, { id, type, payload }].slice(-200));
    };
    const source = new EventSource(`/api/orchestration/${encodeURIComponent(jobId)}/events`);
    source.onopen = () => { connectedRef.current = true; setConnected(true); setFallback(false); };
    source.onerror = () => { connectedRef.current = false; setConnected(false); setFallback(true); };
    for (const type of eventTypes) source.addEventListener(type, (raw) => {
      const event = raw as MessageEvent<string>;
      try { accept(type, Number(event.lastEventId), JSON.parse(event.data) as Record<string, unknown>); }
      catch { /* Ignore malformed progress frames; status polling remains available. */ }
    });

    const poll = async () => {
      try {
        const response = await fetch(`/api/orchestration/${encodeURIComponent(jobId)}/progress?after=${cursor.current}`, { cache: 'no-store' });
        if (!response.ok) return;
        const value = await response.json() as { job: { status: string }; source_mode?: 'demo' | 'live'; events: Array<{ sequence: number; event_type: string; payload: Record<string, unknown> }> };
        if (!alive) return;
        if (value.source_mode) setSourceMode(value.source_mode);
        setStatus(value.job.status);
        for (const event of value.events) accept(event.event_type, event.sequence, event.payload);
        if (['completed', 'failed', 'cancelled'].includes(value.job.status)) source.close();
      } catch { /* The SSE connection retries automatically. */ }
    };
    const timer = window.setInterval(() => { if (!connectedRef.current) void poll(); }, 1500);
    void poll();
    return () => { alive = false; source.close(); window.clearInterval(timer); };
  }, [jobId]);

  const specialists = events.filter((item) => item.type === 'specialist_update');
  const latestByAgent = new Map<string, ProgressItem>();
  for (const item of specialists) if (typeof item.payload.specialist === 'string') latestByAgent.set(item.payload.specialist, item);
  const evidence = specialists.filter((item) => !verifiedEvidenceOnly || item.payload.integrity_ok === true).flatMap((item) => Array.isArray(item.payload.evidence) ? item.payload.evidence as Array<Record<string, unknown>> : []);
  const hypotheses = specialists.flatMap((item) => Array.isArray(item.payload.hypotheses) ? item.payload.hypotheses as Array<Record<string, unknown>> : []);
  const evaluation = [...events].reverse().find((item) => item.type === 'evaluation');
  const incidentValue = [...events].reverse().find((item) => item.type === 'completed')?.payload.incident_id;
  const terminalIncident = typeof incidentValue === 'string' ? incidentValue : null;

  useEffect(() => { if (terminalIncident) onCompleted?.(terminalIncident); }, [terminalIncident, onCompleted]);

  useEffect(() => { if (status === 'failed' || status === 'cancelled') onFailed?.(); }, [status, onFailed]);

  return <section className="panel orchestrationProgress" aria-live="polite">
    <div className="sectionLabel">{sourceMode === 'demo' ? 'DEMO · DADOS SINTÉTICOS' : 'INVESTIGAÇÃO REAL · TELEMETRIA DAS FONTES CONFIGURADAS'}</div>
    <h2>Progresso da investigação</h2>
    <p className="muted">Coleta somente leitura. Hipóteses parciais são consultivas; apenas o avaliador determinístico pode selecionar a conclusão.</p>
    <div className="orchestrationStatus"><strong>{status === 'running' ? 'Em andamento' : status === 'completed' ? 'Concluída' : status === 'failed' ? 'Falhou' : 'Na fila'}</strong><span>{connected ? 'Fluxo ao vivo' : fallback ? 'Reconectando · consulta alternativa ativa' : 'Conectando'}</span></div>
    {terminalIncident && <a className="diagnosisTextLink" href={`/incidents/${encodeURIComponent(String(terminalIncident))}?jobId=${encodeURIComponent(jobId)}`}>Abrir investigação concluída →</a>}
    {status === 'failed' && <p role="alert">A investigação falhou. Consulte os eventos e tente novamente após verificar as fontes configuradas.</p>}
    {!!latestByAgent.size && <div className="orchestrationAgents">{[...latestByAgent].map(([name, item]) => <article key={name}><strong>{name}</strong><span>{String(item.payload.status)}</span><small>{String(item.payload.query_count ?? 0)} / {String(item.payload.query_limit ?? 0)} consultas · integridade {item.payload.integrity_ok === true ? 'verificada' : 'não verificada'}</small></article>)}</div>}
    {!!evidence.length && <div><h3>Evidências recebidas ({evidence.length})</h3><ul className="orchestrationEvidence">{evidence.slice(-30).map((item, i) => <li key={`${String(item.id)}-${i}`}><strong>{String(item.source)} · {String(item.category)}</strong><p>{String(item.summary)}</p>{verifiedEvidenceOnly && <small>{String(item.id)} · integridade verificada</small>}</li>)}</ul></div>}
    {!!hypotheses.length && <div><h3>Hipóteses consultivas</h3><ul>{hypotheses.slice(-20).map((item, i) => <li key={`${String(item.id)}-${i}`}>{String(item.statement)} <small>· proposta, ainda sem decisão</small></li>)}</ul></div>}
    {evaluation && <div className="orchestrationEvaluation"><h3>Avaliação determinística</h3><p>Status: {String(evaluation.payload.status)}</p>{Array.isArray(evaluation.payload.decisions) && <ul>{(evaluation.payload.decisions as Array<Record<string, unknown>>).map((decision, i) => <li key={i}><strong>{String(decision.decision)}</strong> · {String(decision.hypothesis_id)}{Array.isArray(decision.reasons) && <span> — {(decision.reasons as string[]).join('; ')}</span>}</li>)}</ul>}</div>}
    {!!events.length && <details><summary>Linha do tempo ({events.length} eventos)</summary><ol>{events.map((item) => <li key={item.id || `${item.type}-gap`}>#{item.id || 'replay'} · {item.type} {item.type === 'replay_gap' && '(parte do histórico expirou)'}</li>)}</ol></details>}
  </section>;
}
