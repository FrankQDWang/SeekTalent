import type { SourceKind } from './types';

export type PlaybackState = 'idle' | 'running' | 'paused' | 'complete';

export const RECRUITER_RUN_DURATION_SECONDS = 34;

export type RecruiterPhase = {
  id: 'parse' | 'first-search' | 'reflect' | 'aha' | 'second-search' | 'complete';
  label: string;
  at: number;
  tone: 'blue' | 'teal' | 'violet' | 'amber' | 'green';
};

export type RecruiterGraphNode = {
  id: string;
  at: number;
  kind: '岗位' | '拆解' | '检索' | '命中' | '过滤' | '反思' | '灵光' | '排序';
  label: string;
  detail: string;
  x: number;
  y: number;
  tone: RecruiterPhase['tone'] | 'neutral' | 'rose';
};

export type RecruiterGraphEdge = {
  from: string;
  to: string;
  tone: RecruiterPhase['tone'] | 'neutral' | 'rose';
};

export type RecruiterLogEntry = {
  id: string;
  at: number;
  tag: 'SYS' | 'THINK' | 'PLAN' | 'SCAN' | 'HIT' | 'REFLECT' | 'AHA';
  text: string;
};

export type RecruiterCandidate = {
  id: string;
  at: number;
  rank: string;
  name: string;
  meta: string;
  title: string;
  current: string;
  score: number;
  evidenceTag: string;
  evidence: string[];
  why: string;
  source: string;
};

export type RecruiterChannelSnapshot = {
  sourceKind: SourceKind;
  subtitle: string;
  status: string;
  scanned: number;
  hits: number;
  active: boolean;
  complete: boolean;
  tone: RecruiterPhase['tone'];
};

export type RecruiterRunSnapshot = {
  elapsedSeconds: number;
  totalSeconds: number;
  progressPercent: number;
  statusLabel: string;
  topTimer: string;
  bottomTimer: string;
  activePhaseId: RecruiterPhase['id'];
  nodeCount: number;
  shortlistCount: number;
  graphNodes: RecruiterGraphNode[];
  graphEdges: RecruiterGraphEdge[];
  logEntries: RecruiterLogEntry[];
  candidates: RecruiterCandidate[];
  completionText: string | null;
};

export const RECRUITER_PHASES: RecruiterPhase[] = [
  { id: 'parse', label: '解析', at: 0, tone: 'blue' },
  { id: 'first-search', label: '第 1 轮检索', at: 8, tone: 'teal' },
  { id: 'reflect', label: '反思', at: 18, tone: 'violet' },
  { id: 'aha', label: '灵光', at: 22, tone: 'amber' },
  { id: 'second-search', label: '第 2 轮检索', at: 26, tone: 'teal' },
  { id: 'complete', label: '完成', at: RECRUITER_RUN_DURATION_SECONDS, tone: 'green' },
];

