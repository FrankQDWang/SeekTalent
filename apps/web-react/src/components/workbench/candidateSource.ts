import type { WorkbenchV2CandidateSummary } from "../../lib/api/workbenchV2Types";

export type CandidateSourceKind = NonNullable<
  WorkbenchV2CandidateSummary["sourceKinds"]
>[number];

const sourceLabels: Record<CandidateSourceKind, string> = {
  cts: "CTS 实验",
  liepin: "猎聘",
};

export function candidateSourceLabel(
  sourceKinds: readonly CandidateSourceKind[] | null | undefined,
): string {
  const uniqueKinds = [...new Set(sourceKinds ?? [])];
  if (uniqueKinds.includes("liepin")) {
    return sourceLabels.liepin;
  }
  if (uniqueKinds.includes("cts")) {
    return sourceLabels.cts;
  }
  const [sourceKind] = uniqueKinds;
  return sourceKind ? sourceLabels[sourceKind] : "来源待确认";
}
