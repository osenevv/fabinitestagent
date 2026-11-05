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

const PROCESS_STEPS = [
  "Načítám fixture vstup…",
  "Simuluji výstup modelu…",
  "Validuji schéma JSON…",
  "Přepočítávám skóre a brány…",
];

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

  const [phase, setPhase] = useState("idle"); // idle | running | done
  const [stepIdx, setStepIdx] = useState(-1);
  const [view, setView] = useState("guarded"); // guarded | raw
  const [output, setOutput] = useState(null);
  const [gateInfo, setGateInfo] = useState({ normalizace: [], politika: [] });

  useEffect(() => {
    setPhase("idle");
    setStepIdx(-1);
    setOutput(null);
    setView("guarded");
    setGateInfo({ normalizace: [], politika: [] });
  }, [selectedId]);

  async function runExample() {
    if (!selected || phase === "running") return;
    setPhase("running");
    setOutput(null);
    setView("guarded");

    for (let i = 0; i < PROCESS_STEPS.length; i++) {
      setStepIdx(i);
      await sleep(380 + i * 90);
    }

    // Prefer fixture gate meta; if raw exists, also run live JS gates for the story.
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
        <div className="hero-top">
          <span className="offline-badge">Offline showcase · bez API klíčů</span>
        </div>
        <h1 className="brand">Viagem</h1>
        <p className="hero-lead">AI konzultant výkup parcel</p>
        <p className="hero-sub">
          Interaktivní ukázka: klikněte na příklad, spusťte vyhodnocení a prohlédněte
          doporučení, rizika i zásahy deterministických bran — vše z fixture dat, bez
          živého LLM.
        </p>
        <div className="hero-cta">
          <a className="btn btn-primary" href="#demo">
            Otevřít příklady
          </a>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => {
              document.getElementById("demo")?.scrollIntoView({ behavior: "smooth" });
              setSelectedId(cases[0]?.id);
            }}
          >
            Happy path Brno
          </button>
        </div>
      </header>

      <section className="demo" id="demo">
        <aside className="sidebar">
          <p className="panel-label">Příklady</p>
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
                  <span className="chip">{c.tag}</span>
                  {c.hasGateRewrite ? <span className="chip">gate</span> : null}
                </span>
              </button>
            ))}
          </div>
        </aside>

        <div className="stage">
          {selected ? (
            <>
              <div className="stage-head">
                <div>
                  <h2>{selected.title}</h2>
                  <p>{selected.blurb}</p>
                </div>
                <div className="actions">
                  {canToggleRaw && phase === "done" ? (
                    <div className="toggle" role="group" aria-label="Zobrazení výstupu">
                      <button
                        type="button"
                        className={view === "raw" ? "is-on" : ""}
                        onClick={() => setView("raw")}
                      >
                        Surový model
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
                    {phase === "running" ? "Běží…" : phase === "done" ? "Spustit znovu" : "Spustit"}
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

              {selected.parcel?.highlights?.length ? (
                <div className="highlights">
                  {selected.parcel.highlights.map((h) => (
                    <span key={h}>{h}</span>
                  ))}
                </div>
              ) : null}

              {phase === "idle" ? (
                <div className="empty-state">
                  Stiskněte <strong>Spustit</strong> — načte se offline fixture a
                  (u rizikových případů) ukáže se zásah bran.
                </div>
              ) : null}

              {phase === "running" ? (
                <div className="processing">
                  <p className="panel-label">Zpracování</p>
                  <ul className="steps">
                    {PROCESS_STEPS.map((s, i) => (
                      <li
                        key={s}
                        className={
                          i < stepIdx ? "done" : i === stepIdx ? "active" : ""
                        }
                      >
                        {s}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {shown ? <ResultView data={shown} gateInfo={gateInfo} view={view} /> : null}
            </>
          ) : (
            <div className="empty-state">Vyberte příklad vlevo.</div>
          )}
        </div>
      </section>

      <p className="footer-note">
        {catalog.note} · {cases.length} příkladů
      </p>
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
          <div className="label">Doporučení{view === "raw" ? " (surový)" : ""}</div>
          <div className="rec">{REC_LABEL[rec] || rec}</div>
          <p className="shrnuti">{data.shrnuti}</p>
        </div>
        <div className="scores">
          <div className="score-box">
            <div className="l">Skóre zájmu</div>
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
        <h3>Osy rizik</h3>
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
          <strong>Konflikty / injekce:</strong>
          <ul className="checklist">
            {data.konflikty_dat.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="section">
        <h3>Checklist — ověřit ručně</h3>
        <ul className="checklist">
          {(data.overit_rucne || []).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>

      {(gateInfo.politika?.length || gateInfo.normalizace?.length) && view === "guarded" ? (
        <div className="gate-box">
          <h4>Zásah deterministických bran</h4>
          <ul>
            {[...(gateInfo.normalizace || []), ...(gateInfo.politika || [])].map((g) => (
              <li key={g}>{g}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {view === "raw" ? (
        <div className="gate-box">
          <h4>Surový výstup (před branami)</h4>
          <ul>
            <li>Toto je simulovaný / zachycený modelový verdikt bez enforce_gates.</li>
            <li>Přepněte na „Po branách“ pro finální offline výsledek.</li>
          </ul>
        </div>
      ) : null}
    </div>
  );
}
