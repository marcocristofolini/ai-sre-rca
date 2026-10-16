import { notFound } from 'next/navigation';
import InvestigationView from '../../../components/InvestigationView';
import IncidentCopilot from '../../../components/IncidentCopilot';
import { fetchIncident, IncidentFetchError } from '../../../lib/api';

export default async function IncidentPage({ params, searchParams }: {
  params: Promise<{ incidentId: string }>;
  searchParams: Promise<{ jobId?: string }>;
}) {
  const { incidentId } = await params;
  const { jobId } = await searchParams;
  if (!/^[a-zA-Z0-9_-]{1,128}$/.test(incidentId)) notFound();
  try {
    const data = await fetchIncident(incidentId);
    return <><InvestigationView data={data} mode="persisted" jobId={jobId} /><div className="diagnosis diagnosisCopilot"><IncidentCopilot incidentId={incidentId} /></div></>;
  } catch (error) {
    if (error instanceof IncidentFetchError) {
      if (error.status === 404) notFound();
      if (error.status === 401 || error.status === 403) {
        return <main lang="pt-BR"><section className="panel errorPanel"><h1>Acesso à investigação restrito</h1><p>Use uma sessão autorizada para este tenant e cluster.</p><a href="/ops">Consultar acesso</a></section></main>;
      }
    }
    throw error;
  }
}
