import type { AgenticDiagnosis } from '../lib/api';

const statusLabel: Record<string, string> = {
  completed: 'Concluída', partial: 'Parcial', failed: 'Falhou',
  skipped: 'Não executado', budget_exhausted: 'Limite de consultas atingido',
};

export default function AgenticExecution({ data }: { data: AgenticDiagnosis }) {
  const names = new Map(data.explanation_graph.nodes.map((node) => [node.id, node.label]));
  const relationLabel: Record<string, string> = {
    supports: 'sustenta', contradicts: 'contradiz', blocks: 'bloqueia',
    selected: 'seleciona', considered: 'considera',
  };
  return <section id="agentic-execution" className="panel diagnosisSection">
    <div className="sectionLabel">Investigação agentic</div><h2>Execução dos especialistas</h2>
    <p className="muted">O planner escolhe onde investigar. O avaliador determinístico decide o que as evidências permitem concluir.</p>
    <dl className="diagnosisAlertGrid">
      <div><dt>Plano</dt><dd><code>{data.plan_id}</code></dd></div>
      <div><dt>Planejamento</dt><dd>{data.planner_origin === 'model-assisted' ? 'Assistido por modelo' : 'Determinístico'}</dd></div>
      <div><dt>Execução</dt><dd>{statusLabel[data.execution_status] ?? data.execution_status}</dd></div>
      <div><dt>Integridade no motor</dt><dd>{data.evidence_integrity_ok ? 'Verificada na execução' : 'Falhou na execução'}</dd></div>
    </dl>
    <p className="diagnosisNote">Conclusão da execução não comprova recuperação do serviço. Contexto persistente orienta a coleta e não substitui evidência atual.</p>
    <div className="agenticSpecialists">{data.specialists.map((item) => <article className="agenticSpecialist" key={item.task_id}>
      <div className="evidenceMeta"><strong>{item.specialist}</strong><span>{statusLabel[item.status] ?? item.status}</span></div>
      <p>{item.reason || 'Sem observação adicional registrada.'}</p>
      <small>{item.query_count} / {item.query_limit} consultas · {item.integrity_ok ? 'Integridade verificada' : 'Falha de integridade'}</small>
      {item.error_type && <p>Erro registrado: <code>{item.error_type}</code></p>}
      <details><summary>Horários da execução</summary><p>Início: <time dateTime={item.started_at}>{item.started_at}</time></p><p>Fim: <time dateTime={item.finished_at}>{item.finished_at}</time></p></details>
    </article>)}</div>
    <details className="agenticExplanation"><summary>Consultar relações da explicação do avaliador</summary>
      <p className="diagnosisNote">Estas relações explicam a decisão; são distintas da topologia entre serviços.</p>
      <ul>{data.explanation_graph.edges.map((edge, i) => <li key={i}>{names.get(edge.source) ?? edge.source} <strong>{relationLabel[edge.relationship] ?? edge.relationship}</strong> {names.get(edge.target) ?? edge.target}</li>)}</ul>
      {!data.explanation_graph.edges.length && <p className="muted">Sem relações de explicação registradas.</p>}
    </details>
  </section>;
}
