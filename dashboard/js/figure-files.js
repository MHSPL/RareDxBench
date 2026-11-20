  function packBits(src) {
    const out = new Uint8Array(src.length * 2 + 2);
    let i = 0, o = 0;
    const n = src.length;
    while (i < n) {
      let j = i + 1;
      while (j < n && src[j] === src[i] && j - i < 128) j++;
      if (j - i >= 2) {
        out[o++] = 257 - (j - i);
        out[o++] = src[i];
        i = j;
      } else {
        let k = i;
        while (k < n && k - i < 128 && !(k + 1 < n && src[k] === src[k + 1])) k++;
        if (k === i) k = i + 1;
        out[o++] = k - i - 1;
        for (let m = i; m < k; m++) out[o++] = src[m];
        i = k;
      }
    }
    return out.subarray(0, o);
  }

  async function deflate(bytes) {
    const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream("deflate"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  // Baseline RGB TIFF with DPI tags. Strips are Deflate-compressed with a
  // horizontal predictor; browsers without CompressionStream fall back to PackBits.
  async function encodeTiff(rgbaData, w, h, dpi) {
    const useDeflate = typeof CompressionStream === "function";
    const rps = 32;
    const nStrips = Math.ceil(h / rps);
    const strips = [];
    let dataLen = 0;
    for (let s0 = 0; s0 < h; s0 += rps) {
      const rows = Math.min(rps, h - s0);
      const raw = new Uint8Array(rows * w * 3);
      const parts = [];
      for (let r = 0; r < rows; r++) {
        const base = r * w * 3;
        for (let x = 0, src = (s0 + r) * w * 4, d = base; x < w; x++, src += 4) {
          raw[d++] = rgbaData[src]; raw[d++] = rgbaData[src + 1]; raw[d++] = rgbaData[src + 2];
        }
        if (useDeflate) {
          for (let i = base + w * 3 - 1; i >= base + 3; i--) raw[i] = (raw[i] - raw[i - 3]) & 0xff;
        } else {
          parts.push(packBits(raw.subarray(base, base + w * 3)));
        }
      }
      let strip;
      if (useDeflate) strip = await deflate(raw);
      else {
        strip = new Uint8Array(parts.reduce((a, q) => a + q.length, 0));
        let o = 0;
        parts.forEach((q) => { strip.set(q, o); o += q.length; });
      }
      strips.push(strip);
      dataLen += strip.length;
    }

    const SHORT = 3, LONG = 4, RATIONAL = 5;
    const tags = [
      [256, LONG, 1, w], [257, LONG, 1, h], [258, SHORT, 3, "bps"],
      [259, SHORT, 1, useDeflate ? 8 : 32773], [262, SHORT, 1, 2],
      [273, LONG, nStrips, "offsets"], [277, SHORT, 1, 3], [278, LONG, 1, rps],
      [279, LONG, nStrips, "counts"], [282, RATIONAL, 1, "xres"], [283, RATIONAL, 1, "yres"],
      [284, SHORT, 1, 1], [296, SHORT, 1, 2]
    ];
    if (useDeflate) tags.push([317, SHORT, 1, 2]);
    const ifdStart = 8;
    let off = ifdStart + 2 + tags.length * 12 + 4;
    const at = {};
    at.bps = off; off += 6;
    at.xres = off; off += 8;
    at.yres = off; off += 8;
    if (nStrips > 1) { at.offsets = off; off += 4 * nStrips; at.counts = off; off += 4 * nStrips; }
    const dataOff = off;
    const buf = new ArrayBuffer(dataOff + dataLen);
    const dv = new DataView(buf), u8 = new Uint8Array(buf);
    dv.setUint16(0, 0x4949); dv.setUint16(2, 42, true); dv.setUint32(4, ifdStart, true);
    dv.setUint16(ifdStart, tags.length, true);
    let t = ifdStart + 2;
    tags.forEach(([id, type, count, value]) => {
      if (value === "offsets" && nStrips === 1) value = dataOff;
      else if (value === "counts" && nStrips === 1) value = strips[0].length;
      else if (typeof value === "string") value = at[value];
      dv.setUint16(t, id, true); dv.setUint16(t + 2, type, true); dv.setUint32(t + 4, count, true);
      if (type === SHORT && count === 1) dv.setUint16(t + 8, value, true); else dv.setUint32(t + 8, value, true);
      t += 12;
    });
    dv.setUint32(t, 0, true);
    [8, 8, 8].forEach((v, i) => dv.setUint16(at.bps + i * 2, v, true));
    dv.setUint32(at.xres, dpi, true); dv.setUint32(at.xres + 4, 1, true);
    dv.setUint32(at.yres, dpi, true); dv.setUint32(at.yres + 4, 1, true);
    let pos = dataOff;
    strips.forEach((st, i) => {
      if (nStrips > 1) { dv.setUint32(at.offsets + i * 4, pos, true); dv.setUint32(at.counts + i * 4, st.length, true); }
      u8.set(st, pos);
      pos += st.length;
    });
    return new Blob([buf], { type: "image/tiff" });
  }

  async function svgToTiff(svg, w, h, dpi) {
    const MAX_PIXELS = 120e6;
    let scale = dpi / 96;
    if (w * h * scale * scale > MAX_PIXELS) {
      scale = Math.sqrt(MAX_PIXELS / (w * h));
      dpi = Math.floor(scale * 96);
    }
    const img = new Image();
    const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
    try {
      img.src = url;
      await img.decode();
      const cw = Math.round(w * scale), ch = Math.round(h * scale);
      const canvas = document.createElement("canvas");
      canvas.width = cw; canvas.height = ch;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, cw, ch);
      ctx.drawImage(img, 0, 0, cw, ch);
      return { blob: await encodeTiff(ctx.getImageData(0, 0, cw, ch).data, cw, ch, dpi), dpi };
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  function download(blob, name) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

  function figureName(card) {
    const parts = ["raredxbench", card.querySelector(".card-head h3").textContent];
    const p = card.querySelector("#cmGrid") ? "cm" : card.querySelector("#critGrid") ? "crit" : null;
    if (p && state[SHOWN[p].col]) parts.push(shownModels(p).map((m) => m.short_name).join(" vs "));
    if (card.querySelector("#cmGrid")) parts.push(...(state.cmView === "matrix" ? [state.cmScale + "-class", state.cmMode === "count" ? "counts" : "row-pct"] : ["overview"]));
    if (card.querySelector("#agreeHeat")) parts.push(state.agree === "exact_pct" ? "exact" : "directional");
    return slug(parts.join(" ")).slice(0, 140).replace(/-$/, "");
  }

  async function exportFigure(card, fmt, dpi) {
    const name = figureName(card);
    try {
      const { svg, w, h } = figureSvg(card);
      if (fmt === "svg") {
        download(new Blob([svg], { type: "image/svg+xml" }), name + ".svg");
        return;
      }
      toast("Rendering TIFF…");
      const res = await svgToTiff(svg, w, h, dpi);
      download(res.blob, `${name}-${res.dpi}dpi.tiff`);
      toast(res.dpi < dpi ? `Saved at ${res.dpi} dpi; ${dpi} dpi would exceed the browser's canvas limit.` : `Saved ${name}-${res.dpi}dpi.tiff`);
    } catch (e) {
      toast("Export failed: " + e.message);
    }
  }

  function setupExport() {
    document.querySelectorAll(".card").forEach((card) => {
      const head = card.querySelector(".card-head");
      if (!head || !head.querySelector("h3")) return;
      const menu = document.createElement("details");
      menu.className = "fig-export";
      menu.innerHTML = `<summary class="btn">Export</summary><div class="menu" role="menu">
        <button type="button" role="menuitem" data-fmt="svg">SVG (vector)</button>
        <button type="button" role="menuitem" data-fmt="tiff" data-dpi="300">TIFF, 300 dpi</button>
        <button type="button" role="menuitem" data-fmt="tiff" data-dpi="600">TIFF, 600 dpi</button>
        <div class="menu-note">White background</div></div>`;
      menu.querySelectorAll("button").forEach((b) => b.onclick = () => {
        menu.open = false;
        exportFigure(card, b.dataset.fmt, +b.dataset.dpi || 300);
      });
      head.appendChild(menu);
    });
    document.addEventListener("click", (e) => {
      document.querySelectorAll(".fig-export[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
    });
  }

