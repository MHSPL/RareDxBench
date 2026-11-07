  // ---------- categories ----------
  function categoriesWithData() {
    return (D.metadata.categories || []).filter((c) => D.models.some((m) => (D.categories_data[m.key] || {})[c]?.count));
  }
  const catCount = (c) => Math.max(0, ...D.models.map((m) => ((D.categories_data[m.key] || {})[c] || {}).count || 0));

  // ---------- confusion ----------
  function confusionTable(m) {
    const cm = D.confusion_matrices[m.key];
    if (!cm) return `<p class="empty">No confusion matrix for this model.</p>`;
    const five = state.cmScale === "5";
    const mat = five ? cm.matrix_5 : collapse3(cm.matrix_5);
    const labels = five ? LABELS.map((l) => ABBR[l]) : ["P/LP", "VUS", "B/LB"];
    const full = five ? LABELS : ["Pathogenic or likely pathogenic", "Uncertain significance", "Benign or likely benign"];
    const dirOf = (i) => (five ? ["p", "p", "v", "b", "b"] : ["p", "v", "b"])[i];
    const cell = window.matchMedia("(max-width: 640px)").matches ? (five ? 44 : 64) : (five ? 52 : 72);
    const ylabH = mat.length * cell;
    const ylab = `<td class="cm-ylab" rowspan="${mat.length}"><svg width="16" height="${ylabH}" aria-hidden="true"><text transform="translate(12 ${ylabH / 2}) rotate(-90)" text-anchor="middle">Expert classification</text></svg></td>`;
    // Model calls run along the bottom, like an x axis: labels, then the axis title.
    const foot = `<tfoot><tr><th colspan="2"></th>${labels.map((l, j) => `<th class="colh" scope="col"${tipAttr(full[j])}><span class="cm-lab">${esc(l)}</span></th>`).join("")}</tr>` +
      `<tr><th colspan="2"></th><th class="cm-axt" colspan="${labels.length}">Model call</th></tr></tfoot>`;
    const body = mat.map((row, i) => {
      const sum = row.reduce((a, b) => a + b, 0);
      return `<tr>${i === 0 ? ylab : ""}<td class="rowh"${tipAttr(full[i])}><span class="cm-lab">${esc(labels[i])}</span></td>${row.map((v, j) => {
        const share = sum ? v / sum : 0;
        const x = (dirOf(i) === "p" && dirOf(j) === "b") || (dirOf(i) === "b" && dirOf(j) === "p");
        const shown = state.cmMode === "count" ? int(v) : (share * 100).toFixed(1);
        return `<td class="h${i === j ? " diag" : ""}${x && v > 0 ? " xdir" : ""}" style="${x && v > 0 ? "" : heatStyle(share)}"${tipAttr(`${fullName(m)}\nExpert: ${full[i]}\nModel: ${full[j]}\n${int(v)} variants, ${(share * 100).toFixed(1)}% of row${x && v > 0 ? "\nCross-direction error" : ""}`)}>${shown}</td>`;
      }).join("")}</tr>`;
    }).join("");
    return `<div class="table-wrap"><table class="heat cm${five ? "" : " cm3"}" style="--cm-cell:${cell}px" aria-label="${esc(fullName(m))}: rows are the expert classification, columns the model call"><tbody>${body}</tbody>${foot}</table></div>`;
  }

  function renderConfusion() {
    seg("cmScaleSeg", state.cmScale, (v) => { state.cmScale = v; renderConfusion(); });
    seg("cmModeSeg", state.cmMode, (v) => { state.cmMode = v; renderConfusion(); });
    $("cmSub").textContent = `${state.cmMode === "count" ? "Cells are variant counts, shaded by share of the row." : "Cells are the percentage of the row."} Red cells are cross-direction errors (B/LB called P/LP, or the reverse).`;
    renderShownPicker("cm");
    // Leaderboard order: by section, ranked by overall F1 on the leaderboard's scale
    const ms = bySection(lbColumns(shownModels("cm"), state.lbScale === "3").cols).flatMap(([, g]) => g);
    $("cmGrid").innerHTML = ms.map((m) => `<div><p class="cm-title">${mname(m, ms)}</p>${confusionTable(m)}</div>`).join("");
  }

  // ---------- criteria ----------
  // Criteria down the side (grouped by evidence strength), models across in the
  // leaderboard's column order (by section, ranked by overall F1 on the leaderboard's
  // scale). Dot area is the share of evaluated variants citing the criterion, on one
  // scale for both panels; rust for pathogenic evidence, green for benign.
  const CG_R = 13;
  function renderCriteria() {
    renderShownPicker("crit");
    const groups = bySection(lbColumns(shownModels("crit"), state.lbScale === "3").cols);
    const order = groups.flatMap(([, ms]) => ms);
    const groupStart = new Set(groups.slice(1).map(([, ms]) => ms[0].key));
    const gs = (m) => (groupStart.has(m.key) ? " lbt-gs" : "");
    const share = (m, c) => (D.criteria_data[m.key] ? ((D.criteria_data[m.key][c] || 0) / (m.evaluated_count || 1)) * 100 : null);
    const all = [...CRITERIA.pathogenic, ...CRITERIA.benign];
    const max = Math.max(1, ...all.flatMap(([c]) => order.map((m) => share(m, c) || 0)));
    const radius = (v) => (v > 0 ? Math.max(1.5, CG_R * Math.sqrt(v / max)) : 0);
    const dot = (v, color) => {
      const d = n2(2 * radius(v));
      return `<span class="cg-dot-box">${d ? `<span class="cg-dot" style="width:${d}px;height:${d}px;background:${color}"></span>` : `<span class="cg-dot cg-zero"></span>`}</span>`;
    };

    const keys = [5, 10, 25, 50, 75].filter((k) => k <= max).slice(-3);
    $("critKey").innerHTML = keys.map((k) => `<span>${dot(k, "var(--text-2)")}${k}%</span>`).join("");

    const table = $("critGrid");
    if (!order.length) { table.innerHTML = `<tbody><tr><td class="empty">No models selected.</td></tr></tbody>`; return; }
    const span = order.length + 1;
    const head = `<thead><tr class="lbt-groups"><th class="lbt-first"></th>${groups.map(([g, ms, k], i) => `<th colspan="${ms.length}" class="lbt-group${i ? " lbt-gs" : ""}"${tipAttr(SECTION_TIP[k])}>${esc(g)}</th>`).join("")}</tr>` +
      `<tr><th class="lbt-first">Criterion</th>${order.map((m) => `<th class="lbt-model${gs(m)}"${tipAttr(fullName(m))}><span class="lbt-label">${swatch(m)}${esc(m.short_name)}</span><br><span class="lbt-meta">${esc(providerOf(m))}</span></th>`).join("")}</tr></thead>`;

    const draw = (compact) => {
      const body = [["Pathogenic evidence", CRITERIA.pathogenic, "var(--tier-p)"], ["Benign evidence", CRITERIA.benign, "var(--tier-b)"]].map(([title, list, color]) => {
        let rows = `<tr class="lbt-sec"><td class="lbt-first" colspan="${span}">${title}</td></tr>`;
        let strength = null;
        list.forEach(([c, def, short]) => {
          if (strengthOf(c) !== strength) {
            strength = strengthOf(c);
            rows += `<tr class="cg-str"><td class="lbt-first" colspan="${span}">${strength}</td></tr>`;
          }
          rows += `<tr><td class="lbt-first cg-label"${tipAttr(`${c}: ${def}`)}><span class="cg-code mono">${c}</span> <span class="cg-def">${esc(short)}</span></td>${order.map((m) => {
            const v = share(m, c);
            if (v == null) return `<td class="cg-c na${gs(m)}"${tipAttr(`${fullName(m)}: no criteria recorded`)}>—</td>`;
            const n = D.criteria_data[m.key][c] || 0;
            const txt = v === 0 ? "" : v < 0.1 ? "<0.1" : v.toFixed(1);
            return `<td class="cg-c${gs(m)}"${tipAttr(`${c} · ${fullName(m)}\n${int(n)} variants, ${v.toFixed(1)}% of evaluated\n${def}`)}><span class="cg-cell">${dot(v, color)}${compact ? "" : `<span class="cg-v">${txt}</span>`}</span></td>`;
          }).join("")}</tr>`;
        });
        return rows;
      }).join("");
      table.innerHTML = head + `<tbody>${body}</tbody>`;
      table.classList.toggle("compact", compact);
    };
    // Values print beside the dots when the grid fits; otherwise they show on hover.
    draw(false);
    const wrap = table.parentElement;
    if (wrap.scrollWidth > wrap.clientWidth + 1) draw(true);
  }

  // ---------- agreement (leaderboard) ----------
  // Pairwise agreement among the models shown on the leaderboard, grouped by
  // section, then provider, then rank, so models of one family sit together.
  const AGREE_PROVIDERS = ["Google", "OpenAI", "Anthropic"];
  function renderAgreeHeat() {
    const overallKey = state.lbScale === "3" ? "macro_f1_3" : "macro_f1_5";
    const shown = lbShown();
    const pool = shown.filter((m) => D.concordance[m.key]).sort((a, b) => (isFull(b) - isFull(a)) || b[overallKey] - a[overallKey]);
    const missing = shown.filter((m) => !D.concordance[m.key]);
    // By section, then provider (Gemini, GPT, Claude, then others), then rank.
    const provOrder = (m) => { const i = AGREE_PROVIDERS.indexOf(m.family); return i < 0 ? AGREE_PROVIDERS.length : i; };
    const models = bySection(pool).flatMap(([, ms]) => [...ms].sort((a, b) => provOrder(a) - provOrder(b)));
    const exact = state.agree === "exact_pct";
    $("agreeTitle").textContent = `Agreement between classifiers, ${exact ? "same 5-class call" : "same direction (P/LP, VUS, B/LB)"}`;
    $("agreeSub").textContent = "Share of variants on which two classifiers make the same call, regardless of the expert classification. Models are grouped by section, then provider.";
    $("agreeNote").textContent = missing.length ? `Not shown: ${missing.map((m) => m.short_name).join(", ")}, carried over from an earlier build without variant-level results.` : "";
    const table = $("agreeHeat");
    if (models.length < 2) {
      table.innerHTML = `<tbody><tr><td class="empty">Show at least two models to compare their calls.</td></tr></tbody>`;
      $("agreeMin").textContent = $("agreeMax").textContent = "";
      return;
    }
    const vals = [];
    models.forEach((a) => models.forEach((b) => { if (a !== b) { const v = D.concordance[a.key]?.[b.key]; if (v && v.overlap) vals.push(v[state.agree]); } }));
    const lo = vals.length ? Math.floor(Math.min(...vals)) : 0, hi = vals.length ? Math.ceil(Math.max(...vals)) : 100;
    $("agreeMin").textContent = lo + "%";
    $("agreeMax").textContent = hi + "%";
    const draw = (compact) => {
      const head = `<thead><tr><th></th>${models.map((m) => `<th class="colh"${tipAttr(fullName(m))}>${swatch(m)}<br>${esc(nameIn(m, models))}</th>`).join("")}</tr></thead>`;
      const body = models.map((a) => `<tr><td class="rowh">${mname(a, models)}</td>${models.map((b) => {
        const r = D.concordance[a.key]?.[b.key];
        if (a === b || !r || !r.overlap) return `<td class="h" style="color:var(--muted)">—</td>`;
        const v = r[state.agree];
        return `<td class="h" style="${heatStyle((v - lo) / Math.max(1, hi - lo))}"${tipAttr(`${fullName(a)} vs ${fullName(b)}\n${pct(v)} agreement\n${int(r.overlap)} shared variants`)}>${compact ? "" : f1(v)}</td>`;
      }).join("")}</tr>`).join("");
      table.innerHTML = head + `<tbody>${body}</tbody>`;
      table.classList.toggle("compact", compact);
    };
    // Values print in the cells when the table fits the card; otherwise they show on hover.
    draw(false);
    const wrap = table.parentElement;
    if (wrap.scrollWidth > wrap.clientWidth + 1) draw(true);
  }

  // ---------- variants CSV ----------
  // Every benchmark variant with the expert classification and each model's call.
  const CSV_COLUMN = { id: "variant_id", variation: "variant", ground_truth: "expert_classification" };
  const csvCell = (v) => { const t = v == null ? "" : String(v); return /[",\r\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t; };

  function downloadVariantsCsv() {
    const t = D.variant_table;
    if (!t || !t.rows || !t.rows.length) { toast("This build has no variant table; rebuild with python scripts/update_dashboard.py."); return; }
    const ms = D.models.filter((m) => t.calls[m.key]);
    const head = [...t.columns.map((c) => CSV_COLUMN[c] || c), ...ms.map(fullName)];
    const lines = [head.map(csvCell).join(",")];
    t.rows.forEach((r, i) => {
      const calls = ms.map((m) => t.labels[t.calls[m.key][i]] || "");
      lines.push([...r, ...calls].map(csvCell).join(","));
    });
    download(new Blob([lines.join("\r\n") + "\r\n"], { type: "text/csv" }), `raredxbench-variants-${t.rows.length}.csv`);
    toast(`Downloaded ${int(t.rows.length)} variants with calls from ${ms.length} models.`);
  }

  // ---------- tabs & URL ----------
  const RENDER = {
    leaderboard: renderLeaderboard,
    confusion: renderConfusion,
    criteria: renderCriteria,
    overall: renderOverall,
    ranking: () => {}
  };
  // Overall and Ranking are single-view sections; every other tab belongs to Classification.
  const sectionOf = (tab) => (tab === "ranking" || tab === "overall" ? tab : "classification");

  function readHash() {
    const [tab, q] = location.hash.slice(1).split("?");
    const p = new URLSearchParams(q || "");
    return { tab: tab || "overall", s: p.get("s"), show: p.get("show") };
  }

  function hashQuery() {
    const q = [];
    // The confusion and criteria tabs order their models by the leaderboard scale, so their links keep it too
    if (PICKER_OF[state.tab] && state.lbScale !== "3") q.push("s=" + state.lbScale);
    const p = PICKER_OF[state.tab];
    if (p && state[SHOWN[p].col]) q.push("show=" + state[SHOWN[p].col].map(encodeURIComponent).join(","));
    return q.length ? "?" + q.join("&") : "";
  }

  function writeHash() {
    const h = `#${state.tab}${hashQuery()}`;
    if (location.hash !== h) history.replaceState(null, "", h);
  }

  function applyHash() {
    const h = readHash();
    if (h.s === "3" || h.s === "5") state.lbScale = h.s;
    const p = PICKER_OF[h.tab];
    if (p) {
      const keys = (h.show || "").split(",").filter((k) => byKey(k));
      state[SHOWN[p].col] = keys.length && keys.length < D.models.length ? keys : null;
    }
    state.tab = RENDER[h.tab] ? h.tab : "overall";
  }

  function setTab(tab) {
    if (!RENDER[tab]) tab = "overall";
    state.tab = tab;
    const section = sectionOf(tab);
    if (section === "classification") state.clsTab = tab;
    document.body.dataset.tab = tab;
    document.body.dataset.section = section;
    document.querySelectorAll(".section-tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.section === section)));
    document.querySelectorAll(".tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
    document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== "view-" + tab));
    RENDER[tab]();
    writeHash();
  }

  function renderAll() {
    renderChrome();
    pruneShown();
    setTab(state.tab);
  }

