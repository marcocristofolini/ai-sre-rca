import { fetchIdentity, fetchIncidents, IncidentFetchError } from '../../lib/api';
import IncidentNavigation from '../../components/IncidentNavigation';

const statusLabel: Record<string, string> = { resolved: 'Diagnóstico concluído', inconclusive: 'Inconclusivo', investigating: 'Em investigação' };

export default async function IncidentsPage({ searchParams }: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const raw = await searchParams;
  const value = (key: string) => typeof raw[key] === 'string' ? raw[key] as string : '';
  const params = new URLSearchParams({ limit: '25' });
  for (const key of ['q', 'cluster', 'status']) if (value(key)) params.set(key, value(key));
  const offset = Number(value('offset') || 0);
  if (!Number.isInteger(offset) || offset < 0 || offset > 10000) {
    return <main className="diagnosis" lang="pt-BR"><IncidentNavigation /><section className="panel"><h1>Página inválida</h1><a href="/incidents">Voltar aos incidentes</a></section></main>;
  }
  params.set('offset', String(offset));
  try {
    const [identity, result] = await Promise.all([fetchIdentity(), fetchIncidents(params)]);
    const pageLink = (nextOffset: number) => {
      const next = new URLSearchParams(params); next.set('offset', String(nextOffset)); next.delete('limit');
      return `/incidents?${next}`;
    };
    return <main className="diagnosis incidentWorkspace" lang="pt-BR">
      <IncidentNavigation />
      <section className="diagnosisHero"><div className="diagnosisKicker">Histórico de investigações · tenant {identity.tenant_id}</div><h1>Incidentes</h1>
        <p className="muted">Reabra diagnósticos clássicos e agentic salvos. Um diagnóstico concluído não comprova recuperação do serviço.</p>
        {identity.can_investigate && <a className="incidentPrimaryLink" href="/incidents/new">Iniciar investigação</a>}
        {identity.demo_mode && <p className="diagnosisNote">Ambiente em modo de demonstração. As investigações usam adapters demo.</p>}
      </section>
      <section className="panel diagnosisSection"><h2>Investigações salvas</h2>
        <form method="get" action="/incidents" className="incidentFilters">
          <label>Buscar título ou ID<input name="q" defaultValue={value('q')} maxLength={120} /></label>
          <label>Cluster{identity.clusters.includes('*') ? <input name="cluster" defaultValue={value('cluster')} maxLength={255} placeholder="Todos os autorizados" /> : <select name="cluster" defaultValue={value('cluster')}><option value="">Todos os autorizados</option>{identity.clusters.map(cluster => <option key={cluster}>{cluster}</option>)}</select>}</label>
          <label>Estado<select name="status" defaultValue={value('status')}><option value="">Todos</option>{Object.entries(statusLabel).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
          <button type="submit">Filtrar</button><a href="/incidents">Limpar filtros</a>
        </form>
        <div className="incidentList">{result.items.map(item => <article className="incidentListCard" key={item.incident_id}>
          <div className="evidenceMeta"><span>{item.severity} · {item.investigation_mode}</span><span>{statusLabel[item.status] ?? item.status}</span></div>
          <h3><a href={`/incidents/${encodeURIComponent(item.incident_id)}`}>{item.title}</a></h3>
          <p>{item.root_cause}</p><p className="muted">{item.tenant_id} / {item.cluster}{item.namespace ? ` / ${item.namespace}` : ''}</p>
          <small><code>{item.incident_id}</code> · <time dateTime={item.created_at}>{new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'medium', timeZone: 'UTC' }).format(new Date(item.created_at))} UTC</time></small>
        </article>)}</div>
        {!result.items.length && <p className="muted">Nenhuma investigação encontrada para estes filtros e permissões.</p>}
        <nav className="incidentPagination" aria-label="Paginação">{offset > 0 && <a href={pageLink(Math.max(0, offset - 25))}>Anterior</a>}<span>Página {Math.floor(offset / 25) + 1}</span>{result.next_offset != null && <a href={pageLink(result.next_offset)}>Próxima</a>}</nav>
      </section>
    </main>;
  } catch (error) {
    const restricted = error instanceof IncidentFetchError && [401, 403].includes(error.status);
    return <main className="diagnosis" lang="pt-BR"><IncidentNavigation /><section className="panel errorPanel"><h1>{restricted ? 'Acesso aos incidentes restrito' : 'Não foi possível carregar os incidentes'}</h1><p>{restricted ? 'Use uma sessão autorizada para este tenant e cluster.' : 'Confira os filtros e a conexão com o backend.'}</p><a href="/incidents">Tentar novamente</a></section></main>;
  }
}