const graphNodes: RecruiterGraphNode[] = [
  {
    id: 'job',
    at: 2,
    kind: '岗位',
    label: '岗位需求',
    detail: '增长运营总监 · 上海',
    x: 9,
    y: 50,
    tone: 'neutral',
  },
  { id: 'industry', at: 4, kind: '拆解', label: '行业锚点', detail: '新消费 / 美妆 / 食饮', x: 24, y: 20, tone: 'blue' },
  { id: 'metrics', at: 4, kind: '拆解', label: '能力硬指标', detail: 'GMV 3亿+ · DTC · 团队 20+', x: 24, y: 36, tone: 'blue' },
  { id: 'platform', at: 6, kind: '拆解', label: '平台经验', detail: '抖音 / 小红书 / 淘宝', x: 24, y: 53, tone: 'blue' },
  { id: 'seniority', at: 6, kind: '拆解', label: '职级与年限', detail: '总监 · 8+ 年', x: 24, y: 70, tone: 'blue' },
  { id: 'soft', at: 6, kind: '拆解', label: '软性匹配', detail: '0-1 搭建 / 跨部门', x: 24, y: 86, tone: 'blue' },
  { id: 'exact', at: 8, kind: '检索', label: '完全正面匹配', detail: '新消费 + 增长总监 + 上海', x: 43, y: 25, tone: 'teal' },
  { id: 'hard-filter', at: 8, kind: '检索', label: '硬指标筛选', detail: 'GMV ≥ 3亿 AND 团队 ≥ 20', x: 43, y: 43, tone: 'teal' },
  { id: 'platform-search', at: 10, kind: '检索', label: '平台专家', detail: '抖音头部 操盘手', x: 43, y: 62, tone: 'teal' },
  { id: 'local', at: 10, kind: '检索', label: '本地库', detail: 'CTS · 语义索引', x: 58, y: 22, tone: 'teal' },
  { id: 'liepin', at: 12, kind: '检索', label: '猎聘', detail: '简介卡片 · 顺序浏览', x: 58, y: 40, tone: 'teal' },
  { id: 'ats', at: 12, kind: '检索', label: '协作池', detail: '历史项目与内部推荐', x: 58, y: 58, tone: 'violet' },
  { id: 'li', at: 14, kind: '命中', label: '李 ***', detail: '美妆品牌 · 增长总监 · 86', x: 74, y: 15, tone: 'green' },
  { id: 'chen', at: 14, kind: '命中', label: '陈 ***', detail: '食饮 DTC · 高级经理 · 71', x: 74, y: 30, tone: 'green' },
  { id: 'wang', at: 16, kind: '命中', label: '王 ***', detail: '抖音代运营 · 合伙人 · 64', x: 74, y: 45, tone: 'green' },
  { id: 'pool', at: 16, kind: '过滤', label: '候选池 · 37 人', detail: '初筛通过 · 均分偏低', x: 74, y: 60, tone: 'rose' },
  { id: 'reflect', at: 18, kind: '反思', label: '反思：行业锚点过窄', detail: '高分样本过少，召回可能偏窄', x: 48, y: 76, tone: 'violet' },
  { id: 'aha', at: 22, kind: '灵光', label: '灵光一闪', detail: '母婴 / 宠物 / 轻医美 的增长操盘手', x: 35, y: 88, tone: 'amber' },
  { id: 'adjacent', at: 24, kind: '检索', label: '相邻行业迁移', detail: '母婴 / 宠物 / 轻医美 + 增长总监', x: 56, y: 78, tone: 'teal' },
  { id: 'semantic', at: 24, kind: '检索', label: '语义召回', detail: '用户分层 × 私域复购 · 长文本匹配', x: 56, y: 92, tone: 'teal' },
  { id: 'public', at: 26, kind: '检索', label: '公开信息', detail: '专访 / 演讲 / 媒体报道', x: 70, y: 80, tone: 'amber' },
  { id: 'local-semantic', at: 26, kind: '检索', label: '本地库 · 语义', detail: '相邻能力样本扩召回', x: 70, y: 93, tone: 'teal' },
  { id: 'zhao', at: 28, kind: '命中', label: '赵 ***', detail: '宠物食品 · 增长副总 · 91', x: 84, y: 72, tone: 'green' },
  { id: 'zhou', at: 28, kind: '命中', label: '周 ***', detail: '轻医美 · 前淘宝增长 · 88', x: 84, y: 84, tone: 'green' },
  { id: 'huang', at: 28, kind: '命中', label: '黄 ***', detail: '母婴头部 · 操盘 5亿 GMV · 94', x: 84, y: 95, tone: 'green' },
  { id: 'verify', at: 32, kind: '反思', label: '交叉验证', detail: '36氪专访印证 GMV 数据', x: 78, y: 88, tone: 'violet' },
  { id: 'rank', at: 34, kind: '排序', label: '聚合排序', detail: '3 位强匹配 · 4 位待评估', x: 92, y: 50, tone: 'neutral' },
];

