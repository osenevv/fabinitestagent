import { useEffect, useMemo, useState } from "react";
import catalog from "./data/catalog.json";
import { applyGates } from "./policy.js";

const REC_LABEL = {
  vykupit: "vykupit",
  zkoumat_dal: "zkoumat dál",
  odlozit: "odložit",
  odmitnout: "odmítnout",
};

const AXIS_LABEL = {
  zaplava: "Záplava",
  ochrana_prirody: "Ochrana přírody",
  uzemni_plan: "Územní plán",
  pristup: "Přístup",
  pravni_stav: "Právní stav",
};

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

export default function App() {
  const cases = catalog.cases;
  const [selectedId, setSelectedId] = useState(cases[0]?.id ?? null);
  const selected = useMemo(
    () => cases.find((c) => c.id === selectedId) ?? null,
    [cases, selectedId]
  );

  const [phase, setPhase] = useState("idle");
  const [view, setView] = useState("guarded");
  const [output, setOutput] = useState(null);
  const [gateInfo, setGateInfo] = useState({ normalizace: [], politika: [] });

  useEffect(() => {
    setPhase("idle");
    setOutput(null);
    setView("guarded");
    setGateInfo({ normalizace: [], politika: [] });
  }, [selectedId]);

  async function runExample() {
    if (!selected || phase === "running") return;
    setPhase("running");
    setOutput(null);
    setView("guarded");
    await sleep(900);

    let meta = {
      normalizace: selected.gateMeta?.normalizace || [],
      politika: selected.gateMeta?.politika || [],
    };

    if (selected.rawResult) {
      const live = applyGates(selected.rawResult);
      meta = {
        normalizace: live.normalizace.length ? live.normalizace : meta.normalizace,
        politika: live.politika.length ? live.politika : meta.politika,
      };
    }

    setGateInfo(meta);
    setOutput(selected.result);
    setPhase("done");
  }

  const shown =
    phase === "done" && output
      ? view === "raw" && selected?.rawResult
        ? selected.rawResult
        : output
      : null;

  const canToggleRaw = Boolean(selected?.rawResult);

  return (
    <div className="app">
      <header className="hero">
        <span className="offline-badge">Offline · bez API</span>
        <h1 className="brand">Viagem</h1>
        <p className="hero-lead">AI konzultant výkup parcel</p>
      </header>

      <section className="demo" id="demo">
        <aside className="sidebar">
          <div className="case-list">
            {cases.map((c) => (
              <button
                key={c.id}
                type="button"
                className={`case-btn${c.id === selectedId ? " is-active" : ""}`}
                onClick={() => setSelectedId(c.id)}
              >
                <span className="title">{c.title}</span>
                <span className="meta">
                  <span className={`chip ${c.category === "adversarial" ? "adv" : "suite"}`}>
                    {c.category === "adversarial" ? "adversarial" : "suite"}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </aside>

        <div className="stage">
          {selected ? (
            <>
              <div className="stage-head">
                <h2>{selected.title}</h2>
                <div className="actions">
                  {canToggleRaw && phase === "done" ? (
                    <div className="toggle" role="group" aria-label="Výstup">
                      <button
                        type="button"
                        className={view === "raw" ? "is-on" : ""}
                        onClick={() => setView("raw")}
                      >
                        Surový
                      </button>
                      <button
                        type="button"
                        className={view === "guarded" ? "is-on" : ""}
                        onClick={() => setView("guarded")}
                      >
                        Po branách
                      </button>
                    </div>
                  ) : null}
                  <button
                    type="button"
                    className={`btn btn-run${phase === "running" ? " is-running" : ""}`}
                    onClick={runExample}
                    disabled={phase === "running"}
                  >
                    {phase === "running" ? "…" : "Spustit"}
                  </button>
                </div>
              </div>

              <div className="parcel-grid">
                <div className="kv">
                  <span>KÚ</span>
                  <strong>{selected.parcel?.ku ?? "—"}</strong>
                </div>
                <div className="kv">
                  <span>Parcela</span>
                  <strong>{selected.parcel?.cislo_parcely ?? "—"}</strong>
                </div>
                <div className="kv">
                  <span>Výměra</span>
                  <strong>
                    {selected.parcel?.vymera_m2 != null
                      ? `${selected.parcel.vymera_m2} m²`
                      : "—"}
                  </strong>
                </div>
                <div className="kv">
                  <span>LV</span>
                  <strong>{selected.parcel?.lv ?? "—"}</strong>
                </div>
              </div>

              {phase === "running" ? <div className="processing-pulse" aria-hidden /> : null}
              {shown ? <ResultView data={shown} gateInfo={gateInfo} view={view} /> : null}
            </>
          ) : null}
        </div>
      </section>
    </div>
  );
}

function ResultView({ data, gateInfo, view }) {
  const rec = data.doporuceni;
  const rizika = data.rizika || {};
  const axes = Object.keys(AXIS_LABEL);

  return (
    <div className="result">
      <div className="result-top">
        <div className={`verdict ${rec}`}>
          <div className="label">Doporučení{view === "raw" ? " · surový" : ""}</div>
          <div className="rec">{REC_LABEL[rec] || rec}</div>
          <p className="shrnuti">{data.shrnuti}</p>
        </div>
        <div className="scores">
          <div className="score-box">
            <div className="l">Skóre</div>
            <div className="n">{data.skore_zajmu}</div>
          </div>
          <div className="score-box">
            <div className="l">Jistota</div>
            <div className="n">{data.jistota?.skore ?? "—"}</div>
          </div>
          <div className="score-box">
            <div className="l">Potenciál</div>
            <div className="n" style={{ fontSize: "1.15rem", paddingTop: "0.35rem" }}>
              {data.vyuzitelnost?.hlavni_potencial ?? "—"}
            </div>
          </div>
          <div className="score-box">
            <div className="l">Konflikty</div>
            <div className="n">{data.konflikty_dat?.length ?? 0}</div>
          </div>
        </div>
      </div>

      <div className="section">
        <h3>Rizika</h3>
        <div className="axes">
          {axes.map((key) => {
            const r = rizika[key] || {};
            return (
              <div className="axis" key={key}>
                <div>{AXIS_LABEL[key]}</div>
                <div className={`lvl ${r.uroven || ""}`}>{r.uroven || "—"}</div>
                <div>{r.duvod || ""}</div>
              </div>
            );
          })}
        </div>
      </div>

      {data.konflikty_dat?.length ? (
        <div className="conflicts">
          <ul className="checklist">
            {data.konflikty_dat.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {(data.overit_rucne || []).length ? (
        <div className="section">
          <h3>Checklist</h3>
          <ul className="checklist">
            {data.overit_rucne.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {(gateInfo.politika?.length || gateInfo.normalizace?.length) && view === "guarded" ? (
        <div className="gate-box">
          <h4>_meta</h4>
          <ul>
            {[...(gateInfo.normalizace || []), ...(gateInfo.politika || [])].map((g) => (
              <li key={g}>{g}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
