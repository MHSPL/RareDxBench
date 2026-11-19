  // ---------- leaderboard table (shared by the Overall tab and the Leaderboard tab) ----------
  const LB_SMALL_N = 100;
  const overallKeyOf = (two) => (two === true || two === "2" || two === "3" ? "macro_f1_3" : "macro_f1_5");

  // Columns: ranked models (near-full coverage) by overall F1, then partial runs.
  function lbColumns(models, two) {
    const k = overallKeyOf(two);
    const ranked = models.filter(isFull).sort((a, b) => b[k] - a[k]);
    const partial = models.filter((m) => !isFull(m)).sort((a, b) => b[k] - a[k]);
    return { ranked, partial, cols: [...ranked, ...partial] };
  }

  // The Overall row followed by one row per expert class (P/LP and B/LB directional, or the four ACMG classes).
  function lbClassRows(cols, two) {
    const isTwo = two === true || two === "2" || two === "3";
    const overallKey = overallKeyOf(isTwo);
    const classRows = isTwo
      ? [["pathogenic", "P/LP", "Pathogenic or likely pathogenic"], ["benign", "B/LB", "Benign or likely benign"]]
          .map(([k, abbr, label]) => ({ label, abbr, n: (cols[0].per_class_3[k] || {}).support, get: (m) => (m.per_class_3 || {})[k]?.f1, f1: true, indent: true }))
      : LABELS.map((l) => ({ label: l, abbr: ABBR[l], n: (cols[0].per_class_5[l] || {}).support, get: (m) => (m.per_class_5 || {})[l]?.f1, f1: true, indent: true }));
    const overall = { label: "Overall", n: D.metadata.total_variants, get: (m) => m[overallKey], f1: true, strong: true };
    return [overall, ...classRows];
  }

  // Notes on partial runs and models that skipped variants.
  function lbCoverageNotes(ranked, partial) {
    return [
      partial.length ? "* Partial runs scored on a subset of variants; not ranked." : "",
      ranked.some((m) => !isComplete(m)) ? "Some models returned no-call for a fraction of variants and are scored on classified variants only." : ""
    ];
  }

  // Measures down the side, models across grouped by section.
  function drawLbTable(table, cols, ranked, rows) {
    if (!cols.length) { table.innerHTML = `<tbody><tr><td class="empty">No models selected.</td></tr></tbody>`; return; }
    const groups = bySection(cols);
    const order = groups.flatMap(([, ms]) => ms);
    const groupStart = new Set(groups.slice(1).map(([, ms]) => ms[0].key));
    const gs = (m) => (groupStart.has(m.key) ? " lbt-gs" : "");
    const rankOf = (m) => (isFull(m) ? ranked.indexOf(m) + 1 : null);
    const groupHead = `<tr class="lbt-groups"><th class="lbt-first" colspan="2"></th>${groups.map(([g, ms, k], i) => `<th colspan="${ms.length}" class="lbt-group${i ? " lbt-gs" : ""}"${tipAttr(SECTION_TIP[k])}>${esc(g)}</th>`).join("")}</tr>`;
    const head = `<thead>${groupHead}<tr><th class="lbt-first">Category</th><th class="lbt-nh">Variants</th>${order.map((m) => {
      const r = rankOf(m);
      const tip = tipAttr(`${m.name}\n${[providerOf(m), m.type].filter(Boolean).join(" \u00b7 ")}${isComplete(m) ? "" : `\nScored on ${int(m.evaluated_count)} of ${int(m.total_cohort)} variants${isFull(m) ? "" : "; not ranked"}`}\nSelect to open its analysis`);
      const inner = `
        <span class="lbt-rank">${r ? "#" + r : "not ranked"}</span>
        <span class="lbt-label">${swatch(m)}${esc(m.short_name)}${isFull(m) ? "" : "*"}</span>
        <span class="lbt-meta">${esc(providerOf(m))}${isComplete(m) ? "" : ` \u00b7 n=${int(m.evaluated_count)}`}</span>`;
      return `<th class="lbt-model${gs(m)}"><button type="button" class="lbt-name" data-key="${esc(m.key)}"${tip}>${inner}</button></th>`;
    }).join("")}</tr></thead>`;
    const rowHtml = (r) => {
      const vals = order.filter(isFull).map((m) => r.get(m)).filter((v) => v != null);
      const bestVal = vals.length ? Math.max(...vals) : null;
      const label = `<td class="lbt-first"><span class="lbt-row">${esc(r.label)}${r.small ? `<sup class="lbt-dag"${tipAttr(`Fewer than ${LB_SMALL_N} variants: interpret with caution`)}>\u2020</sup>` : ""}</span></td>`;
      const n = `<td class="lbt-n${r.small ? " small" : ""}"><span class="lbt-nv">${int(r.n)}</span><span class="lbt-nbar"><span style="width:${Math.max(2, r.bar * 100)}%"></span></span></td>`;
      return `<tr${r.small ? ' class="lbt-small"' : ""}>${label}${n}${order.map((m) => {
        const v = r.get(m);
        if (v == null) return `<td class="lbt-v na${gs(m)}">\u2014</td>`;
        const isBest = bestVal != null && isFull(m) && Math.abs(v - bestVal) < 1e-9;
        const tint = isBest ? ` style="background:color-mix(in srgb, ${m.color} var(--best-mix), var(--surface))"` : "";
        return `<td class="lbt-v${gs(m)}${isBest ? " best" : ""}${isFull(m) ? "" : " partial"}"${tint}${tipAttr(`${fullName(m)}\n${r.label} F1: ${f1(v)}%\nn = ${int(r.n)} variants${isBest ? "\nBest in row" : ""}`)}>${f1(v)}</td>`;
      }).join("")}</tr>`;
    };
    table.innerHTML = head + `<tbody>${rows.map(rowHtml).join("")}</tbody>`;
    table.querySelectorAll(".lbt-name").forEach((b) => b.onclick = () => openAnalysis(b.dataset.key));
  }

  // Leaderboard tables: models down the side (grouped by section, ranked), measures across.
  // groups: [[title, rows]] where each row is {label, short, abbr, get, fmt, low, n, small, strong}.
  // opts.links: each model name opens that model's confusion matrices. opts.countText: header over the model column.
  function drawOverallTable(table, cols, ranked, groups, opts = {}) {
    const { links = false, countText = "" } = opts;
    // A flat row list is a single untitled group
    if (groups.length && !Array.isArray(groups[0])) groups = [[null, groups]];
    const rows = groups.flatMap(([, rs]) => rs);
    if (!cols.length || !rows.length) { table.innerHTML = `<tbody><tr><td class="empty">No models.</td></tr></tbody>`; return; }
    const titled = groups.some(([t]) => t);
    const groupStart = new Set(groups.slice(1).map(([, rs]) => rs[0]));
    const gs = (r) => (titled && groupStart.has(r) ? " ovt-gs" : "");
    const mHead = (r) => {
      const tip = `${r.label}${r.n != null ? `\nn = ${int(r.n)}` : ""}${r.small ? `\nFewer than ${LB_SMALL_N}: interpret with caution` : ""}`;
      return `<th class="ovt-m${gs(r)}" scope="col"${tipAttr(tip)}>${r.abbr ? tierChip(r.abbr) : esc(r.short || r.label)}${r.small ? `<sup class="lbt-dag">\u2020</sup>` : ""}</th>`;
    };
    const groupHead = titled
      ? `<tr class="ovt-groups"><th class="ovt-rank"></th><th class="ovt-name"></th>${groups.map(([t, rs], i) => `<th colspan="${rs.length}" scope="colgroup" class="ovt-group${i ? " ovt-gs" : ""}">${esc(t || "")}</th>`).join("")}</tr>`
      : "";
    const best = rows.map((r) => {
      const vals = cols.filter(isFull).map((m) => r.get(m)).filter((v) => v != null);
      return !vals.length ? null : (r.low ? Math.min(...vals) : Math.max(...vals));
    });
    const rowFor = (m) => {
      const rk = isFull(m) ? ranked.indexOf(m) + 1 : null;
      const tip = tipAttr(`${fullName(m)}\n${[providerOf(m), m.type].filter(Boolean).join(" \u00b7 ")}${isComplete(m) ? "" : `\nScored on ${int(m.evaluated_count)} of ${int(m.total_cohort)}${isFull(m) ? "" : "; not ranked"}`}${links ? "\nSelect to open its analysis" : ""}`);
      const inner = `<span class="ovt-label">${swatch(m)}${esc(nameIn(m, cols))}${isFull(m) ? "" : "*"}</span><span class="lbt-meta">${esc(providerOf(m))} \u00b7 ${esc(methodShort(m.type))}${isComplete(m) ? "" : ` \u00b7 n=${int(m.evaluated_count)}`}</span>`;
      const cells = rows.map((r, i) => {
        const v = r.get(m);
        if (v == null) return `<td class="lbt-v na${gs(r)}">\u2014</td>`;
        const isBest = best[i] != null && isFull(m) && Math.abs(v - best[i]) < 1e-9;
        const tint = isBest ? ` style="background:color-mix(in srgb, ${m.color} var(--best-mix), var(--surface))"` : "";
        const fmt = r.fmt || ((x) => f1(x));
        return `<td class="lbt-v${gs(r)}${isBest ? " best" : ""}${isFull(m) ? "" : " partial"}"${tint}${tipAttr(`${fullName(m)}\n${r.label}${r.f1 ? " F1" : ""}: ${fmt(v)}${r.fmt ? "" : "%"}${r.n != null ? `\nn = ${int(r.n)}` : ""}${isBest ? "\nBest in column" : ""}`)}>${fmt(v)}</td>`;
      }).join("");
      return `<tr><td class="ovt-rank">${rk ? "#" + rk : ""}</td><th class="ovt-name" scope="row"${tip}>${links ? `<button type="button" class="lbt-name ovt-link" data-key="${esc(m.key)}">${inner}</button>` : inner}</th>${cells}</tr>`;
    };
    const span = rows.length + 2;
    const body = bySection(cols).map(([label, ms, k]) => `<tr class="ovt-sec"><th colspan="${span}" scope="colgroup"${tipAttr(SECTION_TIP[k])}>${esc(label)}</th></tr>${ms.map(rowFor).join("")}`).join("");
    table.innerHTML = `<thead>${groupHead}<tr><th class="ovt-rank"></th><th class="ovt-name">${esc(countText)}</th>${rows.map(mHead).join("")}</tr></thead><tbody>${body}</tbody>`;
    if (links) table.querySelectorAll(".ovt-link").forEach((b) => b.onclick = () => openAnalysis(b.dataset.key));
  }

  function renderLeaderboard() {
    renderShownPicker("lb");
    seg("lbScaleSeg", state.lbScale, (v) => { state.lbScale = v; renderLeaderboard(); writeHash(); });
    // One scale for every leaderboard figure: table, scatter and agreement heatmap.
    state.scatterScale = state.lbScale;
    state.agree = (state.lbScale === "4" || state.lbScale === "5") ? "exact_pct" : "dir_pct";
    const two = state.lbScale === "2" || state.lbScale === "3";
    const { ranked, partial, cols } = lbColumns(lbShown(), two);
    $("lbTitle").textContent = `Classification leaderboard (${two ? "2-class" : "4-class"})`;
    $("lbSub").innerHTML = (two
      ? "Macro F1 against ClinGen expert classifications across P/LP and B/LB."
      : "Macro F1 against ClinGen expert classifications across 4 definitive ACMG classes.") +
      `<span class="ui-hint"> Select a model to open its confusion matrix. </span><button type="button" class="ov-link" id="lbCatLink">By disease category</button>`;
    $("lbCatLink").onclick = () => { setTab("categories"); window.scrollTo({ top: 0 }); };

    renderLbScatter(cols);
    renderAgreeHeat();

    if (!cols.length) { drawOverallTable($("lbTable"), cols, ranked, []); $("lbNote").innerHTML = ""; return; }

    const safetyRows = [
      { label: "Cross-direction errors per 1,000 variants", short: "Errors per 1,000", get: per1k, low: true, fmt: (v) => v.toFixed(2) }
    ];
    drawOverallTable($("lbTable"), cols, ranked, [
      ["By expert classification", lbClassRows(cols, two)],
      ["Safety", safetyRows]
    ], { links: true, countText: `${int(D.metadata.total_variants)} variants` });
    $("lbNote").innerHTML = [
      ...lbCoverageNotes(ranked, partial),
      "Cross-direction errors: B/LB called P/LP, or reverse."
    ].filter(Boolean).join("<br>");
  }

  // ---------- disease categories ----------
  // Categories down the side, models across (by section, ranked by overall F1 on the leaderboard scale).
  function renderCategories() {
    renderShownPicker("lb");
    seg("lbScaleSeg", state.lbScale, (v) => { state.lbScale = v; renderCategories(); writeHash(); });
    const two = state.lbScale === "2" || state.lbScale === "3";
    const { ranked, partial, cols } = lbColumns(lbShown(), two);
    $("catTitle").textContent = `F1 by disease category (${two ? "2-class" : "4-class"})`;
    $("catSub").innerHTML = `Macro F1 within each ClinGen expert panel category. Best in row is highlighted.<span class="ui-hint"> Select a model to open its confusion matrix.</span>`;
    const catKey = two ? "macro_f1_3" : "macro_f1";
    const cats = categoriesWithData().sort((a, b) => catCount(b) - catCount(a));
    const maxCat = Math.max(1, ...cats.map(catCount));
    const rows = cats.map((c) => ({
      label: c, short: c.replace(/ Disorders$/, ""), n: catCount(c), small: catCount(c) < LB_SMALL_N, f1: true,
      get: (m) => { const r = (D.categories_data[m.key] || {})[c]; return r && r.count ? r[catKey] : null; }
    }));
    drawLbTable($("catTable"), cols, ranked, rows);
    $("catNote").innerHTML = [
      ...lbCoverageNotes(ranked, partial),
      cats.some((c) => catCount(c) < LB_SMALL_N) ? `\u2020 Fewer than ${LB_SMALL_N} variants.` : ""
    ].filter(Boolean).join("<br>");
  }

  // ---------- overall ----------
  // A snapshot of each benchmark. Classification: every model, 2-class, Overall plus P/LP and B/LB.
  function renderOverall() {
    const { ranked, partial, cols } = lbColumns(D.models.slice(), true);
    $("ovSub").innerHTML = `Macro F1 against ClinGen expert classifications (P/LP vs B/LB).<span class="ui-hint"> <button type="button" class="ov-link" id="ovFull">Full leaderboard</button></span>`;
    $("ovFull").onclick = () => { state.lbCols = null; state.lbScale = "2"; setTab("leaderboard"); window.scrollTo({ top: 0 }); };
    drawOverallTable($("ovTable"), cols, ranked, cols.length ? lbClassRows(cols, true) : [], { countText: `${int(D.metadata.total_variants)} variants` });
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
  const RK_CAT_SHORT = {
    known_gene_disease: "Established",
    known_gene_known_disease: "Known gene, known disease",
    known_gene_new_disease: "Known gene, new disease",
    new_gene_known_disease: "Novel gene, known disease",
    new_gene_new_disease: "Novel gene & disease"
  };
  const RK_CAT_COUNTS = {
    known_gene_disease: 54,
    known_gene_known_disease: 74,
    known_gene_new_disease: 41,
    new_gene_known_disease: 74,
    new_gene_new_disease: 68
  };

  function rankingOverallRows() {
    const totalN = D.ranking_data?.metadata?.total_cohort || 311;
    return [
      { label: "Recall@1 (Top-1 accuracy)", short: "Recall@1", n: totalN, get: (m) => m.top1_pct, strong: true },
      { label: "Recall@5 (Top-5)", short: "Recall@5", n: totalN, get: (m) => m.top5_pct, indent: true },
      { label: "Recall@10 (Top-10)", short: "Recall@10", n: totalN, get: (m) => m.top10_pct, indent: true },
      { label: "Mean Reciprocal Rank (MRR)", short: "MRR", n: totalN, get: (m) => m.mrr, fmt: (v) => Number(v).toFixed(4), strong: true },
      { label: "NDCG@10", short: "NDCG@10", n: totalN, get: (m) => m.ndcg10, fmt: (v) => Number(v).toFixed(4) },
      { label: "Median Rank", short: "Median rank", n: totalN, get: (m) => m.median_rank, fmt: (v) => Number(v).toFixed(0), low: true }
    ];
  }

  function rankingCatRows(metricKey, suffix = "", fmt = null) {
    const counts = (D.ranking_data && D.ranking_data.metadata && D.ranking_data.metadata.category_counts) || RK_CAT_COUNTS;
    const maxN = Math.max(...Object.values(counts)) || 100;
    return Object.keys(RK_CAT_LABELS).map((cat) => ({
      label: RK_CAT_LABELS[cat],
      short: RK_CAT_SHORT[cat],
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
    $("ovRankSub").innerHTML = `Recall@K and MRR across 311 rare disease challenge cases.<span class="ui-hint"> <button type="button" class="ov-link" id="ovRankFull">Full ranking leaderboard</button></span>`;
    $("ovRankFull").onclick = () => { setTab("ranking"); window.scrollTo({ top: 0 }); };
    drawOverallTable($("ovRankTable"), rkCols, rkCols, rankingOverallRows(), { countText: `${int(D.ranking_data?.metadata?.total_cohort || 311)} cases` });
    $("ovRankNote").innerHTML = "Recall@K: share of cases where causal gene ranked &le; K. MRR denominator is 311 total cases.";
  }

  function renderRanking() {
    const rk = D.ranking_data;
    if (!rk || !rk.models || !rk.models.length || !$("rkTable")) return;
    renderShownPicker("rk");
    const shown = rkShown();
    const rkCols = shown.slice().sort((a, b) => b.mrr - a.mrr);
    $("rkTitle").textContent = "Variant ranking: 311 challenge cases";
    $("rkSub").innerHTML = "Recall@K and Mean Reciprocal Rank (MRR) across 311 hard & discriminative rare disease patient cases. Best in row is highlighted.";

    const sections = [
      ["Overall performance", rankingOverallRows()],
      ["Recall@1 by disease & gene familiarity category", rankingCatRows("top1_pct")]
    ];
    if (!rkCols.length) {
      $("rkTable").innerHTML = `<tbody><tr><td class="empty">No models selected. Choose models above.</td></tr></tbody>`;
    } else {
      drawOverallTable($("rkTable"), rkCols, rkCols, sections, { countText: `${int(rk.metadata?.total_cohort || 311)} cases` });
    }
    $("rkNote").innerHTML = "Recall@K: percentage of cases where causal gene ranked &le; K. MRR denominator is 311 total cases.";
  }

  // ---------- why not top 1 ----------
  // For each model, the type of gene ranked first in the cases where the causal gene was not first.
  const DX_COLORS = {
    pathogenic_pheno_irrel: "#B8322A", phenotype_distractor: "#E07B1A", unlabelled: "#8E9AA0",
    universal_distractor: "#7B3FA0", tissue_distractor: "#2C73B0", non_syndromic_phenotype: "#2BA55A",
    common_fp: "#D9442E", insufficient_explanatory: "#C23B5E"
  };
  const dxModels = () => (D.ranking_data?.models || []).filter((m) => D.ranking_data.distractor_data?.[m.key]);

  // Gene types down the side, models across (by section, ranked by MRR). Dot area is the
  // share of a model's non first cases where that type of gene took first place, on one scale.
  function renderDistractors() {
    const rk = D.ranking_data;
    if (!dxModels().length || !$("dxGrid")) return;
    renderShownPicker("dx");
    const types = rk.metadata.distractor_types || [];
    const groups = bySection(shownModels("dx").sort((a, b) => b.mrr - a.mrr));
    const order = groups.flatMap(([, ms]) => ms);
    const gs = (i) => (i === 0 ? "" : " lbt-gs");
    $("dxSub").innerHTML = `The type of gene ranked first instead of the causal gene. Dot area is the share of each model's misses.<span class="ui-hint"> Hover a dot for the case count.</span>`;
    const data = (m) => rk.distractor_data[m.key];
    const share = (m, t) => (data(m).non_top1 ? ((data(m).counts[t] || 0) * 100) / data(m).non_top1 : 0);
    const max = Math.max(1, ...order.flatMap((m) => types.map(([t]) => share(m, t))));
    const dot = (v, color) => {
      const d = n2(2 * Math.max(v > 0 ? 1.5 : 0, CG_R * Math.sqrt(v / max)));
      return `<span class="cg-dot-box">${d ? `<span class="cg-dot" style="width:${d}px;height:${d}px;background:${color}"></span>` : `<span class="cg-dot cg-zero"></span>`}</span>`;
    };
    const keys = [5, 10, 25, 50].filter((k) => k <= max).slice(-3);
    $("dxKey").innerHTML = keys.map((k) => `<span>${dot(k, "var(--text-2)")}${k}%</span>`).join("");
    const table = $("dxGrid");
    if (!order.length) { table.innerHTML = `<tbody><tr><td class="empty">No models selected.</td></tr></tbody>`; return; }
    // The first model of each section starts a group; the leading section needs no divider
    const starts = new Set(groups.map(([, ms]) => ms[0].key));
    const cls = (m, i) => (i > 0 && starts.has(m.key) ? " lbt-gs" : "");
    const head = `<thead><tr class="lbt-groups"><th class="lbt-first"></th>${groups.map(([g, ms, k], i) => `<th colspan="${ms.length}" class="lbt-group${gs(i)}"${tipAttr(SECTION_TIP[k])}>${esc(g)}</th>`).join("")}</tr>` +
      `<tr><th class="lbt-first">Top ranked gene</th>${order.map((m, i) => `<th class="lbt-model${cls(m, i)}"${tipAttr(fullName(m))}><span class="lbt-label">${swatch(m)}${esc(nameIn(m, order))}</span><br><span class="lbt-meta">N = ${int(data(m).non_top1)}</span></th>`).join("")}</tr></thead>`;
    const draw = (compact) => {
      const body = types.map(([t, label]) => `<tr><td class="lbt-first cg-label"><span class="cg-code">${esc(label)}</span></td>${order.map((m, i) => {
        const n = data(m).counts[t] || 0, v = share(m, t);
        const txt = v === 0 ? "" : v < 0.1 ? "<0.1" : v.toFixed(1);
        return `<td class="cg-c${cls(m, i)}"${tipAttr(`${label} · ${fullName(m)}\n${int(n)} of ${int(data(m).non_top1)} cases, ${v.toFixed(1)}%`)}><span class="cg-cell">${dot(v, DX_COLORS[t] || "#8E9AA0")}${compact ? "" : `<span class="cg-v">${txt}</span>`}</span></td>`;
      }).join("")}</tr>`).join("");
      table.innerHTML = head + `<tbody>${body}</tbody>`;
      table.classList.toggle("compact", compact);
    };
    draw(false);
    const wrap = table.parentElement;
    if (wrap.scrollWidth > wrap.clientWidth + 1) draw(true);
    const skipped = (rk.models || []).length - dxModels().length;
    $("dxNote").textContent = "N is the number of cases where the causal gene was not ranked first. Not labelled means the top gene was not among the case's candidate genes." + (skipped > 0 ? ` ${skipped} models without a ranked gene list are not shown.` : "");
  }

  // Accuracy against safety: score across, cross-direction errors up (inverted).
  const SCATTER_X = {
    "4": { key: "macro_f1_5", label: "Macro F1, 4-class" },
    "5": { key: "macro_f1_5", label: "Macro F1, 4-class" },
    "2": { key: "macro_f1_3", label: "Macro F1, 2-class" },
    "3": { key: "macro_f1_3", label: "Macro F1, 2-class" }
  };

  function renderLbScatter(models) {
    const ax = SCATTER_X[state.scatterScale];
    $("scTitle").textContent = `Accuracy vs Safety: ${ax.label}`;
    const scored = models.every(isFull)
      ? ` Every model was scored on all ${int(D.metadata.total_variants)} variants.`
      : " Hollow dots were scored on part of the cohort.";
    $("scSub").innerHTML = `Overall F1 against cross-direction errors per 1,000 variants (up and right is better).${scored}<span class="ui-hint"> Hover a dot for values; select to open model.</span>`;
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
      (models.some((m) => !isFull(m)) ? `<p class="note">* Partial run; not ranked.</p>` : "");
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
