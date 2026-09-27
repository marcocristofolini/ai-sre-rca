import type { Investigation } from '../lib/api';

const statusSymbol: Record<string, string> = {
  confirmed: '✓',
  ruled_out: '×',
  testing: '⋯',
  proposed: '?',
  inconclusive: '–'
};

type StatefulSignal = Investigation['stateful_dependencies'][number]['signals'][number];

function formatSignalValue(signal: StatefulSignal): string {
  if (signal.value === undefined || signal.value === null) return 'n/a';
  if (signal.unit === 'ratio') return `${(signal.value * 100).toFixed(1)}%`;
  if (signal.unit === 'bytes') {
    if (signal.value >= 1024 ** 3) return `${(signal.value / 1024 ** 3).toFixed(1)} GiB`;
    return `${(signal.value / 1024 ** 2).toFixed(1)} MiB`;
  }
  if (signal.unit === 'seconds') {
    return signal.value < 1
      ? `${(signal.value * 1000).toFixed(1)} ms`
      : `${signal.value.toFixed(1)} s`;
  }
  return `${Number.isInteger(signal.value) ? signal.value.toFixed(0) : signal.value.toFixed(2)}${signal.unit ? ` ${signal.unit}` : ''}`;
}

export default function InvestigationView({ data }: { data: Investigation }) {
  const evidence = Object.fromEntries(data.evidence.map((item) => [item.id, item]));
  const entities = Object.fromEntries(data.entities.map((item) => [item.id, item]));

  return (
    <main>
      <header className="topbar">
        <div className="brand"><span className="brandMark">SRE</span><span>Investigator</span></div>
        <div className="topActions">
          <a className="demoLink" href="/">K8S DEMO</a>
          <a className="demoLink" href="/trace">TRACE DEMO</a>
          <a className="demoLink" href="/stateful/postgresql">STATEFUL DEMO</a>
          <div className="readonly">READ ONLY · EVIDENCE FIRST</div>
        </div>
      </header>

      <section className="hero panel">
        <div>
          <div className="eyebrow">{data.incident.severity.toUpperCase()} · {data.incident.alert_name}</div>
          <h1>{data.incident.title}</h1>
          <p className="muted">
            {[data.incident.cluster, data.incident.namespace, data.incident.pod ?? data.incident.service].filter(Boolean).join(' / ')}
          </p>
        </div>
        <div className="statusBadge">{data.status.toUpperCase()}</div>
      </section>

      <section className="root panel">
        <div className="sectionLabel">ROOT CAUSE</div>
        <h2>{data.root_cause.title}</h2>
        <p>{data.root_cause.summary}</p>
        <div className="signalRow">
          <span>Evidence: <b>{data.root_cause.evidence_strength}</b></span>
          <span>{data.root_cause.supporting_signals} supporting</span>
          <span>{data.root_cause.contradicting_signals} contradicting</span>
          <span>{data.graph_edges.length} graph edges</span>
        </div>
      </section>

      {!!data.stateful_dependencies.length && (
        <section className="panel wideSection statefulSection">
          <div className="statefulHeader">
            <div>
              <div className="sectionLabel">STATEFUL DEPENDENCY RCA · v0.5</div>
              <h2>Dependency-specific runtime diagnosis</h2>
              <p className="muted">Trace origin is validated against read-only PostgreSQL, Kafka or Redis telemetry before root-cause promotion.</p>
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
                    <span>confirmed dependency cause</span>
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
              <div className="sectionLabel">TRACE-AWARE CAUSAL PROPAGATION</div>
              <h2>Representative traces and failure ancestry</h2>
            </div>
            <div className="traceCount">{data.trace_analyses.length} traces analyzed</div>
          </div>
          <div className="traceGrid">
            {data.trace_analyses.map((trace) => (
              <article className="traceCard" key={trace.trace_id}>
                <div className="traceMeta">
                  <span>{trace.trace_id.slice(0, 16)}</span>
                  <span>{trace.duration_ms.toFixed(0)} ms</span>
                  <span>{trace.error_count} errors</span>
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
                    <span>candidate origin</span>
                    <strong>{trace.candidate_service}</strong>
                    <small>{trace.candidate_operation} · {trace.candidate_reason}</small>
                  </div>
                )}
              </article>
            ))}
          </div>
        </section>
      )}

      <div className="columns">
        <section className="panel">
          <div className="sectionLabel">AWARENESS GRAPH</div>
          <div className="entityGraph">
            {data.graph_edges.map((edge, index) => (
              <div className="graphEdge" key={`${edge.source}-${edge.relationship}-${edge.target}-${index}`}>
                <div className="graphNode">
                  <span>{entities[edge.source]?.kind ?? 'Entity'}</span>
                  <strong>{entities[edge.source]?.name ?? edge.source}</strong>
                </div>
                <div className="edgeArrow"><small>{edge.relationship}</small><b>→</b></div>
                <div className="graphNode">
                  <span>{entities[edge.target]?.kind ?? 'Entity'}</span>
                  <strong>{entities[edge.target]?.name ?? edge.target}</strong>
                </div>
              </div>
            ))}
            {!data.graph_edges.length && data.entities.map((entity, index) => (
              <div className="entityLine" key={entity.id}>
                <div className="nodeIndex">{index + 1}</div>
                <div className="entityCard">
                  <span className="kind">{entity.kind}</span>
                  <strong>{entity.name}</strong>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="sectionLabel">HYPOTHESES</div>
          <div className="hypotheses">
            {[...data.hypotheses].sort((a, b) => b.score - a.score).map((h) => (
              <article className={`hypothesis ${h.status} ${h.origin === 'llm' ? 'llm' : ''}`} key={h.id}>
                <div className="hypothesisHeader">
                  <span className="symbol">{statusSymbol[h.status] ?? '?'}</span>
                  <strong>{h.statement}</strong>
                  <span className="score">{h.origin === 'llm' ? 'LLM · proposed' : h.score.toFixed(1)}</span>
                </div>
                <p>{h.rationale}</p>
                {!!h.supporting_evidence.length && (
                  <div className="chips">
                    {h.supporting_evidence.map((id) => <span className="chip" key={id}>{evidence[id]?.source}: {evidence[id]?.category}</span>)}
                  </div>
                )}
                {h.origin === 'llm' && !!h.required_checks.length && (
                  <div className="checks">Needs checks: {h.required_checks.join(' · ')}</div>
                )}
              </article>
            ))}
          </div>
        </section>
      </div>

      <section className="panel wideSection">
        <div className="sectionLabel">EVIDENCE</div>
        <div className="evidenceGrid">
          {data.evidence.map((e) => (
            <article className="evidenceCard" key={e.id}>
              <div className="evidenceMeta"><span>{e.source}</span><span>{e.strength}</span></div>
              <h3>{e.category}</h3>
              <p>{e.summary}</p>
            </article>
          ))}
        </div>
      </section>

      {!!data.similar_incidents.length && (
        <section className="panel wideSection">
          <div className="sectionLabel">INCIDENT MEMORY · PGVECTOR</div>
          <div className="memoryGrid">
            {data.similar_incidents.map((item) => (
              <article className="memoryCard" key={item.incident_id}>
                <div className="memoryScore">{Math.round(item.similarity * 100)}% similar</div>
                <strong>{item.title}</strong>
                <p>{item.root_cause}</p>
                <small>{item.cluster} · {new Date(item.occurred_at).toLocaleString()}</small>
              </article>
            ))}
          </div>
          <p className="memoryWarning">Historical incidents are hints only. Current telemetry must independently confirm the cause.</p>
        </section>
      )}

      <div className="columns lower">
        <section className="panel">
          <div className="sectionLabel">TIMELINE · CHANGE CORRELATION</div>
          {data.timeline.map((t) => (
            <div className="timeline" key={`${t.timestamp}-${t.source}-${t.message}`}>
              <time>{new Date(t.timestamp).toLocaleTimeString()}</time>
              <div><b>{t.source}</b><p>{t.message}</p></div>
            </div>
          ))}
        </section>

        <section className="panel">
          <div className="sectionLabel">REMEDIATION</div>
          {data.remediation.map((r) => (
            <article className="remediation" key={`${r.priority}-${r.action}`}>
              <span>{r.priority}</span>
              <div><strong>{r.action}</strong><p>{r.reason}</p></div>
            </article>
          ))}
        </section>
      </div>

      <footer>Incident {data.incident_id} · deterministic RCA · LLM cannot confirm hypotheses · no write permissions</footer>
    </main>
  );
}