const graphEdges: RecruiterGraphEdge[] = [
  { from: 'job', to: 'industry', tone: 'blue' },
  { from: 'job', to: 'metrics', tone: 'blue' },
  { from: 'job', to: 'platform', tone: 'blue' },
  { from: 'job', to: 'seniority', tone: 'blue' },
  { from: 'job', to: 'soft', tone: 'blue' },
  { from: 'industry', to: 'exact', tone: 'teal' },
  { from: 'metrics', to: 'hard-filter', tone: 'teal' },
  { from: 'platform', to: 'platform-search', tone: 'teal' },
  { from: 'exact', to: 'local', tone: 'teal' },
  { from: 'hard-filter', to: 'liepin', tone: 'teal' },
  { from: 'platform-search', to: 'ats', tone: 'violet' },
  { from: 'local', to: 'li', tone: 'green' },
  { from: 'local', to: 'chen', tone: 'green' },
  { from: 'liepin', to: 'wang', tone: 'green' },
  { from: 'ats', to: 'pool', tone: 'rose' },
  { from: 'pool', to: 'reflect', tone: 'violet' },
  { from: 'reflect', to: 'aha', tone: 'amber' },
  { from: 'aha', to: 'adjacent', tone: 'amber' },
  { from: 'aha', to: 'semantic', tone: 'amber' },
  { from: 'adjacent', to: 'public', tone: 'amber' },
  { from: 'semantic', to: 'local-semantic', tone: 'teal' },
  { from: 'public', to: 'zhao', tone: 'green' },
  { from: 'public', to: 'zhou', tone: 'green' },
  { from: 'local-semantic', to: 'huang', tone: 'green' },
  { from: 'huang', to: 'verify', tone: 'violet' },
  { from: 'li', to: 'rank', tone: 'neutral' },
  { from: 'zhao', to: 'rank', tone: 'neutral' },
  { from: 'zhou', to: 'rank', tone: 'neutral' },
  { from: 'huang', to: 'rank', tone: 'neutral' },
];

const logEntries: RecruiterLogEntry[] = [
  { id: 'start', at: 0.8, tag: 'SYS', text: '启动检索任务 · Aurora-2026-037' },
  { id: 'parse', at: 2, tag: 'THINK', text: '解析岗位需求 …' },
  { id: 'dimensions', at: 4, tag: 'THINK', text: '识别 5 个检索维度：行业、硬指标、平台、职级、软性' },
  { id: 'plan-one', at: 8, tag: 'PLAN', text: '制定第 1 轮检索策略：正面匹配 + 硬指标过滤' },
  { id: 'query-one', at: 10, tag: 'SYS', text: '→ 查询 CTS · 猎聘 · 协作池' },
  { id: 'scan-local', at: 12, tag: 'SCAN', text: 'CTS：扫描 12,847 份 · 命中 218' },
  { id: 'scan-liepin', at: 12.5, tag: 'SCAN', text: '猎聘：解析简介卡片 1,204 份' },
  { id: 'scan-ats', at: 13, tag: 'SCAN', text: '协作池：筛出 89 条可用' },
  { id: 'hit-one', at: 14, tag: 'HIT', text: '初筛 37 人 · 3 人 >80 分' },
  { id: 'narrow', at: 16, tag: 'THINK', text: '高分样本密度偏低，召回过窄' },
  { id: 'reflect', at: 18, tag: 'REFLECT', text: '反思：行业锚点限制过强，强行业未必强能力' },
  { id: 'migration', at: 20, tag: 'THINK', text: '能力迁移假设：消费品增长逻辑可跨品类' },
  { id: 'aha', at: 22, tag: 'AHA', text: '灵光：相邻行业，母婴 / 宠物 / 轻医美' },
  { id: 'plan-two', at: 24, tag: 'PLAN', text: '第 2 轮：相邻行业召回 + 语义长文本匹配' },
  { id: 'query-two', at: 26, tag: 'SYS', text: '→ 启用公开信息抓取 + 本地语义通道' },
  { id: 'scan-public', at: 28, tag: 'SCAN', text: '抓取 36氪 / 虎嗅 / 界面 · 专访 148 篇' },
  { id: 'scan-semantic', at: 29, tag: 'SCAN', text: '语义通道：embedding 近邻 64 条' },
  { id: 'hit-two', at: 30, tag: 'HIT', text: '7 位候选人进入短名单，按岗位权重重排' },
  { id: 'verify', at: 32, tag: 'REFLECT', text: '交叉验证：黄 *** 的 5.2亿 GMV 在专访中获印证' },
  { id: 'rank', at: 33, tag: 'THINK', text: '排序：按岗位权重重计算得分' },
  { id: 'done', at: 34, tag: 'SYS', text: '✓ 完成 · 7 位候选人进入短名单' },
  { id: 'summary', at: 34, tag: 'SYS', text: '耗时 33.1s · 检索轮次 2 · 调用渠道 2' },
];

