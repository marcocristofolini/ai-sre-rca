import { fetchIdentity } from '../../../lib/api';
import IncidentNavigation from '../../../components/IncidentNavigation';
import InvestigationForm from '../../../components/InvestigationForm';

export default async function NewInvestigationPage() {
  try {
    const identity = await fetchIdentity();
    return <main className="diagnosis incidentWorkspace" lang="pt-BR"><IncidentNavigation />
      <section className="diagnosisHero"><div className="diagnosisKicker">Nova investigação</div><h1>Iniciar investigação</h1><p className="muted">Informe o incidente para coletar evidências e avaliar possíveis causas.</p></section>
      <section className="panel diagnosisSection">{identity.can_investigate && identity.clusters.length ? <InvestigationForm identity={identity} /> : <><h2>Acesso restrito à investigação</h2><p>Sua sessão precisa de permissão de investigador e acesso a um cluster.</p><a href="/incidents">Consultar incidentes</a></>}</section>
    </main>;
  } catch {
    return <main className="diagnosis" lang="pt-BR"><IncidentNavigation /><section className="panel errorPanel"><h1>Não foi possível verificar seu acesso</h1><p>Confira sua sessão e a conexão com o backend.</p><a href="/incidents">Voltar aos incidentes</a></section></main>;
  }
}
