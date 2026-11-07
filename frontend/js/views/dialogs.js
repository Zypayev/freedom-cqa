import { api } from "../api.js";
import { $, channelLabel, esc, fmtCost, fmtScore, scoreTone, toast } from "../ui.js";

let state = { dataset: "", search: "", lang: "", channel: "", flag: "" };

export async function renderDialogs(main) {
  const [{ dialogs, datasets }, { checklists }] = await Promise.all([api.dialogs(), api.checklists()]);
  if (state.dataset && !datasets.includes(state.dataset)) state.dataset = "";
  const active = checklists.find((c) => c.is_active) || checklists[0];

  main.innerHTML = `
    <div class="stack">
      <div class="panel">
        <div class="row">
          <h2 style="margin:0">Загрузка данных</h2>
          <span class="grow"></span>
        </div>
        <div class="row" style="margin-top:10px">
          <input id="d-group" placeholder="group_id, например DEV" style="width:200px">
          <input id="d-url" placeholder="Адрес GET (если не задан в .env)" class="grow" style="min-width:220px">
          <button id="d-fetch" class="primary">Загрузить из API</button>
          <span class="muted small">или</span>
          <label class="btn">Файл JSON <input id="d-file" type="file" accept=".json,application/json" class="hidden"></label>
        </div>
      </div>

      <div class="panel">
        <div class="row">
          <h2 style="margin:0">Оценка набора</h2>
          <span class="grow"></span>
        </div>
        <div class="row" style="margin-top:10px">
          <label class="field">Набор
            <select id="b-dataset">${datasets.map((d) => `<option ${d === state.dataset ? "selected" : ""}>${esc(d)}</option>`).join("")}</select>
          </label>
          <label class="field">Чек-лист
            <select id="b-checklist">${checklists.map((c) => `<option value="${c.id}" ${c.id === active?.id ? "selected" : ""}>${esc(c.name)} · v${c.version}${c.is_active ? " (активный)" : ""}</option>`).join("")}</select>
          </label>
          <label class="field">Прогонов на диалог
            <select id="b-votes"><option value="1">1 — быстро</option><option value="3">3 — голосование</option><option value="5">5 — максимум точности</option></select>
          </label>
          <label class="row small" style="margin-top:16px"><input type="checkbox" id="b-missing"> только неоценённые</label>
          <button id="b-run" class="primary" style="margin-top:16px" ${datasets.length ? "" : "disabled"}>Оценить набор</button>
          <a class="btn" style="margin-top:16px" id="b-export" href="#">Экспорт CSV</a>
        </div>
        <div id="b-progress" class="small muted" style="margin-top:8px"></div>
      </div>

      <div class="row">
        <select id="f-dataset"><option value="">Все наборы</option>${datasets.map((d) => `<option ${d === state.dataset ? "selected" : ""}>${esc(d)}</option>`).join("")}</select>
        <select id="f-lang"><option value="">Все языки</option><option>RU</option><option>KZ</option><option>EN</option></select>
        <select id="f-channel"><option value="">Все каналы</option><option value="chat">Чаты</option><option value="call">Звонки</option></select>
        <select id="f-flag">
          <option value="">Все</option><option value="gross">Грубые ошибки</option><option value="review">Нужна проверка</option>
          <option value="appeal">Открытые апелляции</option><option value="none">Не оценены</option>
        </select>
        <input id="f-search" placeholder="Поиск по ID" value="${esc(state.search)}">
        <span class="grow"></span>
        <span class="muted small" id="f-count"></span>
      </div>
      <div class="table-wrap"><table>
        <thead><tr>
          <th>ID диалога</th><th>Набор</th><th>Язык</th><th>Канал</th><th class="num">Реплик</th>
          <th class="num">Итог</th><th>Флаги</th><th>Чек-лист</th><th>Оценён</th><th class="num">Стоимость</th>
        </tr></thead>
        <tbody id="d-rows"></tbody>
      </table></div>
    </div>`;

  for (const [id, key] of [["#f-lang", "lang"], ["#f-channel", "channel"], ["#f-flag", "flag"]]) $(id).value = state[key];

  const draw = () => {
    const rows = dialogs.filter((d) =>
      (!state.dataset || d.dataset === state.dataset) &&
      (!state.lang || d.language === state.lang) &&
      (!state.channel || (state.channel === "call" ? (d.channel || "").includes("call") : !(d.channel || "").includes("call"))) &&
      (!state.search || d.id.toLowerCase().includes(state.search.toLowerCase())) &&
      (!state.flag || (state.flag === "gross" && d.gross_error) || (state.flag === "review" && d.needs_review > 0) ||
        (state.flag === "appeal" && d.open_appeals > 0) || (state.flag === "none" && !d.evaluation_id)));
    $("#f-count").textContent = `${rows.length} из ${dialogs.length}`;
    $("#d-rows").innerHTML = rows.length ? rows.map((d) => {
      const changed = d.final_total !== null && d.total !== null && d.final_total !== d.total;
      const total = d.status === "error" ? `<span class="badge bad">ошибка</span>` :
        d.evaluation_id ? `${changed ? `<span class="strike small">${fmtScore(d.total)}</span> ` : ""}<span class="badge ${scoreTone(d.final_total)}">${fmtScore(d.final_total)}</span>` : `<span class="muted">—</span>`;
      const flags = [
        d.gross_error ? `<span class="badge bad">грубая ошибка</span>` : "",
        d.needs_review > 0 ? `<span class="badge warn" title="Критерии с низкой уверенностью">проверить: ${d.needs_review}</span>` : "",
        d.open_appeals > 0 ? `<span class="badge accent">апелляция</span>` : "",
      ].join(" ");
      return `<tr data-id="${esc(d.id)}">
        <td class="mono">${esc(d.id)}</td><td>${esc(d.dataset)}</td><td>${esc(d.language)}</td><td>${channelLabel(d.channel)}</td>
        <td class="num">${d.turns}</td><td class="num">${total}</td><td>${flags}</td>
        <td class="small muted">${d.checklist_version ? "v" + d.checklist_version : ""}</td>
        <td class="small muted">${d.evaluated_at ? new Date(d.evaluated_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : ""}</td>
        <td class="num small muted">${d.cost_usd !== null && d.cost_usd !== undefined ? fmtCost(d.cost_usd) : ""}</td>
      </tr>`;
    }).join("") : `<tr><td colspan="10" class="empty">${dialogs.length ? "Ничего не найдено" : "Загрузите набор данных выше"}</td></tr>`;
  };
  draw();

  $("#d-rows").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (tr) location.hash = `#/dialog/${encodeURIComponent(tr.dataset.id)}`;
  });
  $("#f-dataset").addEventListener("change", (e) => { state.dataset = e.target.value; draw(); });
  $("#f-lang").addEventListener("change", (e) => { state.lang = e.target.value; draw(); });
  $("#f-channel").addEventListener("change", (e) => { state.channel = e.target.value; draw(); });
  $("#f-flag").addEventListener("change", (e) => { state.flag = e.target.value; draw(); });
  $("#f-search").addEventListener("input", (e) => { state.search = e.target.value; draw(); });

  $("#d-fetch").addEventListener("click", async () => {
    const g = $("#d-group").value.trim();
    if (!g) return toast("Укажите group_id", "error");
    $("#d-fetch").disabled = true;
    try {
      const r = await api.fetchDataset(g, $("#d-url").value.trim());
      toast(`Загружено ${r.imported} диалогов в набор ${r.dataset}`, "good");
      state.dataset = r.dataset;
      renderDialogs(main);
    } catch (e) { toast(e.message, "error"); } finally { $("#d-fetch") && ($("#d-fetch").disabled = false); }
  });
  $("#d-file").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const payload = JSON.parse(await f.text());
      const r = await api.uploadDataset(payload);
      toast(`Загружено ${r.imported} диалогов в набор ${r.dataset}`, "good");
      state.dataset = r.dataset;
      renderDialogs(main);
    } catch (err) { toast(err.message, "error"); }
  });

  const exportLink = () => { $("#b-export").href = api.exportUrl($("#b-dataset").value, $("#b-checklist").value); };
  exportLink();
  $("#b-dataset").addEventListener("change", exportLink);
  $("#b-checklist").addEventListener("change", exportLink);

  $("#b-run").addEventListener("click", async () => {
    try {
      const job = await api.createJob({
        dataset: $("#b-dataset").value, checklist_id: Number($("#b-checklist").value),
        votes: Number($("#b-votes").value), only_missing: $("#b-missing").checked,
      });
      toast(`Запущена оценка ${job.total} диалогов`, "good");
      watchJob(job.id, main);
    } catch (e) { toast(e.message, "error"); }
  });
}

function watchJob(id, main) {
  const tick = async () => {
    const el = document.getElementById("b-progress");
    let job;
    try { job = await api.job(id); } catch { return; }
    if (el) {
      el.innerHTML = `${job.status === "running" ? '<span class="spinner"></span>' : "✓"} Готово ${job.done.length} из ${job.total}` +
        (job.failed.length ? `, ошибок: ${job.failed.length}` : "") + ` · стоимость ${job.cost_usd.toFixed(4)} $` +
        (job.failed.length ? `<div class="small" style="color:var(--bad)">${job.failed.slice(0, 3).map((f) => esc(f.dialog_id + ": " + f.error)).join("<br>")}</div>` : "");
    }
    if (job.status === "running") setTimeout(tick, 2000);
    else if (location.hash.startsWith("#/dialogs") || !location.hash) renderDialogs(main);
  };
  tick();
}
