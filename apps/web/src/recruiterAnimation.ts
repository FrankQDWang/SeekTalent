import type { SourceKind } from './types';

export type RecruiterTone = 'blue' | 'teal' | 'violet' | 'amber' | 'green' | 'neutral' | 'rose';
export type RecruiterLane = 'shared' | SourceKind;

export type RecruiterGraphNode = {
  id: string;
  at: number;
  kind: '岗位' | '拆解' | '检索' | '命中' | '过滤' | '反思' | '灵光' | '排序';
  label: string;
  detail: string;
  x: number;
  y: number;
  tone: RecruiterTone;
  sourceKind?: SourceKind | 'all';
  sourceLabel?: string;
  lane?: RecruiterLane;
};

export type RecruiterGraphEdge = {
  from: string;
  to: string;
  tone: RecruiterTone;
  label?: string;
};

export type RecruiterLogEntry = {
  id: string;
  at: number;
  tag: 'SYS' | 'THINK' | 'PLAN' | 'SCAN' | 'HIT' | 'REFLECT' | 'AHA';
  text: string;
  sourceKind?: SourceKind | 'all';
  sourceLabel?: string;
  lane?: RecruiterLane;
};
