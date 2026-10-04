/* Stock Focus Dashboard — static front end. Reads data/stocks.json produced by pipeline/fetch_data.py. */
(() => {
  "use strict";

  const FACTORS = [
    { key: "momentum", label: "Momentum", desc: "1M/3M/6M returns, RSI, price vs 50/200-day MA" },
    { key: "volume", label: "Volume", desc: "Latest volume vs 20-day average" },
    { key: "value", label: "Value", desc: "P/E, P/B, dividend yield vs market peers" },
    { key: "attention", label: "Attention", desc: "News count (7d); Reddit + StockTwits for US" },
    { key: "reporting", label: "Reporting", desc: "Days to earnings, last EPS surprise" },
  ];
  const PRESETS = {
    balanced: { momentum: 20, volume: 20, value: 20, attention: 20, reporting: 20 },
    momentum: { momentum: 45, volume: 25, value: 5, attention: 15, reporting: 10 },
    value: { momentum: 10, volume: 5, value: 60, attention: 10, reporting: 15 },
    event: { momentum: 10, volume: 25, value: 5, attention: 25, reporting: 35 },
  };
  const MARKET_LABEL = { US: "United States", SG: "Singapore", MY: "Malaysia" };
  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } },
  };

  const state = {
    data: null,
    market: LS.get("sfd-market", "ALL"),
    weights: { ...PRESETS.balanced, ...LS.get("sfd-weights", {}) },
    heat: LS.get("sfd-heat", "change_1d"),
    sort: { key: "focus", dir: -1 },
    filters: { q: "", sector: "", min: 0, earn: null },
    detail: null,
    range: "3M",
  };
  const charts = {};
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const isNum = (v) => typeof v === "number" && isFinite(v);

  // ------------------------------------------------------------------ formatting
  const fmtNum = (v, d = 2) => (isNum(v) ? v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }) : "—");
  const fmtPct = (v, d = 1) => (isNum(v) ? `${v > 0 ? "+" : ""}${v.toFixed(d)}%` : "—");
  const fmtPrice = (v) => (isNum(v) ? fmtNum(v, v < 1 ? 3 : 2) : "—");
  const fmtBig = (v) => {
    if (!isNum(v)) return "—";
    const a = Math.abs(v);
    if (a >= 1e12) return (v / 1e12).toFixed(2) + "T";
    if (a >= 1e9) return (v / 1e9).toFixed(1) + "B";
    if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
    if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
    return v.toFixed(0);
  };
  const signCls = (v) => (isNum(v) ? (v > 0 ? "pos" : v < 0 ? "neg" : "") : "");
  const fmtDate = (iso) => (iso ? new Date(iso + (iso.length === 10 ? "T00:00:00" : "")).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—");

  // ------------------------------------------------------------------ colour
  const hex2rgb = (h) => { h = h.replace("#", ""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); };
  const rgb2hex = (r) => "#" + r.map((x) => Math.round(x).toString(16).padStart(2, "0")).join("");
  const mix = (a, b, t) => { const A = hex2rgb(a), B = hex2rgb(b); return rgb2hex(A.map((x, i) => x + (B[i] - x) * t)); };
  const lum = (h) => { const [r, g, b] = hex2rgb(h).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const inkOn = (bg) => (lum(bg) > 0.33 ? "#0b0b0b" : "#ffffff");
  const diverging = (v, lim, center = 0) => {
    if (!isNum(v)) return css("--surface-2");
    const t = Math.max(-1, Math.min(1, (v - center) / lim));
    return t >= 0 ? mix(css("--mid"), css("--up"), t) : mix(css("--mid"), css("--down"), -t);
  };
  const sequential = (v, lo = 0, hi = 100) => {
    if (!isNum(v)) return css("--surface-2");
    const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
    return mix(css("--seq-lo"), css("--seq-hi"), t);
  };
  const scoreBg = (v) => (isNum(v) ? mix(css("--surface"), css("--accent"), Math.max(0, Math.min(1, v / 100)) * 0.55) : "transparent");

  // ------------------------------------------------------------------ scores
  function focusScore(s) {
    if (!s.factors) return null;
    let num = 0, den = 0;
    for (const f of FACTORS) {
      const w = state.weights[f.key] || 0, v = s.factors[f.key];
      if (w > 0 && isNum(v)) { num += w * v; den += w; }
    }
    return den ? num / den : null;
  }

  function visible() {
    const { q, sector, min, earn } = state.filters;
    const ql = q.trim().toLowerCase();
    return state.data.stocks.filter((s) => {
      if (state.market !== "ALL" && s.market !== state.market) return false;
      if (sector && s.sector !== sector) return false;
      if (min > 0 && !((s._focus ?? -1) >= min)) return false;
      if (earn != null && !(isNum(s.days_to_earnings) && s.days_to_earnings >= 0 && s.days_to_earnings <= earn)) return false;
      if (ql && !(`${s.name} ${s.long_name || ""} ${s.symbol} ${s.code} ${s.bursa_name || ""}`.toLowerCase().includes(ql))) return false;
      return true;
    });
  }

  // ------------------------------------------------------------------ links (patterns verified Oct 2026)
  function links(s) {
    const L = [];
    const y = `https://finance.yahoo.com/quote/${encodeURIComponent(s.symbol)}/`;
    if (s.market === "US") {
      const dot = s.symbol.toLowerCase().replace("-", ".");
      const exch = ["NMS", "NGM", "NCM", "NAS"].includes(s.exchange) ? "xnas" : "xnys";
      L.push(["Yahoo Finance", y],
        ["StockAnalysis", `https://stockanalysis.com/stocks/${dot}/`],
        ["Finviz", `https://finviz.com/quote.ashx?t=${encodeURIComponent(s.symbol)}`],
        ["Morningstar", `https://www.morningstar.com/stocks/${exch}/${dot}/quote`],
        ["Bloomberg", `https://www.bloomberg.com/quote/${s.symbol.replace("-", "/")}:US`]);
    } else if (s.market === "SG") {
      const sgxName = s.sgx_name || (s.long_name || s.name).toUpperCase();
      L.push(["Yahoo Finance", y],
        ["StockAnalysis", `https://stockanalysis.com/quote/sgx/${s.code}/`],
        ["Morningstar", `https://www.morningstar.com/stocks/xses/${s.code.toLowerCase()}/quote`],
        ["Bloomberg", `https://www.bloomberg.com/quote/${s.code}:SP`],
        ["SGX announcements", `https://www.sgx.com/securities/company-announcements?value=${encodeURIComponent(sgxName)}&type=company`],
        ["The Business Times", `https://www.businesstimes.com.sg/search?query=${encodeURIComponent(s.name)}`]);
    } else if (s.market === "MY") {
      L.push(["Yahoo Finance", y]);
      if (s.bursa_name) L.push(["StockAnalysis", `https://stockanalysis.com/quote/klse/${encodeURIComponent(s.bursa_name)}/`]);
      L.push(["Morningstar", `https://www.morningstar.com/stocks/xkls/${s.code}/quote`],
        ["Bloomberg", `https://www.bloomberg.com/quote/${s.code}:MK`],
        ["Bursa announcements", `https://www.bursamalaysia.com/market_information/announcements/company_announcement?company=${s.code}`],
        ["The Edge Malaysia", `https://theedgemalaysia.com/askedge/klse/${s.code}`]);
    }
    return L;
  }

  // ------------------------------------------------------------------ header / alerts / sources
  function renderAsOf() {
    const d = state.data;
    const gen = new Date(d.generated_at);
    const ageH = (Date.now() - gen.getTime()) / 36e5;
    const when = gen.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short" });
    const age = ageH < 1 ? "just now" : ageH < 48 ? `${Math.round(ageH)}h ago` : `${Math.floor(ageH / 24)} days ago`;
    const badge = ageH > 48
      ? `<span class="badge warn" title="Data is more than 2 days old">⚠ Stale data</span>`
      : `<span class="badge ok">Up to date</span>`;
    $("#asof").innerHTML = `<span>Data as of <b>${esc(when)}</b> (${age})</span>${badge}`;

    const stale = d.stocks.filter((s) => s.stale);
    const alerts = [];
    if (stale.length) alerts.push(`⚠ ${stale.length} stock(s) failed to refresh and show their last good values: ${stale.map((s) => esc(s.symbol)).join(", ")}.`);
    $("#alerts").innerHTML = alerts.map((a) => `<div class="alert">${a}</div>`).join("");
  }

  function renderSources() {
    const d = state.data;
    const src = Object.entries(d.sources).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
    const fac = Object.entries(d.factor_definitions).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
    const fx = Object.entries(d.fx_to_usd || {}).filter(([k]) => k !== "USD").map(([k, v]) => `1 ${k} = ${fmtNum(v, 4)} USD`).join(" · ");
    $("#sources").innerHTML = `
      <p>Prices are shown in each stock's <b>trading currency</b> (USD, SGD or MYR — note Hongkong Land and Jardine Matheson trade in USD on SGX).
      Factor scores are percentiles <b>within each market</b>, so they compare a stock only with its own market's peers.
      Social attention (Reddit, StockTwits) is available for US stocks only; Singapore and Malaysia show N/A rather than an estimate.</p>
      <h3>Sources</h3><dl>${src}</dl>
      <h3>Factor definitions</h3><dl>${fac}</dl>
      <p>FX used for heat-map sizing: ${esc(fx)}. Pipeline run took ${d.run_seconds}s; ${d.errors.length} error(s) logged.</p>`;
  }

  // ------------------------------------------------------------------ weights
  function renderSliders() {
    const total = FACTORS.reduce((a, f) => a + (state.weights[f.key] || 0), 0) || 1;
    $("#sliders").innerHTML = FACTORS.map((f) => `
      <div class="slider">
        <label for="w-${f.key}">${f.label}<span>${Math.round((state.weights[f.key] / total) * 100)}%</span></label>
        <input id="w-${f.key}" type="range" min="0" max="100" step="5" value="${state.weights[f.key]}" data-factor="${f.key}">
        <small>${f.desc}</small>
      </div>`).join("");
    const match = Object.entries(PRESETS).find(([, p]) => FACTORS.every((f) => p[f.key] === state.weights[f.key]));
    document.querySelectorAll(".presets button").forEach((b) => b.classList.toggle("active", !!match && b.dataset.preset === match[0]));
  }

  function setWeights(w) {
    state.weights = { ...w };
    LS.set("sfd-weights", state.weights);
    renderSliders();
    refresh();
  }

  // ------------------------------------------------------------------ focus panel
  function renderFocus(list) {
    const markets = state.market === "ALL" ? ["US", "SG", "MY"] : [state.market];
    $("#focus").innerHTML = markets.map((m) => {
      const top = list.filter((s) => s.market === m && isNum(s._focus)).sort((a, b) => b._focus - a._focus).slice(0, 5);
      const items = top.map((s, i) => {
        const sig = (s.signals || []).slice(0, 4);
        const reasons = sig.length
          ? sig.map((x) => `<li class="${esc(x.kind)}"><span>${esc(x.text)}</span></li>`).join("")
          : `<li>No rule triggered today — ranked on factor scores (strongest: ${esc(strongest(s))})</li>`;
        return `<div class="pick" data-symbol="${esc(s.symbol)}" tabindex="0" role="button" aria-label="Open ${esc(s.name)} details">
          <div class="rank">${i + 1}</div>
          <div><span class="nm">${esc(s.name)}</span><span class="tk">${esc(s.code)}</span>${s.stale ? '<span class="stale-tag">STALE</span>' : ""}
            <div class="px"><span class="ccy">${esc(s.currency)}</span>${fmtPrice(s.price)} <span class="${signCls(s.change_1d)}">${fmtPct(s.change_1d)}</span> · ${esc(s.sector || "")}</div></div>
          <div class="score-pill" style="background:${scoreBg(s._focus)}" title="Focus score">${s._focus.toFixed(0)}</div>
          <ul class="reasons">${reasons}</ul>
        </div>`;
      }).join("");
      return `<div class="focus-col"><h3>${MARKET_LABEL[m]}</h3>${items || '<p class="empty">No stocks match the current filters.</p>'}</div>`;
    }).join("");
  }
  function strongest(s) {
    const best = FACTORS.filter((f) => isNum(s.factors?.[f.key])).sort((a, b) => s.factors[b.key] - s.factors[a.key])[0];
    return best ? `${best.label} ${s.factors[best.key].toFixed(0)}` : "n/a";
  }

  // ------------------------------------------------------------------ heat map
  function heatValue(s) { return state.heat === "focus" ? s._focus : s[state.heat]; }
  function heatScale(list) {
    const m = state.heat;
    if (m === "focus") return { kind: "seq", lo: 0, hi: 100, fmt: (v) => v.toFixed(0) };
    if (m === "rel_volume") return { kind: "div", center: 1, lim: 1, fmt: (v) => v.toFixed(2) + "x" };
    const vals = list.map((s) => Math.abs(s[m])).filter(isNum).sort((a, b) => a - b);
    const p90 = vals.length ? vals[Math.floor(vals.length * 0.9)] : 1;
    const nice = { change_1d: [1, 2, 3, 5], change_1w: [2, 3, 5, 8, 10], change_1m: [3, 5, 10, 15, 20, 30] }[m];
    const lim = nice.find((n) => n >= p90) || nice[nice.length - 1];
    return { kind: "div", center: 0, lim, fmt: (v) => fmtPct(v) };
  }
  function colorFor(v, sc) { return sc.kind === "seq" ? sequential(v, sc.lo, sc.hi) : diverging(v, sc.lim, sc.center); }

  function renderTreemap(list) {
    if (!window.echarts) return;
    const el = $("#treemap");
    charts.tree = charts.tree || echarts.init(el, null, { renderer: "canvas" });
    const sc = heatScale(list);
    $("#heatHint").textContent = state.market === "ALL"
      ? "Tile size = market cap. In this All view each market gets equal area so Singapore and Malaysia stay readable; compare sizes within a market. Click a tile for details."
      : "Tile size = market cap (in USD, so USD-quoted SGX stocks size correctly). Click a tile for details.";
    const groupBy = (arr, k) => arr.reduce((m, s) => ((m[s[k] || "Other"] = m[s[k] || "Other"] || []).push(s), m), {});
    // In the All view each market gets an equal share of the area (otherwise US mega-caps
    // shrink SG/MY to a sliver); within a market, tiles stay proportional to market cap.
    const mktTotal = {};
    for (const s of list) mktTotal[s.market] = (mktTotal[s.market] || 0) + (s.market_cap_usd || 0);
    const size = (s) => (state.market === "ALL" ? (s.market_cap_usd || 0) / (mktTotal[s.market] || 1) * 1e6 : s.market_cap_usd || 1);
    const leaf = (s) => {
      const v = heatValue(s), bg = colorFor(v, sc);
      return {
        name: s.bursa_name || s.code, value: size(s), symbol: s.symbol,
        itemStyle: { color: bg },
        label: { color: inkOn(bg), formatter: () => `{a|${s.bursa_name || s.code}}\n{b|${isNum(v) ? sc.fmt(v) : "n/a"}}` },
      };
    };
    const sectors = (arr) => Object.entries(groupBy(arr, "sector")).map(([sec, ss]) => ({ name: sec, children: ss.map(leaf) }));
    const data = state.market === "ALL"
      ? ["US", "SG", "MY"].map((m) => ({ name: MARKET_LABEL[m], children: sectors(list.filter((s) => s.market === m)) })).filter((n) => n.children.length)
      : sectors(list);
    const bySym = Object.fromEntries(list.map((s) => [s.symbol, s]));
    charts.tree.setOption({
      tooltip: {
        backgroundColor: css("--surface"), borderColor: css("--border"), textStyle: { color: css("--text") },
        formatter: (p) => {
          const s = bySym[p.data?.symbol];
          if (!s) return esc(p.name);
          return `<b>${esc(s.name)}</b> (${esc(s.symbol)})<br>${esc(s.sector || "")}<br>
            Price: ${esc(s.currency)} ${fmtPrice(s.price)}<br>1D ${fmtPct(s.change_1d)} · 1W ${fmtPct(s.change_1w)} · 1M ${fmtPct(s.change_1m)}<br>
            Rel. volume: ${isNum(s.rel_volume) ? s.rel_volume.toFixed(2) + "x" : "—"} · Focus: ${isNum(s._focus) ? s._focus.toFixed(0) : "—"}<br>
            Mkt cap: ${esc(s.currency)} ${fmtBig(s.market_cap)} (USD ${fmtBig(s.market_cap_usd)})`;
        },
      },
      series: [{
        type: "treemap", data, roam: false, nodeClick: false, breadcrumb: { show: false },
        width: "100%", height: "100%", top: 0, left: 0, sort: "desc", squareRatio: 1.2,
        label: { show: true, overflow: "truncate", rich: { a: { fontWeight: 700, fontSize: 12 }, b: { fontSize: 11 } } },
        upperLabel: { show: true, height: 20, color: css("--text-2"), fontSize: 11, fontWeight: 600, backgroundColor: "transparent" },
        itemStyle: { borderColor: css("--surface"), borderWidth: 2, gapWidth: 2 },
        levels: state.market === "ALL"
          ? [{ itemStyle: { borderWidth: 0, gapWidth: 4, borderColor: css("--surface") }, upperLabel: { show: true, color: css("--text"), fontSize: 12 } },
             { itemStyle: { borderColor: css("--surface"), borderWidth: 2, gapWidth: 2, color: css("--surface-2") } },
             { itemStyle: { borderColor: css("--surface"), borderWidth: 1, gapWidth: 1 } }]
          : [{ itemStyle: { borderWidth: 0, gapWidth: 3 } },
             { itemStyle: { borderColor: css("--surface"), borderWidth: 2, gapWidth: 1, color: css("--surface-2") } },
             { itemStyle: { borderColor: css("--surface"), borderWidth: 1, gapWidth: 1 } }],
      }],
    }, true);
    charts.tree.off("click");
    charts.tree.on("click", (p) => { if (p.data?.symbol) openDetail(p.data.symbol); });

    // legend
    const steps = 9, stops = [];
    for (let i = 0; i < steps; i++) {
      const t = i / (steps - 1);
      const v = sc.kind === "seq" ? sc.lo + t * (sc.hi - sc.lo) : sc.center - sc.lim + 2 * sc.lim * t;
      stops.push(colorFor(v, sc));
    }
    const lo = sc.kind === "seq" ? sc.fmt(sc.lo) : sc.fmt(sc.center - sc.lim) + (sc.center === 0 ? " or less" : "");
    const hi = sc.kind === "seq" ? sc.fmt(sc.hi) : sc.fmt(sc.center + sc.lim) + " or more";
    const mid = sc.kind === "seq" ? "" : `<span>${sc.fmt(sc.center)}</span>`;
    $("#heatLegend").innerHTML = `<span>${lo}</span><span class="bar" style="background:linear-gradient(90deg,${stops.join(",")})"></span><span>${hi}</span>
      ${mid ? `<span class="hint" style="margin:0">(grey = ${sc.fmt(sc.center)}${state.heat === "rel_volume" ? ", i.e. normal volume" : ""}; blue = higher, red = lower)</span>` : ""}`;
  }

  // ------------------------------------------------------------------ table
  const COLS = [
    { key: "name", label: "Stock", cls: "l", sort: (s) => s.name, render: (s) => `<span class="nm">${esc(s.name)}${s.stale ? '<span class="stale-tag">STALE</span>' : ""}</span><span class="tk">${esc(s.symbol)}</span>` },
    { key: "market", label: "Mkt", cls: "l", render: (s) => esc(s.market) },
    { key: "sector", label: "Sector", cls: "l", render: (s) => esc(s.sector || "—") },
    { key: "price", label: "Price", render: (s) => `<span class="ccy">${esc(s.currency)}</span>${fmtPrice(s.price)}` },
    ...[["change_1d", "1D"], ["change_1w", "1W"], ["change_1m", "1M"], ["change_3m", "3M"], ["change_6m", "6M"]].map(([k, l]) => ({ key: k, label: l, render: (s) => `<span class="${signCls(s[k])}">${fmtPct(s[k])}</span>` })),
    { key: "rel_volume", label: "Rel vol", render: (s) => (isNum(s.rel_volume) ? `<span class="cell-score" style="background:${diverging(s.rel_volume, 1, 1)};color:${inkOn(diverging(s.rel_volume, 1, 1))}">${s.rel_volume.toFixed(2)}x</span>` : "—") },
    { key: "rsi14", label: "RSI", render: (s) => fmtNum(s.rsi14, 0) },
    { key: "pct_from_high", label: "vs 52w high", render: (s) => `<span class="${signCls(s.pct_from_high)}">${fmtPct(s.pct_from_high)}</span>` },
    { key: "pe", label: "P/E", render: (s) => (isNum(s.pe) ? (s.pe < 0 ? "neg" : fmtNum(s.pe, 1)) + (s.pe_basis === "forward" ? "ᶠ" : "") : "—") },
    { key: "pb", label: "P/B", render: (s) => fmtNum(s.pb, 2) },
    { key: "div_yield", label: "Div %", render: (s) => (isNum(s.div_yield) ? s.div_yield.toFixed(2) + "%" : "—") },
    { key: "market_cap_usd", label: "Mkt cap", render: (s) => `<span class="ccy">${esc(s.currency)}</span>${fmtBig(s.market_cap)}` },
    { key: "days_to_earnings", label: "Earnings", sortVal: (s) => (isNum(s.days_to_earnings) && s.days_to_earnings >= 0 ? s.days_to_earnings : 9999), dirDefault: 1,
      render: (s) => (s.next_earnings ? `${fmtDate(s.next_earnings)} <span class="ccy">(${s.days_to_earnings}d)</span>` : "—") },
    { key: "news_count_7d", label: "News 7d", render: (s) => (isNum(s.news_count_7d) ? (s.news_count_7d >= 100 ? "100+" : s.news_count_7d) : "—") },
    { key: "reddit", label: "Reddit 24h", sortVal: (s) => s.social?.reddit_mentions ?? -1, render: (s) => (s.social ? fmtNum(s.social.reddit_mentions, 0) : '<span class="ccy">N/A</span>') },
    ...FACTORS.map((f) => ({ key: "f_" + f.key, label: f.label, sortVal: (s) => s.factors?.[f.key] ?? -1, render: (s) => scoreCell(s.factors?.[f.key]) })),
    { key: "focus", label: "Focus", sortVal: (s) => s._focus ?? -1, render: (s) => scoreCell(s._focus, true) },
  ];
  function scoreCell(v, bold) {
    if (!isNum(v)) return "—";
    return `<span class="cell-score" style="background:${scoreBg(v)};${bold ? "font-weight:700" : ""}">${v.toFixed(0)}</span>`;
  }

  function renderTable(list) {
    const col = COLS.find((c) => c.key === state.sort.key) || COLS[COLS.length - 1];
    const val = col.sortVal || col.sort || ((s) => s[col.key]);
    const rows = [...list].sort((a, b) => {
      const x = val(a), y = val(b);
      if (typeof x === "string" || typeof y === "string") return state.sort.dir * String(x ?? "").localeCompare(String(y ?? ""));
      const xn = isNum(x) ? x : -Infinity, yn = isNum(y) ? y : -Infinity;
      return state.sort.dir * (xn - yn);
    });
    const head = `<thead><tr>${COLS.map((c) => `<th data-key="${c.key}" class="${c.cls || ""} ${c.key === state.sort.key ? "sorted" + (state.sort.dir > 0 ? " asc" : "") : ""}" scope="col">${c.label}</th>`).join("")}</tr></thead>`;
    const body = rows.map((s) => `<tr data-symbol="${esc(s.symbol)}">${COLS.map((c) => `<td class="${c.cls || ""}">${c.render(s)}</td>`).join("")}</tr>`).join("");
    $("#table").innerHTML = head + `<tbody>${body || `<tr><td class="l" colspan="${COLS.length}">No stocks match the current filters.</td></tr>`}</tbody>`;
  }

  // ------------------------------------------------------------------ filters
  function renderSectorOptions() {
    const sel = $("#fSector");
    const sectors = [...new Set(state.data.stocks.filter((s) => state.market === "ALL" || s.market === state.market).map((s) => s.sector).filter(Boolean))].sort();
    if (!sectors.includes(state.filters.sector)) state.filters.sector = "";
    sel.innerHTML = `<option value="">All sectors</option>` + sectors.map((s) => `<option ${s === state.filters.sector ? "selected" : ""}>${esc(s)}</option>`).join("");
  }

  // ------------------------------------------------------------------ main refresh
  function refresh() {
    for (const s of state.data.stocks) s._focus = focusScore(s);
    const list = visible();
    const total = state.data.stocks.filter((s) => state.market === "ALL" || s.market === state.market).length;
    $("#filterCount").textContent = `Showing ${list.length} of ${total} stocks.`;
    renderFocus(list);
    renderTreemap(list);
    renderTable(list);
    if (state.detail) renderDetail(state.detail, true);
  }

  function setMarket(m) {
    state.market = m;
    LS.set("sfd-market", m);
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.market === m)));
    renderSectorOptions();
    refresh();
  }

  // ------------------------------------------------------------------ detail drawer
  function openDetail(symbol) {
    state.detail = symbol;
    $("#drawer").classList.add("open");
    $("#drawer").setAttribute("aria-hidden", "false");
    $("#drawerBackdrop").hidden = false;
    document.body.style.overflow = "hidden";
    renderDetail(symbol);
  }
  function closeDetail() {
    state.detail = null;
    $("#drawer").classList.remove("open");
    $("#drawer").setAttribute("aria-hidden", "true");
    $("#drawerBackdrop").hidden = true;
    document.body.style.overflow = "";
  }

  function renderDetail(symbol, keepScroll) {
    const s = state.data.stocks.find((x) => x.symbol === symbol);
    if (!s) return;
    const soc = s.social;
    const kv = (label, value) => `<div><span>${label}</span><b>${value}</b></div>`;
    const socialHtml = s.market !== "US"
      ? `<p class="hint">Social attention: <b>N/A</b> — no free Reddit/StockTwits coverage for ${MARKET_LABEL[s.market]} stocks. Attention uses news count only.</p>`
      : `<div class="kv">
          ${kv("Reddit mentions (24h)", soc?.reddit_mentions != null ? fmtNum(soc.reddit_mentions, 0) : "—")}
          ${kv("Reddit mentions (prev 24h)", soc?.reddit_mentions_24h_ago != null ? fmtNum(soc.reddit_mentions_24h_ago, 0) : "—")}
          ${kv("Reddit rank (ApeWisdom)", soc?.reddit_rank ? "#" + soc.reddit_rank : "not ranked")}
          ${kv("StockTwits msgs/day (est.)", soc?.stocktwits_msgs_per_day != null ? fmtNum(soc.stocktwits_msgs_per_day, 0) : "—")}
          ${kv("StockTwits watchers", soc?.stocktwits_watchers != null ? fmtBig(soc.stocktwits_watchers) : "—")}
          ${kv("StockTwits bull / bear (last 30)", soc?.stocktwits_sample ? `${soc.stocktwits_bullish} / ${soc.stocktwits_bearish}` : "—")}
        </div>`;
    const news = (s.news || []).map((n) => `<li><a href="${esc(n.url)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a>
      <small>${esc(n.publisher || "")}${n.published ? " · " + fmtDate(n.published.slice(0, 10)) : ""}</small></li>`).join("");
    const rating = s.rating ? s.rating.replace(/_/g, " ") : "—";
    const body = $("#drawerBody");
    const scroll = $("#drawer").scrollTop;
    body.innerHTML = `
      <div class="d-head">
        <div>
          <h2 id="dTitle">${esc(s.long_name || s.name)}</h2>
          <div class="d-meta">${esc(s.symbol)} · ${MARKET_LABEL[s.market]} · ${esc(s.sector || "")}${s.industry ? " / " + esc(s.industry) : ""}</div>
          <div class="d-price"><span class="ccy" style="font-size:14px">${esc(s.currency)}</span> ${fmtPrice(s.price)}
            <span class="${signCls(s.change_1d)}" style="font-size:15px">${fmtPct(s.change_1d, 2)}</span></div>
          <div class="d-meta">Last close ${fmtDate(s.last_trade_date)}${s.stale ? ` · <span class="stale-tag">STALE since ${fmtDate((s.stale_since || "").slice(0, 10))}</span>` : ""}</div>
        </div>
        <button class="close" type="button" aria-label="Close details">×</button>
      </div>

      <section>
        <div class="section-title"><h3>Price (${esc(s.currency)})</h3>
          <div class="range-btns">${["1M", "3M", "1Y"].map((r) => `<button data-range="${r}" class="${r === state.range ? "active" : ""}">${r}</button>`).join("")}</div></div>
        <div id="chartPrice" class="chart-price" role="img" aria-label="Price chart with 50 and 200 day moving averages and volume"></div>
      </section>

      ${(s.signals || []).length ? `<section><h3>Rule-based signals</h3><ul class="reasons" style="margin-top:6px">${s.signals.map((x) => `<li class="${esc(x.kind)}">${esc(x.text)}</li>`).join("")}</ul>
        <p class="hint">Rule-based signals for education — not investment advice.</p></section>` : ""}

      <section>
        <div class="section-title"><h3>Factor scores (percentile in ${MARKET_LABEL[s.market]})</h3><span class="hint" style="margin:0">Focus score: <b>${isNum(s._focus) ? s._focus.toFixed(0) : "—"}</b></span></div>
        <div id="chartFactors" class="chart-factors" role="img" aria-label="Factor score bar chart"></div>
      </section>

      <section><h3>Key ratios</h3><div class="kv" style="margin-top:6px">
        ${kv("Market cap", `${esc(s.currency)} ${fmtBig(s.market_cap)}`)}
        ${kv(`P/E (${s.pe_basis || "trailing"})`, fmtNum(s.pe, 1))}
        ${kv("P/B", fmtNum(s.pb, 2))}
        ${kv("Dividend yield", isNum(s.div_yield) ? s.div_yield.toFixed(2) + "%" : "—")}
        ${kv("52-week high", fmtPrice(s.high_52w))}
        ${kv("52-week low", fmtPrice(s.low_52w))}
        ${kv("RSI (14)", fmtNum(s.rsi14, 1))}
        ${kv("Relative volume", isNum(s.rel_volume) ? s.rel_volume.toFixed(2) + "x" : "—")}
        ${kv("50-day MA", fmtPrice(s.ma50))}
        ${kv("200-day MA", fmtPrice(s.ma200))}
        ${kv("1M / 3M / 6M", `${fmtPct(s.change_1m, 0)} / ${fmtPct(s.change_3m, 0)} / ${fmtPct(s.change_6m, 0)}`)}
        ${kv("Analyst rating", `${esc(rating)}${s.n_analysts ? ` (${s.n_analysts})` : ""}`)}
        ${kv("Mean target", isNum(s.target_mean) ? `${fmtPrice(s.target_mean)} (${fmtPct(s.target_upside, 0)})` : "—")}
      </div></section>

      <section><h3>Earnings</h3><div class="kv" style="margin-top:6px">
        ${kv("Next earnings", s.next_earnings ? `${fmtDate(s.next_earnings)} (in ${s.days_to_earnings} days)` : "Not published")}
        ${kv("Last reported", fmtDate(s.last_earnings_date))}
        ${kv("EPS actual vs est.", isNum(s.last_eps_actual) ? `${fmtNum(s.last_eps_actual, 2)} vs ${fmtNum(s.last_eps_estimate, 2)}` : "—")}
        ${kv("EPS surprise", fmtPct(s.last_surprise_pct))}
      </div></section>

      <section><h3>Social attention</h3><div style="margin-top:6px">${socialHtml}</div></section>

      <section><div class="section-title"><h3>Latest headlines</h3><span class="hint" style="margin:0">${s.news_count_7d >= 100 ? "100+" : s.news_count_7d ?? 0} in 7 days · source: ${esc(s.news_source || "none")}</span></div>
        <ul class="news">${news || '<li class="empty">No headlines found in the last 7 days.</li>'}</ul></section>

      <section><h3>Research links</h3><div class="links" style="margin-top:6px">
        ${links(s).map(([l, u]) => `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(l)} ↗</a>`).join("")}</div></section>`;
    if (keepScroll) $("#drawer").scrollTop = scroll; else $("#drawer").scrollTop = 0;
    body.querySelector(".close").addEventListener("click", closeDetail);
    body.querySelectorAll(".range-btns button").forEach((b) => b.addEventListener("click", () => { state.range = b.dataset.range; renderDetail(symbol, true); }));
    drawPrice(s);
    drawFactors(s);
  }

  function axisStyle() {
    return {
      axisLine: { lineStyle: { color: css("--axis") } }, axisTick: { show: false },
      axisLabel: { color: css("--muted"), fontSize: 11 }, splitLine: { lineStyle: { color: css("--grid") } },
    };
  }

  function drawPrice(s) {
    if (!window.echarts || !s.history) return;
    charts.price?.dispose();
    charts.price = echarts.init($("#chartPrice"));
    const n = { "1M": 21, "3M": 63, "1Y": 252 }[state.range];
    const h = s.history, start = Math.max(0, h.dates.length - n);
    const sl = (a) => a.slice(start);
    const dates = sl(h.dates);
    const ax = axisStyle();
    charts.price.setOption({
      animation: false,
      legend: { top: 0, left: 0, itemWidth: 14, itemHeight: 3, textStyle: { color: css("--text-2"), fontSize: 11 }, data: ["Close", "50-day MA", "200-day MA"] },
      tooltip: {
        trigger: "axis", axisPointer: { type: "cross", label: { backgroundColor: css("--text-2") } },
        backgroundColor: css("--surface"), borderColor: css("--border"), textStyle: { color: css("--text"), fontSize: 12 },
        valueFormatter: (v) => (isNum(v) ? (v > 1e5 ? fmtBig(v) : fmtPrice(v)) : "—"),
      },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [{ left: 56, right: 12, top: 28, height: "58%" }, { left: 56, right: 12, top: "76%", bottom: 24 }],
      xAxis: [
        { type: "category", data: dates, gridIndex: 0, boundaryGap: false, ...ax, axisLabel: { show: false }, splitLine: { show: false } },
        { type: "category", data: dates, gridIndex: 1, boundaryGap: true, ...ax, splitLine: { show: false },
          axisLabel: { color: css("--muted"), fontSize: 11, formatter: (d) => fmtDate(d).replace(/ \d{4}$/, "") } },
      ],
      yAxis: [
        { type: "value", gridIndex: 0, scale: true, ...ax, axisLabel: { color: css("--muted"), fontSize: 11, formatter: (v) => fmtPrice(v) } },
        { type: "value", gridIndex: 1, ...ax, splitNumber: 2, axisLabel: { color: css("--muted"), fontSize: 10, formatter: (v) => fmtBig(v) } },
      ],
      series: [
        { name: "Close", type: "line", data: sl(h.close), xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { width: 2, color: css("--s1") }, itemStyle: { color: css("--s1") },
          areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: css("--s1") + "33" }, { offset: 1, color: css("--s1") + "00" }]) } },
        { name: "50-day MA", type: "line", data: sl(h.ma50), xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { width: 1.5, color: css("--s2") }, itemStyle: { color: css("--s2") } },
        { name: "200-day MA", type: "line", data: sl(h.ma200), xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { width: 1.5, color: css("--s3"), type: "dashed" }, itemStyle: { color: css("--s3") } },
        { name: "Volume", type: "bar", data: sl(h.volume), xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 8, itemStyle: { color: css("--axis"), borderRadius: [2, 2, 0, 0] } },
      ],
    });
  }

  function drawFactors(s) {
    if (!window.echarts) return;
    charts.factors?.dispose();
    charts.factors = echarts.init($("#chartFactors"));
    const names = [...FACTORS.map((f) => f.label), "Focus"];
    const vals = [...FACTORS.map((f) => s.factors?.[f.key] ?? null), s._focus ?? null];
    const comps = s.factor_components || {};
    const ax = axisStyle();
    charts.factors.setOption({
      animation: false,
      grid: { left: 80, right: 40, top: 6, bottom: 22 },
      tooltip: {
        trigger: "item", backgroundColor: css("--surface"), borderColor: css("--border"), textStyle: { color: css("--text"), fontSize: 12 },
        formatter: (p) => {
          const f = FACTORS[p.dataIndex];
          if (!f) return `<b>Focus score</b>: ${isNum(p.value) ? p.value.toFixed(0) : "—"}<br>Weighted by your slider settings`;
          const parts = Object.entries(comps[f.key] || {}).map(([k, v]) => `${esc(k)}: ${v == null ? "n/a" : v.toFixed(0)}`).join("<br>");
          return `<b>${f.label}</b>: ${isNum(p.value) ? p.value.toFixed(0) : "n/a"}<br><span style="opacity:.75">Component percentiles</span><br>${parts}`;
        },
      },
      xAxis: { type: "value", min: 0, max: 100, interval: 25, ...ax },
      yAxis: { type: "category", data: names, inverse: true, ...ax, axisLabel: { color: css("--text-2"), fontSize: 12 }, splitLine: { show: false } },
      series: [{
        type: "bar", barWidth: 14,
        data: vals.map((v, i) => ({ value: v, itemStyle: { color: i === vals.length - 1 ? css("--accent-ink") : css("--s1"), borderRadius: [0, 4, 4, 0] } })),
        label: { show: true, position: "right", color: css("--text-2"), fontSize: 11, formatter: (p) => (isNum(p.value) ? p.value.toFixed(0) : "n/a") },
      }],
    });
  }

  // ------------------------------------------------------------------ wiring
  function wire() {
    document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => setMarket(b.dataset.market)));
    $("#sliders").addEventListener("input", (e) => {
      const f = e.target.dataset.factor;
      if (!f) return;
      state.weights[f] = Number(e.target.value);
      LS.set("sfd-weights", state.weights);
      renderSliders();
      $(`#w-${f}`).focus();
      refresh();
    });
    document.querySelectorAll(".presets button").forEach((b) => b.addEventListener("click", () => {
      setWeights(b.dataset.preset === "reset" ? PRESETS.balanced : PRESETS[b.dataset.preset]);
    }));
    $("#fSearch").addEventListener("input", (e) => { state.filters.q = e.target.value; refresh(); });
    $("#fSector").addEventListener("change", (e) => { state.filters.sector = e.target.value; refresh(); });
    $("#fMin").addEventListener("input", (e) => { state.filters.min = Number(e.target.value); $("#fMinVal").textContent = e.target.value; refresh(); });
    $("#fEarn").addEventListener("input", (e) => { const v = e.target.value; state.filters.earn = v === "" ? null : Math.max(0, Number(v)); refresh(); });
    $("#fClear").addEventListener("click", () => {
      state.filters = { q: "", sector: "", min: 0, earn: null };
      $("#fSearch").value = ""; $("#fMin").value = 0; $("#fMinVal").textContent = "0"; $("#fEarn").value = "";
      renderSectorOptions(); refresh();
    });
    $("#heatMetric").value = state.heat;
    $("#heatMetric").addEventListener("change", (e) => { state.heat = e.target.value; LS.set("sfd-heat", state.heat); refresh(); });
    $("#table").addEventListener("click", (e) => {
      const th = e.target.closest("th");
      if (th) {
        const key = th.dataset.key, col = COLS.find((c) => c.key === key);
        if (state.sort.key === key) state.sort.dir *= -1;
        else state.sort = { key, dir: col.dirDefault || (col.cls === "l" ? 1 : -1) };
        renderTable(visible());
        return;
      }
      const tr = e.target.closest("tr[data-symbol]");
      if (tr) openDetail(tr.dataset.symbol);
    });
    $("#focus").addEventListener("click", (e) => { const p = e.target.closest(".pick"); if (p) openDetail(p.dataset.symbol); });
    $("#focus").addEventListener("keydown", (e) => { const p = e.target.closest(".pick"); if (p && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openDetail(p.dataset.symbol); } });
    $("#drawerBackdrop").addEventListener("click", closeDetail);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.detail) closeDetail(); });
    $("#themeBtn").addEventListener("click", () => {
      const root = document.documentElement;
      const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("sfd-theme", root.dataset.theme); } catch (err) { /* ignore */ }
      refresh();
    });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (!document.documentElement.dataset.theme) refresh(); });
    let t;
    window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => Object.values(charts).forEach((c) => c && !c.isDisposed() && c.resize()), 120); });
  }

  async function init() {
    // The theme script stores a raw string; normalise if JSON-encoded.
    try { const t = localStorage.getItem("sfd-theme"); if (t && t.startsWith('"')) document.documentElement.dataset.theme = JSON.parse(t); } catch (e) { /* ignore */ }
    try {
      const res = await fetch(`data/stocks.json?v=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      state.data = await res.json();
    } catch (err) {
      $("#asof").innerHTML = `<span class="badge warn">Could not load data/stocks.json (${esc(err.message)})</span>`;
      return;
    }
    if (!["ALL", "US", "SG", "MY"].includes(state.market)) state.market = "ALL";
    wire();
    renderAsOf();
    renderSources();
    renderSliders();
    setMarket(state.market);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
