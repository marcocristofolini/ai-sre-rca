import type { Investigation } from '../lib/api';
import IncidentGraph from './IncidentGraph';

type Evidence = Investigation['evidence'][number];
type StatefulSignal = Investigation['stateful_dependencies'][number]['signals'][number];

const statusLabel: Record<string, string> = {
  confirmed: 'Confirmada', ruled_out: 'Descartada', testing: 'Em análise',
  proposed: 'Proposta', inconclusive: 'Inconclusiva',
};
const strengthLabel: Record<string, string> = { strong: 'Forte', medium: 'Moderada', weak: 'Fraca' };
const statusSymbol: Record<string, string> = { confirmed: '✓', ruled_out: '×', testing: '…', proposed: '?', inconclusive: '?' };

function dateLabel(value?: string): string {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Não informado';
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short', timeStyle: 'medium', timeZone: 'UTC',
  }).format(new Date(value)) + ' UTC';
}

function formatSignalValue(signal: StatefulSignal): string {
  if (signal.value === undefined || signal.value === null || !Number.isFinite(signal.value)) return 'n/a';
  if (signal.unit === 'ratio') return `${(signal.value * 100).toFixed(1)}%`;
  if (signal.unit === 'bytes') {
    if (signal.value >= 1024 ** 3) return `${(signal.value / 1024 ** 3).toFixed(1)} GiB`;
    return `${(signal.value / 1024 ** 2).toFixed(1)} MiB`;
  }
  if (signal.unit === 'seconds') return signal.value < 1 ? `${(signal.value * 1000).toFixed(1)} ms` : `${signal.value.toFixed(1)} s`;
  return `${Number.isInteger(signal.value) ? signal.value.toFixed(0) : signal.value.toFixed(2)}${signal.unit ? ` ${signal.unit}` : ''}`;
}

function EvidenceDetail({ item }: { item: Evidence }) {
  return (
    <details className="diagnosisEvidenceDetail">
      <summary>Consultar fatos e consulta de origem</summary>
      <dl className="evidenceProvenance">
        <div><dt>ID</dt><dd>{item.id}</dd></div>
        <div><dt>Observada em</dt><dd>{dateLabel(item.observed_at)}</dd></div>
        <div><dt>Força</dt><dd>{strengthLabel[item.strength] ?? item.strength}</dd></div>
        {item.raw_reference && <div><dt>Referência</dt><dd>{item.raw_reference}</dd></div>}
        {item.integrity_sha256 && <div><dt>SHA-256 registrado</dt><dd><code>{item.integrity_sha256}</code></dd></div>}
      </dl>
      {item.query && <div><h4>Consulta executada</h4><pre>{item.query}</pre></div>}
      <h4>Fatos coletados</h4>
      <pre>{JSON.stringify(item.facts, null, 2)}</pre>
    </details>
  );
}

function EvidenceRefs({ ids, evidence, label }: {
  ids: string[]; evidence: Map<string, Evidence>; label: string;
}) {
  if (!ids.length) return null;
  return (
    <div className="hypothesisEvidence">
      <h4>{label}</h4>
      {ids.map((id) => {
        const item = evidence.get(id);
        return item ? (
          <article className="linkedEvidence" key={id}>
            <div className="evidenceMeta"><span>{item.source} · {item.category}</span><a href={`#evidence-${encodeURIComponent(id)}`}>Ver evidência</a></div>
            <p>{item.summary}</p>
            <EvidenceDetail item={item} />
          </article>
        ) : <p className="muted" key={id}>Evidência {id} não disponível neste resultado.</p>;
      })}
    </div>
  );
}

