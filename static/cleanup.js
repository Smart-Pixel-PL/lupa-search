"use strict";
// Porządki — disk cleanup tab. Uses helpers from app.js: $, $$, esc, api, toast, fmtSize, fmtDate.

const CL = { rep: null, sel: new Set(), open: new Set(["caches"]), poll: null, expanded: new Set() };
const RISK = { safe: ["Bezpieczne", "safe"], check: ["Sprawdź", "check"], careful: ["Uważaj", "careful"] };
const HOME_RE = /^\/Users\/[^/]+/;
const short = p => p.replace(HOME_RE, "~");

function setTab(tab) {
  document.body.classList.toggle("mode-clean", tab === "clean");
  if (tab !== "clean") setTabSearch(tab);
  $$("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
  $("#clean").hidden = tab !== "clean";
  try { localStorage.setItem("lupa.tab", tab); } catch {}
  if (tab === "clean") loadCleanup(); else if (tab === "search") $("#q").focus();
  try { localStorage.setItem("lupa.tab", tab); } catch {}
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

const canTrash = i => !i.keep && !i.nas && !(i.kind === "app" && i.method && i.method !== "trash");
const UNINSTALL_BTN = { adobe: "Odinstaluj w Creative Cloud", uninstaller: "Odinstaluj (deinstalator producenta)", vendor: "Odinstaluj (otwórz aplikację)" };

function appDetails(i) {
  if (i.kind !== "app" || !i.method) return "";
  const relBytes = (i.related || []).reduce((a, r) => a + r.size, 0);
  if (i.method === "adobe")
    return `<div class="cl-un"><span class="pill warn">⚠︎ wymaga Creative Cloud</span>
      <button class="cl-mini" data-a="uninstall">${UNINSTALL_BTN[i.method]}</button></div>`;
  if (i.method !== "trash")
    return `<div class="cl-un"><button class="cl-mini danger" data-a="plan">🗑 Odinstaluj…</button>
      <span class="pill warn">ma pliki systemowe — macOS poprosi o hasło</span></div>`;
  const open = CL.expanded?.has(i.path);
  return `<div class="cl-un"><button class="cl-mini danger" data-a="plan">🗑 Odinstaluj…</button><span class="pill ok">✓ pełne odinstalowanie</span>
    aplikacja ${fmtSize(i.app_size)} + dane ${fmtSize(relBytes)} (${(i.related || []).length} folderów)
    ${(i.related || []).length ? `<a class="cl-link" data-a="details">${open ? "ukryj" : "pokaż pliki"}</a>` : ""}</div>
    ${open ? `<ul class="cl-rel">${i.related.map(r => `<li><span>${esc(short(r.path))}</span><b>${fmtSize(r.size)}</b></li>`).join("")}</ul>` : ""}`;
}

function itemRow(c, i) {
  const when = i.last_used ? `otwarte ${fmtDate(i.last_used)}` : i.mtime ? `zmienione ${fmtDate(i.mtime)}` : "";
  const conf = i.confidence === "high" ? "pewne dopasowanie" : i.confidence === "medium" ? "dopasowanie po nazwie" : "";
  return `<div class="cl-row ${i.keep ? "keep" : ""}" data-path="${esc(i.path)}">
    <input type="checkbox" ${CL.sel.has(i.path) ? "checked" : ""} ${i.keep ? "disabled title='Ta kopia zostaje'" : i.nas ? "disabled title='Plik na NAS — usuń go w Finderze (🔍)'" : !canTrash(i) ? "disabled title='Odinstaluj deinstalatorem'" : ""}>
    <div class="cl-vis">${itemVisual(i)}</div>
    <div class="cl-txt"><div class="cl-name">${esc(i.name)}${i.keep ? ' <span class="pill keep">zostaje</span>' : ""}</div>
      <div class="cl-path" title="${esc(i.path)}">${esc(short(i.path))}</div>
      ${i.note || conf ? `<div class="cl-n">${esc([i.note, conf].filter(Boolean).join(" · "))}</div>` : ""}${appDetails(i)}</div>
    <div class="cl-when">${esc(when)}</div>
    <div class="cl-size">${fmtSize(i.size)}</div>
    <div class="cl-tools">${i.nas_copy ? `<button data-a="reveal-nas" title="Pokaż kopię na NAS-ie">🗄</button>` : ""}<button data-a="reveal" title="Pokaż w Finderze">🔍</button><button data-a="protect" title="Nigdy nie proponuj (chroń)">🛡</button></div>
  </div>`;
}

function catHtml(c) {
  const [rl, rc] = RISK[c.risk];
  const selItems = c.items.filter(i => CL.sel.has(i.path));
  const selBytes = selItems.reduce((a, i) => a + i.size, 0);
  const selectable = c.items.filter(canTrash);
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
    const sel = c.items.filter(canTrash);
    const on = e.target.checked;
    sel.forEach(i => on ? CL.sel.add(i.path) : CL.sel.delete(i.path));
    if (on && (c.risk !== "safe")) toast(`Zaznaczono całą kategorię „${c.title}” — przejrzyj listę przed usunięciem`, 3500);
    return renderCleanup();
  }
  if (row) {
    const p = row.dataset.path;
    const a = e.target.closest("[data-a]")?.dataset.a;
    if (a === "reveal") return api("/api/cleanup/reveal", { method: "POST", json: { path: p } });
    if (a === "reveal-nas") {
      const it = c.items.find(x => x.path === p);
      return api("/api/diskmap/reveal", { method: "POST", json: { path: it.nas_copy } }).catch(() => toast("NAS niedostępny"));
    }
    if (a === "details") { CL.expanded = CL.expanded || new Set(); CL.expanded.has(p) ? CL.expanded.delete(p) : CL.expanded.add(p); return renderCleanup(); }
    if (a === "plan") return openPlan(p);
    if (a === "uninstall-now") {
      const it = c.items.find(x => x.path === p); if (!it) return;
      const rel = it.related || [];
      const list = rel.slice(0, 12).map(r => `   • ${short(r.path)} (${fmtSize(r.size)})`).join("\n") + (rel.length > 12 ? `\n   • …i ${rel.length - 12} więcej` : "");
      if (!confirm(`Odinstalować ${it.name.replace(/\.app$/, "")}?\n\nDo Kosza trafią:\n   • aplikacja (${fmtSize(it.app_size)})` +
        (rel.length ? `\n${list}` : "") + `\n\nRazem ${fmtSize(it.size)}. Możesz to przywrócić z Kosza (Odłóż).\nAplikacja musi być zamknięta.`)) return;
      const btn = e.target.closest("button"); btn.disabled = true; btn.textContent = "Odinstalowuję…";
      try {
        const r = await api("/api/cleanup/trash", { method: "POST", json: { paths: [p] } });
        const res = r.results[p];
        if (res === "ok") {
          const bad = Object.entries(r.results).filter(([, v]) => v !== "ok").length;
          toast(`✓ Odinstalowano ${it.name.replace(/\.app$/, "")} — ${fmtSize(r.freed)} w Koszu` +
                (bad ? ` (${bad} plików pominięto)` : ""), 5000);
          CL.sel.delete(p); CL.lastScan = null; loadCleanup();
        } else { toast("Nie udało się: " + res, 6000); btn.disabled = false; btn.textContent = "🗑 Odinstaluj"; }
      } catch (err) { toast("Błąd: " + err.message, 5000); btn.disabled = false; btn.textContent = "🗑 Odinstaluj"; }
      return;
    }
    if (a === "uninstall") {
      const r = await api("/api/cleanup/uninstaller", { method: "POST", json: { path: p } });
      return toast(r.ok ? "Otwieram — odinstaluj tam aplikację, potem kliknij „Skanuj teraz”" : "Nie udało się: " + r.error, 5000);
    }
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
  const apps = items.filter(i => i.kind === "app");
  if (!confirm(`Przenieść ${items.length} elementów (${fmtSize(bytes)}) do Kosza?` +
    (apps.length ? `\n\nOdinstalowanie ${apps.length} aplikacji: każda razem ze swoimi danymi z ~/Library. Aplikacje muszą być zamknięte.` : "") +
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


// ---------------------------------------------------------------- Raycast-style uninstaller
const UN = { mode: "apps", apps: [], plan: null, sel: new Set(), cur: 0 };
const appIcon = p => `<img src="/api/cleanup/icon?path=${encodeURIComponent(p)}" onerror="this.outerHTML='<span class=ic>🧩</span>'">`;

function unShow(on) { $("#unModal").hidden = !on; if (on) setTimeout(() => $("#unFilter").focus(), 30); }

async function openPicker() {
  UN.mode = "apps"; UN.plan = null; unShow(true);
  $("#unFilter").value = ""; $("#unFilter").placeholder = "Szukaj aplikacji do odinstalowania…";
  $("#unSort").hidden = true; $("#unBack").hidden = true; $("#unWarn").hidden = true;
  $("#unSub").textContent = "Wczytuję aplikacje…"; $("#unList").innerHTML = ""; $("#unGo").hidden = true; $("#unTitle").textContent = "";
  if (!UN.apps.length) UN.apps = await api("/api/uninstall/apps");
  renderUn();
}

async function openPlan(path) {
  UN.mode = "plan"; unShow(true);
  $("#unFilter").value = ""; $("#unFilter").placeholder = "Filtruj pliki i foldery po nazwie…";
  $("#unSort").hidden = false; $("#unBack").hidden = false; $("#unGo").hidden = false;
  $("#unSub").innerHTML = `<span class="spin"></span> Szukam wszystkich plików aplikacji…`; $("#unList").innerHTML = "";
  try { UN.plan = await api("/api/uninstall/plan?path=" + encodeURIComponent(path)); }
  catch (e) { $("#unSub").textContent = "Błąd: " + e.message; return; }
  UN.sel = new Set(UN.plan.items.map(i => i.path));
  renderUn();
}

function renderUn() {
  const f = $("#unFilter").value.trim().toLowerCase();
  if (UN.mode === "apps") {
    const list = UN.apps.filter(a => !f || a.name.toLowerCase().includes(f));
    UN.cur = Math.min(UN.cur, Math.max(0, list.length - 1));
    $("#unSub").textContent = `${list.length} aplikacji`;
    $("#unList").innerHTML = list.map((a, k) => `<div class="un-row app ${k === UN.cur ? "cur" : ""}" data-app="${esc(a.path)}">
      <div class="un-ic">${appIcon(a.path)}</div><div class="un-n"><b>${esc(a.name)}</b><span>${esc(short(a.path))}</span></div></div>`).join("");
    return;
  }
  const pl = UN.plan; if (!pl) return;
  let items = pl.items.filter(i => !f || i.path.toLowerCase().includes(f));
  items = [...items].sort($("#unSort").value === "path" ? (a, b) => a.path.localeCompare(b.path) : (a, b) => b.size - a.size);
  const selItems = pl.items.filter(i => UN.sel.has(i.path));
  const selBytes = selItems.reduce((a, i) => a + i.size, 0);
  $("#unSub").innerHTML = `<b>${pl.items.length}</b> plików · ${fmtSize(pl.total)}` +
    (pl.needs_admin ? ` · <span class="pill warn">🔒 pliki systemowe — macOS poprosi o hasło</span>` : "");
  $("#unList").innerHTML = items.map(i => {
    const dir = short(i.path.slice(0, i.path.lastIndexOf("/"))) || "/";
    const name = i.path.slice(i.path.lastIndexOf("/") + 1);
    return `<label class="un-row" data-p="${esc(i.path)}">
      <input type="checkbox" ${UN.sel.has(i.path) ? "checked" : ""} ${i.is_app ? "disabled title='Sama aplikacja — zawsze usuwana'" : ""}>
      <div class="un-n"><b>${esc(name)}</b><span>${esc(dir)}</span></div>
      ${i.scope === "system" ? `<span class="pill warn" title="Plik systemowy — wymaga hasła administratora">🔒 system</span>` : ""}
      <span class="un-size">${fmtSize(i.size)}</span>
      <span class="un-ic sm">${i.is_app ? appIcon(pl.app) : /\.(plist|binarycookies)$/.test(name) ? "📄" : "📁"}</span></label>`;
  }).join("");
  const warn = [];
  if (pl.running) warn.push("Aplikacja jest teraz otwarta — zamknij ją (⌘Q) przed odinstalowaniem.");
  if (pl.warn === "adobe") warn.push("To aplikacja Adobe — odinstaluj ją w Creative Cloud, żeby nie popsuć Creative Cloud.");
  if (pl.warn === "uninstaller") warn.push("Producent dołączył własny deinstalator — możesz go użyć zamiast tego (przycisk w kategorii „Nieużywane aplikacje”).");
  $("#unWarn").hidden = !warn.length; $("#unWarn").innerHTML = warn.map(esc).join("<br>");
  $("#unTitle").innerHTML = `<span class="un-ic sm">${appIcon(pl.app)}</span> Odinstaluj <b>${esc(pl.name)}</b> · zaznaczono ${selItems.length} z ${pl.items.length} · ${fmtSize(selBytes)}`;
  $("#unGo").disabled = pl.warn === "adobe" || pl.running;
}

async function runUninstall() {
  const pl = UN.plan; if (!pl || $("#unGo").disabled) return;
  const paths = pl.items.filter(i => UN.sel.has(i.path)).map(i => i.path);
  if (!confirm(`Odinstalować ${pl.name}?\n\n${paths.length} plików (${fmtSize(pl.items.filter(i => UN.sel.has(i.path)).reduce((a, i) => a + i.size, 0))}) trafi do Kosza.` +
    (pl.items.some(i => i.scope === "system" && UN.sel.has(i.path)) ? "\n\nmacOS poprosi o hasło administratora, żeby usunąć pliki systemowe (usługi w tle aplikacji zostaną zatrzymane)." : ""))) return;
  $("#unGo").disabled = true; $("#unGo").textContent = "Odinstalowuję…";
  try {
    const r = await api("/api/uninstall/run", { method: "POST", json: { app: pl.app, paths } });
    if (r.error) { toast(r.error, 6000); return; }
    const bad = Object.entries(r.results || {}).filter(([, v]) => v !== "ok");
    toast(bad.length ? `Odinstalowano częściowo (${fmtSize(r.freed)}). Pominięto ${bad.length}: ${bad[0][1]}`
                     : `✓ Odinstalowano ${pl.name} — ${fmtSize(r.freed)} w Koszu`, 6000);
    UN.apps = UN.apps.filter(a => a.path !== pl.app);
    unShow(false); CL.lastScan = null; loadCleanup();
  } catch (e) { toast("Błąd: " + e.message, 6000); }
  finally { $("#unGo").disabled = false; $("#unGo").innerHTML = "Odinstaluj aplikację <kbd>↵</kbd>"; }
}

$("#unOpen").addEventListener("click", openPicker);
$("#unBack").addEventListener("click", () => UN.apps.length ? openPicker() : unShow(false));
$("#unFilter").addEventListener("input", () => { UN.cur = 0; renderUn(); });
$("#unSort").addEventListener("change", renderUn);
$("#unGo").addEventListener("click", runUninstall);
$("#unModal").addEventListener("click", e => {
  if (e.target.id === "unModal") return unShow(false);
  const a = e.target.closest("[data-app]"); if (a) return openPlan(a.dataset.app);
  const row = e.target.closest(".un-row[data-p]");
  if (row && e.target.matches("input")) { e.target.checked ? UN.sel.add(row.dataset.p) : UN.sel.delete(row.dataset.p); renderUn(); }
});
$("#unModal").addEventListener("keydown", e => {
  if (e.key === "Escape") { e.preventDefault(); UN.mode === "plan" && UN.apps.length ? openPicker() : unShow(false); }
  else if (e.key === "Enter") { e.preventDefault(); if (UN.mode === "plan") runUninstall(); else { const r = $$(".un-row.app")[UN.cur]; if (r) openPlan(r.dataset.app); } }
  else if (UN.mode === "apps" && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault(); const n = $$(".un-row.app").length; UN.cur = (UN.cur + (e.key === "ArrowDown" ? 1 : -1) + n) % Math.max(n, 1); renderUn();
    $(".un-row.app.cur")?.scrollIntoView({ block: "nearest" });
  }
});


// ---------------------------------------------------------------- disk usage map (squarified treemap)
const DM = { src: "local", path: "/", poll: null, data: null, seq: 0 };
const DM_COLORS = ["#4f5dff", "#e5484d", "#30a46c", "#f76b15", "#8e4ec6", "#0090ff", "#d6409f", "#12a594", "#ad7f58", "#3e63dd", "#e54666", "#46a758"];

function squarify(items, x, y, w, h) {
  // Bruls et al.: lay rows along the shorter side, keep tile aspect ratios close to 1
  const out = [], total = items.reduce((a, i) => a + i.size, 0);
  if (!total) return out;
  const scale = (w * h) / total;
  let rest = items.map(i => ({ ...i, area: i.size * scale }));
  const worst = (row, side) => {
    const s = row.reduce((a, r) => a + r.area, 0);
    return Math.max(...row.map(r => Math.max(side * side * r.area / (s * s), (s * s) / (side * side * r.area))));
  };
  while (rest.length) {
    const side = Math.min(w, h);
    const row = [rest.shift()];
    while (rest.length && worst([...row, rest[0]], side) <= worst(row, side)) row.push(rest.shift());
    const s = row.reduce((a, r) => a + r.area, 0);
    if (w >= h) {  // column on the left
      const cw = s / h; let cy = y;
      row.forEach(r => { const rh = r.area / cw; out.push({ ...r, x, y: cy, w: cw, h: rh }); cy += rh; });
      x += cw; w -= cw;
    } else {       // row on top
      const rh = s / w; let cx = x;
      row.forEach(r => { const rw = r.area / rh; out.push({ ...r, x: cx, y, w: rw, h: rh }); cx += rw; });
      y += rh; h -= rh;
    }
  }
  return out;
}

async function dmLoad(path) {
  if (path !== undefined) DM.path = path;
  clearTimeout(DM.poll);
  const seq = ++DM.seq;
  const r = await api(`/api/diskmap?source=${DM.src}&path=${encodeURIComponent(DM.path)}`);
  if (seq !== DM.seq || $("#dmModal").hidden) return;  // a newer request (or closing) superseded this one
  if (r.path) DM.path = r.path;
  DM.data = r;
  dmRender();
  if (r.pending) DM.poll = setTimeout(() => dmLoad(), 1500);
}

function dmRender() {
  const r = DM.data, map = $("#dmMap");
  const parts = DM.path.split("/").filter(Boolean);
  const crumbs = DM.src === "local" ? [["Macintosh HD", "/"]] : [];
  parts.forEach((p, i) => { const full = "/" + parts.slice(0, i + 1).join("/"); if (DM.src === "local" || i >= 1) crumbs.push([p, full]); });
  $("#dmCrumbs").innerHTML = crumbs.map(([n, p]) => `<a data-p="${esc(p)}">${esc(n)}</a>`).join("<span>›</span>");
  $$("#dmSrc button").forEach(b => b.classList.toggle("on", b.dataset.s === DM.src));
  if (r.pending) {
    $("#dmSub").textContent = "";
    map.innerHTML = `<div class="dm-wait"><div><span class="spin"></span> Liczę rozmiary folderów…<br><small>Duże foldery (np. Twój katalog domowy) mogą zająć minutę — wynik jest zapamiętywany na 30 min.</small></div></div>`;
    return;
  }
  const items = (r.items || []).filter(i => i.size > 0).sort((a, b) => b.size - a.size);
  const shown = items.slice(0, 60), restSize = items.slice(60).reduce((a, i) => a + i.size, 0);
  if (restSize) shown.push({ name: `${items.length - 60} mniejszych`, size: restSize, path: "", dir: false, system: true });
  $("#dmSub").innerHTML = `<b>${fmtSize(r.total || 0)}</b> w ${items.length} elementach` + (r.at ? ` · policzone ${new Date(r.at * 1000).toLocaleTimeString("pl-PL")}` : "") +
    (DM.src === "nas" ? " · tylko pliki znane Lupie" : "");
  if (!shown.length) { map.innerHTML = `<div class="dm-wait">Pusto — albo brak dostępu do tego folderu.</div>`; return; }
  const W = map.clientWidth, H = map.clientHeight;
  map.innerHTML = squarify(shown, 0, 0, W, H).map((t, k) => {
    const cls = ["dm-t", t.system ? "sys" : "", t.dir ? "" : "file", t.w < 70 || t.h < 34 ? "small" : ""].join(" ");
    const pct = r.total ? (t.size / r.total * 100).toFixed(1) : 0;
    return `<div class="${cls}" data-p="${esc(t.path)}" data-dir="${t.dir ? 1 : 0}" title="${esc(t.name)} — ${fmtSize(t.size)} (${pct}%)"
      style="left:${t.x}px;top:${t.y}px;width:${t.w}px;height:${t.h}px;background:${DM_COLORS[k % DM_COLORS.length]}">
      <b>${esc(t.name)}</b><span>${fmtSize(t.size)} · ${pct}%</span></div>`;
  }).join("");
}

function dmUp() {
  if (DM.path === "/" || (DM.src === "nas" && DM.path.split("/").length <= 3)) return;
  dmLoad(DM.path.replace(/\/[^/]+$/, "") || "/");
}
$("#dmOpen").addEventListener("click", () => { $("#dmModal").hidden = false; dmLoad(DM.src === "local" ? "/" : ""); });
$("#dmClose").addEventListener("click", () => { $("#dmModal").hidden = true; clearTimeout(DM.poll); });
$("#dmBack").addEventListener("click", dmUp);
$("#dmCrumbs").addEventListener("click", e => { const a = e.target.closest("a"); if (a) dmLoad(a.dataset.p); });
$("#dmSrc").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; DM.src = b.dataset.s; dmLoad(DM.src === "local" ? "/" : ""); });
$("#dmMap").addEventListener("click", e => {
  const t = e.target.closest(".dm-t"); if (!t || !t.dataset.p) return;
  if (e.altKey || t.dataset.dir !== "1") return api("/api/diskmap/reveal", { method: "POST", json: { path: t.dataset.p } }).catch(() => toast("Nie można pokazać tego miejsca"));
  dmLoad(t.dataset.p);
});
document.addEventListener("keydown", e => {
  if ($("#dmModal").hidden) return;
  if (e.key === "Escape") { e.stopPropagation(); $("#dmClose").click(); }
  if (e.key === "Backspace") { e.preventDefault(); dmUp(); }
}, true);
window.addEventListener("resize", () => { if (!$("#dmModal").hidden && DM.data && !DM.data.pending) dmRender(); });
