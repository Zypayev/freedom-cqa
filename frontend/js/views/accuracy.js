import { api } from "../api.js";
import { $, esc, fmtScore } from "../ui.js";

let checklistId = null;

export async function renderAccuracy(main) {
  const { checklists } = await api.checklists();
  checklistId ||= (checklists.find((c) => c.is_active) || checklists[0])?.id;
  const acc = await api.accuracy(checklistId);
  const pct = (v) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);
  const tone = (v) => (v === null || v === undefined ? "" : v >= 0.85 ? "good" : v >= 0.7 ? "warn" : "bad");

  main.innerHTML = `
    <div class="stack">
      <div class="row">
        <h1>Точность относительно эталона</h1>
        <select id="ac-cl">${checklists.map((c) => `<option value="${c.id}" ${c.id === checklistId ? "selected" : ""}>${esc(c.name)} · v${c.version}</option>`).join("")}</select>
      </div>
      <div class="small muted">Эталон размечается вручную в карточке диалога («Режим эталонной разметки»). Сравниваются последние оценки выбранной версии чек-листа.
        Используйте после каждой правки чек-листа или промпта, чтобы видеть, стало ли лучше.</div>
      <div class="kpis">
        <div class="kpi"><div class="t">Размечено диалогов</div><div class="v">${acc.labeled_dialogs ?? 0}</div></div>
        <div class="kpi"><div class="t">Совпадение ответов по критериям</div><div class="v">${pct(acc.overall_accuracy)}</div></div>
        <div class="kpi"><div class="t">Средняя ошибка итога (MAE)</div><div class="v">${acc.total_mae ?? "—"}</div><div class="t">баллов из 100</div></div>
      </div>
      ${acc.labeled_dialogs ? `
      <div class="table-wrap"><table>
        <thead><tr><th>Критерий</th><th class="num">Размечено</th><th class="num">Совпадение</th><th class="num">Ошибки N/A</th><th class="num">Ср. ошибка шкалы</th><th>Расхождения (эталон → ИИ)</th></tr></thead>
        <tbody>${acc.criteria.map((c) => `<tr>
          <td>${esc(c.title)}</td><td class="num">${c.n}</td>
          <td class="num"><span class="badge ${tone(c.accuracy)}">${pct(c.accuracy)}</span></td>
          <td class="num">${c.na_errors || ""}</td>
          <td class="num">${c.n ? fmtScore(c.abs_err / c.n) : "—"}</td>
          <td class="small">${c.errors.map((e) => `<a class="mono" href="#/dialog/${encodeURIComponent(e.dialog_id)}">${esc(e.dialog_id)}</a>: ${esc(e.gold)} → ${esc(e.pred)}`).join("<br>")}</td>
        </tr>`).join("")}</tbody>
      </table></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Диалог</th><th class="num">Итог по эталону</th><th class="num">Итог ИИ</th><th class="num">Разница</th></tr></thead>
        <tbody>${acc.dialogs.map((d) => `<tr onclick="location.hash='#/dialog/${encodeURIComponent(d.dialog_id)}'">
          <td class="mono">${esc(d.dialog_id)}</td><td class="num">${fmtScore(d.gold_total)}</td><td class="num">${fmtScore(d.pred_total)}</td>
          <td class="num">${d.gold_total !== null && d.pred_total !== null ? fmtScore(d.pred_total - d.gold_total) : "—"}</td></tr>`).join("")}</tbody>
      </table></div>` : `<div class="panel empty">Пока нет размеченных диалогов с оценкой этой версии чек-листа.</div>`}
    </div>`;

  $("#ac-cl").addEventListener("change", (e) => { checklistId = Number(e.target.value); renderAccuracy(main); });
}
