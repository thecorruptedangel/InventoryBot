/* InventoryBot Mini App — column-first inventory editor */
(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const $ = (sel) => document.querySelector(sel);

  // ---------- state ----------
  const SOURCE_COL = "E", ORDERTEXT_COL = "L", QTY_COL = "D", UTIL_COL = "F", PACK_COL = "C";
  const SORTS = ["sheet", "urgent", "name"];
  const SORT_LABELS = { sheet: "↕ Sheet", urgent: "↕ Urgent", name: "↕ Name" };

  // Columns hidden from the chip row (name is the left label; Days is the 📅 pill).
  const HIDDEN_CHIPS = new Set(["A", "G"]);
  // Friendly names over the sheet's messy/typo'd headers.
  const LABELS = {
    A: "Item", B: "Unit", C: "Packing", D: "Qty", E: "Source", F: "Util",
    G: "Days", H: "Needed", I: "Days left", J: "Shortfall", K: "Order", L: "Order text",
  };
  function label(letter) { return LABELS[letter] || colMeta(letter).header || letter; }

  const state = {
    columns: [],          // [{index, letter, header, computed}]
    items: [],            // [{row, name, source, orderable, values:{letter:val}}]
    colTypes: {},         // letter -> 'number' | 'text'
    planningDays: null,   // master K1 ("plan for N days")
    activeCol: "D",       // default Qty
    search: "",
    supplier: "All",
    needsOnly: false,
    sort: "sheet",
    tab: "list",
    gathered: new Set(),  // item rows ticked off this shopping session (not saved)
  };

  // ---------- telegram boot ----------
  function boot() {
    if (!tg) {
      fatal("Open this from inside Telegram.");
      return;
    }
    tg.ready();
    tg.expand();
    tg.setHeaderColor && tg.setHeaderColor("bg_color");
    tg.disableClosingConfirmation && tg.disableClosingConfirmation();
    if (!tg.initData) {
      fatal("No Telegram session. Reopen the app from the bot.");
      return;
    }
    wireUi();
    load();
    setInterval(pollSync, POLL_MS);
  }

  function haptic(kind) {
    try {
      const h = tg.HapticFeedback;
      if (!h) return;
      if (kind === "ok") h.notificationOccurred("success");
      else if (kind === "err") h.notificationOccurred("error");
      else h.impactOccurred("light");
    } catch (_) {}
  }

  // ---------- api ----------
  async function api(path, opts) {
    opts = opts || {};
    const res = await fetch(path, {
      method: opts.method || "GET",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": tg.initData,
      },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!res.ok) {
      let detail = "HTTP " + res.status;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return res.json();
  }

  const POLL_MS = 60000;
  let lastSig = "";

  // Cheap fingerprint of all item values + planning days, to detect external changes.
  function sigOf(items, days) {
    return days + "|" + items.map((i) => i.row + ":" + Object.values(i.values).join(",")).join(";");
  }

  async function fetchGrid(force) {
    const grid = await api("/api/grid" + (force ? "?force=true" : ""));
    state.columns = grid.columns;
    state.items = grid.items;
    state.planningDays = grid.planningDays;
    state.colTypes = inferTypes(grid);
    if (!state.columns.some((c) => c.letter === state.activeCol)) {
      state.activeCol = (grid.columns[3] || grid.columns[0]).letter;
    }
    lastSig = sigOf(state.items, state.planningDays);
    renderControls();
    renderChips();
    render();
  }

  // Background auto-sync: pick up edits made elsewhere (sheet or another user).
  async function pollSync() {
    if (document.hidden) return;
    if (!$("#sheet").classList.contains("hidden")) return;   // mid-edit
    if (!$("#detail").classList.contains("hidden")) return;
    if (!$("#errorBox").classList.contains("hidden")) return;
    let grid;
    try { grid = await api("/api/grid?force=true"); } catch (_) { return; }
    const ns = sigOf(grid.items, grid.planningDays);
    if (ns === lastSig) return;                                // nothing changed
    lastSig = ns;
    const y = window.scrollY;
    state.columns = grid.columns;
    state.items = grid.items;
    state.planningDays = grid.planningDays;
    state.colTypes = inferTypes(grid);
    renderControls(); renderChips(); render();
    window.scrollTo(0, y);
    toast("Synced");
  }

  async function load(force) {
    show("#loading");
    hide("#errorBox");
    try {
      await fetchGrid(force);
    } catch (e) {
      fatal(e.message);
    } finally {
      hide("#loading");
    }
  }

  // Re-pull data without the full-screen spinner or losing scroll position.
  // Used after a Days change, which recomputes every item's order math.
  async function silentRefresh() {
    const y = window.scrollY;
    try {
      await fetchGrid(true);
      window.scrollTo(0, y);
    } catch (e) {
      toast(e.message, true);
    }
  }

  function inferTypes(grid) {
    const types = {};
    grid.columns.forEach((c) => {
      if (c.computed) { types[c.letter] = "computed"; return; }
      let nums = 0, bools = 0, seen = 0;
      for (const it of grid.items) {
        const v = it.values[c.letter];
        if (v === "" || v === null || v === undefined) continue;
        seen++;
        if (v === true || v === false || v === "TRUE" || v === "FALSE") bools++;
        else if (typeof v === "number" || (!isNaN(parseFloat(v)) && isFinite(v))) nums++;
        if (seen >= 30) break;
      }
      if (seen && bools === seen) types[c.letter] = "bool";
      else if (seen && nums === seen) types[c.letter] = "number";
      else types[c.letter] = "text";
    });
    return types;
  }

  // ---------- helpers ----------
  function fmt(v) {
    if (v === "" || v === null || v === undefined) return "—";
    if (v === true) return "✓";
    if (v === false) return "✗";
    if (typeof v === "number") {
      return Number.isInteger(v) ? String(v) : (Math.round(v * 100) / 100).toString();
    }
    return String(v);
  }

  function colMeta(letter) {
    return state.columns.find((c) => c.letter === letter) || { letter, header: letter };
  }

  function filteredItems() {
    const q = state.search.trim().toLowerCase();
    let out = state.items.filter((it) => {
      if (q && !it.name.toLowerCase().includes(q) && !(it.source || "").toLowerCase().includes(q)) return false;
      if (state.supplier !== "All" && it.source !== state.supplier) return false;
      if (state.needsOnly && !(it.orderable > 0)) return false;
      return true;
    });
    if (state.sort === "urgent") out = out.slice().sort((a, b) => b.orderable - a.orderable);
    else if (state.sort === "name") out = out.slice().sort((a, b) => a.name.localeCompare(b.name));
    return out;
  }

  function sources() {
    return Array.from(new Set(state.items.map((i) => i.source).filter(Boolean))).sort();
  }

  // ---------- chips ----------
  function renderChips() {
    const colBox = $("#colChips");
    colBox.innerHTML = "";
    if (HIDDEN_CHIPS.has(state.activeCol)) state.activeCol = QTY_COL;
    state.columns.forEach((c) => {
      if (HIDDEN_CHIPS.has(c.letter) || c.computed) return;   // editable columns only
      const b = document.createElement("button");
      b.className = "chip" + (c.letter === state.activeCol ? " active" : "");
      b.textContent = label(c.letter);
      b.onclick = () => { state.activeCol = c.letter; renderChips(); render(); haptic("tick"); };
      colBox.appendChild(b);
    });

    const sups = ["All", ...Array.from(new Set(state.items.map((i) => i.source).filter(Boolean)))];
    const supBox = $("#supChips");
    supBox.innerHTML = "";
    sups.forEach((s) => {
      const b = document.createElement("button");
      b.className = "chip" + (s === state.supplier ? " active" : "");
      b.textContent = s;
      b.onclick = () => { state.supplier = s; renderChips(); render(); haptic("tick"); };
      supBox.appendChild(b);
    });
  }

  function renderControls() {
    $("#daysBtn").textContent = "📅 " + (state.planningDays != null ? state.planningDays + " days" : "Days");
    const needs = $("#needsBtn");
    needs.classList.toggle("active", state.needsOnly);
    $("#sortBtn").textContent = SORT_LABELS[state.sort];
  }

  // Hide pills that don't apply to the current tab.
  function applyTabUi() {
    const order = state.tab === "order";
    $("#colChips").classList.toggle("hidden", order);
    $("#needsBtn").classList.toggle("hidden", order);
    $("#sortBtn").classList.toggle("hidden", order);
    $("#search").placeholder = order ? "Search order…" : "Search items…";
  }

  // ---------- render ----------
  function render() {
    const orderItems = state.items.filter((i) => i.orderable > 0);
    $("#orderCount").textContent = orderItems.length ? String(orderItems.length) : "";
    applyTabUi();
    if (state.tab === "order") { renderOrder(orderItems); return; }
    renderList();
  }

  function renderList() {
    show("#listView"); hide("#orderView");
    const view = $("#listView");
    view.innerHTML = "";
    const items = filteredItems();
    if (!items.length) {
      view.innerHTML = '<div class="empty">No items match.</div>';
      return;
    }
    const col = colMeta(state.activeCol);
    const frag = document.createDocumentFragment();
    items.forEach((it) => {
      const row = document.createElement("div");
      row.className = "row";

      const need = it.orderable > 0;
      const name = document.createElement("div");
      name.className = "name";
      name.innerHTML =
        '<span class="nm">' + escapeHtml(it.name) +
        (need ? ' <span class="need">↓' + fmt(it.orderable) + "</span>" : "") + "</span>" +
        '<span class="sub">' + escapeHtml(it.source || "") + "</span>";
      name.onclick = () => openDetail(it);

      const val = document.createElement("div");
      val.className = "val" + (col.computed ? " computed" : "");
      val.textContent = fmt(it.values[state.activeCol]);
      val.onclick = () => col.computed ? openDetail(it) : openEdit(it, state.activeCol);

      const chev = document.createElement("span");
      chev.className = "chev";
      chev.textContent = "›";
      chev.onclick = () => openDetail(it);

      row.append(name, val, chev);
      frag.appendChild(row);
    });
    view.appendChild(frag);
  }

  function renderOrder(allOrder) {
    hide("#listView"); show("#orderView");
    const view = $("#orderView");
    view.innerHTML = "";

    const q = state.search.trim().toLowerCase();
    const items = allOrder.filter((it) => {
      if (q && !it.name.toLowerCase().includes(q)) return false;
      if (state.supplier !== "All" && it.source !== state.supplier) return false;
      return true;
    });

    if (!allOrder.length) {
      view.innerHTML = '<div class="empty">Nothing to order 🎉</div>';
      return;
    }
    if (!items.length) {
      view.innerHTML = '<div class="empty">No matches in the order list.</div>';
      return;
    }

    const groups = {};
    items.forEach((it) => { const k = it.source || "Other"; (groups[k] = groups[k] || []).push(it); });
    const supplierCount = Object.keys(groups).length;
    const gatheredN = items.filter((it) => state.gathered.has(it.row)).length;

    const summary = document.createElement("div");
    summary.className = "order-summary";
    summary.innerHTML =
      "<span><b>" + items.length + "</b> to order · " + supplierCount + " supplier" + (supplierCount > 1 ? "s" : "") + "</span>" +
      '<span id="prog" class="prog">' + gatheredN + "/" + items.length + " ✓</span>";
    view.appendChild(summary);

    Object.keys(groups).sort().forEach((src) => {
      const card = document.createElement("div");
      card.className = "order-card";
      const head = document.createElement("div");
      head.className = "order-card-head";
      head.innerHTML = "<span>" + escapeHtml(src) + "</span>";
      const right = document.createElement("div");
      right.className = "head-right";
      right.innerHTML = "<span class='cnt'>" + groups[src].length + "</span>";
      const cbtn = document.createElement("button");
      cbtn.className = "mini-copy";
      cbtn.textContent = "📋";
      cbtn.title = "Copy " + src;
      cbtn.onclick = (e) => { e.stopPropagation(); copySupplier(src, groups[src]); };
      right.appendChild(cbtn);
      head.appendChild(right);
      card.appendChild(head);

      groups[src].forEach((it) => {
        const done = state.gathered.has(it.row);
        const line = document.createElement("div");
        line.className = "order-line" + (done ? " done" : "");

        const badge = document.createElement("button");
        badge.className = "qbadge";
        badge.textContent = fmt(it.orderable);
        badge.title = "Adjust stock";
        badge.onclick = (e) => { e.stopPropagation(); openEdit(it, "D"); };

        const txt = document.createElement("div");
        txt.className = "txt";
        const unit = [it.values["C"], it.values["B"]].filter((x) => x && x !== "—").join(" · ");
        txt.innerHTML = "<div class='nm'>" + escapeHtml(it.name) + "</div>" +
          (unit ? "<div class='sub'>" + escapeHtml(unit) + "</div>" : "");

        line.append(badge, txt);
        line.onclick = () => {
          if (state.gathered.has(it.row)) state.gathered.delete(it.row);
          else state.gathered.add(it.row);
          line.classList.toggle("done");
          const p = $("#prog");
          if (p) p.textContent = items.filter((x) => state.gathered.has(x.row)).length + "/" + items.length + " ✓";
          haptic("tick");
        };
        card.appendChild(line);
      });
      view.appendChild(card);
    });

    const bar = document.createElement("div");
    bar.className = "order-bar";
    const copyBtn = document.createElement("button");
    copyBtn.className = "primary";
    copyBtn.textContent = "📋 Copy list";
    copyBtn.onclick = () => copyOrder(groups);
    bar.appendChild(copyBtn);
    const shareBtn = document.createElement("button");
    shareBtn.className = "ghost";
    shareBtn.textContent = "Share";
    shareBtn.onclick = () => shareOrder(groups);
    bar.appendChild(shareBtn);
    if (gatheredN > 0) {
      const clr = document.createElement("button");
      clr.className = "ghost";
      clr.textContent = "Clear ✓";
      clr.onclick = () => { state.gathered.clear(); render(); };
      bar.appendChild(clr);
    }
    view.appendChild(bar);
  }

  function orderLabel(it) {
    const t = it.values[ORDERTEXT_COL];
    if (t !== "" && t !== null && t !== undefined) return String(t).trim();
    return fmt(it.orderable) + " " + (it.values["C"] || "") + " " + it.name;
  }

  function orderText(groups) {
    const out = [];
    Object.keys(groups).sort().forEach((src) => {
      out.push("— " + src + " —");
      groups[src].forEach((it) => out.push(orderLabel(it)));
      out.push("");
    });
    return out.join("\n").trim();
  }
  function writeClip(text, msg) {
    if (navigator.clipboard) navigator.clipboard.writeText(text);
    toast(msg); haptic("ok");
  }
  function copyOrder(groups) { writeClip(orderText(groups), "Order list copied"); }
  function copySupplier(src, list) { const g = {}; g[src] = list; writeClip(orderText(g), src + " copied"); }
  async function shareOrder(groups) {
    const text = orderText(groups);
    if (navigator.share) {
      try { await navigator.share({ text }); haptic("ok"); } catch (_) {}
    } else {
      writeClip(text, "Copied (share not supported)");
    }
  }

  // ---------- edit sheet ----------
  function openEdit(it, letter) {
    if (letter === SOURCE_COL) return openSourcePicker(it, letter);
    const type = state.colTypes[letter];
    let html = '<div class="sheet-title">' + escapeHtml(label(letter)) + "</div>";
    html += '<div class="sheet-item">' + escapeHtml(it.name) + "</div>";

    let cur = it.values[letter];
    const isNum = type === "number";
    const step = letter === UTIL_COL ? 0.1 : 1;
    if (isNum) {
      let n = parseFloat(cur);
      if (isNaN(n)) n = 0;
      html +=
        '<div class="stepper">' +
        '<button data-step="-1">−</button>' +
        '<div class="cur" id="curVal">' + fmt(n) + "</div>" +
        '<button data-step="1">+</button>' +
        "</div>";
      html += '<div class="pad">' +
        ["1","2","3","4","5","6","7","8","9",".","0","⌫"]
          .map((k) => '<button data-key="' + k + '">' + k + "</button>").join("") +
        "</div>";
      html += '<div class="preview" id="prev"></div>';
    } else {
      html += '<input class="txt-input" id="curVal" type="text" value="' + escapeAttr(cur === "" ? "" : String(cur)) + '" />';
    }
    html += '<button class="save-btn" id="saveBtn">Save</button>';
    $("#sheetCard").innerHTML = html;
    show("#sheet");

    let buffer = isNum ? String(fmt(parseFloat(cur) || 0)) : null;

    function refresh() {
      if (!isNum) return;
      $("#curVal").textContent = buffer === "" ? "0" : buffer;
      previewFormula(it, letter, parseFloat(buffer) || 0);
    }
    if (isNum) previewFormula(it, letter, parseFloat(buffer) || 0);

    $("#sheetCard").querySelectorAll("[data-step]").forEach((b) =>
      b.onclick = () => {
        let v = (parseFloat(buffer) || 0) + parseInt(b.dataset.step, 10) * step;
        if (v < 0) v = 0;
        buffer = String(Math.round(v * 100) / 100);
        refresh(); haptic("tick");
      });
    $("#sheetCard").querySelectorAll("[data-key]").forEach((b) =>
      b.onclick = () => {
        const k = b.dataset.key;
        if (k === "⌫") buffer = buffer.length > 1 ? buffer.slice(0, -1) : "0";
        else if (k === ".") { if (!buffer.includes(".")) buffer += "."; }
        else buffer = (buffer === "0" || buffer === "") ? k : buffer + k;
        refresh(); haptic("tick");
      });

    $("#saveBtn").onclick = async () => {
      const raw = isNum ? buffer : $("#curVal").value;
      $("#saveBtn").disabled = true;
      const ok = await saveCell(it, letter, raw);
      if (ok) { closeSheet("#sheet"); render(); }
      else $("#saveBtn").disabled = false;
    };
    backdrop("#sheet");
  }

  function openSourcePicker(it, letter) {
    const cur = String(it.values[letter] || "");
    let html = '<div class="sheet-title">' + escapeHtml(label(letter)) + "</div>";
    html += '<div class="sheet-item">' + escapeHtml(it.name) + "</div>";
    html += '<div class="picker">';
    sources().forEach((s) => {
      html += '<button class="pick' + (s === cur.trim() ? " on" : "") + '" data-src="' + escapeAttr(s) + '">' + escapeHtml(s) + "</button>";
    });
    html += "</div>";
    html += '<input class="txt-input" id="newSrc" type="text" placeholder="…or type a new supplier" />';
    html += '<button class="save-btn" id="saveBtn">Save new supplier</button>';
    $("#sheetCard").innerHTML = html;
    show("#sheet");
    $("#sheetCard").querySelectorAll(".pick").forEach((b) =>
      b.onclick = async () => {
        const ok = await saveCell(it, letter, b.dataset.src);
        if (ok) { closeSheet("#sheet"); render(); }
      });
    $("#saveBtn").onclick = async () => {
      const v = $("#newSrc").value.trim();
      if (!v) return;
      $("#saveBtn").disabled = true;
      const ok = await saveCell(it, letter, v);
      if (ok) { closeSheet("#sheet"); render(); } else $("#saveBtn").disabled = false;
    };
    backdrop("#sheet");
  }

  function openDaysEditor() {
    let buf = String(state.planningDays != null ? state.planningDays : 20);
    let html = '<div class="sheet-title">Planning horizon (applies to all items)</div>';
    html += '<div class="sheet-item">Plan for N days</div>';
    html += '<div class="stepper"><button data-step="-1">−</button>' +
      '<div class="cur" id="curVal">' + buf + '</div><button data-step="1">+</button></div>';
    html += '<div class="pad">' +
      ["1","2","3","4","5","6","7","8","9","","0","⌫"]
        .map((k) => k === "" ? "<span></span>" : '<button data-key="' + k + '">' + k + "</button>").join("") +
      "</div>";
    html += '<div class="preview">Changes the master Days cell — recomputes every item\'s order need.</div>';
    html += '<button class="save-btn" id="saveBtn">Save</button>';
    $("#sheetCard").innerHTML = html;
    show("#sheet");
    const upd = () => { $("#curVal").textContent = buf === "" ? "0" : buf; };
    $("#sheetCard").querySelectorAll("[data-step]").forEach((b) =>
      b.onclick = () => { buf = String(Math.max(1, (parseInt(buf, 10) || 0) + parseInt(b.dataset.step, 10))); upd(); haptic("tick"); });
    $("#sheetCard").querySelectorAll("[data-key]").forEach((b) =>
      b.onclick = () => {
        const k = b.dataset.key;
        if (k === "⌫") buf = buf.length > 1 ? buf.slice(0, -1) : "";
        else buf = (buf === "0" || buf === "") ? k : buf + k;
        upd(); haptic("tick");
      });
    $("#saveBtn").onclick = async () => {
      const days = parseInt(buf, 10);
      if (!days || days < 1) { toast("Enter a valid number", true); return; }
      $("#saveBtn").disabled = true;
      try {
        const res = await api("/api/planning", { method: "POST", body: { days } });
        state.planningDays = res.planningDays;
        haptic("ok"); toast("Planning set to " + res.planningDays + " days");
        closeSheet("#sheet");
        await silentRefresh();   // recompute every item's order math, no spinner/scroll jump
      } catch (e) {
        haptic("err"); toast(e.message, true);
        $("#saveBtn").disabled = false;
      }
    };
    backdrop("#sheet");
  }

  function previewFormula(it, letter, newVal) {
    // Light client preview: show current Orderable; server returns the real recompute on save.
    const prev = $("#prev");
    if (!prev) return;
    prev.innerHTML = "Current orderable: <b>" + fmt(it.values["K"]) + "</b> · saved value recomputes formulas";
  }

  // ---------- detail sheet ----------
  function openDetail(it) {
    let html = '<div class="sheet-item">' + escapeHtml(it.name) + "</div>";
    if (it.orderable > 0) {
      html += '<div class="order-hero">🛒 ' + escapeHtml(orderLabel(it)) + "</div>";
    }
    html += "<div class='dl'>";
    state.columns.forEach((c) => {
      if (c.letter === "A") return;
      const cls = c.computed ? "field computed" : "field editable";
      const v = fmt(it.values[c.letter]);
      html += '<div class="' + cls + '" data-col="' + c.letter + '">' +
        '<div class="k">' + escapeHtml(label(c.letter)) + (c.computed ? ' <span class="tag">auto ⚙</span>' : "") + "</div>" +
        '<div class="v">' + escapeHtml(v) + "</div></div>";
    });
    html += "</div>";
    $("#detailCard").innerHTML = html;
    show("#detail");
    $("#detailCard").querySelectorAll(".field.editable").forEach((f) =>
      f.onclick = () => { closeSheet("#detail"); openEdit(it, f.dataset.col); });
    backdrop("#detail");
  }

  // ---------- save ----------
  async function saveCell(it, letter, value, rollback) {
    try {
      const res = await api("/api/cell", { method: "POST", body: { row: it.row, col: letter, value } });
      Object.assign(it.values, res.values);
      it.orderable = res.orderable;
      if (res.values.A !== undefined) it.name = String(res.values.A).trim();
      if (res.values.E !== undefined) it.source = String(res.values.E).trim();
      lastSig = sigOf(state.items, state.planningDays);
      haptic("ok");
      toast("Saved");
      return true;
    } catch (e) {
      if (rollback) rollback();
      haptic("err");
      toast(e.message, true);
      return false;
    }
  }

  // ---------- ui plumbing ----------
  function wireUi() {
    document.querySelectorAll(".tab").forEach((t) =>
      t.onclick = () => {
        document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
        t.classList.add("active");
        state.tab = t.dataset.tab;
        render();
      });
    $("#refreshBtn").onclick = () => { haptic("tick"); load(true); };
    $("#daysBtn").onclick = () => openDaysEditor();
    $("#needsBtn").onclick = () => { state.needsOnly = !state.needsOnly; renderControls(); if (state.tab === "list") renderList(); haptic("tick"); };
    $("#sortBtn").onclick = () => {
      state.sort = SORTS[(SORTS.indexOf(state.sort) + 1) % SORTS.length];
      renderControls(); if (state.tab === "list") renderList(); haptic("tick");
    };
    let deb;
    $("#search").oninput = (e) => {
      clearTimeout(deb);
      const v = e.target.value;
      deb = setTimeout(() => { state.search = v; render(); }, 120);
    };
  }

  function backdrop(sel) {
    $(sel).querySelector(".sheet-backdrop").onclick = () => closeSheet(sel);
    if (tg.BackButton) {
      tg.BackButton.show();
      tg.BackButton.onClick(() => closeSheet(sel));
    }
  }
  function closeSheet(sel) { hide(sel); tg.BackButton && tg.BackButton.hide(); }

  let toastTimer;
  function toast(msg, isErr) {
    const el = $("#toast");
    el.textContent = msg;
    el.className = "toast" + (isErr ? " err" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), 2200);
  }

  function fatal(msg) {
    hide("#loading");
    const box = $("#errorBox");
    box.innerHTML = "<div>" + escapeHtml(msg) + "</div><button id='retry'>Retry</button>";
    box.classList.remove("hidden");
    $("#retry").onclick = () => load(true);
  }

  function show(sel) { $(sel).classList.remove("hidden"); }
  function hide(sel) { $(sel).classList.add("hidden"); }
  function escapeHtml(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
  function escapeAttr(s) { return escapeHtml(s).replace(/'/g, "&#39;"); }

  boot();
})();
