export type Policy = "sshape" | "largest_gap" | "optimal";

export const POLICIES: Policy[] = ["sshape", "largest_gap", "optimal"];

export type Dock = {
  trucks: number;
  dwell_avg_min: number | null;
  dwell_p90_min: number | null;
  door_utilization: number | null;
};

export type Kpis = {
  orders: { by_status: Record<string, number>; total: number };
  shipped: { orders: number; otif: number | null; on_time: number | null; in_full: number | null };
  lines: { lines: number; cases: number; allocated: number; picked: number; short: number };
  picking: {
    tours_done: number;
    distance_m: number;
    stops: number;
    picker_hours: number;
    meters_per_stop: number | null;
  };
  open_tasks: Record<string, number>;
  stock: { pallets: number; cases: number; allocated: number; oversold_pallets: number };
  inbound: Dock;
  outbound: Dock;
};

export type Door = { code: string; kind: string; x: number; y: number };

export type Layout = {
  aisles: number;
  bays: number;
  levels: number;
  pitch: number;
  length: number;
  bay_width: number;
  end_gap: number;
  dock_depth: number;
  depot: { x: number; y: number };
  doors: Door[];
};

export type Slot = {
  code: string;
  aisle: number;
  bay: number;
  side: string;
  x: number;
  y: number;
  sku: string | null;
  velocity: string | null;
  available: number;
};

export type TourRow = {
  id: number;
  wave: string;
  seq: number;
  status: string;
  worker: string | null;
  routing: Policy;
  distance_m: number;
  stops: number;
  cases: number;
  started_at: string | null;
  done_at: string | null;
};

export type Stop = { code: string; side: string; x: number; y: number };

export type Plan = { distance_m: number; order: number[]; path: [number, number][] };

export type RouteSet = {
  tour: number;
  routing: Policy;
  stops: Stop[];
  routes: Record<Policy, Plan>;
};

export type WaveRow = {
  code: string;
  status: string;
  routing: Policy;
  batching: string;
  released_at: string | null;
  appointment: string | null;
  departure: string | null;
  orders: number;
  tours: number;
  tours_done: number;
  distance_m: number;
};

export type WaveDetail = {
  code: string;
  status: string;
  tours: { id: number; seq: number; status: string; worker: string | null; distance_m: number; stops: number; cases: number }[];
  orders: { code: string; customer: string; status: string; on_time: boolean | null; in_full: boolean | null }[];
};

export type OrderRow = {
  code: string;
  status: string;
  ship_by: string;
  customer: string;
  appointment: string | null;
  wave: string | null;
};

export type Appointment = {
  code: string;
  direction: "inbound" | "outbound";
  carrier: string;
  status: string;
  door: string | null;
  window_start: string;
  window_end: string;
  scheduled_start: string | null;
  duration_minutes: number;
  gate_in_at: string | null;
  docked_at: string | null;
  done_at: string | null;
  gate_out_at: string | null;
};

export type SkuRow = {
  code: string;
  description: string;
  category: string;
  velocity: string | null;
  cases_per_pallet: number;
  on_hand: number;
  allocated: number;
  pallets: number;
};

export type SkuDetail = {
  code: string;
  description: string;
  category: string;
  velocity: string | null;
  cases_per_pallet: number;
  shelf_life_days: number;
  pick_slot: string | null;
  stock: {
    lpn: string;
    lot: string;
    expires_on: string;
    location: string;
    kind: string;
    cases_on_hand: number;
    cases_allocated: number;
    status: string;
  }[];
};

export type EventRow = { at: string; kind: string; ref: string; data: Record<string, unknown> };

async function read<T>(response: Response, path: string): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    const detail = typeof body.detail === "string" ? body.detail : `status ${response.status}`;
    throw new Error(`${path}: ${detail}`);
  }
  return (await response.json()) as T;
}

export async function get<T>(path: string): Promise<T> {
  return read<T>(await fetch(path), path);
}

export async function post<T>(path: string, body: unknown = {}): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return read<T>(response, path);
}
