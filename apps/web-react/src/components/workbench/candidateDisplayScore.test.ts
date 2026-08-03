import { describe, expect, it } from "vitest";
import { candidateDisplayScore } from "./candidateDisplayScore";

describe("candidateDisplayScore", () => {
  it.each([
    [0, 60],
    [20, 66],
    [40, 73],
    [59, 79],
    [60, 80],
    [80, 90],
    [100, 100],
  ])("maps raw score %i to display score %i", (rawScore, displayScore) => {
    expect(candidateDisplayScore(rawScore)).toBe(displayScore);
  });

  it("always returns an integer and clamps the display boundary", () => {
    expect(Number.isInteger(candidateDisplayScore(42.5))).toBe(true);
    expect(candidateDisplayScore(-10)).toBe(60);
    expect(candidateDisplayScore(110)).toBe(100);
  });
});
