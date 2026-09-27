import InvestigationView from '../../components/InvestigationView';
import { fetchTraceDemo } from '../../lib/api';

export default async function TraceDemoPage() {
  try {
    const data = await fetchTraceDemo();
    return <InvestigationView data={data} />;
  } catch (error) {
    return (
      <main>
        <section className="panel errorPanel">
          <div className="sectionLabel">TRACE DEMO UNAVAILABLE</div>
          <h1>AI SRE RCA</h1>
          <p>Start the FastAPI service on port 8080, then reload this page.</p>
          <pre>{String(error)}</pre>
        </section>
      </main>
    );
  }
}
