import type { Layout, Plan, Slot, Stop } from "./api";

type Props = {
  layout: Layout;
  slots: Slot[];
  stops?: Stop[];
  plan?: Plan;
  drawKey?: string;
};

export function FloorMap({ layout, slots, stops = [], plan, drawKey }: Props) {
  const left = -layout.pitch * 0.8;
  const width = (layout.aisles - 1) * layout.pitch + layout.pitch * 1.6;
  const bottom = layout.length + layout.dock_depth;
  const flip = (value: number) => layout.length - value;
  const slotWidth = layout.pitch * 0.26;
  const slotHeight = layout.bay_width * 0.84;
  const offset = layout.pitch * 0.3;
  const shift = (side: string) => (side === "L" ? -offset : offset);
  const tape = plan?.path.map(([x, y]) => `${x},${flip(y)}`).join(" ");
  return (
    <svg
      className="floor-map"
      viewBox={`${left} -9 ${width} ${bottom + 15}`}
      role="img"
      aria-label={`Floor map with ${layout.aisles} aisles and the docks along the bottom`}
    >
      <rect className="floor" x={left} y={-9} width={width} height={bottom + 15} />
      <rect className="dock-strip" x={left} y={layout.length} width={width} height={layout.dock_depth + 6} />
      {Array.from({ length: layout.aisles }, (_, aisle) => (
        <g key={aisle} className="aisle-sign">
          <rect x={aisle * layout.pitch - 1.7} y={-7.6} width={3.4} height={3.6} rx={0.4} />
          <text x={aisle * layout.pitch} y={-4.9}>
            {aisle + 1}
          </text>
        </g>
      ))}
      {slots.map((slot) => (
        <rect
          key={slot.code}
          className={`slot slot-${slot.velocity ? slot.velocity.toLowerCase() : "empty"}`}
          x={slot.x + shift(slot.side) - slotWidth / 2}
          y={flip(slot.y) - slotHeight / 2}
          width={slotWidth}
          height={slotHeight}
        >
          <title>{slot.sku ? `${slot.code} holds ${slot.sku}` : `${slot.code} is empty`}</title>
        </rect>
      ))}
      {layout.doors
        .filter((door) => door.kind !== "stage")
        .map((door) => (
          <g key={door.code} className={door.kind === "door_in" ? "door door-in" : "door door-out"}>
            <rect x={door.x - 2.4} y={flip(door.y) - 1.2} width={4.8} height={2.4} />
            <text x={door.x} y={flip(door.y) + 4.4}>
              {door.code}
            </text>
          </g>
        ))}
      <circle className="depot" cx={layout.depot.x} cy={flip(0)} r={1.1}>
        <title>Pickers start here and drop their pallets here</title>
      </circle>
      {tape && (
        <g key={drawKey} className="route">
          <polyline className="tape-edge" points={tape} pathLength={1} />
          <polyline className="tape" points={tape} pathLength={1} />
        </g>
      )}
      {plan?.order.map((index, position) => {
        const stop = stops[index];
        if (!stop) {
          return null;
        }
        const x = stop.x + shift(stop.side);
        return (
          <g key={stop.code} className="stop">
            <circle cx={x} cy={flip(stop.y)} r={1.35} />
            <text x={x} y={flip(stop.y) + 0.55}>
              {position + 1}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
