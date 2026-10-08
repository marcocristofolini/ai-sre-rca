'use client';

export default function IncidentError({ reset }: { reset: () => void }) {
  return <main lang="pt-BR"><section className="panel errorPanel"><h1>Não foi possível carregar o diagnóstico</h1><p>A investigação está indisponível. Tente novamente quando a API estiver acessível.</p><button type="button" onClick={reset}>Tentar novamente</button></section></main>;
}