export default function InvestigationView({ data, mode = 'demo' }: {
  data: Investigation; mode?: 'demo' | 'persisted';
}) {
  const evidence = new Map(data.evidence.map((item) => [item.id, item]));
  const entities = new Map(data.entities.map((item) => [item.id, item]));
  const rootHypothesis = data.hypotheses.find((item) => item.id === data.root_cause.hypothesis_id);
  const hasConfirmedRoot = data.status === 'resolved' && rootHypothesis?.status === 'confirmed' && rootHypothesis.origin !== 'llm';
  const groups = new Map<string, Investigation['entities']>();
  for (const entity of data.entities) groups.set(entity.kind, [...(groups.get(entity.kind) ?? []), entity]);
  const observedServices = new Set([
    ...(data.incident.service ? [data.incident.service] : []),
    ...data.trace_analyses.flatMap((trace) => trace.services),
  ]);
  const sortedHypotheses = [...data.hypotheses].sort((a, b) => {
    if (a.id === rootHypothesis?.id) return -1;
    if (b.id === rootHypothesis?.id) return 1;
    return b.score - a.score;
  });

  return (
    <main className="diagnosis" lang="pt-BR">
      <a className="diagnosisSkip" href="#diagnosis-content">Ir para o diagnóstico</a>
      <header className="topbar">
        <a className="brand" href="/"><span className="brandMark">SRE</span><span>AI SRE RCA</span></a>
        <nav className="topActions" aria-label="Aplicação">
          <a className="demoLink" href="/">Kubernetes</a>
          <a className="demoLink" href="/trace">Traces</a>
          <a className="demoLink" href="/stateful/postgresql">PostgreSQL</a>
          <a className="demoLink" href="/stateful/kafka">Kafka</a>
          <a className="demoLink" href="/stateful/redis">Redis</a>
          <a className="demoLink" href="/awareness">Grafo</a>
          <a className="demoLink" href="/ops">Operação</a>
        </nav>
      </header>
      <div className="diagnosisBreadcrumb">Investigações <span>/</span> {mode === 'demo' ? 'Demonstração' : 'Incidente salvo'} <span>/</span> <code>{data.incident_id}</code></div>
      <section className="diagnosisHero">
        <div className="diagnosisKicker"><span className={`diagnosisSeverity ${data.incident.severity}`}>{data.incident.severity}</span><span>{mode === 'demo' ? 'Dados de demonstração' : 'Investigação persistida'}</span><span>Somente leitura</span></div>
        <h1>{data.incident.title}</h1>
        <p className="muted">{[data.incident.cluster, data.incident.namespace, data.incident.pod ?? data.incident.service].filter(Boolean).join(' / ')}</p>
      </section>
      <div className="diagnosisLayout">
        <aside className="diagnosisSidebar">
          <nav aria-label="Seções do diagnóstico">
            <span className="sectionLabel">Nesta investigação</span>
            <a href="#alert-info">Informações do alerta</a>
            <a href="#entities">Entidades e relações</a>
            <a href="#hypotheses">Hipóteses</a>
            <a href="#runtime">Telemetria e traces</a>
            <a href="#evidence">Evidências</a>
            <a href="#timeline">Linha do tempo</a>
            <a href="#final-rca">RCA final</a>
            <a href="#remediation">Remediação</a>
          </nav>
          <div className="diagnosisSidebarNote"><strong>Evidence-first</strong><p>O diagnóstico mantém os estados e vínculos retornados pelo motor.</p></div>
          {mode === 'demo' && <a className="diagnosisSavedLink" href={`/incidents/${encodeURIComponent(data.incident_id)}`}>Abrir investigação salva ↗</a>}
        </aside>
        <div id="diagnosis-content" className="diagnosisContent">
          <section className={`panel diagnosisConclusion ${hasConfirmedRoot ? 'confirmed' : 'inconclusive'}`} aria-label="Resumo do diagnóstico">
            <div className="diagnosisConclusionTop"><span className="sectionLabel">Diagnóstico</span><span className="diagnosisPill">{hasConfirmedRoot ? 'Causa confirmada pelo motor' : data.status === 'investigating' ? 'Investigação em andamento' : 'Diagnóstico inconclusivo'}</span></div>
            <h2>{data.root_cause.title}</h2><p>{data.root_cause.summary}</p>
            <div className="signalRow"><span>Força: <b>{strengthLabel[data.root_cause.evidence_strength] ?? data.root_cause.evidence_strength}</b></span><span>{data.root_cause.supporting_signals} sinais de suporte</span><span>{data.root_cause.contradicting_signals} sinais contrários</span></div>
            <a className="diagnosisTextLink" href="#final-rca">Consultar conclusão e evidências →</a>
          </section>
          <section id="alert-info" className="panel diagnosisSection">
            <div className="sectionLabel">01 · Contexto</div><h2>Informações do alerta</h2>
            <dl className="diagnosisAlertGrid">
              <div><dt>Severidade</dt><dd>{data.incident.severity}</dd></div>
              <div><dt>Alerta</dt><dd>{data.incident.alert_name}</dd></div>
              <div><dt>Cluster</dt><dd>{data.incident.cluster}</dd></div>
              <div><dt>Detectado em</dt><dd>{dateLabel(data.incident.starts_at)}</dd></div>
              <div><dt>Serviço / Pod</dt><dd>{data.incident.pod ?? data.incident.service ?? 'Não informado'}</dd></div>
              <div><dt>Namespace</dt><dd>{data.incident.namespace ?? 'Não informado'}</dd></div>
              <div><dt>Tenant</dt><dd>{data.incident.tenant_id}</dd></div>
              <div><dt>Investigação registrada</dt><dd>{dateLabel(data.created_at)}</dd></div>
            </dl>
            <div className="diagnosisStats"><div><strong>{data.evidence.length}</strong><span>Evidências coletadas</span></div><div><strong>{data.hypotheses.length}</strong><span>Hipóteses avaliadas</span></div><div><strong>{observedServices.size}</strong><span>Serviços observados</span></div><div><strong>{data.entities.length}</strong><span>Entidades identificadas</span></div></div>
            <p className="diagnosisNote">Serviços e entidades representam o contexto observado. Essa contagem não mede indisponibilidade nem blast radius confirmado.</p>
          </section>
          <section id="entities" className="panel diagnosisSection">
            <div className="sectionLabel">02 · Topologia</div><h2>Entidades identificadas</h2>
            <div className="diagnosisEntityGroups">{[...groups].map(([kind, items]) => (
              <div className="diagnosisEntityGroup" key={kind}><h3>{kind}</h3>{items.map((entity) => (
                <details className="diagnosisEntity" key={entity.id}><summary><strong>{entity.name}</strong>{entity.namespace && <small>{entity.namespace}</small>}</summary><pre>{JSON.stringify(entity.metadata, null, 2)}</pre></details>
              ))}</div>
            ))}</div>
            {!data.entities.length && <p className="muted">Nenhuma entidade identificada nesta investigação.</p>}
            <h3>Como se relacionam</h3>
            <IncidentGraph data={data} />
            {!!data.graph_edges.length && <details className="diagnosisRelationsText"><summary>Consultar relações em texto</summary>
            <div className="entityGraph">{data.graph_edges.map((edge, index) => (
              <div className="graphEdge" key={`${edge.source}-${edge.relationship}-${edge.target}-${index}`}>
                <div className="graphNode"><span>{entities.get(edge.source)?.kind ?? 'Entidade'}</span><strong>{entities.get(edge.source)?.name ?? edge.source}</strong></div>
                <div className="edgeArrow"><small>{edge.relationship}</small><b aria-hidden="true">→</b></div>
                <div className="graphNode"><span>{entities.get(edge.target)?.kind ?? 'Entidade'}</span><strong>{entities.get(edge.target)?.name ?? edge.target}</strong></div>
              </div>
            ))}</div>
            </details>}
            {!data.graph_edges.length && <p className="muted">Nenhuma relação retornada pelo motor.</p>}
          </section>
          <section id="hypotheses" className="panel diagnosisSection">
            <div className="sectionLabel">03 · Caminhos da investigação</div><h2>Hipóteses e verificações</h2>
            <p className="muted">Abra uma hipótese para consultar o raciocínio, os sinais de suporte e as evidências que a contradizem.</p>
            <div className="diagnosisHypothesisTree">{sortedHypotheses.map((h) => {
              const status = h.origin === 'llm' ? 'proposed' : h.status;
              const isRoot = hasConfirmedRoot && h.id === rootHypothesis?.id;
              return (
                <details className={`diagnosisHypothesis ${status} ${isRoot ? 'rootHypothesis' : ''}`} key={h.id} open={status !== 'ruled_out'}>
                  <summary><span className="diagnosisHypothesisSymbol" aria-hidden="true">{statusSymbol[status] ?? '?'}</span><strong>{h.statement}</strong><span className={`diagnosisHypothesisStatus ${status}`}>{isRoot ? 'Causa raiz' : statusLabel[status] ?? status}</span></summary>
                  <div className="diagnosisHypothesisBody"><p>{h.rationale || 'Sem justificativa registrada.'}</p>
                    <div className="diagnosisNote">{h.origin === 'llm' ? 'Proposta do LLM. Requer validação independente.' : `Avaliada pelo playbook · score ${h.score.toFixed(1)} (não é probabilidade)`}</div>
                    <EvidenceRefs ids={h.supporting_evidence} evidence={evidence} label="Evidências de suporte" />
                    <EvidenceRefs ids={h.contradicting_evidence} evidence={evidence} label="Evidências contrárias" />
                    {!!h.required_checks.length && <div><h4>Verificações necessárias</h4><ul>{h.required_checks.map((check, i) => <li key={`${h.id}-${i}`}>{check}</li>)}</ul></div>}
                    {!h.supporting_evidence.length && !h.contradicting_evidence.length && <p className="muted">Sem evidências vinculadas a esta hipótese.</p>}
                  </div>
                </details>
              );
            })}</div>
            {!data.hypotheses.length && <p className="muted">Nenhuma hipótese retornada. Não há causa confirmada para apresentar.</p>}
          </section>
          <section id="runtime" className="panel diagnosisSection">
            <div className="sectionLabel">04 · Execução observada</div><h2>Telemetria e propagação causal</h2>
      {!!data.stateful_dependencies.length && (
        <section className="panel wideSection statefulSection">
          <div className="statefulHeader">
            <div>
              <div className="sectionLabel">DEPENDÊNCIAS STATEFUL</div>
              <h2>Diagnóstico das dependências</h2>
              <p className="muted">A origem nos traces é confrontada com a telemetria atual da dependência.</p>
            </div>
            <div className="statefulDemoNav">
              <a href="/stateful/postgresql">POSTGRESQL</a>
              <a href="/stateful/kafka">KAFKA</a>
              <a href="/stateful/redis">REDIS</a>
            </div>
          </div>

          <div className="dependencyGrid">
            {data.stateful_dependencies.map((dependency) => (
              <article className={`dependencyCard ${dependency.health}`} key={dependency.dependency_id}>
                <div className="dependencyTop">
                  <div>
                    <span className="dependencyKind">{dependency.kind}</span>
                    <h3>{dependency.name}</h3>
                  </div>
                  <div className={`dependencyHealth ${dependency.health}`}>{dependency.health}</div>
                </div>

                <div className="dependencyMeta">
                  <span>role: <b>{dependency.causal_role}</b></span>
                  {dependency.target && <span>target: <b>{dependency.target}</b></span>}
                  <span>evidence: <b>{dependency.cause_strength}</b></span>
                </div>

                {dependency.cause && (
                  <div className="dependencyCause">
                    <span>Diagnóstico da dependência</span>
                    <strong>{dependency.cause}</strong>
                  </div>
                )}

                <div className="dependencySignals">
                  {dependency.signals.map((signal) => (
                    <div className={`dependencySignal ${signal.status}`} key={`${dependency.dependency_id}-${signal.name}`}>
                      <div>
                        <span>{signal.name.replaceAll('_', ' ')}</span>
                        <strong>{formatSignalValue(signal)}</strong>
                      </div>
                      <small>
                        {signal.status}
                        {signal.threshold !== undefined && signal.threshold !== null
                          ? ` · threshold ${formatSignalValue({ ...signal, value: signal.threshold })}`
                          : ''}
                      </small>
                    </div>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      {!!data.trace_analyses.length && (
        <section className="panel wideSection traceSection">
          <div className="traceSectionHeader">
            <div>
              <div className="sectionLabel">PROPAGAÇÃO CAUSAL NOS TRACES</div>
              <h2>Traces representativos e origem da falha</h2>
            </div>
            <div className="traceCount">{data.trace_analyses.length} traces analisados</div>
          </div>
          <div className="traceGrid">
            {data.trace_analyses.map((trace) => (
              <article className="traceCard" key={trace.trace_id}>
                <div className="traceMeta">
                  <span>{trace.trace_id.slice(0, 16)}</span>
                  <span>{trace.duration_ms.toFixed(0)} ms</span>
                  <span>{trace.error_count} erros</span>
                </div>
                <div className="traceRoot">
                  <strong>{trace.root_service}</strong>
                  <span>{trace.root_operation}</span>
                </div>
                <div className="causalChain">
                  {trace.causal_chain.map((hop, index) => (
                    <div className={`causalHop ${hop.role} ${hop.status}`} key={`${trace.trace_id}-${hop.span_id}`}>
                      <div className="hopIndex">{index + 1}</div>
                      <div className="hopBody">
                        <div className="hopTop">
                          <strong>{hop.service}</strong>
                          <span>{hop.role}</span>
                        </div>
                        <div className="hopOperation">{hop.operation}</div>
                        <div className="hopSignals">
                          <span>{hop.kind}</span>
                          <span>{hop.status}</span>
                          <span>{hop.duration_ms.toFixed(1)} ms</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
                {trace.candidate_service && (
                  <div className="traceCandidate">
                    <span>Origem candidata</span>
                    <strong>{trace.candidate_service}</strong>
                    <small>{trace.candidate_operation} · {trace.candidate_reason}</small>
                  </div>
                )}
              </article>
            ))}
          </div>
        </section>
      )}

            {!data.stateful_dependencies.length && !data.trace_analyses.length && <p className="muted">Nenhuma análise stateful ou trace disponível para este incidente.</p>}
          </section>
          <section id="evidence" className="panel diagnosisSection">
            <div className="sectionLabel">05 · Fatos coletados</div><h2>Evidências</h2>
            <div className="evidenceGrid">{data.evidence.map((item) => (
              <article id={`evidence-${encodeURIComponent(item.id)}`} className="evidenceCard" key={item.id}>
                <div className="evidenceMeta"><span>{item.source}</span><span>{strengthLabel[item.strength] ?? item.strength}</span></div>
                <h3>{item.category}</h3><p>{item.summary}</p><EvidenceDetail item={item} />
              </article>
            ))}</div>
            {!data.evidence.length && <p className="muted">Nenhuma evidência coletada.</p>}
            <p className="diagnosisNote">O hash exibido é o registro retornado pela API. A exibição não realiza uma nova verificação de integridade.</p>
          </section>
          <section id="timeline" className="panel diagnosisSection">
            <div className="sectionLabel">06 · Sequência observada</div><h2>Linha do tempo</h2>
            {[...data.timeline].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp)).map((t, index) => (
              <div className="timeline" key={`${t.timestamp}-${index}`}><time dateTime={t.timestamp}>{dateLabel(t.timestamp)}</time><div><b>{t.source}</b><p>{t.message}</p></div></div>
            ))}
            {!data.timeline.length && <p className="muted">Nenhum evento registrado.</p>}
          </section>
          <section id="final-rca" className={`panel diagnosisSection diagnosisFinal ${hasConfirmedRoot ? 'confirmed' : 'inconclusive'}`}>
            <div className="sectionLabel">07 · Conclusão</div><h2>{hasConfirmedRoot ? 'RCA final' : 'RCA inconclusivo'}</h2>
            <h3>{data.root_cause.title}</h3><p>{data.root_cause.summary}</p>
            {hasConfirmedRoot && rootHypothesis ? <EvidenceRefs ids={rootHypothesis.supporting_evidence} evidence={evidence} label="Evidências que sustentam a conclusão" /> : <p className="diagnosisNote">Nenhuma causa raiz confirmada pelo motor neste resultado. Hipóteses e lacunas permanecem disponíveis para revisão.</p>}
          </section>
          <section id="remediation" className="panel diagnosisSection">
            <div className="sectionLabel">08 · Próximas ações</div><h2>Remediação recomendada</h2>
            <p className="muted">Recomendações retornadas pelo motor para revisão do operador.</p>
            {data.remediation.map((r, index) => (
              <article className="remediation" key={`${r.priority}-${index}`}><span>{r.priority}</span><div><strong>{r.action}</strong><p>{r.reason}</p><small>{r.requires_approval === false ? 'Recomendação informativa' : 'Requer aprovação'}</small></div></article>
            ))}
            {!data.remediation.length && <p className="muted">Nenhuma recomendação registrada.</p>}
          </section>
          {!!data.similar_incidents.length && <section className="panel diagnosisSection">
            <div className="sectionLabel">Contexto histórico</div><h2>Incidentes semelhantes</h2>
            <div className="memoryGrid">{data.similar_incidents.map((item) => (
              <article className="memoryCard" key={item.incident_id}><div className="memoryScore">{Math.round(item.similarity * 100)}% de similaridade</div><a href={`/incidents/${encodeURIComponent(item.incident_id)}`}><strong>{item.title}</strong></a><p>{item.root_cause}</p><small>{item.cluster} · {dateLabel(item.occurred_at)}</small></article>
            ))}</div><p className="diagnosisNote">Incidentes históricos oferecem contexto. A conclusão exige evidências do incidente atual.</p>
          </section>}
          <footer>Incidente {data.incident_id} · {data.query_budget.total_calls ?? 0} consultas · {data.query_budget.denied?.length ?? 0} consultas negadas pelo budget · RCA determinístico</footer>
        </div>
      </div>
    </main>
  );
}
