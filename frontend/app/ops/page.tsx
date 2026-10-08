import { fetchCapabilities, fetchIdentity } from '../../lib/api';

type Props = {
  searchParams?: Promise<{ cluster?: string }>;
};

export default async function OpsPage({ searchParams }: Props) {
  const params = searchParams ? await searchParams : {};
  const fallbackCluster = process.env.DEFAULT_CLUSTER ?? 'prod-us-east-1';
  const cluster = params.cluster ?? fallbackCluster;

  try {
    const [identity, report] = await Promise.all([
      fetchIdentity(),
      fetchCapabilities(cluster),
    ]);

    return (
      <main>
        <header className="topbar">
          <div className="brand"><span className="brandMark">SRE</span><span>Operations</span></div>
          <div className="topActions">
            <a className="demoLink" href="/incidents">INCIDENTES</a>
            <a className="demoLink" href="/">K8S DEMO</a>
            <a className="demoLink" href="/trace">TRACE DEMO</a>
            <a className="demoLink" href="/stateful">STATEFUL</a>
            <a className="demoLink" href="/awareness">AWARENESS</a>
          </div>
        </header>

        <section className="panel wideSection opsHero">
          <div className="sectionLabel">OPERATIONAL CONTROL PLANE</div>
          <h1>Tenant, RBAC and source health</h1>
          <div className="opsSummary">
            <div><span>subject</span><strong>{identity.subject}</strong></div>
            <div><span>tenant</span><strong>{identity.tenant_id}</strong></div>
            <div><span>roles</span><strong>{identity.roles.join(', ') || 'none'}</strong></div>
            <div><span>cluster</span><strong>{report.cluster}</strong></div>
            <div><span>persistence</span><strong>{report.persistence}</strong></div>
            <div><span>notifications</span><strong>{report.notifications_enabled ? 'enabled' : 'disabled'}</strong></div>
          </div>
        </section>

        <section className="panel wideSection">
          <div className="sectionLabel">SOURCE CAPABILITIES</div>
          <div className="capabilityGrid">
            {report.sources.map((source) => (
              <article className={`capabilityCard ${source.status}`} key={source.source}>
                <div className="capabilityTop">
                  <strong>{source.source}</strong>
                  <span>{source.status}</span>
                </div>
                <div className="capabilityDetail">{source.detail}</div>
                <div className="capabilityMeta">
                  {source.latency_ms !== undefined && source.latency_ms !== null
                    ? `${source.latency_ms.toFixed(1)} ms`
                    : 'no latency sample'}
                </div>
              </article>
            ))}
          </div>
        </section>
      </main>
    );
  } catch (error) {
    return (
      <main>
        <section className="panel errorPanel">
          <div className="sectionLabel">OPERATIONS UNAVAILABLE</div>
          <h1>AI SRE RCA</h1>
          <p>Check OIDC claims, cluster scope and backend connectivity.</p>
          <pre>{String(error)}</pre>
        </section>
      </main>
    );
  }
}
