import { notFound } from 'next/navigation';
import OrchestrationProgress from '../../../../components/OrchestrationProgress';

export default async function InvestigationJobPage({ params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = await params;
  if (!/^[a-zA-Z0-9_-]{1,128}$/.test(jobId)) notFound();
  return <main className="diagnosis" lang="pt-BR">
    <header className="topbar"><a className="brand" href="/"><span className="brandMark">SRE</span><span>Tracecroft AI</span></a><nav className="topActions"><a href="/incidents">Incidentes</a><a href="/incidents/new">Nova investigação</a></nav></header>
    <div className="diagnosisBreadcrumb">Investigações <span>/</span> Execução ao vivo <span>/</span> <code>{jobId}</code></div>
    <section className="diagnosisHero"><div className="diagnosisKicker"><span>INVESTIGAÇÃO REAL</span><span>Somente leitura</span></div><h1>Investigação em andamento</h1><p className="muted">Acompanhe a coleta e a avaliação determinística em tempo real.</p></section>
    <OrchestrationProgress jobId={jobId} />
  </main>;
}
