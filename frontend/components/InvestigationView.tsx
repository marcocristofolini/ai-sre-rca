import type { Investigation } from '../lib/api';

const statusSymbol: Record<string, string> = {
  confirmed: '✓',
  ruled_out: '×',
  testing: '⋯',
  proposed: '?',
  inconclusive: '–'
};

export default function InvestigationView({ data }: { data: Investigation }) {
  const evidence = Object.fromEntries(data.evidence.map((item) => [item.id, item]));

  return (
    <main>
      <header className="topbar">
        <div className="brand"><span className="brandMark">SRE</span><span>Investigator</span></div>
        <div className="readonly">READ ONLY</div>
      </header>

      <section className="hero panel">
        <div>
          <div className="eyebrow">{data.incident.severity.toUpperCase()} · {data.incident.alert_name}</div>
          <h1>{data.incident.title}</h1>
          <p className="muted">{data.incident.cluster} / {data.incident.namespace} / {data.incident.pod}</p>
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
        </div>
      </section>

      <div className="columns">
        <section className="panel">
          <div className="sectionLabel">AWARENESS GRAPH</div>
          <div className="entityGraph">
            {data.entities.map((entity, index) => (
              <div className="entityLine" key={entity.id}>
                <div className="nodeIndex">{index + 1}</div>
                <div className="entityCard">
                  <span className="kind">{entity.kind}</span>
                  <strong>{entity.name}</strong>
                  {entity.relationship && <small>{entity.relationship}</small>}
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="sectionLabel">HYPOTHESES</div>
          <div className="hypotheses">
            {data.hypotheses.sort((a, b) => b.score - a.score).map((h) => (
              <article className={`hypothesis ${h.status}`} key={h.id}>
                <div className="hypothesisHeader">
                  <span className="symbol">{statusSymbol[h.status] ?? '?'}</span>
                  <strong>{h.statement}</strong>
                  <span className="score">{h.score.toFixed(1)}</span>
                </div>
                <p>{h.rationale}</p>
                {!!h.supporting_evidence.length && (
                  <div className="chips">
                    {h.supporting_evidence.map((id) => <span className="chip" key={id}>{evidence[id]?.source}: {evidence[id]?.category}</span>)}
                  </div>
                )}
              </article>
            ))}
          </div>
        </section>
      </div>

      <section className="panel">
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

      <div className="columns lower">
        <section className="panel">
          <div className="sectionLabel">TIMELINE</div>
          {data.timeline.map((t) => (
            <div className="timeline" key={`${t.timestamp}-${t.message}`}>
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

      <footer>Incident {data.incident_id} · evidence-first RCA · no write permissions</footer>
    </main>
  );
}
