export const RECOMMENDATION_MIN_SCORE = 60;

export function candidateDisplayScore(rawScore: number): number {
  const boundedScore = Math.min(100, Math.max(0, rawScore));
  if (boundedScore < RECOMMENDATION_MIN_SCORE) {
    return 60 + Math.round((boundedScore * 19) / 59);
  }
  return 80 + Math.round(((boundedScore - 60) * 20) / 40);
}
