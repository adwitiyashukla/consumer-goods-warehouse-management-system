import type { Policy } from "./api";

export const POLICY_NAMES: Record<Policy, string> = {
  sshape: "S-shape",
  largest_gap: "Largest gap",
  optimal: "Optimal",
};

export function percent(value: number | null): string {
  return value === null ? "no data yet" : `${(value * 100).toFixed(1)}%`;
}

export function meters(value: number): string {
  return `${Math.round(value).toLocaleString("en-US")} m`;
}

export function km(value: number): string {
  return `${(value / 1000).toFixed(1)} km`;
}

export function clock(iso: string | null): string {
  if (!iso) {
    return "";
  }
  return new Date(iso).toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "UTC",
  });
}

export function minutesOfDay(iso: string): number {
  const at = new Date(iso);
  return at.getUTCHours() * 60 + at.getUTCMinutes() + at.getUTCSeconds() / 60;
}

export function versus(base: number, value: number): string {
  const change = base > 0 ? Math.round((value / base - 1) * 100) : 0;
  if (change === 0) {
    return "the same as";
  }
  return change < 0 ? `${-change}% less than` : `${change}% more than`;
}

export function sentence(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1).replace(".", " ");
}
