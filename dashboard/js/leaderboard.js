  // ---------- leaderboard table (shared by the Overall tab and the Leaderboard tab) ----------
  const LB_SMALL_N = 100;
  const overallKeyOf = (three) => (three ? "macro_f1_3" : "macro_f1_5");

  // Columns: ranked models (near-full coverage) by overall F1, then partial runs.
  function lbColumns(models, three) {
    const k = overallKeyOf(three);
    const ranked = models.filter(isFull).sort((a, b) => b[k] - a[k]);
    const partial = models.filter((m) => !isFull(m)).sort((a, b) => b[k] - a[k]);
    return { ranked, partial, cols: [...ranked, ...partial] };
  }

  // The Overall row followed by one row per expert class (P/LP, VUS, B/LB or the five ACMG classes).
  function lbClassRows(cols, three) {
    const overallKey = overallKeyOf(three);
    const classRows = three
      ? [["pathogenic", "P/LP", "Pathogenic or likely pathogenic"], ["vus", "VUS", "Uncertain significance"], ["benign", "B/LB", "Benign or likely benign"]]
          .map(([k, abbr, label]) => ({ label, abbr, n: (cols[0].per_class_3[k] || {}).support, get: (m) => (m.per_class_3 || {})[k]?.f1, f1: true, indent: true }))
      : LABELS.map((l) => ({ label: l, abbr: ABBR[l], n: (cols[0].per_class_5[l] || {}).support, get: (m) => (m.per_class_5 || {})[l]?.f1, f1: true, indent: true }));
    const overall = { label: "Overall", n: D.metadata.total_variants, get: (m) => m[overallKey], f1: true, strong: true };
    return [overall, ...classRows];
  }

  // Notes on partial runs and models that skipped variants.
  function lbCoverageNotes(ranked, partial) {
    return [
      partial.length ? `* ${partial.map((m) => esc(m.short_name)).join(", ")} ${partial.length > 1 ? "were" : "was"} scored on part of the cohort, so ${partial.length > 1 ? "they are" : "it is"} not ranked and not eligible for best-in-row.` : "",
      ranked.filter((m) => !isComplete(m)).map((m) => `${esc(m.short_name)} returned no call for ${int(m.total_cohort - m.evaluated_count)} variant${m.total_cohort - m.evaluated_count === 1 ? "" : "s"}; it is ranked on the ${int(m.evaluated_count)} it classified.`).join("<br>")
    ];
  }

  // Measures down the side, models across grouped by section. sections: [title or null, rows].
  // With links, each model name opens that model's confusion matrices.
  function drawLbTable(table, cols, ranked, sections, links = true, countLabel = "Variants") {
    if (!cols.length) { table.innerHTML = `<tbody><tr><td class="empty">No models selected.</td></tr></tbody>`; return; }

    // Columns grouped by section, in rank order within each
    const groups = bySection(cols);
    const order = groups.flatMap(([, ms]) => ms);
    const groupStart = new Set(groups.slice(1).map(([, ms]) => ms[0].key));
    const gs = (m) => (groupStart.has(m.key) ? " lbt-gs" : "");
    const rankOf = (m) => (isFull(m) ? ranked.indexOf(m) + 1 : null);
    const SMALL_N = LB_SMALL_N;

    const groupHead = `<tr class="lbt-groups"><th class="lbt-first" colspan="2"></th>${groups.map(([g, ms, k], i) => `<th colspan="${ms.length}" class="lbt-group${i ? " lbt-gs" : ""}"${tipAttr(SECTION_TIP[k])}>${esc(g)}</th>`).join("")}</tr>`;
    const head = `<thead>${groupHead}<tr><th class="lbt-first">Measure</th><th class="lbt-nh">${countLabel}</th>${order.map((m) => {
      const r = rankOf(m);
      // Name, then provider and method (e.g. "Anthropic · AIVA agent"); the column group already shows the section
      const tip = tipAttr(`${m.name}\n${[providerOf(m), m.type].filter(Boolean).join(" · ")}${isComplete(m) ? "" : `\nScored on ${int(m.evaluated_count)} of ${int(m.total_cohort)} variants${isFull(m) ? "" : "; not ranked"}`}${links ? "\nSelect to open its analysis" : ""}`);
      const inner = `
        <span class="lbt-rank">${r ? "#" + r : "not ranked"}</span>
        <span class="lbt-label">${swatch(m)}${esc(m.short_name)}${isFull(m) ? "" : "*"}</span>
        <span class="lbt-meta">${esc(providerOf(m))}${isComplete(m) ? "" : ` · n=${int(m.evaluated_count)}`}</span>`;
      return `<th class="lbt-model${gs(m)}">${links
        ? `<button type="button" class="lbt-name" data-key="${esc(m.key)}"${tip}>${inner}</button>`
        : `<div class="lbt-name lbt-static"${tip}>${inner}</div>`}</th>`;
    }).join("")}</tr></thead>`;

    const rowHtml = (r) => {
      const rankedVals = order.filter(isFull).map((m) => r.get(m)).filter((v) => v != null);
      const bestVal = !rankedVals.length ? null : (r.low ? Math.min(...rankedVals) : Math.max(...rankedVals));
      const fmt = r.fmt || ((v) => f1(v));
      const label = `<td class="lbt-first${r.indent ? " indent" : ""}"><span class="lbt-row">${r.abbr ? tierChip(r.abbr) : ""}${esc(r.label)}${r.small ? `<sup class="lbt-dag"${tipAttr(`Fewer than ${SMALL_N} variants: interpret with caution`)}>†</sup>` : ""}</span></td>`;
      const n = r.n != null
        ? `<td class="lbt-n${r.small ? " small" : ""}"><span class="lbt-nv">${int(r.n)}</span>${r.bar != null ? `<span class="lbt-nbar"><span style="width:${Math.max(2, r.bar * 100)}%"></span></span>` : ""}</td>`
        : `<td class="lbt-n"></td>`;
      return `<tr class="${r.strong ? "lbt-overall" : ""}${r.small ? " lbt-small" : ""}">${label}${n}${order.map((m) => {
        const v = r.get(m);
        if (v == null) return `<td class="lbt-v na${gs(m)}">—</td>`;
        const isBest = bestVal != null && isFull(m) && Math.abs(v - bestVal) < 1e-9;
        // The best value in each row is tinted in the winning model's colour
        const tint = isBest ? ` style="background:color-mix(in srgb, ${m.color} var(--best-mix), var(--surface))"` : "";
        return `<td class="lbt-v${gs(m)}${isBest ? " best" : ""}${isFull(m) ? "" : " partial"}"${tint}${tipAttr(`${fullName(m)}\n${r.label}${r.f1 ? " F1" : ""}: ${fmt(v)}${r.fmt ? "" : "%"}${r.n != null ? `\nn = ${int(r.n)} variants` : ""}${isBest ? "\nBest in row" : ""}`)}>${fmt(v)}</td>`;
      }).join("")}</tr>`;
    };
    const span = order.length + 2;
    const body = sections.map(([title, rows]) => (title ? `<tr class="lbt-sec"><td class="lbt-first" colspan="${span}">${title}</td></tr>` : "") + rows.map(rowHtml).join("")).join("");
    table.innerHTML = head + `<tbody>${body}</tbody>`;
    table.querySelectorAll(".lbt-name").forEach((b) => b.onclick = () => openAnalysis(b.dataset.key));
  }

  function renderLeaderboard() {
    renderShownPicker("lb");
    seg("lbScaleSeg", state.lbScale, (v) => { state.lbScale = v; renderLeaderboard(); writeHash(); });
    // One scale for every leaderboard figure: table, scatter and agreement heatmap.
    state.scatterScale = state.lbScale;
    state.agree = state.lbScale === "5" ? "exact_pct" : "dir_pct";
    const three = state.lbScale === "3";
    const { ranked, partial, cols } = lbColumns(lbShown(), three);
    $("lbTitle").textContent = `F1 score by expert classification and disease category, ${three ? "3-class" : "5-class"}`;
    $("lbSub").innerHTML = (three
      ? "Macro F1 against the ClinGen expert classification, across P/LP, VUS and B/LB, in percent."
      : "Macro F1 against the ClinGen expert classification, across the five ACMG classes, in percent.") +
      ` The best value in each row is shaded in that model's colour.${state.lbCols ? " Ranks and highlights are among the models shown." : ""}<span class="ui-hint"> Select a model name to open its confusion matrices.</span>`;

    renderLbScatter(cols);
    renderAgreeHeat();

    if (!cols.length) { drawLbTable($("lbTable"), cols, ranked, []); $("lbNote").innerHTML = ""; return; }

    // Rows: F1 rows are shaded on one shared scale; other measures are not
    const catKey = three ? "macro_f1_3" : "macro_f1";
    const cats = categoriesWithData().sort((a, b) => catCount(b) - catCount(a));
    const maxCat = Math.max(1, ...cats.map(catCount));
    const catRows = cats.map((c) => ({
      label: c, n: catCount(c), bar: catCount(c) / maxCat, small: catCount(c) < LB_SMALL_N, f1: true,
      get: (m) => { const r = (D.categories_data[m.key] || {})[c]; return r && r.count ? r[catKey] : null; }
    }));
    const safetyRows = [
      { label: "Cross-direction errors per 1,000 variants", get: per1k, low: true, fmt: (v) => v.toFixed(2) }
    ];
    drawLbTable($("lbTable"), cols, ranked, [
      ["By expert classification", lbClassRows(cols, three)],
      ["By disease category (ClinGen expert panel)", catRows],
      ["Safety", safetyRows]
    ]);
    $("lbNote").innerHTML = [
      ...lbCoverageNotes(ranked, partial),
      cats.some((c) => catCount(c) < LB_SMALL_N) ? `† Fewer than ${LB_SMALL_N} variants; F1 on small categories varies widely.` : "",
      "Cross-direction errors: a benign or likely benign variant called pathogenic or likely pathogenic, or the reverse."
    ].filter(Boolean).join("<br>");
  }

  // ---------- overall ----------
  // A snapshot of each benchmark. Classification: every model, 3-class, Overall plus P/LP, VUS and B/LB.
  function renderOverall() {
    const { ranked, partial, cols } = lbColumns(D.models.slice(), true);
    $("ovSub").innerHTML = `Macro F1 against the ClinGen expert classification, across P/LP, VUS and B/LB, in percent, for all ${D.models.length} models. The best value in each row is shaded in that model's colour.<span class="ui-hint"> <button type="button" class="ov-link" id="ovFull">See the full leaderboard</button> for disease categories, safety and model filters.</span>`;
    $("ovFull").onclick = () => { state.lbCols = null; state.lbScale = "3"; setTab("leaderboard"); window.scrollTo({ top: 0 }); };
    drawLbTable($("ovTable"), cols, ranked, [[null, cols.length ? lbClassRows(cols, true) : []]], false);
    $("ovNote").innerHTML = lbCoverageNotes(ranked, partial).filter(Boolean).join("<br>");
    renderOverallRanking();
  }

  // ---------- ranking ----------
  const RK_CAT_LABELS = {
    known_gene_disease: "Established cases (known gene & disease)",
    known_gene_known_disease: "Known gene, known disease (extended KG)",
    known_gene_new_disease: "Known gene, new disease association",
    new_gene_known_disease: "Novel gene, known disease",
    new_gene_new_disease: "Novel gene & novel disease"
  };
  const RK_CAT_COUNTS = {
    known_gene_disease: 422,
    known_gene_known_disease: 234,
    known_gene_new_disease: 244,
    new_gene_known_disease: 246,
    new_gene_new_disease: 250
  };

  function rankingOverallRows() {
    const totalN = D.ranking_data?.metadata?.total_cohort || 1396;
    return [
      { label: "Recall@1 (Top-1 accuracy)", n: totalN, get: (m) => m.top1_pct, strong: true },
      { label: "Recall@5 (Top-5)", n: totalN, get: (m) => m.top5_pct, indent: true },
      { label: "Recall@10 (Top-10)", n: totalN, get: (m) => m.top10_pct, indent: true },
      { label: "Recall@20 (Top-20)", n: totalN, get: (m) => m.top20_pct, indent: true },
      { label: "Mean Reciprocal Rank (MRR)", n: totalN, get: (m) => m.mrr, fmt: (v) => Number(v).toFixed(4), strong: true },
      { label: "NDCG@10", n: totalN, get: (m) => m.ndcg10, fmt: (v) => Number(v).toFixed(4) },
      { label: "Median Rank", n: totalN, get: (m) => m.median_rank, fmt: (v) => Number(v).toFixed(0), low: true }
    ];
  }

  function rankingCatRows(metricKey, suffix = "", fmt = null) {
    const counts = (D.ranking_data && D.ranking_data.metadata && D.ranking_data.metadata.category_counts) || RK_CAT_COUNTS;
    const maxN = Math.max(...Object.values(counts)) || 100;
    return Object.keys(RK_CAT_LABELS).map((cat) => ({
      label: RK_CAT_LABELS[cat],
      n: counts[cat] || 100,
      bar: (counts[cat] || 100) / maxN,
      get: (m) => (m.per_category?.[cat] ? m.per_category[cat][metricKey] : null),
      fmt: fmt || ((v) => f1(v) + suffix)
    }));
  }

  function renderOverallRanking() {
    const rk = D.ranking_data;
    if (!rk || !rk.models || !rk.models.length || !$("ovRankTable")) return;
    const rkCols = rk.models.slice().sort((a, b) => b.mrr - a.mrr);
    $("ovRankSub").innerHTML = `Recall@K and Mean Reciprocal Rank (MRR) across rare disease cases for all ${rkCols.length} models and baselines. Models run through AIVA (Sonnet 5.5, Gemini 3 Pro, Haiku 5.5) are compared against automated phenotype tools. The best value in each row is shaded in that model's colour.<span class="ui-hint"> <button type="button" class="ov-link" id="ovRankFull">See the full ranking leaderboard</button> for category breakdowns.</span>`;
    $("ovRankFull").onclick = () => { setTab("ranking"); window.scrollTo({ top: 0 }); };
    drawLbTable($("ovRankTable"), rkCols, rkCols, [[null, rankingOverallRows()]], false, "Cases");
    const nCohort = D.ranking_data?.metadata?.total_cohort || 1396;
    $("ovRankNote").innerHTML = `MRR denominator is n_total (cases where true gene was absent contribute 0). All models were evaluated across all ${nCohort} rare-disease patient cases. Recall@K is the percentage of cases where the true causal gene was ranked &le; K.`;
  }

  function renderRanking() {
    const rk = D.ranking_data;
    if (!rk || !rk.models || !rk.models.length || !$("rkTable")) return;
    const rkCols = rk.models.slice().sort((a, b) => b.mrr - a.mrr);
    $("rkTitle").textContent = "Variant ranking: Gene prioritization across rare disease cases";
    $("rkSub").innerHTML = "Recall@K and Mean Reciprocal Rank (MRR) across simulated rare-disease patient cases, evaluated overall and across 5 gene and disease familiarity categories. Models run through the AIVA agent (GPT-6.1 Sol, Claude Sonnet 5.5, Claude Opus 5.5, Gemini 3 Pro, Claude Haiku 5.5) are compared against classical rule-based phenotype tools (LIRICAL, ClinPrior, Exomiser). The best value in each row is shaded in that model's colour.";

    const sections = [
      ["Overall performance", rankingOverallRows()],
      ["Recall@1 by disease & gene familiarity category", rankingCatRows("top1_pct")],
      ["Recall@5 by disease & gene familiarity category", rankingCatRows("top5_pct")],
      ["Mean Reciprocal Rank (MRR) by category", rankingCatRows("mrr", "", (v) => Number(v).toFixed(4))]
    ];
    drawLbTable($("rkTable"), rkCols, rkCols, sections, false, "Cases");
    const rkCohort = D.ranking_data?.metadata?.total_cohort || 1396;
    $("rkNote").innerHTML = [
      "MRR denominator is n_total (cases where true gene was absent contribute 0).",
      `All models were evaluated across all ${rkCohort} rare-disease patient cases.`,
      "Categories test model generalisation from established gene-disease pairs to novel diseases and entirely novel disease genes without existing KG connections.",
      "Recall@K is the percentage of cases where the true causal gene was ranked &le; K."
    ].join("<br>");
  }

  // Accuracy against safety: score across, cross-direction errors up (inverted).
  const SCATTER_X = {
    "5": { key: "macro_f1_5", label: "Macro F1, 5-class" },
    "3": { key: "macro_f1_3", label: "Macro F1, 3-class" }
  };

  function renderLbScatter(models) {
    const ax = SCATTER_X[state.scatterScale];
    $("scTitle").textContent = `Accuracy against safety: ${ax.label}`;
    const scored = models.every(isFull)
      ? ` Every model was scored on all ${int(D.metadata.total_variants)} variants.`
      : " Hollow dots were scored on part of the cohort.";
    $("scSub").innerHTML = `The table's Overall row against cross-direction errors per 1,000 variants; up and to the right is better.${scored}<span class="ui-hint"> Hover a dot for its values; select it to open the model.</span>`;
    const box = $("lbScatter");
    if (!models.length) { box.innerHTML = `<p class="empty">No models for this filter.</p>`; return; }
    const W = Math.max(300, box.clientWidth || 1000);
    const narrow = W < 640;
    const H = narrow ? 340 : 440, top = 14, bottom = 52, left = narrow ? 44 : 64, right = narrow ? 8 : 24;
    const xs = models.map((m) => m[ax.key] ?? 0), es = models.map(per1k);
    const lo = Math.max(0, Math.floor((Math.min(...xs) - 3) / 10) * 10), hi = Math.min(100, Math.ceil((Math.max(...xs) + 2) / 10) * 10);
    const ymax = Math.max(2, Math.ceil((Math.max(...es) + 0.4) / 2) * 2);
    // Padding inside both axes so no dot or label sits on the plot edge
    const xPad = (hi - lo) * 0.04, yPad = ymax * 0.08;
    const x = (v) => left + ((v - (lo - xPad)) / Math.max(1, hi - lo + 2 * xPad)) * (W - left - right);
    const y = (v) => top + ((v + yPad) / (ymax + yPad)) * (H - top - bottom);
    const out = [];
    for (let t = lo; t <= hi; t += 10) out.push(`<line class="sc-grid" x1="${n2(x(t))}" x2="${n2(x(t))}" y1="${top}" y2="${H - bottom}"/><text class="sc-tick" x="${n2(x(t))}" y="${H - bottom + 16}" text-anchor="middle">${t}%</text>`);
    const ystep = ymax > 6 ? 2 : 1;
    for (let t = 0; t <= ymax; t += ystep) out.push(`<line class="sc-grid" x1="${left}" x2="${W - right}" y1="${n2(y(t))}" y2="${n2(y(t))}"/><text class="sc-tick" x="${left - 8}" y="${n2(y(t) + 4)}" text-anchor="end">${t}</text>`);
    out.push(`<line class="sc-axis" x1="${left}" x2="${W - right}" y1="${H - bottom}" y2="${H - bottom}"/><line class="sc-axis" x1="${left}" x2="${left}" y1="${top}" y2="${H - bottom}"/>`);
    out.push(`<text class="sc-axl" x="${n2((left + W - right) / 2)}" y="${H - 12}" text-anchor="middle">${ax.label} →</text>`);
    const yc = (top + H - bottom) / 2;
    out.push(`<text class="sc-axl" x="14" y="${n2(yc)}" text-anchor="middle" transform="rotate(-90 14 ${n2(yc)})">${narrow ? "↑ Fewer errors / 1,000" : "↑ Fewer cross-direction errors per 1,000 variants"}</text>`);

    // Labels: try eight positions around each dot, keep the first that collides with nothing
    measureCtx.font = `500 12px ${getComputedStyle(document.body).fontFamily}`;
    const pts = models.map((m) => ({ m, cx: x(m[ax.key] ?? 0), cy: y(per1k(m)) }));
    const boxes = pts.map((p) => [p.cx - 8, p.cy - 8, p.cx + 8, p.cy + 8]);
    const overlaps = (b, list) => list.some((o) => !(b[2] < o[0] || b[0] > o[2] || b[3] < o[1] || b[1] > o[3]));
    // On narrow screens the colour key above the chart names the models instead.
    if (!narrow) pts.slice().sort((a, b) => a.cy - b.cy || b.cx - a.cx).forEach((p) => {
      const text = `${nameIn(p.m, models)}${isFull(p.m) ? "" : "*"}${narrow ? "" : "  " + f1(p.m[ax.key])}`;
      const w = measureCtx.measureText(text).width + 4, h = 14;
      const cands = [[12, 4, "start"], [-12, 4, "end"], [0, -14, "middle"], [0, 22, "middle"], [12, -12, "start"], [12, 20, "start"], [-12, -12, "end"], [-12, 20, "end"]];
      p.text = text;
      p.lab = null;
      for (const [dx, dy, a] of cands) {
        const lx = p.cx + dx, ly = p.cy + dy;
        const x0 = a === "start" ? lx : a === "end" ? lx - w : lx - w / 2;
        const b = [x0, ly - h + 3, x0 + w, ly + 3];
        if (!overlaps(b, boxes) && b[0] > left && b[2] < W - 2 && b[1] > 0 && b[3] < H - bottom) { boxes.push(b); p.lab = [lx, ly, a]; break; }
      }
    });
    pts.forEach((p) => {
      const m = p.m, full = isFull(m);
      const tip = `${fullName(m)}${full ? "" : `, scored on ${int(m.evaluated_count)} variants`}\n${ax.label}: ${pct(m[ax.key])}\nCross-direction errors: ${int(m.critical_error_count)} (${per1k(m).toFixed(2)} per 1,000)`;
      out.push(`<g class="sc-pt" data-key="${esc(m.key)}"${tipAttr(tip)}>
        <circle cx="${n2(p.cx)}" cy="${n2(p.cy)}" r="7" style="fill:${full ? m.color : "var(--surface)"};stroke:${full ? "var(--surface)" : m.color};stroke-width:2"/>
        ${p.lab ? `<text class="sc-lbl" x="${n2(p.lab[0])}" y="${n2(p.lab[1])}" text-anchor="${p.lab[2]}">${esc(p.text)}</text>` : ""}
      </g>`);
    });
    const unlabelled = narrow ? pts : pts.filter((p) => !p.lab);
    const key = unlabelled.length ? `<div class="legend sc-key">${models.map((m) => `<span>${mname(m, models)}${isFull(m) ? "" : "*"}</span>`).join("")}</div>` : "";
    box.innerHTML = key + `<svg class="scatter" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(ax.label)} against cross-direction errors for each model">${out.join("")}</svg>` +
      (models.some((m) => !isFull(m)) ? `<p class="note">* Scored on part of the cohort, not ranked.</p>` : "");
  }

  function setupLbScatter() {
    const box = $("lbScatter");
    const clear = () => { const svg = box.querySelector("svg"); if (svg) { svg.classList.remove("hl"); svg.querySelectorAll(".on").forEach((e) => e.classList.remove("on")); } };
    box.addEventListener("pointerover", (e) => { const g = e.target.closest(".sc-pt"); clear(); if (g) { g.ownerSVGElement.classList.add("hl"); g.classList.add("on"); } });
    box.addEventListener("pointerleave", clear);
    box.addEventListener("click", (e) => { const g = e.target.closest(".sc-pt"); if (g) openAnalysis(g.dataset.key); });
  }

  function openAnalysis(key) {
    if (!byKey(key)) return;
    state.cmCols = D.models.length > 1 ? [key] : null;
    setTab("confusion");
    window.scrollTo({ top: 0 });
  }

