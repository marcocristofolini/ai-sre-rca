import {
  fetchAwarenessChanges,
  fetchAwarenessTopology,
  type AwarenessEntity,
  type AwarenessFreshness,
} from '../../lib/api';

type Props = {
  searchParams?: Promise<{
    cluster?: string;
    namespace?: string;
    service?: string;
    tenant?: string;
  }>;
};

const freshnessOrder: AwarenessFreshness[] = [
  'fresh',
  'stale',
  'historical',
  'tombstoned',
];

function compactId(value: string): string {
  if (value.length <= 18) return value;
  return `${value.slice(0, 9)}…${value.slice(-7)}`;
}

function formatWhen(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  }).format(date);
}

function entityLabel(entity: AwarenessEntity | undefined, fallback: string): string {
  if (!entity) return compactId(fallback);
  return `${entity.kind} · ${entity.name}`;
}

export default async function AwarenessPage({ searchParams }: Props) {
  const params = searchParams ? await searchParams : {};
  const cluster = params.cluster?.trim() || process.env.DEFAULT_CLUSTER || 'prod-us-east-1';
  const namespace = params.namespace?.trim() || undefined;
  const service = params.service?.trim() || undefined;
  const tenantId = params.tenant?.trim() || undefined;
  const query = {
    cluster,
    namespace,
    service,
    tenantId,
    depth: 2,
    limit: 500,
  };

  try {
    const [topology, changes] = await Promise.all([
      fetchAwarenessTopology(query),
      fetchAwarenessChanges({ ...query, limit: 100 }),
    ]);
    const entityById = Object.fromEntries(
      topology.entities.map((entity) => [entity.id, entity]),
    );
    const freshnessCounts = Object.fromEntries(
      freshnessOrder.map((freshness) => [
        freshness,
        topology.entities.filter((entity) => entity.freshness === freshness).length,
      ]),
    ) as Record<AwarenessFreshness, number>;

    return (
      <main>
        <header className="topbar">
          <div className="brand">
            <span className="brandMark">SRE</span>
            <span>Awareness Explorer</span>
          </div>
          <div className="topActions">
            <a className="demoLink" href="/showcase">PRODUTO</a>
            <a className="demoLink" href="/">INVESTIGATOR</a>
            <a className="demoLink" href="/ops">OPS</a>
            <div className="readonly">READ ONLY · PERSISTENT GRAPH</div>
          </div>
        </header>

        <section className="panel awarenessHero">
          <div>
            <div className="sectionLabel">PERSISTENT AWARENESS GRAPH · v0.8</div>
            <h1>Operational topology and change history</h1>
            <p className="muted">
              Reconciled graph facts are persisted across incidents. Historical change events
              remain context only and never become causal by proximity.
            </p>
          </div>
          <div className="awarenessScope">
            <span>{topology.tenant_id}</span>
            <strong>{topology.cluster}</strong>
          </div>
        </section>

        <section className="panel wideSection">
          <form className="awarenessFilters" method="get">
            <label>
              <span>Cluster</span>
              <input name="cluster" defaultValue={cluster} required />
            </label>
            <label>
              <span>Namespace</span>
              <input name="namespace" defaultValue={namespace ?? ''} placeholder="all" />
            </label>
            <label>
              <span>Service</span>
              <input name="service" defaultValue={service ?? ''} placeholder="all" />
            </label>
            <label>
              <span>Tenant override</span>
              <input name="tenant" defaultValue={tenantId ?? ''} placeholder="global principals only" />
            </label>
            <button type="submit">APPLY SCOPE</button>
          </form>
        </section>

        <section className="awarenessStats">
          <article className="panel awarenessStat">
            <span>entities</span>
            <strong>{topology.entities.length}</strong>
          </article>
          <article className="panel awarenessStat">
            <span>relationships</span>
            <strong>{topology.relationships.length}</strong>
          </article>
          <article className="panel awarenessStat fresh">
            <span>fresh</span>
            <strong>{freshnessCounts.fresh}</strong>
          </article>
          <article className="panel awarenessStat stale">
            <span>stale</span>
            <strong>{freshnessCounts.stale}</strong>
          </article>
          <article className="panel awarenessStat historical">
            <span>historical</span>
            <strong>{freshnessCounts.historical}</strong>
          </article>
          <article className="panel awarenessStat tombstoned">
            <span>tombstoned</span>
            <strong>{freshnessCounts.tombstoned}</strong>
          </article>
          <article className="panel awarenessStat">
            <span>change events</span>
            <strong>{changes.length}</strong>
          </article>
        </section>

        {topology.root && (
          <section className="panel wideSection awarenessRoot">
            <div className="sectionLabel">SERVICE PROJECTION ROOT</div>
            <div className="awarenessRootBody">
              <div>
                <span className={`freshnessBadge ${topology.root.freshness}`}>
                  {topology.root.freshness}
                </span>
                <h2>{topology.root.name}</h2>
                <p className="muted">
                  {topology.root.kind} · {topology.root.namespace ?? 'cluster-scoped'}
                </p>
              </div>
              <div className="awarenessProjectionMeta">
                <span>depth {topology.effective_depth ?? topology.requested_depth ?? 0}</span>
                <span>{topology.truncated ? 'truncated' : 'complete within budget'}</span>
              </div>
            </div>
          </section>
        )}

        <section className="columns awarenessColumns">
          <div className="panel">
            <div className="sectionLabel">RECONCILED ENTITIES</div>
            <h2>Current and historical facts</h2>
            <div className="awarenessEntityList">
              {topology.entities.length === 0 && (
                <p className="muted">No graph entities found in this scope.</p>
              )}
              {topology.entities.map((entity) => (
                <article className="awarenessEntity" key={entity.id}>
                  <div className="awarenessEntityTop">
                    <span className="kind">{entity.kind}</span>
                    <span className={`freshnessBadge ${entity.freshness}`}>
                      {entity.freshness}
                    </span>
                  </div>
                  <strong>{entity.name}</strong>
                  <small>
                    {entity.namespace ?? 'cluster-scoped'} · {compactId(entity.id)}
                  </small>
                  <div className="awarenessEntityMeta">
                    <span>{entity.observation_count} observations</span>
                    <span>last {formatWhen(entity.last_seen)}</span>
                    {entity.tombstoned_at && (
                      <span>
                        removed {formatWhen(entity.tombstoned_at)}
                        {entity.tombstone_source
                          ? ` · ${entity.tombstone_source}`
                          : ''}
                      </span>
                    )}
                    {entity.depth !== undefined && entity.depth !== null && (
                      <span>depth {entity.depth}</span>
                    )}
                  </div>
                </article>
              ))}
            </div>
          </div>

          <div className="panel">
            <div className="sectionLabel">RELATIONSHIPS</div>
            <h2>Stored graph edges</h2>
            <div className="awarenessRelationshipList">
              {topology.relationships.length === 0 && (
                <p className="muted">No relationships found in this scope.</p>
              )}
              {topology.relationships.map((relationship) => (
                <article className="awarenessRelationship" key={relationship.id}>
                  <div className="awarenessRelationshipNode">
                    <span>{entityLabel(entityById[relationship.source_id], relationship.source_id)}</span>
                  </div>
                  <div className="awarenessRelationshipEdge">
                    <small>{relationship.relationship}</small>
                    <b>→</b>
                    <span className={`freshnessDot ${relationship.freshness}`} />
                  </div>
                  <div className="awarenessRelationshipNode">
                    <span>{entityLabel(entityById[relationship.target_id], relationship.target_id)}</span>
                  </div>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="panel wideSection">
          <div className="awarenessTimelineHeader">
            <div>
              <div className="sectionLabel">DEPLOYMENT / CHANGE TIMELINE</div>
              <h2>Historical context</h2>
              <p className="muted">
                Events are ordered by authoritative operation time and remain non-causal context
                until deterministic runtime evidence confirms a hypothesis.
              </p>
            </div>
            <span>{changes.length} visible</span>
          </div>

          <div className="awarenessTimeline">
            {changes.length === 0 && (
              <p className="muted">No ChangeEvents found in this scope.</p>
            )}
            {changes.map((event) => (
              <article className="awarenessChange" key={event.entity_id}>
                <time>{formatWhen(event.occurred_at)}</time>
                <div>
                  <div className="awarenessChangeTitle">
                    <strong>{event.application}</strong>
                    <span>{event.change_type}</span>
                  </div>
                  <p>
                    {event.workload_kind && event.workload_name
                      ? `${event.workload_kind}/${event.workload_name}`
                      : 'target unresolved'}
                    {event.service_name ? ` · service/${event.service_name}` : ''}
                  </p>
                  <div className="awarenessChangeMeta">
                    <span>rev {event.revision ? compactId(event.revision) : 'n/a'}</span>
                    <span>{event.observation_count} observations</span>
                    <span>{event.evidence_ids.length} evidence refs</span>
                  </div>
                  {event.repository && <small>{event.repository}</small>}
                </div>
              </article>
            ))}
          </div>
        </section>

        <footer>
          Generated {formatWhen(topology.generated_at)} · read-only persistent awareness surface
        </footer>
      </main>
    );
  } catch (error) {
    return (
      <main>
        <section className="panel errorPanel">
          <div className="sectionLabel">AWARENESS EXPLORER UNAVAILABLE</div>
          <h1>Persistent Awareness Graph</h1>
          <p>Check viewer authorization, tenant/cluster scope and backend connectivity.</p>
          <pre>{String(error)}</pre>
          <a className="demoLink" href="/ops">OPEN OPS</a>
        </section>
      </main>
    );
  }
}
