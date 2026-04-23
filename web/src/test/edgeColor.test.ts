import { describe, expect, it } from "vitest";

import { stateBadgeClass, styleForState } from "../utils/edgeColor";

describe("styleForState", () => {
  it("maps up to emerald", () => {
    expect(styleForState("up").stroke).toBe("#10b981");
    expect(styleForState("up").strokeDasharray).toBeUndefined();
  });

  it("maps degraded to amber", () => {
    expect(styleForState("degraded").stroke).toBe("#f59e0b");
  });

  it("maps down to rose with thick stroke", () => {
    expect(styleForState("down").stroke).toBe("#f43f5e");
    expect(styleForState("down").strokeWidth).toBeGreaterThanOrEqual(3);
  });

  it("maps unknown / null to slate dashed", () => {
    for (const value of ["unknown", undefined, null, "garbage"] as const) {
      const s = styleForState(value as string | undefined);
      expect(s.stroke).toBe("#94a3b8");
      expect(s.strokeDasharray).toBe("6 4");
    }
  });

  it("stateBadgeClass composes tailwind utilities", () => {
    expect(stateBadgeClass("up")).toMatch(/bg-emerald-50/);
    expect(stateBadgeClass("up")).toMatch(/text-emerald-700/);
    expect(stateBadgeClass("down")).toMatch(/bg-rose-50/);
  });
});
