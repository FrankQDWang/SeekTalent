import { describe, expect, it } from 'vitest';

import { fallbackLayout, stackLanePositions, toElkGraph } from './strategyGraphLayout';
import type { RecruiterGraphEdge, RecruiterGraphNode, RecruiterLane } from './recruiterAnimation';

const bounds = { width: 900, height: 500 };

function graphNode(id: string, lane: RecruiterLane, x: number, y: number): RecruiterGraphNode {
  return {
    id,
    at: 1,
    kind: '检索',
    label: id,
    detail: id,
    x,
    y,
    tone: 'blue',
    lane,
  };
}

describe('strategy graph layout', () => {
  it('builds an ELK layered LTR graph without lane partitions', () => {
    const nodes = [
      graphNode('job', 'shared', 0.08, 0.42),
      graphNode('requirements', 'shared', 0.24, 0.42),
      graphNode('cts-query', 'cts', 0.4, 0.22),
    ];
    const edges: RecruiterGraphEdge[] = [
      { from: 'job', to: 'requirements', tone: 'neutral' },
      { from: 'requirements', to: 'cts-query', tone: 'blue' },
    ];

    const graph = toElkGraph(nodes, edges);
    const ctsQuery = graph.children?.find((child) => child.id === 'cts-query');

    expect(graph.layoutOptions?.['elk.algorithm']).toBe('layered');
    expect(graph.layoutOptions?.['elk.direction']).toBe('RIGHT');
    expect(ctsQuery).toBeDefined();
    expect(ctsQuery?.layoutOptions).toBeUndefined();
    expect(graph.edges?.[0]).toEqual({
      id: 'job->requirements',
      sources: ['job'],
      targets: ['requirements'],
    });
  });

  it('stacks CTS and Liepin lanes vertically after preserving ELK x positions', () => {
    const nodes = [
      graphNode('cts-query', 'cts', 0.42, 0.22),
      graphNode('liepin-search', 'liepin', 0.42, 0.62),
      graphNode('final-shortlist', 'shared', 0.8, 0.42),
    ];
    const positions = stackLanePositions(
      new Map([
        ['cts-query', { x: 120, y: 100 }],
        ['liepin-search', { x: 120, y: 100 }],
        ['final-shortlist', { x: 300, y: 100 }],
      ]),
      nodes,
      bounds,
    );
    const cts = positions.get('cts-query');
    const liepin = positions.get('liepin-search');
    const final = positions.get('final-shortlist');

    expect(cts).toBeDefined();
    expect(liepin).toBeDefined();
    expect(final).toBeDefined();
    expect(cts?.y).toBeLessThan(liepin?.y ?? 0);
    expect(cts?.x).toBe(liepin?.x);
    expect(final?.x).toBeGreaterThan(cts?.x ?? 0);
    expect(final?.x).toBeGreaterThan(liepin?.x ?? 0);
  });

  it('uses lane-stacked positions in fallback layout', () => {
    const nodes = [
      graphNode('cts-query', 'cts', 0.42, 0.22),
      graphNode('liepin-search', 'liepin', 0.42, 0.62),
      graphNode('final-shortlist', 'shared', 0.8, 0.42),
    ];
    const edges: RecruiterGraphEdge[] = [{ from: 'cts-query', to: 'final-shortlist', tone: 'blue' }];

    const layout = fallbackLayout(nodes, edges, bounds);
    const cts = layout.nodes.find((node) => node.id === 'cts-query');
    const liepin = layout.nodes.find((node) => node.id === 'liepin-search');
    const final = layout.nodes.find((node) => node.id === 'final-shortlist');

    expect(cts).toBeDefined();
    expect(liepin).toBeDefined();
    expect(final).toBeDefined();
    expect(cts?.position.y).toBeLessThan(liepin?.position.y ?? 0);
    expect(cts?.position.x).toBe(liepin?.position.x);
    expect(cts?.selected).toBe(false);
    expect(cts?.data.selected).toBe(false);
    expect(final?.position.x).toBe(bounds.width - 168 - 34);
  });
});
