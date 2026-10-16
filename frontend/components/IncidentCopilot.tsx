'use client';

import { useState } from 'react';
import type { CopilotAnswer } from '../lib/api';

export default function IncidentCopilot({ incidentId, evidenceIds }: { incidentId: string; evidenceIds: string[] }) {
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<CopilotAnswer | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!question.trim() || busy) return;
    setBusy(true); setError('');
    try {
      const response = await fetch(`/api/incidents/${encodeURIComponent(incidentId)}/copilot`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: question.trim() }) });
      if (!response.ok) throw new Error('request failed');
      const result = await response.json() as CopilotAnswer;
      const allowed = new Set(evidenceIds);
      setAnswer({ ...result, evidence_ids: result.evidence_ids.filter((id) => allowed.has(id)) });
    }
    catch { setError('Não foi possível consultar o Copilot com esta sessão.'); }
    finally { setBusy(false); }
  }
  return (
    <section className="panel diagnosisSection" aria-labelledby="copilot-title">
      <div className="sectionLabel">COPILOT · SOMENTE LEITURA</div><h2 id="copilot-title">Pergunte sobre esta investigação</h2>
      <form onSubmit={submit} className="copilotForm"><label htmlFor="copilot-question">Pergunta</label><div><input id="copilot-question" value={question} maxLength={500} onChange={(e) => setQuestion(e.target.value)} placeholder="Quais evidências sustentam o diagnóstico?" /><button type="submit" disabled={busy || !question.trim()}>{busy ? 'Consultando…' : 'Perguntar'}</button></div></form>
      {error && <p role="alert" className="muted">{error}</p>}
      {answer && <div className="copilotAnswer" aria-live="polite"><p>{answer.answer}</p><p className="muted">{answer.insufficient_evidence ? 'Evidência insuficiente' : 'Resposta baseada nos dados salvos'} · {answer.mode === 'model' ? 'modelo consultivo' : 'resposta determinística'}</p>{!!answer.evidence_ids.length && <p>Evidências: {answer.evidence_ids.map((id) => <a key={id} href={`#evidence-${encodeURIComponent(id)}`}>{id}</a>)}</p>}</div>}
    </section>
  );
}
