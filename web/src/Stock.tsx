import { useEffect, useState } from "react";
import { get, type SkuDetail, type SkuRow } from "./api";

export function Stock({ version }: { version: number }) {
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<SkuRow[]>([]);
  const [chosen, setChosen] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkuDetail | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      get<SkuRow[]>(`/api/skus?limit=25&q=${encodeURIComponent(query)}`)
        .then(setRows)
        .catch(() => setRows([]));
    }, 200);
    return () => window.clearTimeout(timer);
  }, [query, version]);

  useEffect(() => {
    if (!chosen) {
      setDetail(null);
      return;
    }
    get<SkuDetail>(`/api/skus/${chosen}`)
      .then(setDetail)
      .catch(() => setDetail(null));
  }, [chosen, version]);

  return (
    <section className="stock-page">
      <div className="panel table-wrap">
        <label className="search">
          Find a SKU
          <input
            type="search"
            value={query}
            placeholder="Code or name, like dish soap"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <table>
          <thead>
            <tr>
              <th>SKU</th>
              <th>Description</th>
              <th>Speed</th>
              <th className="num">On hand</th>
              <th className="num">Allocated</th>
              <th className="num">Pallets</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.code} className={row.code === chosen ? "row open" : "row"}>
                <td>
                  <button type="button" className="link" onClick={() => setChosen(row.code)}>
                    {row.code}
                  </button>
                </td>
                <td>{row.description}</td>
                <td>{row.velocity ?? ""}</td>
                <td className="num">{row.on_hand}</td>
                <td className="num">{row.allocated}</td>
                <td className="num">{row.pallets}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <p className="quiet">No SKU matches that search.</p>}
      </div>
      <div className="panel table-wrap">
        {!detail ? (
          <p className="quiet">Pick a SKU to see its pallets, oldest expiry first.</p>
        ) : (
          <>
            <h2>
              {detail.code} {detail.description}
            </h2>
            <p className="quiet">
              {detail.cases_per_pallet} cases a pallet, {detail.shelf_life_days} days shelf life, pick slot{" "}
              <span className="loc">{detail.pick_slot ?? "none"}</span>
            </p>
            <table>
              <thead>
                <tr>
                  <th>Pallet</th>
                  <th>Lot</th>
                  <th>Expires</th>
                  <th>Location</th>
                  <th className="num">On hand</th>
                  <th className="num">Allocated</th>
                </tr>
              </thead>
              <tbody>
                {detail.stock.map((pallet) => (
                  <tr key={pallet.lpn}>
                    <td>{pallet.lpn}</td>
                    <td>{pallet.lot}</td>
                    <td className="figure">{pallet.expires_on}</td>
                    <td>
                      <span className="loc">{pallet.location}</span>
                    </td>
                    <td className="num">{pallet.cases_on_hand}</td>
                    <td className="num">{pallet.cases_allocated}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </section>
  );
}
