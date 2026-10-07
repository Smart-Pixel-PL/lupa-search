"use strict";
// Porządki — disk cleanup tab. Uses helpers from app.js: $, $$, esc, api, toast, fmtSize, fmtDate.

const CL = { rep: null, sel: new Set(), open: new Set(["caches"]), poll: null };
const RISK = { safe: ["Bezpieczne", "safe"], check: ["Sprawdź", "check"], careful: ["Uważaj", "careful"] };
const HOME_RE = /^\/Users\/[^/]+/;
const short = p => p.replace(HOME_RE, "~");

function setTab(tab) {
  document.body.classList.toggle("mode-clean", tab === "clean");
  $$("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
  $("#clean").hidden = tab !== "clean";
  try { localStorage.setItem("lupa.tab", tab); } catch {}
  if (tab === "clean") loadCleanup(); else $("#q").focus();
}
$("#tabs").addEventListener("click", e => { const b = e.target.closest("button"); if (b) setTab(b.dataset.tab); });

async function loadCleanup() {
  const rep = await api("/api/cleanup");
  CL.rep = rep;
  if (!rep.scanned_at && !rep.running) { await api("/api/cleanup/scan", { method: "POST" }); rep.running = true; }
  if (CL.sel.size === 0 || CL.lastScan !== rep.scanned_at) {  // fresh report → default selection
    CL.sel = new Set(rep.categories.flatMap(c => c.items.filter(i => i.preselect && !i.keep).map(i => i.path)));
    CL.lastScan = rep.scanned_at;
  }
  renderCleanup();
  clearTimeout(CL.poll);
  if (rep.running) CL.poll = setTimeout(loadCleanup, 1500);
}

function itemVisual(i) {
  if (i.id) return `<img loading="lazy" src="/api/thumb/${i.id}" onerror="this.outerHTML='<span class=ic>📄</span>'">`;
  if (i.app) return `<img loading="lazy" src="/api/cleanup/icon?path=${encodeURIComponent(i.app)}" onerror="this.outerHTML='<span class=ic>📁</span>'">`;
  return `<span class="ic">${i.kind === "app" ? "🧩" : i.kind === "file" ? "📄" : "📁"}</span>`;
}

function itemRow(c, i) {
  const when = i.last_used ? `otwarte ${fmtDate(i.last_used)}` : i.mtime ? `zmienione ${fmtDate(i.mtime)}` : "";
  const conf = i.confidence === "high" ? "pewne dopasowanie" : i.confidence === "medium" ? "dopasowanie po nazwie" : "";
  return `<div class="cl-row ${i.keep ? "keep" : ""}" data-path="${esc(i.path)}">
    <input type="checkbox" ${CL.sel.has(i.path) ? "checked" : ""} ${i.keep ? "disabled title='Ta kopia zostaje'" : ""}>
    <div class="cl-vis">${itemVisual(i)}</div>
    <div class="cl-txt"><div class="cl-name">${esc(i.name)}${i.keep ? ' <span class="pill keep">zostaje</span>' : ""}</div>
      <div class="cl-path" title="${esc(i.path)}">${esc(short(i.path))}</div>
      ${i.note || conf ? `<div class="cl-n">${esc([i.note, conf].filter(Boolean).join(" · "))}</div>` : ""}</div>
    <div class="cl-when">${esc(when)}</div>
    <div class="cl-size">${fmtSize(i.size)}</div>
    <div class="cl-tools"><button data-a="reveal" title="Pokaż w Finderze">🔍</button><button data-a="protect" title="Nigdy nie proponuj (chroń)">🛡</button></div>
  </div>`;
}

function catHtml(c) {
  const [rl, rc] = RISK[c.risk];
  const selItems = c.items.filter(i => CL.sel.has(i.path));
  const selBytes = selItems.reduce((a, i) => a + i.size, 0);
  const selectable = c.items.filter(i => !i.keep);
  const all = selectable.length && selectable.every(i => CL.sel.has(i.path));
  const open = CL.open.has(c.id);
  let rows = "";
  if (open) {
    if (c.id === "duplicates") {
      let g = -1;
      for (const i of c.items) {
        if (i.group !== g) { g = i.group; rows += `<div class="cl-group">Grupa ${g + 1} · ${fmtSize(i.size)} × ${c.items.filter(x => x.group === g).length}</div>`; }
        rows += itemRow(c, i);
      }
    } else rows = c.items.map(i => itemRow(c, i)).join("");
  }
  return `<div class="cl-cat ${open ? "open" : ""}" data-cat="${c.id}">
    <div class="cl-cat-h">
      <input type="checkbox" class="cl-all" ${all ? "checked" : ""} ${c.count ? "" : "disabled"} title="Zaznacz wszystko w kategorii">
      <span class="cl-icon">${c.icon}</span>
      <div class="cl-cat-t"><b>${esc(c.title)}</b> <span class="risk ${rc}">${rl}</span><div class="cl-desc">${esc(c.desc)}</div></div>
      <div class="cl-cat-n">${c.count ? `<b>${fmtSize(c.total)}</b><small>${c.count} poz.${selItems.length ? ` · zazn. ${fmtSize(selBytes)}` : ""}</small>` : `<small>nic do zrobienia ✓</small>`}</div>
      <span class="chev">${c.count ? (open ? "▾" : "▸") : ""}</span>
    </div>
    ${open && c.count ? `<div class="cl-items">${rows}</div>` : ""}
  </div>`;
}

function renderCleanup() {
  const r = CL.rep; if (!r) return;
  const d = r.disk;
  const all = r.categories.flatMap(c => c.items);
  const selBytes = all.filter(i => CL.sel.has(i.path)).reduce((a, i) => a + i.size, 0);
  const total = r.categories.reduce((a, c) => a + c.total, 0);
  $("#clDiskTxt").innerHTML = `wolne <b class="${d.low ? "low" : ""}">${fmtSize(d.free)}</b> z ${fmtSize(d.total)} (${d.free_pct}%)`;
  const usedPct = d.used / d.total * 100, selPct = Math.min(usedPct, selBytes / d.total * 100);
  $("#clUsed").style.width = (usedPct - selPct) + "%";
  $("#clSel").style.width = selPct + "%";
  $("#clUsed").classList.toggle("low", d.low);
  $("#clTotal").textContent = r.scanned_at ? fmtSize(total) : "—";
  $("#clMeta").innerHTML = r.running ? `<span class="spin"></span> ${esc(r.progress || "Skanuję…")}` :
    r.scanned_at ? `Ostatni skan: ${new Date(r.scanned_at * 1000).toLocaleString("pl-PL")} · ${r.took || "?"} s<br>Kolejny automatycznie za ${r.settings.every_days} dni` : "";
  $("#clScan").disabled = r.running;
  $("#clTrash").innerHTML = d.trash > 50e6 ? `🗑️ W Koszu leży <b>${fmtSize(d.trash)}</b> — to miejsce odzyskasz dopiero po opróżnieniu Kosza.
    <button data-t="open">Otwórz Kosz</button><button data-t="empty" class="danger">Opróżnij Kosz…</button>` : "";
  $("#clTrash").hidden = !$("#clTrash").innerHTML;
  $("#clCats").innerHTML = r.categories.length ? r.categories.map(catHtml).join("") :
    `<div class="empty"><b>Skanuję dysk…</b>Pierwszy skan trwa kilkanaście sekund.</div>`;
  $("#clAuto").checked = r.settings.auto_caches;
  const prot = r.settings.protected || [];
  $("#clProtected").innerHTML = prot.length ? `<b>🛡 Chronione:</b> ${prot.map(p => `<span class="pill">${esc(short(p))} <a data-unprotect="${esc(p)}">✕</a></span>`).join(" ")}` : "";
  const n = CL.sel.size;
  $("#clBottom").hidden = !n;
  $("#clSelTxt").innerHTML = `Zaznaczono <b>${n}</b> ${n === 1 ? "element" : "elementów"} · <b>${fmtSize(selBytes)}</b>`;
  $("#cleanBadge").hidden = !d.low;
  $("#cleanBadge").textContent = "!";
}

$("#clCats").addEventListener("click", async e => {
  const cat = e.target.closest(".cl-cat"); if (!cat) return;
  const c = CL.rep.categories.find(x => x.id === cat.dataset.cat);
  const row = e.target.closest(".cl-row");
  if (e.target.classList.contains("cl-all")) {
    const sel = c.items.filter(i => !i.keep);
    const on = e.target.checked;
    sel.forEach(i => on ? CL.sel.add(i.path) : CL.sel.delete(i.path));
    if (on && (c.risk !== "safe")) toast(`Zaznaczono całą kategorię „${c.title}” — przejrzyj listę przed usunięciem`, 3500);
    return renderCleanup();
  }
  if (row) {
    const p = row.dataset.path;
    const a = e.target.closest("[data-a]")?.dataset.a;
    if (a === "reveal") return api("/api/cleanup/reveal", { method: "POST", json: { path: p } });
    if (a === "protect") {
      if (!confirm(`Chronić „${short(p)}”? Lupa nie będzie go więcej proponować do usunięcia.`)) return;
      await api("/api/cleanup/protect", { method: "POST", json: { path: p, add: true } });
      CL.sel.delete(p); c.items = c.items.filter(i => i.path !== p); c.count = c.items.length;
      c.total = c.items.reduce((s, i) => s + (i.keep ? 0 : i.size), 0);
      CL.rep.settings.protected = [...(CL.rep.settings.protected || []), p];
      return renderCleanup();
    }
    if (e.target.matches("input[type=checkbox]")) { e.target.checked ? CL.sel.add(p) : CL.sel.delete(p); return renderCleanup(); }
    return;
  }
  if (e.target.closest(".cl-cat-h") && c.count) { CL.open.has(c.id) ? CL.open.delete(c.id) : CL.open.add(c.id); renderCleanup(); }
});

$("#clScan").addEventListener("click", async () => { await api("/api/cleanup/scan", { method: "POST" }); CL.sel.clear(); setTimeout(loadCleanup, 300); });
$("#clAuto").addEventListener("change", async e => {
  await api("/api/cleanup/settings", { method: "POST", json: { auto_caches: e.target.checked } });
  toast(e.target.checked ? "Włączone: co tydzień cache i logi trafią do Kosza" : "Automatyczne czyszczenie wyłączone");
});
$("#clProtected").addEventListener("click", async e => {
  const p = e.target.dataset.unprotect; if (!p) return;
  await api("/api/cleanup/protect", { method: "POST", json: { path: p, add: false } });
  toast("Usunięto z chronionych — pojawi się po kolejnym skanie"); loadCleanup();
});
$("#clTrash").addEventListener("click", async e => {
  const t = e.target.dataset.t; if (!t) return;
  if (t === "open") return api("/api/cleanup/open-trash", { method: "POST" });
  if (!confirm(`Opróżnić Kosz (${fmtSize(CL.rep.disk.trash)})?\n\nTego NIE da się cofnąć — pliki z Kosza zostaną trwale usunięte.`)) return;
  toast("Opróżniam Kosz…", 4000);
  const r = await api("/api/cleanup/empty-trash", { method: "POST" });
  toast(r.ok ? "Kosz opróżniony ✓" : "Nie udało się: " + r.error, 4000);
  loadCleanup();
});
$("#clDo").addEventListener("click", async () => {
  const items = CL.rep.categories.flatMap(c => c.items).filter(i => CL.sel.has(i.path));
  const bytes = items.reduce((a, i) => a + i.size, 0);
  const risky = CL.rep.categories.filter(c => c.risk !== "safe" && c.items.some(i => CL.sel.has(i.path))).map(c => "• " + c.title);
  if (!confirm(`Przenieść ${items.length} elementów (${fmtSize(bytes)}) do Kosza?` +
    (risky.length ? `\n\nZawiera kategorie do sprawdzenia:\n${risky.join("\n")}` : "") +
    `\n\nMożesz je przywrócić z Kosza. macOS może zapytać o zgodę na sterowanie Finderem.`)) return;
  $("#clDo").disabled = true; $("#clDo").textContent = "Przenoszę…";
  try {
    const r = await api("/api/cleanup/trash", { method: "POST", json: { paths: items.map(i => i.path) } });
    const bad = Object.entries(r.results).filter(([, v]) => v !== "ok");
    bad.length ? toast(`Przeniesiono ${fmtSize(r.freed)}. Pominięto ${bad.length}: ${bad[0][1]}`, 6000)
               : toast(`Przeniesiono do Kosza ${fmtSize(r.freed)} ✓  Opróżnij Kosz, aby odzyskać miejsce.`, 5000);
    CL.sel.clear(); CL.lastScan = null;
  } finally { $("#clDo").disabled = false; $("#clDo").textContent = "Przenieś do Kosza"; }
  loadCleanup();
});

// initial tab (remembered) + low-space badge on the tab even when on the search tab
(async () => {
  let tab = "search";
  try { tab = localStorage.getItem("lupa.tab") || "search"; } catch {}
  if (location.hash === "#porzadki") tab = "clean";
  if (tab === "clean") setTab("clean");
  try { const r = await api("/api/cleanup"); CL.rep = CL.rep || r; $("#cleanBadge").hidden = !r.disk.low; $("#cleanBadge").textContent = "!"; } catch {}
})();
