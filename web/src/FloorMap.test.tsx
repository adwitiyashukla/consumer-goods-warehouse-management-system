import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Layout, Plan, Slot, Stop } from "./api";
import { FloorMap } from "./FloorMap";

const layout: Layout = {
  aisles: 2,
  bays: 2,
  levels: 3,
  pitch: 6,
  length: 10,
  bay_width: 3,
  end_gap: 2,
  dock_depth: 5,
  depot: { x: 0, y: 0 },
  doors: [
    { code: "IN01", kind: "door_in", x: 4, y: -5 },
    { code: "OUT01", kind: "door_out", x: 2, y: -5 },
    { code: "STAGE", kind: "stage", x: 0, y: 0 },
  ],
};

const slots: Slot[] = [
  { code: "01-01-L1", aisle: 0, bay: 0, side: "L", x: 0, y: 3.5, sku: "SKU00001", velocity: "A", available: 10 },
  { code: "02-02-R1", aisle: 1, bay: 1, side: "R", x: 6, y: 6.5, sku: null, velocity: null, available: 0 },
];

const stops: Stop[] = [
  { code: "01-01-L1", side: "L", x: 0, y: 3.5 },
  { code: "02-02-R1", side: "R", x: 6, y: 6.5 },
];

const plan: Plan = {
  distance_m: 32,
  order: [1, 0],
  path: [
    [0, 0],
    [6, 0],
    [6, 6.5],
    [6, 0],
    [0, 0],
  ],
};

describe("FloorMap", () => {
  it("draws slots by speed and skips the stage", () => {
    const { container } = render(<FloorMap layout={layout} slots={slots} />);
    expect(container.querySelectorAll(".slot-a")).toHaveLength(1);
    expect(container.querySelectorAll(".slot-empty")).toHaveLength(1);
    expect(container.querySelectorAll(".door")).toHaveLength(2);
    expect(container.querySelector(".tape")).toBeNull();
  });

  it("numbers the stops in walking order along the tape", () => {
    const { container } = render(<FloorMap layout={layout} slots={slots} stops={stops} plan={plan} />);
    const tape = container.querySelector(".tape");
    expect(tape?.getAttribute("points")).toBe("0,10 6,10 6,3.5 6,10 0,10");
    const labels = [...container.querySelectorAll(".stop text")].map((node) => node.textContent);
    expect(labels).toEqual(["1", "2"]);
    const first = container.querySelector(".stop circle");
    expect(first?.getAttribute("cx")).toBe(String(6 + 6 * 0.3));
  });
});
