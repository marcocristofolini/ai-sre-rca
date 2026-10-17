'use client';

import { useCallback, useEffect, useState } from 'react';
import type { CopilotAnswer } from '../lib/api';
import OrchestrationProgress from './OrchestrationProgress';

type Proposal = { proposal_id: string; question: string; incident: { title: string; tenant_id: string; cluster: string; namespace?: string; pod?: string; node?: string; service?: string; starts_at: string }; scope_notice: string };

export default function IncidentCopilot({ incidentId, evidenceIds }: { incidentId: string; evidenceIds: string[] }) {
  const [activeIncident, setActiveIncident] = useState(incidentId);
  const [activeEvidence, setActiveEvidence] = useState(evidenceIds);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [jobId, setJobId] = useState('');
  const [completed, setCompleted] = useState(false);
  const [jobFailed, setJobFailed] = useState(false);
  const [completionTarget, setCompletionTarget] = useState('');
  const pending = !!jobId && !completed && !jobFailed;
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<CopilotAnswer | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => setHydrated(true), []);
  const loadCompleted = useCallback(async (id: string) => {
    setCompletionTarget(id);
    try {
      const response = await fetch(`/api/incidents/${encodeURIComponent(id)}`, { cache: 'no-store' });
      if (!response.ok) throw new Error('context unavailable');
      const context = await response.json() as { incident_id: string; evidence_ids: string[] };
      if (context.incident_id !== id) throw new Error('context mismatch');
      setActiveIncident(id); setActiveEvidence(context.evidence_ids); setCompleted(true); setError('');
    } catch { setError('Não foi possível carregar a investigação concluída.'); }
  }, []);
  const failJob = useCallback(() => {
    setJobFailed(true); setError('A investigação falhou. As perguntas continuam usando a última investigação autorizada.');
  }, []);
  async function propose() {
    if (busy || pending || !question.trim()) return;
    setBusy(true); setError(''); setProposal(null);
    try {
      const response = await fetch(`/api/incidents/${encodeURIComponent(activeIncident)}/copilot/proposals`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: question.trim() }) });
      if (!response.ok) throw new Error('proposal failed');
      setProposal({ ...await response.json(), question: question.trim() });
    } catch { setError('Não foi possível propor uma investigação com esta sessão.'); }
    finally { setBusy(false); }
  }
  async function confirm() {
    if (!proposal || busy || pending) return;
    setBusy(true); setError('');
    try {
      const response = await fetch(`/api/incidents/${encodeURIComponent(activeIncident)}/copilot/confirm`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: proposal.question, proposal_id: proposal.proposal_id, confirmed: true }) });
      if (!response.ok) throw new Error('confirmation failed');
      const job = await response.json() as { id: string; status: string };
      setJobId(job.id); setCompleted(false); setJobFailed(job.status === 'failed' || job.status === 'cancelled'); setCompletionTarget(''); setProposal(null); setAnswer(null);
    } catch { setError('Não foi possível confirmar. Tente novamente com a mesma proposta para evitar duplicação.'); }
    finally { setBusy(false); }
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!question.trim() || busy || pending) return;
    setBusy(true); setError('');
    try {
      const response = await fetch(`/api/incidents/${encodeURIComponent(activeIncident)}/copilot`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: question.trim() }) });
      if (!response.ok) throw new Error('request failed');
      const result = await response.json() as CopilotAnswer;
      const allowed = new Set(activeEvidence);
      setAnswer({ ...result, evidence_ids: result.evidence_ids.filter((id) => allowed.has(id)) });
    }
    catch { setError('Não foi possível consultar o Copilot com esta sessão.'); }
    finally { setBusy(false); }
  }
  return (
    <section className="panel diagnosisSection" aria-labelledby="copilot-title" data-copilot-hydrated={hydrated ? 'true' : 'false'}>
      <div className="sectionLabel">COPILOT · SOMENTE LEITURA</div><h2 id="copilot-title">Pergunte sobre esta investigação</h2>
      <form onSubmit={submit} className="copilotForm"><label htmlFor="copilot-question">Pergunta</label><div><input id="copilot-question" value={question} maxLength={500} onChange={(e) => { setQuestion(e.target.value); setProposal(null); }} placeholder="Quais evidências sustentam o diagnóstico?" /><button type="submit" disabled={busy || pending || !question.trim()}>{busy ? 'Consultando…' : 'Perguntar'}</button></div></form>
      <button type="button" onClick={propose} disabled={busy || pending || question.trim().length < 3}>Propor investigação</button>
      {proposal && <div className="copilotProposal" aria-live="polite"><h3>Revisar investigação</h3><p>{proposal.incident.title}</p><p>Tenant: {proposal.incident.tenant_id} · Cluster: {proposal.incident.cluster}</p><p>Alvo: {[proposal.incident.namespace, proposal.incident.pod, proposal.incident.node, proposal.incident.service].filter(Boolean).join(' · ') || 'cluster'} · Início: {proposal.incident.starts_at}</p><p>Somente o alvo e a janela da investigação salva serão consultados. Coleta somente leitura; conclusão pelo motor determinístico. Pedidos idênticos reutilizam a mesma execução.</p><button type="button" onClick={confirm} disabled={busy}>Confirmar investigação</button><button type="button" onClick={() => setProposal(null)} disabled={busy}>Cancelar proposta</button></div>}
      {jobId && <OrchestrationProgress key={jobId} jobId={jobId} onCompleted={loadCompleted} onFailed={failJob} verifiedEvidenceOnly />}
      {completionTarget && !completed && <button type="button" onClick={() => loadCompleted(completionTarget)}>Tentar carregar contexto</button>}
      {completed && <p role="status">Investigação concluída. As próximas perguntas usam as evidências desta investigação.</p>}
      {error && <p role="alert" className="muted copilotError">{error}</p>}
      {answer && <div className="copilotAnswer" aria-live="polite"><p>{answer.answer}</p><p className="muted">{answer.insufficient_evidence ? 'Evidência insuficiente' : 'Resposta baseada nos dados salvos'} · {answer.mode === 'model' ? 'modelo consultivo' : 'resposta determinística'}</p>{!!answer.evidence_ids.length && <p>Evidências: {answer.evidence_ids.map((id) => <a key={id} href={`${activeIncident === incidentId ? "" : `/incidents/${encodeURIComponent(activeIncident)}`}#evidence-${encodeURIComponent(id)}`}>{id}</a>)}</p>}</div>}
    </section>
  );
}
