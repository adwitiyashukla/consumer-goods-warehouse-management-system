import { useEffect, useState } from "react";
import { get, type EventRow, type Kpis } from "./api";
import { Docks } from "./Docks";
import { Floor } from "./Floor";
import { clock, km, percent } from "./format";
import { Stock } from "./Stock";
import { Waves } from "./Waves";

const TABS = [
  { id: "floor", label: "Floor" },
  { id: "waves", label: "Waves" },
  { id: "docks", label: "Docks" },
  { id: "stock", label: "Stock" },
] as const;

type Tab = (typeof TABS)[number]["id"];

function fromHash(): Tab {
  const hash = window.location.hash.slice(1);
  return TABS.find((tab) => tab.id === hash)?.id ?? "floor";
}

function ShiftBoard({ kpis }: { kpis: Kpis }) {
  const open = Object.values(kpis.open_tasks).reduce((sum, count) => sum + count, 0);
  const figures = [
    { label: "On time and in full", value: percent(kpis.shipped.otif) },
    { label: "Orders shipped", value: `${kpis.shipped.orders} of ${kpis.orders.total}` },
    { label: "Pickers walked", value: km(kpis.picking.distance_m) },
    { label: "Picker hours", value: kpis.picking.picker_hours.toFixed(1) },
    { label: "Open tasks", value: String(open) },
    { label: "Cases short", value: String(kpis.lines.short) },
  ];
  return (
    <dl className="shift-board" aria-label="Today so far">
      {figures.map((figure) => (
        <div key={figure.label}>
          <dt>{figure.label}</dt>
          <dd>{figure.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function App() {
  const [tab, setTab] = useState<Tab>(fromHash);
  const [version, setVersion] = useState(0);
  const [kpis, setKpis] = useState<Kpis | null>(null);
  const [latest, setLatest] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const follow = () => setTab(fromHash());
    window.addEventListener("hashchange", follow);
    return () => window.removeEventListener("hashchange", follow);
  }, []);

  useEffect(() => {
    Promise.all([get<Kpis>("/api/kpis"), get<EventRow[]>("/api/events?limit=1")])
      .then(([nextKpis, events]) => {
        setKpis(nextKpis);
        setLatest(events[0]?.at ?? null);
        setError(null);
      })
      .catch((reason: Error) => setError(reason.message));
  }, [version, tab]);

  return (
    <div className="shell">
      <header className="signboard">
        <p className="brand">
          <span className="name">dockhand</span>
          <span className="site">Distribution center 1, consumer goods</span>
        </p>
        <p className="clock" title="Time of the latest scan in the building">
          {latest ? `Last scan ${clock(latest)}` : "No scans yet"}
        </p>
      </header>
      <nav className="tabs" aria-label="Sections">
        {TABS.map((item) => (
          <a key={item.id} href={`#${item.id}`} aria-current={tab === item.id ? "page" : undefined}>
            {item.label}
          </a>
        ))}
      </nav>
      {error && (
        <p className="error" role="alert">
          The API did not answer. {error}
        </p>
      )}
      {kpis && <ShiftBoard kpis={kpis} />}
      <main>
        {tab === "floor" && <Floor version={version} />}
        {tab === "waves" && <Waves version={version} onChange={() => setVersion((v) => v + 1)} />}
        {tab === "docks" && <Docks version={version} />}
        {tab === "stock" && <Stock version={version} />}
      </main>
    </div>
  );
}
