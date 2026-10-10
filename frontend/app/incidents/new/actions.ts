'use server';

import { redirect } from 'next/navigation';
import { fetchIdentity, IncidentFetchError, startInvestigation } from '../../../lib/api';

export type InvestigationFormState = { error: string };

export async function investigateFromForm(_previous: InvestigationFormState, form: FormData): Promise<InvestigationFormState> {
  let incidentId: string;
  try {
    const identity = await fetchIdentity();
    if (!identity.can_investigate) return { error: 'Sua sessão não tem permissão para iniciar investigações.' };
    const input: Record<string, string> = {};
    const limits: Record<string, number> = { title: 200, cluster: 255, namespace: 63, pod: 253, service: 253, alert_name: 120, tenant_id: 255 };
    for (const [key, limit] of Object.entries(limits)) {
      const raw = form.get(key);
      if (raw !== null && typeof raw !== 'string') return { error: 'Campo inválido no formulário.' };
      const value = (raw ?? '').trim();
      if (value.length > limit) return { error: 'Um dos campos excede o tamanho permitido.' };
      if (value) input[key] = value;
    }
    if (!input.title || input.title.length < 3 || !input.cluster) return { error: 'Informe um título com pelo menos 3 caracteres e um cluster.' };
    if (!identity.clusters.includes('*') && !identity.clusters.includes(input.cluster)) return { error: 'Cluster não autorizado para sua sessão.' };
    input.tenant_id = identity.tenant_id === '*' ? input.tenant_id : identity.tenant_id;
    if (!input.tenant_id || input.tenant_id === '*') return { error: 'Informe um tenant específico para esta investigação.' };
    if (input.namespace && !/^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/.test(input.namespace)) return { error: 'Namespace inválido.' };
    input.severity = String(form.get('severity') ?? 'warning');
    if (!['info', 'warning', 'critical'].includes(input.severity)) return { error: 'Severidade inválida.' };
    const mode = form.get('mode');
    if (mode !== 'classic' && mode !== 'agentic') return { error: 'Modo de investigação inválido.' };
    if (mode === 'agentic' && !identity.agentic_execution_enabled) return { error: 'A investigação agentic está desabilitada neste ambiente.' };
    const startsAt = form.get('starts_at');
    if (startsAt) {
      if (typeof startsAt !== 'string' || startsAt.length > 40 || !/(Z|[+-]\d{2}:\d{2})$/.test(startsAt) || !Number.isFinite(Date.parse(startsAt))) return { error: 'Horário inválido. Use uma data ISO 8601 com fuso horário.' };
      input.starts_at = new Date(startsAt).toISOString();
    }
    incidentId = await startInvestigation(input, mode);
  } catch (error) {
    if (error instanceof IncidentFetchError) {
      if ([401, 403].includes(error.status)) return { error: 'Acesso negado. Verifique sua sessão e permissões.' };
      if (error.status === 404) return { error: 'Este modo de investigação não está disponível no backend.' };
      if (error.status === 422) return { error: 'O backend rejeitou os dados. Confira o cluster e o alvo informado.' };
    }
    return { error: 'Não foi possível confirmar a conclusão. Consulte o histórico antes de tentar novamente.' };
  }
  redirect(`/incidents/${encodeURIComponent(incidentId)}`);
}
