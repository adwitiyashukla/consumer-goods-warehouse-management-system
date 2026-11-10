import { useEffect, useState } from "react";
import { get, POLICIES, type Layout, type Policy, type RouteSet, type Slot, type TourRow } from "./api";
import { FloorMap } from "./FloorMap";
import { POLICY_NAMES, meters, versus } from "./format";

export function Floor({ version }: { version: number }) {
  const [layout, setLayout] = useState<Layout | null>(null);
  const [slots, setSlots] = useState<Slot[]>([]);
  const [tours, setTours] = useState<TourRow[]>([]);
  const [index, setIndex] = useState(0);
  const [routes, setRoutes] = useState<RouteSet | null>(null);
  const [policy, setPolicy] = useState<Policy>("optimal");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([get<Layout>("/api/layout"), get<Slot[]>("/api/slots"), get<TourRow[]>("/api/tours?limit=80")])
      .then(([nextLayout, nextSlots, nextTours]) => {
        const ordered = [...nextTours].reverse();
        const walked = ordered.map((row) => row.status).lastIndexOf("done");
        setLayout(nextLayout);
        setSlots(nextSlots);
        setTours(ordered);
        setIndex(walked >= 0 ? walked : Math.max(ordered.length - 1, 0));
      })
      .catch((reason: Error) => setError(reason.message));
  }, [version]);

  const tour = tours[index];
  useEffect(() => {
    if (!tour) {
      setRoutes(null);
      return;
    }
    get<RouteSet>(`/api/tours/${tour.id}/routes`)
      .then((next) => {
        setRoutes(next);
        setPolicy(next.routing);
      })
      .catch((reason: Error) => setError(reason.message));
  }, [tour]);

  if (error) {
    return <p className="error">Could not load the floor. {error}</p>;
  }
  if (!layout) {
    return <p className="quiet">Loading the floor.</p>;
  }
  const plan = routes?.routes[policy];
  const baseline = routes?.routes.sshape.distance_m ?? 0;
  return (
    <section className="floor-page">
      <div className="map-frame">
        <FloorMap
          layout={layout}
          slots={slots}
          stops={routes?.stops}
          plan={plan}
          drawKey={tour ? `${tour.id}-${policy}` : undefined}
        />
        <ul className="legend" aria-label="Map key">
          <li>
            <span className="swatch slot-a" /> Fast movers
          </li>
          <li>
            <span className="swatch slot-b" /> Medium
          </li>
          <li>
            <span className="swatch slot-c" /> Slow
          </li>
          <li>
            <span className="swatch slot-empty" /> Empty slot
          </li>
          <li>
            <span className="swatch tape-swatch" /> Picker route
          </li>
          <li>
            <span className="swatch door-in" /> Inbound door
          </li>
          <li>
            <span className="swatch door-out" /> Outbound door
          </li>
        </ul>
      </div>
      <aside className="panel tour-panel" aria-live="polite">
        {!tour || !routes ? (
          <p className="quiet">No tours yet. Release a wave on the Waves tab and its tours show up here.</p>
        ) : (
          <>
            <h2>Tour {tour.id}</h2>
            <p className="quiet">
              Wave {tour.wave}, {tour.stops} stops, {tour.cases} cases
              {tour.worker ? `, picked by ${tour.worker}` : ", not started"}
            </p>
            <fieldset className="policies">
              <legend>Walk this tour with</legend>
              {POLICIES.map((name) => (
                <label key={name} className={name === policy ? "policy chosen" : "policy"}>
                  <input
                    type="radio"
                    name="policy"
                    value={name}
                    checked={name === policy}
                    onChange={() => setPolicy(name)}
                  />
                  <span className="policy-name">
                    {POLICY_NAMES[name]}
                    {name === routes.routing ? <span className="used">Used for this tour</span> : null}
                  </span>
                  <span className="figure">{meters(routes.routes[name].distance_m)}</span>
                </label>
              ))}
            </fieldset>
            {plan && (
              <p className="verdict">
                {policy === "sshape"
                  ? "S-shape walks every aisle with a pick end to end. The other two are measured against it."
                  : `${POLICY_NAMES[policy]} walks ${versus(baseline, plan.distance_m)} S-shape on this tour.`}
              </p>
            )}
            <div className="stepper">
              <button type="button" className="button" disabled={index === 0} onClick={() => setIndex(index - 1)}>
                Previous tour
              </button>
              <span className="quiet">
                {index + 1} of {tours.length}
              </span>
              <button
                type="button"
                className="button"
                disabled={index >= tours.length - 1}
                onClick={() => setIndex(index + 1)}
              >
                Next tour
              </button>
            </div>
          </>
        )}
      </aside>
    </section>
  );
}