const weakCandidate: RecruiterCandidate = {
  id: 'C-LI',
  at: 14,
  rank: '01',
  name: '李 ***',
  meta: 'F · 35岁 · 9年',
  title: '美妆品牌 · 增长总监',
  current: '当前 · 某新锐彩妆',
  score: 86,
  evidenceTag: '正面行业匹配，团队规模达标',
  evidence: ['GMV 2.8 亿，接近但未达阈值'],
  why: '行业最贴合，但硬指标略差一口气。',
  source: 'CTS',
};

const finalCandidates: RecruiterCandidate[] = [
  {
    id: 'C-HUANG',
    at: 30,
    rank: '01',
    name: '黄 ***',
    meta: 'F · 36岁 · 11年',
    title: '母婴头部品牌 · 增长副总裁',
    current: '当前 · 某母婴独角兽',
    score: 94,
    evidenceTag: '灵光命中 · 来自相邻行业召回',
    evidence: ['主导品牌 0→1 拆解，2 年内达成 5.2 亿 GMV', '搭建 30 人增长团队，覆盖投放 / 私域 / 内容', '抖音 + 小红书双平台 top 3 操盘经验'],
    why: '跨行业但能力维度高度吻合，3 项硬指标全部超标。',
    source: 'CTS（语义召回）',
  },
  {
    id: 'C-ZHAO',
    at: 30,
    rank: '02',
    name: '赵 ***',
    meta: 'F · 38岁 · 13年',
    title: '宠物食品 · 增长副总',
    current: '当前 · 某头部宠物粮',
    score: 91,
    evidenceTag: '灵光命中 · 来自相邻行业召回',
    evidence: ['快消大厂 8 年 + 新消费 5 年', '操盘过 3 个新品牌从 0 到品类 top 5', 'MBA · 已验证 IPO 前经历'],
    why: '硬指标最稳，且有你偏好的快消 + 互联网双背景。',
    source: '猎聘',
  },
  {
    id: 'C-ZHOU',
    at: 30,
    rank: '03',
    name: '周 ***',
    meta: 'M · 34岁 · 9年',
    title: '轻医美 · 前淘宝增长负责人',
    current: '当前 · 某轻医美连锁',
    score: 88,
    evidenceTag: '灵光命中 · 来自相邻行业召回',
    evidence: ['淘宝时期主导美妆类目站外拉新', '切入轻医美后 DTC 复购率行业 top', '有完整 0-1 搭建 + 跨部门经历'],
    why: '平台基因 + 相邻赛道实战，补齐 DTC 能力。',
    source: '公开信息交叉验证',
  },
  weakCandidate,
];

export function clampRunElapsed(elapsedSeconds: number): number {
  return Math.min(RECRUITER_RUN_DURATION_SECONDS, Math.max(0, elapsedSeconds));
}

