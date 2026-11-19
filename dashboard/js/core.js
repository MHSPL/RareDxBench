  let D = window.BENCHMARK_DATA;
  const LABELS = ["Pathogenic", "Likely Pathogenic", "Likely Benign", "Benign"];
  const ABBR = { "Pathogenic": "P", "Likely Pathogenic": "LP", "Uncertain Significance": "VUS", "Likely Benign": "LB", "Benign": "B" };
  const DIR_LABEL = { pathogenic: "P/LP", benign: "B/LB" };
  const FAMILY_SHORT = {};
  const PROVIDER_ORDER = ["Anthropic", "OpenAI", "Google", "Rule-based", "Other"];
  // [code, definition shown on hover, short label (unused in the grid)]
  const CRITERIA = {
    pathogenic: [
      ["PVS1", "Null variant in a gene where loss of function is a known mechanism", "Null variant, loss-of-function gene"],
      ["PS1", "Same amino-acid change as an established pathogenic variant", "Same amino-acid change as a pathogenic variant"],
      ["PS2", "De novo, maternity and paternity confirmed", "De novo, parentage confirmed"],
      ["PS3", "Well-established functional studies show a damaging effect", "Functional studies show damage"],
      ["PS4", "Prevalence in affected individuals significantly above controls", "Enriched in affected individuals"],
      ["PM1", "Located in a mutational hot spot or critical functional domain", "Hot spot or critical domain"],
      ["PM2", "Absent or at extremely low frequency in population databases", "Absent or rare in population databases"],
      ["PM3", "Detected in trans with a pathogenic variant (recessive disorders)", "In trans with a pathogenic variant"],
      ["PM4", "Protein length change from an in-frame indel or stop-loss", "Protein length change"],
      ["PM5", "Novel missense at a residue where another pathogenic missense is known", "New missense at a pathogenic residue"],
      ["PM6", "Assumed de novo, without confirmation of parentage", "Assumed de novo"],
      ["PP1", "Cosegregation with disease in multiple affected family members", "Cosegregates with disease"],
      ["PP2", "Missense in a gene with a low rate of benign missense variation", "Missense in a missense-intolerant gene"],
      ["PP3", "Computational evidence supports a deleterious effect", "Computational evidence, damaging"],
      ["PP4", "Patient phenotype or family history highly specific for the gene", "Phenotype specific for the gene"],
      ["PP5", "Reputable source reports the variant as pathogenic", "Reputable source reports pathogenic"]
    ],
    benign: [
      ["BA1", "Allele frequency above 5% in population databases", "Allele frequency above 5%"],
      ["BS1", "Allele frequency greater than expected for the disorder", "Too common for the disorder"],
      ["BS2", "Observed in a healthy adult where full penetrance is expected", "Seen in a healthy adult"],
      ["BS3", "Well-established functional studies show no damaging effect", "Functional studies show no damage"],
      ["BS4", "Lack of segregation in affected family members", "Does not segregate with disease"],
      ["BP1", "Missense in a gene where truncating variants cause disease", "Missense where truncating variants cause disease"],
      ["BP2", "Observed in trans or in cis with a pathogenic variant", "In trans or cis with a pathogenic variant"],
      ["BP3", "In-frame indel in a repetitive region without known function", "In-frame indel in a repeat region"],
      ["BP4", "Computational evidence suggests no impact", "Computational evidence, no impact"],
      ["BP5", "Variant found in a case with an alternate molecular basis", "Alternate molecular cause found"],
      ["BP7", "Synonymous variant with no predicted splice impact", "Synonymous, no splice impact"]
    ]
  };
  const STRENGTH = { PVS: "Very strong", PS: "Strong", PM: "Moderate", PP: "Supporting", BA: "Stand-alone", BS: "Strong", BP: "Supporting" };
  const strengthOf = (code) => STRENGTH[code.replace(/\d+$/, "")];
  // ACMG tier chip: t is p, lp, vus, lb or b
  const TIER_OF = { P: "p", LP: "lp", VUS: "vus", LB: "lb", B: "b", "P/LP": "p", "B/LB": "b" };
  const tierChip = (abbr) => `<span class="tier tier-${TIER_OF[abbr]}">${esc(abbr)}</span>`;

  const state = {
    tab: "overall", clsTab: "leaderboard",
    scatterScale: "2", lbScale: "2", lbCols: null,
    cmCols: null, critCols: null, rkCols: null, dxCols: null, rkTab: "ranking",
    cmScale: "4", cmMode: "count",
    agree: "dir_pct"
  };

  // ---------- helpers ----------
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const f1 = (v) => (v == null || isNaN(v) ? "—" : Number(v).toFixed(1));
  const pct = (v) => (v == null || isNaN(v) ? "—" : Number(v).toFixed(1) + "%");
  const int = (v) => Number(v || 0).toLocaleString("en-US");
  const signed = (v, d = 1) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d);
  const byKey = (k) => D.models.find((m) => m.key === k) || (D.ranking_data?.models || []).find((m) => m.key === k);
  // Ranked when at least 99% of the cohort was scored; the rest are partial runs
  const FULL_COVERAGE = 0.99;
  const isFull = (m) => m.evaluated_count >= FULL_COVERAGE * m.total_cohort;
  const isComplete = (m) => m.evaluated_count >= m.total_cohort;
  const shortFam = (f) => FAMILY_SHORT[f] || f;
  const tipAttr = (s) => ` data-tip="${esc(s)}"`;

  function heatStyle(t) {
    t = Math.max(0, Math.min(1, t || 0));
    const p = Math.round(t * 100);
    const ink = t > 0.55 ? "var(--seq-ink)" : "var(--text)";
    return `background:color-mix(in oklab, var(--seq-hi) ${p}%, var(--seq-lo));color:${ink}`;
  }

  function seg(id, value, onPick) {
    const el = $(id);
    el.querySelectorAll("button").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.v === value));
      b.onclick = () => onPick(b.dataset.v);
    });
  }

  function toast(msg) {
    const t = $("toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (t.hidden = true), 4000);
  }

  // ---------- tooltip ----------
  const tip = $("tip");
  function placeTip(e) {
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    let x = e.clientX + pad, y = e.clientY + pad;
    if (x + w > window.innerWidth - 8) x = e.clientX - w - pad;
    if (y + h > window.innerHeight - 8) y = e.clientY - h - pad;
    tip.style.left = Math.max(8, x) + "px";
    tip.style.top = Math.max(8, y) + "px";
  }
  document.addEventListener("pointerover", (e) => {
    const t = e.target.closest && e.target.closest("[data-tip]");
    if (!t) { tip.hidden = true; return; }
    tip.textContent = t.dataset.tip;
    tip.hidden = false;
    placeTip(e);
  });
  document.addEventListener("pointermove", (e) => { if (!tip.hidden) placeTip(e); });
  document.addEventListener("pointerout", (e) => { if (!e.relatedTarget) tip.hidden = true; });
  window.addEventListener("scroll", () => (tip.hidden = true), { passive: true });

  // ---------- footer ----------
  function renderChrome() {
    $("sfYear").textContent = String(new Date().getFullYear());
    // The note under the figures only appears when a model is carried over from an earlier build.
    const archived = D.models.filter((m) => m.archived);
    $("archivedNote").innerHTML = archived.length ? "* Carried over from an earlier build without raw variant-level results." : "";
  }

  // ---------- model selection ----------
  const swatch = (m) => `<span class="swatch" style="background:${m.color}"></span>`;
  // Models are named without their method: Opus 5.5 runs through AIVA and through
  // Claude Code. On the leaderboard the section columns tell them apart; elsewhere,
  // where two models with the same name appear together, the method is added.
  function nameIn(m, set) {
    const dup = (set || []).some((o) => o !== m && o.short_name === m.short_name);
    return dup ? `${m.short_name} (${methodShort(m.type)})` : m.short_name;
  }
  // Tooltips and the CSV name a model unambiguously among all models.
  const fullName = (m) => (D.models.some((o) => o !== m && o.name === m.name) ? `${m.name} (${methodShort(m.type)})` : m.name);
  const mname = (m, set) => `${swatch(m)}${esc(nameIn(m, set))}`;

  const providerOf = (m) => (PROVIDER_ORDER.includes(m.family) ? m.family : "Other");
  const methodShort = (t) => (/claude code/i.test(t || "") ? "Claude Code" : /aiva/i.test(t || "") ? "AIVA" : (t || "Other"));
  // A provider with models from more than one method is split, e.g. "Anthropic · AIVA" and "Anthropic · Claude Code".
  function groupOf(m) {
    const p = providerOf(m);
    const methods = new Set(D.models.filter((x) => providerOf(x) === p).map((x) => methodShort(x.type)));
    return methods.size > 1 ? `${p} · ${methodShort(m.type)}` : p;
  }
  // Leaderboard sections: AIVA runs, Claude app runs (Claude Code, ...), automated rule-based pipelines
  const SECTIONS = [["aiva", "AIVA"], ["agent", "Claude app"], ["automated", "Automated"]];
  const SECTION_TIP = { aiva: "Models run through the AIVA agent", agent: "Models run through the Claude app, such as Claude Code", automated: "Rule-based ACMG classification pipelines" };
  const sectionKey = (m) => {
    if (SECTIONS.some(([k]) => k === m.section)) return m.section;
    if (m.family === "Rule-based" || /rules/i.test(m.type || "")) return "automated";
    return /aiva/i.test(`${m.type || ""} ${m.name}`) ? "aiva" : "agent";
  };
  const sectionLabel = (m) => SECTIONS.find(([k]) => k === sectionKey(m))[1];
  const bySection = (models) => SECTIONS.map(([k, label]) => [label, models.filter((m) => sectionKey(m) === k), k]).filter(([, ms]) => ms.length);

  const collapse2 = (m4) => {
    const rowIdx = [0, 0, 1, 1];
    const colIdx = [0, 0, 1, 1, 2];
    const out = [[0, 0, 0], [0, 0, 0]];
    m4.forEach((row, i) => row.forEach((v, j) => (out[rowIdx[i]][colIdx[j]] += v)));
    return out;
  };
  const collapse3 = collapse2;

  const per1k = (m) => (m.evaluated_count ? (m.critical_error_count / m.evaluated_count) * 1000 : 0);
  const metricVal = (m, k) => (k === "per1k" ? per1k(m) : m[k] ?? 0);
  const metricTxt = (m, k) => (k === "per1k" ? per1k(m).toFixed(2) : f1(m[k]));

  // Show/hide model pickers for the leaderboard ("lb"), confusion ("cm"), criteria ("crit") and ranking ("rk") tabs.
  // state.lbCols / state.cmCols / state.rkCols hold the shown keys; null means all models.
  const SHOWN = {
    lb: { col: "lbCols", where: "the leaderboard", render: () => (state.tab === "categories" ? renderCategories() : renderLeaderboard()) },
    cm: { col: "cmCols", where: "the confusion matrices", render: () => renderConfusion() },
    crit: { col: "critCols", where: "the criteria charts", render: () => renderCriteria() },
    rk: { col: "rkCols", where: "the ranking leaderboard", render: () => renderRanking() },
    dx: { col: "dxCols", where: "the distractor charts", render: () => renderDistractors() }
  };
  const PICKER_OF = { leaderboard: "lb", categories: "lb", confusion: "cm", criteria: "crit", ranking: "rk", distractors: "dx" };
  const modelPool = (p) => (p === "rk" ? (D.ranking_data?.models || []) : p === "dx" ? dxModels() : D.models);
  // Drop keys of models that no longer exist; an emptied selection falls back to all.
  function pruneShown() {
    Object.entries(SHOWN).forEach(([p, { col }]) => {
      if (!state[col]) return;
      const pool = modelPool(p);
      const valid = state[col].filter((k) => pool.some((m) => m.key === k));
      state[col] = valid.length && valid.length < pool.length ? valid : null;
    });
  }
  const shownModels = (p) => {
    const pool = modelPool(p);
    return state[SHOWN[p].col] ? pool.filter((m) => state[SHOWN[p].col].includes(m.key)) : pool.slice();
  };
  const lbShown = () => shownModels("lb");
  const rkShown = () => shownModels("rk");

  function setShown(p, keys) {
    const pool = modelPool(p);
    const valid = keys.filter((k) => pool.some((m) => m.key === k));
    state[SHOWN[p].col] = !valid.length || valid.length === pool.length ? null : valid;
    SHOWN[p].render();
    writeHash();
  }

  function toggleShown(p, key) {
    const cur = shownModels(p).map((m) => m.key);
    if (cur.includes(key)) {
      if (cur.length === 1) { toast("Keep at least one model."); return; }
      setShown(p, cur.filter((k) => k !== key));
    } else setShown(p, [...cur, key]);
  }

  function renderShownPicker(p) {
    const pool = modelPool(p);
    const shown = shownModels(p);
    const all = !state[SHOWN[p].col];
    $(p + "AllBtn").hidden = all;
    $(p + "PickBtn").textContent = all ? `All ${pool.length} models` : `${shown.length} of ${pool.length} models`;
    const q = ($(p + "PickQ").value || "").trim().toLowerCase();
    const keys = shown.map((m) => m.key);
    const list = pool.filter((m) => !q || `${m.name} ${m.family} ${m.type}`.toLowerCase().includes(q));
    const listEl = $(p + "PickList");
    listEl.innerHTML = bySection(list).map(([g, ms]) => {
      const allOn = ms.every((m) => keys.includes(m.key));
      return `<div class="pk-group"><div class="pk-head"><span>${esc(g)}</span><button type="button" class="pk-all" data-group="${esc(g)}">${allOn ? "Clear" : "Select all"}</button></div>${ms.map((m) =>
        `<label class="pk-item"><input type="checkbox" data-key="${esc(m.key)}"${keys.includes(m.key) ? " checked" : ""}>${swatch(m)}<span>${esc(m.name)}</span><small>${esc(m.type)}${isFull(m) ? "" : " · subset"}</small></label>`).join("")}</div>`;
    }).join("") || `<p class="empty">No models match.</p>`;
    listEl.querySelectorAll("input[data-key]").forEach((c) => c.onchange = () => toggleShown(p, c.dataset.key));
    listEl.querySelectorAll(".pk-all").forEach((b) => b.onclick = () => {
      const ms = list.filter((m) => sectionLabel(m) === b.dataset.group).map((m) => m.key);
      const cur = shownModels(p).map((m) => m.key);
      if (ms.every((k) => cur.includes(k))) {
        const rest = cur.filter((k) => !ms.includes(k));
        if (!rest.length) { toast("Keep at least one model."); return; }
        setShown(p, rest);
      } else setShown(p, [...new Set([...cur, ...ms])]);
    });
  }

  function setupShownPicker(p) {
    const pop = $(p + "PickPop"), btn = $(p + "PickBtn");
    const close = () => { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); };
    btn.onclick = () => { pop.hidden = !pop.hidden; btn.setAttribute("aria-expanded", String(!pop.hidden)); if (!pop.hidden) { renderShownPicker(p); $(p + "PickQ").focus(); } };
    $(p + "PickQ").oninput = () => renderShownPicker(p);
    $(p + "AllBtn").onclick = () => setShown(p, []);
    document.addEventListener("click", (e) => { if (!pop.hidden && !pop.parentElement.contains(e.target)) close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.hidden) { close(); btn.focus(); } });
  }

