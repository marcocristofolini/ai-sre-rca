import { useId } from 'react';
import type { Investigation } from '../lib/api';

export default function IncidentGraph({ data }: { data: Investigation }) {
  const markerId = useId().replaceAll(':', '');
  const entityById = new Map(data.entities.map((entity) => [entity.id, entity]));
  const allIds = [...new Set(data.graph_edges.flatMap((edge) => [edge.source, edge.target]))];
  const ids = allIds.slice(0, 48);
  const visible = new Set(ids);
  const eligibleEdges = data.graph_edges.filter((edge) => visible.has(edge.source) && visible.has(edge.target));
  const edges = eligibleEdges.slice(0, 96);
  const incoming = new Map(ids.map((id) => [id, 0]));
  const levels = new Map(ids.map((id) => [id, 0]));
  for (const edge of edges) incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1);
  const queue = ids.filter((id) => incoming.get(id) === 0);
  const visited = new Set<string>();
  while (queue.length) {
    const id = queue.shift()!;
    visited.add(id);
    for (const edge of edges.filter((item) => item.source === id)) {
      levels.set(edge.target, Math.max(levels.get(edge.target) ?? 0, (levels.get(id) ?? 0) + 1));
      incoming.set(edge.target, (incoming.get(edge.target) ?? 1) - 1);
      if (incoming.get(edge.target) === 0) queue.push(edge.target);
    }
  }
  // Cyclic relationships stay visible, without inventing a causal ordering.
  const cycleLevel = Math.max(0, ...levels.values()) + 1;
  for (const id of ids) if (!visited.has(id)) levels.set(id, cycleLevel);
  const bands = new Map<number, string[]>();
  for (const id of ids) {
    const level = levels.get(id) ?? 0;
    bands.set(level, [...(bands.get(level) ?? []), id]);
  }
  const positions = new Map<string, { x: number; y: number }>();
  let row = 0;
  for (const [, band] of [...bands].sort(([a], [b]) => a - b)) {
    for (let offset = 0; offset < band.length; offset += 3) {
      const batch = band.slice(offset, offset + 3);
      batch.forEach((id, column) => positions.set(id, {
        x: 30 + (3 - batch.length) * 145 + column * 290, y: 100 + row * 155,
      }));
      row += 1;
    }
  }
  if (!ids.length) return null;
  return (
    <figure className="incidentTopology">
      <div className="incidentTopologyViewport" tabIndex={0} aria-label="Grafo de relações, rolável em telas pequenas">
        <svg viewBox={`0 0 900 ${Math.max(240, row * 155 + 80)}`} role="img" aria-label="Entidades e relações observadas nesta investigação">
          <defs><marker id={markerId} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="currentColor" /></marker></defs>
          {edges.map((edge, index) => {
            const source = positions.get(edge.source)!;
            const target = positions.get(edge.target)!;
            const down = target.y > source.y;
            const x1 = source.x + 125, y1 = source.y + (down ? 72 : 36);
            const x2 = target.x + 125, y2 = target.y + (down ? 0 : 36);
            const bend = down ? (y1 + y2) / 2 : y1 - 65 - (index % 3) * 16;
            return <g className="incidentTopologyEdge" key={`${index}-${edge.source}-${edge.target}`}>
              <title>{`${entityById.get(edge.source)?.name ?? edge.source} → ${edge.relationship} → ${entityById.get(edge.target)?.name ?? edge.target}`}</title>
              <path d={`M${x1},${y1} C${x1},${bend} ${x2},${bend} ${x2},${y2}`} markerEnd={`url(#${markerId})`} />
              <text x={(x1 + x2) / 2} y={down ? (y1 + y2) / 2 - 5 : bend + 10} textAnchor="middle">{edge.relationship}</text>
            </g>;
          })}
          {ids.map((id) => {
            const entity = entityById.get(id);
            const pos = positions.get(id)!;
            const name = entity?.name ?? id;
            return <g className="incidentTopologyNode" key={id} transform={`translate(${pos.x},${pos.y})`}>
              <title>{`${entity?.kind ?? 'Entidade'}: ${name}`}</title>
              <rect width="250" height="72" rx="8" />
              <text className="incidentTopologyKind" x="14" y="24">{entity?.kind ?? 'Entidade'}</text>
              <text className="incidentTopologyName" x="14" y="49">{name.length > 31 ? `${name.slice(0, 28)}…` : name}</text>
            </g>;
          })}
        </svg>
      </div>
      <figcaption>Relações retornadas pelo motor. Uma conexão de topologia, isoladamente, não confirma causalidade.</figcaption>
      {(allIds.length > ids.length || data.graph_edges.length > edges.length) && <p className="diagnosisNote">Exibindo {ids.length} de {allIds.length} entidades conectadas e {edges.length} de {data.graph_edges.length} relações. A lista em texto abaixo mantém todas as relações.</p>}
    </figure>
  );
}