export function getRecruiterRunSnapshot(playbackState: PlaybackState, elapsedSeconds: number): RecruiterRunSnapshot {
  const elapsed = playbackState === 'idle' ? 0 : clampRunElapsed(elapsedSeconds);
  const visibleNodes = graphNodes.filter((node) => node.at <= elapsed);
  const visibleNodeIds = new Set(visibleNodes.map((node) => node.id));
  const visibleCandidates = elapsed >= 30 ? finalCandidates : elapsed >= weakCandidate.at ? [weakCandidate] : [];

  return {
    elapsedSeconds: elapsed,
    totalSeconds: RECRUITER_RUN_DURATION_SECONDS,
    progressPercent: (elapsed / RECRUITER_RUN_DURATION_SECONDS) * 100,
    statusLabel: statusLabelFor(playbackState, elapsed),
    topTimer: `${elapsed.toFixed(1)}s / ${RECRUITER_RUN_DURATION_SECONDS.toFixed(1)}s`,
    bottomTimer: `${elapsed.toFixed(1)} / ${RECRUITER_RUN_DURATION_SECONDS}s`,
    activePhaseId: phaseFor(elapsed).id,
    nodeCount: visibleNodes.length,
    shortlistCount: visibleCandidates.length,
    graphNodes: visibleNodes,
    graphEdges: graphEdges.filter((edge) => visibleNodeIds.has(edge.from) && visibleNodeIds.has(edge.to)),
    logEntries: logEntries.filter((entry) => entry.at <= elapsed),
    candidates: visibleCandidates,
    completionText: elapsed >= RECRUITER_RUN_DURATION_SECONDS ? '检索完成 · 7 位候选人进入短名单' : null,
  };
}

export function getRecruiterChannelSnapshot(sourceKind: SourceKind, elapsedSeconds: number): RecruiterChannelSnapshot {
  const elapsed = clampRunElapsed(elapsedSeconds);
  if (sourceKind === 'liepin') {
    return {
      sourceKind,
      subtitle: '猎聘在线简历 · 顺序简介扫描',
      status: channelStatus(elapsed, { secondRound: false }),
      scanned: channelCounter(elapsed, [
        [10, 0],
        [12, 418],
        [14, 1204],
      ]),
      hits: channelCounter(elapsed, [
        [14, 1],
      ]),
      active: elapsed >= 10 && elapsed < 16,
      complete: elapsed >= 16,
      tone: 'teal',
    };
  }

  return {
    sourceKind,
    subtitle: '本地简历库 · CTS 语义索引',
    status: channelStatus(elapsed, { secondRound: true }),
    scanned: channelCounter(elapsed, [
      [10, 0],
      [12, 12847],
      [16, 12847],
      [28, 12911],
    ]),
    hits: channelCounter(elapsed, [
      [14, 3],
      [28, 5],
    ]),
    active: (elapsed >= 10 && elapsed < 16) || (elapsed >= 26 && elapsed < 30),
    complete: elapsed >= 30,
    tone: 'green',
  };
}

function statusLabelFor(playbackState: PlaybackState, elapsed: number): string {
  if (playbackState === 'idle') {
    return '待命';
  }
  if (elapsed >= RECRUITER_RUN_DURATION_SECONDS || playbackState === 'complete') {
    return '已完成';
  }
  if (playbackState === 'paused') {
    return '暂停';
  }
  if (elapsed >= 26 && elapsed < 30) {
    return '检索中 · 第 2 轮 (语义)';
  }
  if (elapsed >= 10 && elapsed < 18) {
    return '检索中 · 第 1 轮';
  }
  return '推理中';
}

function phaseFor(elapsed: number): RecruiterPhase {
  return [...RECRUITER_PHASES].reverse().find((phase) => elapsed >= phase.at) ?? RECRUITER_PHASES[0];
}

function channelStatus(elapsed: number, options: { secondRound: boolean }): string {
  if (elapsed <= 0) {
    return '待命';
  }
  if (elapsed >= 30 && options.secondRound) {
    return '两轮完成';
  }
  if (elapsed >= 16 && !options.secondRound) {
    return '本轮完成';
  }
  if (elapsed >= 26 && options.secondRound) {
    return '语义召回';
  }
  if (elapsed >= 10) {
    return '扫描中';
  }
  return '待命';
}

function channelCounter(elapsed: number, points: Array<[number, number]>): number {
  let value = 0;
  for (const [at, nextValue] of points) {
    if (elapsed >= at) {
      value = nextValue;
    }
  }
  return value;
}
