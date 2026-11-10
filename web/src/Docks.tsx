import { useEffect, useState } from "react";
import { get, type Appointment, type Kpis, type Layout } from "./api";
import { clock, minutesOfDay, percent } from "./format";

const ROW = 26;
const LABEL = 58;
const WIDTH = 900;
const RIGHT = 26;

function span(appointments: Appointment[]): [number, number] {
  const marks = appointments.flatMap((a) =>
    [a.scheduled_start, a.window_end, a.gate_in_at, a.gate_out_at].filter((v): v is string => Boolean(v)),
  );
  if (marks.length === 0) {
    return [240, 1320];
  }
  const minutes = marks.map(minutesOfDay);
  const first = Math.floor(Math.min(...minutes) / 60) * 60;
  const last = Math.ceil(Math.max(...minutes) / 60) * 60;
  return [first, Math.max(last, first + 120)];
}

export function Docks({ version }: { version: number }) {
  const [appointments, setAppointments] = useState<Appointment[] | null>(null);
  const [kpis, setKpis] = useState<Kpis | null>(null);
  const [doors, setDoors] = useState<string[]>([]);
  useEffect(() => {
    Promise.all([get<Appointment[]>("/api/appointments"), get<Kpis>("/api/kpis"), get<Layout>("/api/layout")])
      .then(([nextAppointments, nextKpis, layout]) => {
        setAppointments(nextAppointments);
        setKpis(nextKpis);
        setDoors(layout.doors.filter((door) => door.kind !== "stage").map((door) => door.code));
      })
      .catch(() => setAppointments([]));
  }, [version]);
  if (!appointments) {
    return <p className="quiet">Loading the dock schedule.</p>;
  }
  const [first, last] = span(appointments);
  const scale = (WIDTH - LABEL - RIGHT) / (last - first);
  const x = (iso: string) => LABEL + (minutesOfDay(iso) - first) * scale;
  const hours = [];
  for (let minute = first; minute <= last; minute += 120) {
    hours.push(minute);
  }
  const height = doors.length * ROW + 30;
  return (
    <section className="docks-page">
      <div className="panel">
        <h2>Doors through the day</h2>
        {doors.length === 0 ? (
          <p className="quiet">No trucks are booked yet.</p>
        ) : (
          <svg
            className="timeline"
            viewBox={`0 0 ${WIDTH} ${height}`}
            role="img"
            aria-label="Timeline of each dock door with booked and actual truck times"
          >
            {hours.map((minute) => {
              const left = LABEL + (minute - first) * scale;
              return (
                <g key={minute} className="hour">
                  <line x1={left} x2={left} y1={0} y2={height - 22} />
                  <text x={left} y={height - 8}>
                    {String(Math.floor(minute / 60) % 24).padStart(2, "0")}:00
                  </text>
                </g>
              );
            })}
            {doors.map((door, row) => {
              const top = row * ROW + 4;
              return (
                <g key={door}>
                  <text className="door-label" x={0} y={top + 15}>
                    {door}
                  </text>
                  {appointments
                    .filter((a) => a.door === door)
                    .map((a) => {
                      const booked = a.scheduled_start ?? a.window_start;
                      const bookedEnd =
                        a.direction === "outbound"
                          ? a.window_end
                          : new Date(new Date(booked).getTime() + a.duration_minutes * 60000).toISOString();
                      const kind = a.direction === "inbound" ? "in" : "out";
                      return (
                        <g key={a.code}>
                          <title>
                            {`${a.code}, ${a.carrier}, booked ${clock(booked)}${
                              a.docked_at ? `, docked ${clock(a.docked_at)}` : ""
                            }${a.gate_out_at ? `, left ${clock(a.gate_out_at)}` : ""}`}
                          </title>
                          <rect
                            className="booked"
                            x={x(booked)}
                            y={top + 2}
                            width={Math.max(x(bookedEnd) - x(booked), 2)}
                            height={ROW - 8}
                          />
                          {a.gate_in_at && a.docked_at && (
                            <line
                              className="yard"
                              x1={x(a.gate_in_at)}
                              x2={x(a.docked_at)}
                              y1={top + ROW / 2 - 2}
                              y2={top + ROW / 2 - 2}
                            />
                          )}
                          {a.docked_at && (
                            <rect
                              className={`actual actual-${kind}`}
                              x={x(a.docked_at)}
                              y={top + 6}
                              width={Math.max(x(a.done_at ?? a.docked_at) - x(a.docked_at), 2)}
                              height={ROW - 16}
                            />
                          )}
                        </g>
                      );
                    })}
                </g>
              );
            })}
          </svg>
        )}
        <ul className="legend">
          <li>
            <span className="swatch booked-swatch" /> Booked slot
          </li>
          <li>
            <span className="swatch actual-in" /> Unloading
          </li>
          <li>
            <span className="swatch actual-out" /> Loading
          </li>
          <li>
            <span className="swatch yard-swatch" /> Waiting in the yard
          </li>
        </ul>
      </div>
      {kpis && (
        <div className="panel dock-figures">
          <h2>Truck time on site</h2>
          <dl>
            <div>
              <dt>Inbound, gate in to gate out</dt>
              <dd>
                {kpis.inbound.dwell_avg_min ?? 0} min average, {kpis.inbound.dwell_p90_min ?? 0} min at the 90th
                percentile
              </dd>
            </div>
            <div>
              <dt>Outbound, gate in to gate out</dt>
              <dd>{kpis.outbound.dwell_avg_min ?? 0} min average</dd>
            </div>
            <div>
              <dt>Doors busy</dt>
              <dd>
                {percent(kpis.inbound.door_utilization)} inbound, {percent(kpis.outbound.door_utilization)} outbound
              </dd>
            </div>
          </dl>
        </div>
      )}
    </section>
  );
}
