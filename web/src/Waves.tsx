import { useEffect, useState } from "react";
import { get, post, type EventRow, type WaveDetail, type WaveRow } from "./api";
import { POLICY_NAMES, clock, km, sentence } from "./format";

function Outcome({ ok, yes, no }: { ok: boolean | null; yes: string; no: string }) {
  if (ok === null) {
    return <span className="quiet">open</span>;
  }
  return <span className={ok ? "chip chip-ok" : "chip chip-miss"}>{ok ? yes : no}</span>;
}

function Detail({ code, version }: { code: string; version: number }) {
  const [detail, setDetail] = useState<WaveDetail | null>(null);
  useEffect(() => {
    get<WaveDetail>(`/api/waves/${code}`).then(setDetail).catch(() => setDetail(null));
  }, [code, version]);
  if (!detail) {
    return <p className="quiet">Loading orders.</p>;
  }
  return (
    <table className="inner">
      <thead>
        <tr>
          <th>Order</th>
          <th>Customer</th>
          <th>Status</th>
          <th>On time</th>
          <th>In full</th>
        </tr>
      </thead>
      <tbody>
        {detail.orders.map((order) => (
          <tr key={order.code}>
            <td>{order.code}</td>
            <td>{order.customer}</td>
            <td>{sentence(order.status)}</td>
            <td>
              <Outcome ok={order.on_time} yes="On time" no="Late" />
            </td>
            <td>
              <Outcome ok={order.in_full} yes="In full" no="Short" />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Waves({ version, onChange }: { version: number; onChange: () => void }) {
  const [waves, setWaves] = useState<WaveRow[] | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([get<WaveRow[]>("/api/waves?limit=200"), get<EventRow[]>("/api/events?limit=12")])
      .then(([nextWaves, nextEvents]) => {
        setWaves(
          [...nextWaves].sort((a, b) => (a.departure ?? "9").localeCompare(b.departure ?? "9")),
        );
        setEvents(nextEvents);
      })
      .catch((reason: Error) => setMessage(reason.message));
  }, [version]);

  async function release(code: string) {
    setBusy(code);
    try {
      const result = await post<{ tours: number }>(`/api/waves/${code}/release`, {});
      setMessage(`Released ${code} as ${result.tours} tours.`);
      onChange();
    } catch (reason) {
      setMessage((reason as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (!waves) {
    return <p className="quiet">Loading waves.</p>;
  }
  return (
    <section className="waves-page">
      <div className="panel table-wrap">
        <h2>Waves by trailer departure</h2>
        {message && (
          <p className="notice" role="status">
            {message}
          </p>
        )}
        {waves.length === 0 ? (
          <p className="quiet">No waves yet. Waves get planned when orders are allocated to a trailer.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Leaves</th>
                <th>Trailer</th>
                <th>Wave</th>
                <th className="num">Orders</th>
                <th>Tours done</th>
                <th className="num">Walk</th>
                <th>Routing</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {waves.map((wave) => (
                <WaveLine
                  key={wave.code}
                  wave={wave}
                  open={open === wave.code}
                  busy={busy === wave.code}
                  version={version}
                  onToggle={() => setOpen(open === wave.code ? null : wave.code)}
                  onRelease={() => release(wave.code)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="panel">
        <h2>Latest scans</h2>
        <ol className="feed">
          {events.map((event, number) => (
            <li key={`${event.at}-${number}`}>
              <span className="figure">{clock(event.at)}</span> {sentence(event.kind)} {event.ref}
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

type LineProps = {
  wave: WaveRow;
  open: boolean;
  busy: boolean;
  version: number;
  onToggle: () => void;
  onRelease: () => void;
};

function WaveLine({ wave, open, busy, version, onToggle, onRelease }: LineProps) {
  const share = wave.tours ? wave.tours_done / wave.tours : 0;
  return (
    <>
      <tr className={open ? "row open" : "row"}>
        <td className="figure">{clock(wave.departure)}</td>
        <td>{wave.appointment ?? "none"}</td>
        <td>
          <button type="button" className="link" aria-expanded={open} onClick={onToggle}>
            {wave.code}
          </button>
        </td>
        <td className="num">{wave.orders}</td>
        <td>
          {wave.status === "planned" ? (
            <span className="quiet">Not released</span>
          ) : (
            <>
              <span className="bar" aria-hidden="true">
                <span style={{ width: `${Math.round(share * 100)}%` }} />
              </span>
              <span className="figure">
                {wave.tours_done} of {wave.tours}
              </span>
            </>
          )}
        </td>
        <td className="num">{wave.tours ? km(wave.distance_m) : ""}</td>
        <td>{POLICY_NAMES[wave.routing]}</td>
        <td>
          {wave.status === "planned" ? (
            <button type="button" className="button primary" disabled={busy} onClick={onRelease}>
              {busy ? "Releasing" : "Release wave"}
            </button>
          ) : (
            sentence(wave.status)
          )}
        </td>
      </tr>
      {open && (
        <tr className="detail">
          <td colSpan={8}>
            <Detail code={wave.code} version={version} />
          </td>
        </tr>
      )}
    </>
  );
}
