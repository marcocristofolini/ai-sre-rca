export default function IncidentNavigation() {
  return <header className="topbar">
    <a className="brand" href="/incidents"><span className="brandMark">SRE</span><span>AI SRE RCA</span></a>
    <nav className="topActions" aria-label="Aplicação">
      <a className="demoLink" href="/showcase">Produto</a>
      <a className="demoLink" href="/incidents">Incidentes</a>
      <a className="demoLink" href="/incidents/new">Iniciar investigação</a>
      <a className="demoLink" href="/awareness">Grafo</a>
      <a className="demoLink" href="/ops">Operação</a>
      <a className="demoLink" href="/">Demonstrações</a>
    </nav>
  </header>;
}
