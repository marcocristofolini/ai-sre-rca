'use client';

import { useActionState, useState, type ChangeEvent } from 'react';
import { investigateFromForm } from '../app/incidents/new/actions';
import type { SystemIdentity } from '../lib/api';

export default function InvestigationForm({ identity }: { identity: SystemIdentity }) {
  const [values, setValues] = useState<Record<string, string>>({ alert_name: 'ManualInvestigation', severity: 'warning', mode: identity.agentic_execution_enabled ? 'agentic' : 'classic' });
  const field = (name: string) => ({ name, value: values[name] ?? '', onChange: (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setValues(previous => ({ ...previous, [name]: event.target.value })) });
  const [state, action, pending] = useActionState(investigateFromForm, { error: '' });
  return <form action={action} className="investigationForm">
    <fieldset disabled={pending}><legend>Contexto do incidente</legend>
      <label>Título<input {...field('title')} required minLength={3} maxLength={200} placeholder="Falhas no checkout" /></label>
      <label>Cluster{identity.clusters.includes('*') ? <input {...field('cluster')} required maxLength={255} placeholder="Nome do cluster configurado" /> : <select {...field('cluster')} required><option value="" disabled>Selecione um cluster</option>{identity.clusters.map(cluster => <option key={cluster}>{cluster}</option>)}</select>}</label>
      {identity.tenant_id === '*' ? <label>Tenant<input {...field('tenant_id')} required maxLength={255} /></label> : <p className="muted">Tenant: {identity.tenant_id}</p>}
      <label>Alerta<input {...field('alert_name')} maxLength={120} /></label>
      <label>Severidade<select {...field('severity')}><option value="info">Informativa</option><option value="warning">Atenção</option><option value="critical">Crítica</option></select></label>
      <label>Início do incidente (opcional)<input {...field('starts_at')} maxLength={40} placeholder="2026-10-08T16:00:00-03:00" /><small>ISO 8601 com fuso horário. Se vazio, usa o horário de envio.</small></label>
    </fieldset>
    <fieldset disabled={pending}><legend>Alvo da investigação</legend>
      <label>Namespace<input {...field('namespace')} maxLength={63} /></label><label>Pod<input {...field('pod')} maxLength={253} /></label><label>Serviço<input {...field('service')} maxLength={253} /></label>
      <p className="diagnosisNote">Informe o alvo conhecido para orientar a coleta. Campos vazios não serão inferidos como fatos.</p>
      <label>Modo<select {...field('mode')}><option value="classic">Clássico</option>{identity.agentic_execution_enabled && <option value="agentic">Agentic</option>}</select></label>
    </fieldset>
    {identity.demo_mode && <p className="diagnosisNote">Ambiente em modo demo: os dados coletados são de demonstração.</p>}
    {state.error && <p className="incidentFormError" role="alert">{state.error}</p>}
    <p className="muted">A investigação é síncrona e pode levar alguns minutos. Ao terminar, o diagnóstico salvo será aberto.</p>
    <div className="incidentFormActions"><button type="submit" disabled={pending}>{pending ? 'Investigando…' : 'Iniciar investigação'}</button><a href="/incidents">Voltar aos incidentes</a></div>
    <p role="status" aria-live="polite">{pending ? 'Coletando evidências e avaliando hipóteses. Aguarde.' : ''}</p>
  </form>;
}
