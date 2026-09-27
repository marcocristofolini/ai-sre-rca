import InvestigationView from '../../../components/InvestigationView';
import { notFound } from 'next/navigation';
import { fetchStatefulDemo, type StatefulDependencyKind } from '../../../lib/api';

const supported = new Set<StatefulDependencyKind>(['postgresql', 'kafka', 'redis']);

export default async function StatefulDependencyDemoPage({
  params,
}: {
  params: Promise<{ dependency: string }>;
}) {
  const { dependency } = await params;
  if (!supported.has(dependency as StatefulDependencyKind)) {
    notFound();
  }
  const selected = dependency as StatefulDependencyKind;

  try {
    const data = await fetchStatefulDemo(selected);
    return <InvestigationView data={data} />;
  } catch (error) {
    return (
      <main>
        <section className="panel errorPanel">
          <div className="sectionLabel">STATEFUL DEMO UNAVAILABLE</div>
          <h1>AI SRE RCA</h1>
          <p>Start the FastAPI service on port 8080, then reload this page.</p>
          <pre>{String(error)}</pre>
        </section>
      </main>
    );
  }
}
