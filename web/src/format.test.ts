import { describe, expect, it } from "vitest";
import { clock, km, meters, minutesOfDay, percent, sentence, versus } from "./format";

describe("format", () => {
  it("shows ratios as percents", () => {
    expect(percent(0.9631)).toBe("96.3%");
    expect(percent(null)).toBe("no data yet");
  });

  it("rounds distances", () => {
    expect(meters(812.46)).toBe("812 m");
    expect(meters(12345)).toBe("12,345 m");
    expect(km(102000)).toBe("102.0 km");
  });

  it("reads warehouse time in UTC", () => {
    expect(clock("2026-03-02T08:30:00+00:00")).toBe("08:30");
    expect(clock(null)).toBe("");
    expect(minutesOfDay("2026-03-02T13:15:30+00:00")).toBeCloseTo(795.5);
  });

  it("compares a walk against S-shape", () => {
    expect(versus(800, 600)).toBe("25% less than");
    expect(versus(800, 800)).toBe("the same as");
    expect(versus(156, 208)).toBe("33% more than");
    expect(versus(0, 10)).toBe("the same as");
  });

  it("turns event kinds into words", () => {
    expect(sentence("wave.released")).toBe("Wave released");
    expect(sentence("released")).toBe("Released");
  });
});
