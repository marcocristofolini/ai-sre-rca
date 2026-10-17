'use client';

import { useEffect, useState } from 'react';

export type ScopeSelection = { cluster: string; kind: 'Service' | 'Pod' | 'Deployment' | 'StatefulSet'; name: string; namespace?: string; starts_at: string; ends_at: string };
type Options = { clusters: string[]; cluster: string; targets: Array<{ kind: 'Service' | 'Pod' | 'Deployment' | 'StatefulSet'; name: string; namespace?: string }>; truncated: boolean; min_window_minutes: number; max_window_minutes: number };

export default function CopilotScopeSelector({ incidentId, disabled, onChange }: { incidentId: string; disabled: boolean; onChange: (scope: ScopeSelection | null, valid: boolean) => void }) {
  const [enabled, setEnabled] = useState(false);
  const [cluster, setCluster] = useState('');
  const [kind, setKind] = useState<'Service' | 'Pod' | 'Deployment' | 'StatefulSet'>('Service');
  const [name, setName] = useState('');
  const [namespace, setNamespace] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [options, setOptions] = useState<Options | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { setEnabled(false); setCluster(''); setOptions(null); }, [incidentId]);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setOptions(null); setError('');
    fetch(`/api/incidents/${encodeURIComponent(incidentId)}/copilot/scope-options${cluster ? `?cluster=${encodeURIComponent(cluster)}` : ''}`, { signal: controller.signal, cache: 'no-store' }).then(async (response) => {
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.message ?? 'Inventário indisponível para esta sessão.');
      if (!controller.signal.aborted) setOptions(body);
    }).catch((err) => { if (!controller.signal.aborted) setError(String(err.message)); });
    return () => controller.abort();
  }, [enabled, incidentId, cluster]);
  useEffect(() => {
    if (!enabled) { onChange(null, true); return; }
    const a = Date.parse(start), b = Date.parse(end);
    const valid = !!options && !!name.trim() && Number.isFinite(a) && Number.isFinite(b) && b > a && b <= Date.now() && b - a >= options.min_window_minutes * 60000 && b - a <= options.max_window_minutes * 60000;
    onChange(valid ? { cluster: options!.cluster, kind, name: name.trim(), ...(namespace.trim() ? { namespace: namespace.trim() } : {}), starts_at: new Date(a).toISOString(), ends_at: new Date(b).toISOString() } : null, valid);
  }, [enabled, options, kind, name, namespace, start, end, onChange]);
  return <fieldset className="copilotScope" disabled={disabled}>
    <label><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />Selecionar outro alvo ou período</label>
    {enabled && <div>
      {error && <p role="status">{error}</p>}
      {!options && !error && <p role="status">Consultando inventário autorizado…</p>}
      {options && <><label>Cluster autorizado<select value={cluster || options.cluster} onChange={(event) => { setName(''); setNamespace(''); setCluster(event.target.value); }}>{options.clusters.map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>Tipo de alvo<select value={kind} onChange={(event) => { setName(''); setKind(event.target.value as 'Service' | 'Pod' | 'Deployment' | 'StatefulSet'); }}><option value="Service">Serviço</option><option value="Pod">Workload (Pod)</option><option value="Deployment">Deployment</option><option value="StatefulSet">StatefulSet</option></select></label>
      <label>Namespace do alvo<input value={namespace} maxLength={63} onChange={(event) => setNamespace(event.target.value)} /></label>
      <label>Nome do alvo<input list="copilot-inventory-targets" value={name} maxLength={253} onChange={(event) => setName(event.target.value)} /></label>
      <datalist id="copilot-inventory-targets">{options.targets.filter((item) => item.kind === kind).map((item) => <option key={`${item.kind}/${item.namespace}/${item.name}`} value={item.name}>{item.namespace}</option>)}</datalist>
      {options.truncated && <p>A lista é limitada; informe o nome exato e o namespace para resolver o alvo.</p>}
      <label>Início da consulta (hora local)<input type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} /></label>
      <label>Fim da consulta (hora local)<input type="datetime-local" value={end} onChange={(event) => setEnd(event.target.value)} /></label>
      <p>Janela de {options.min_window_minutes} a {options.max_window_minutes} minutos. A identidade e as permissões serão verificadas novamente antes da execução.</p></>}
    </div>}
  </fieldset>;
}
