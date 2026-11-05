/** Minimal port of konzultant policy gates — offline, deterministic. */

const CORE_AXES = ["zaplava", "ochrana_prirody", "uzemni_plan", "pristup", "pravni_stav"];
const PLANNING_AXES = ["zaplava", "ochrana_prirody", "uzemni_plan"];
const SAFE_FOR_VYKUPIT = new Set(["zadne", "nizke", "stredni"]);
const BLOCKING_DALSI = new Set(["vysoke", "kriticke"]);
const PLANNING_PENALTY = { zadne: 0, nizke: 3, stredni: 8, vysoke: 15, neznamo: 6, kriticke: 30 };
const DALSI_PENALTY = { zadne: 0, nizke: 1, stredni: 3, vysoke: 6, neznamo: 2, kriticke: 30 };
const AXIS_POINTS = { zadne: 20, nizke: 16, stredni: 10, vysoke: 5, neznamo: 8, kriticke: 0 };
const CRITICAL_SCORE_CAP = 35;
const VYKUPIT_MIN_SKORE = 70;
const VYKUPIT_MIN_JISTOTA = 60;

function levels(data) {
  const rizika = data.rizika;
  const core = Object.fromEntries(CORE_AXES.map((a) => [a, rizika[a].uroven]));
  const dalsi = (rizika.dalsi || []).map((item) => item.uroven);
  return { core, dalsi };
}

function hasCritical(data) {
  const { core, dalsi } = levels(data);
  return Object.values(core).includes("kriticke") || dalsi.includes("kriticke");
}

function expectedComponents(data) {
  const { core, dalsi } = levels(data);
  const planning = PLANNING_AXES.map((a) => core[a]);
  let rizika;
  if (planning.includes("kriticke") || dalsi.includes("kriticke")) {
    rizika = 0;
  } else {
    const penalty =
      planning.reduce((s, lvl) => s + (PLANNING_PENALTY[lvl] ?? 0), 0) +
      dalsi.reduce((s, lvl) => s + (DALSI_PENALTY[lvl] ?? 0), 0);
    rizika = Math.max(0, 30 - penalty);
  }
  return {
    rizika,
    pravni_stav: AXIS_POINTS[core.pravni_stav] ?? 0,
    pristup: AXIS_POINTS[core.pristup] ?? 0,
  };
}

export function applyGates(input) {
  const data = structuredClone(input);
  const normalizace = [];
  const politika = [];

  const slozky = data.skore_slozky || {};
  const want = expectedComponents(data);
  for (const [key, val] of Object.entries(want)) {
    if (slozky[key] !== val) {
      normalizace.push(`skore_slozky.${key}: ${slozky[key]} → ${val}`);
      slozky[key] = val;
    }
  }
  let total = Object.values(slozky).reduce((a, b) => a + (typeof b === "number" ? b : 0), 0);
  if (hasCritical(data)) total = Math.min(total, CRITICAL_SCORE_CAP);
  if (data.skore_zajmu !== total) {
    normalizace.push(`skore_zajmu: ${data.skore_zajmu} → ${total}`);
    data.skore_zajmu = total;
  }
  data.skore_slozky = slozky;

  if (data.doporuceni === "vykupit") {
    const blockers = [];
    if (typeof data.skore_zajmu !== "number" || data.skore_zajmu < VYKUPIT_MIN_SKORE) {
      blockers.push(`skore_zajmu ${data.skore_zajmu} < ${VYKUPIT_MIN_SKORE}`);
    }
    const jistota = data.jistota?.skore;
    if (typeof jistota !== "number" || jistota < VYKUPIT_MIN_JISTOTA) {
      blockers.push(`jistota ${jistota} < ${VYKUPIT_MIN_JISTOTA}`);
    }
    if (data.konflikty_dat?.length) blockers.push("neprázdné konflikty_dat");
    const { core } = levels(data);
    for (const [axis, lvl] of Object.entries(core)) {
      if (!SAFE_FOR_VYKUPIT.has(lvl)) blockers.push(`rizika.${axis} = ${lvl}`);
    }
    for (const item of data.rizika.dalsi || []) {
      if (BLOCKING_DALSI.has(item.uroven)) {
        blockers.push(`rizika.dalsi '${item.nazev}' = ${item.uroven}`);
      }
    }
    if (blockers.length) {
      const { core: c, dalsi } = levels(data);
      let neu = "zkoumat_dal";
      if (PLANNING_AXES.some((a) => c[a] === "kriticke") || c.pristup === "kriticke") {
        neu = "odmitnout";
      } else if (c.pravni_stav === "kriticke" || dalsi.includes("kriticke")) {
        neu = "odlozit";
      }
      for (const b of blockers) politika.push(`brána vykupit: ${b}`);
      politika.push(`doporuceni: vykupit → ${neu}`);
      data.doporuceni = neu;
    }
  }

  return { data, normalizace, politika };
}
