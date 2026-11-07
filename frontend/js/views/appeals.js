import { api } from "../api.js";
import { $, $$, SPEAKER, allowedAnswers, answerLabel, answerTone, esc, fmtDate, fmtScore, role, toast } from "../ui.js";

let filter = "open";

export async function renderAppeals(main) {
  const { appeals } = await api.appeals(filter === "all" ? "" : filter);
  // Подгружаем контекст: оценку и диалог для каждой апелляции
  const evalIds = [...new Set(appeals.map((a) => a.evaluation_id))];
  const dialogIds = [...new Set(appeals.map((a) => a.dialog_id))];
  const [evals, dialogs] = await Promise.all([
    Promise.all(evalIds.map((id) => api.evaluation(id))),
    Promise.all(dialogIds.map((id) => api.dialog(id))),
  ]);
  const evBy = Object.fromEntries(evals.map((e) => [e.id, e]));
  const dBy = Object.fromEntries(dialogs.map((d) => [d.id, d]));
  const isReviewer = role() === "reviewer";

  main.innerHTML = `
    <div class="stack">
      <div class="row">
        <h1>Апелляции</h1>
        <select id="a-filter">
          <option value="open" ${filter === "open" ? "selected" : ""}>На рассмотрении</option>
          <option value="resolved" ${filter === "resolved" ? "selected" : ""}>Рассмотренные</option>
          <option value="all" ${filter === "all" ? "selected" : ""}>Все</option>
        </select>
        <span class="grow"></span>
        ${isReviewer ? "" : `<span class="badge warn">Решения принимает проверяющий — переключите роль в шапке</span>`}
      </div>
      ${appeals.length ? appeals.map((a) => appealCard(a, evBy[a.evaluation_id], dBy[a.dialog_id], isReviewer)).join("") :
        `<div class="panel empty">Апелляций нет. Оспорить оценку можно в карточке диалога.</div>`}
    </div>`;

  $("#a-filter").addEventListener("change", (e) => { filter = e.target.value; renderAppeals(main); });

  $$("[data-decide]", main).forEach((form) => {
    const aid = Number(form.dataset.decide);
    const actionSel = $("[name=action]", form);
    const answerSel = $("[name=new_answer]", form);
    actionSel.addEventListener("change", () => answerSel.classList.toggle("hidden", actionSel.value !== "change"));
    $("button", form).addEventListener("click", async () => {
      const body = { action: actionSel.value, reason: $("[name=reason]", form).value.trim(), new_answer: answerSel.value };
      if (!body.reason) return toast("Укажите причину решения", "error");
      try {
        const r = await api.decide(aid, body);
        toast(`Решение сохранено. Итог: ${fmtScore(r.total_before)} → ${fmtScore(r.total_after)}`, "good");
        renderAppeals(main);
      } catch (e) { toast(e.message, "error"); }
    });
  });
}

function appealCard(a, ev, dialog, isReviewer) {
  const checklist = ev.checklist.data;
  const c = checklist.criteria.find((x) => x.id === a.criterion_id);
  const orig = a.original;
  const turns = dialog.turns;
  const ids = new Set([...(orig.evidence_turn_ids || []), orig.expected_at_turn_id].filter(Boolean));
  // Фрагмент: реплики-доказательства и по одной соседней
  const idx = turns.map((t, i) => (ids.has(t.id) ? i : -1)).filter((i) => i >= 0);
  const show = new Set();
  idx.forEach((i) => [i - 1, i, i + 1].forEach((j) => j >= 0 && j < turns.length && show.add(j)));
  const fragment = [...show].sort((x, y) => x - y).map((i) => {
    const t = turns[i];
    return `<div class="turn ${esc(t.speaker)} ${ids.has(t.id) ? "evidence" : ""}"><div class="tid">${t.id}</div><div><div class="who">${SPEAKER[t.speaker] || esc(t.speaker)} <span class="ts">${esc(t.timestamp || "")}</span></div><div class="txt">${esc(t.text)}</div></div></div>`;
  }).join("");
  const finalScore = ev.view.final.score.total;

  return `<div class="panel">
    <div class="row">
      <b>#${a.id}</b>
      <a class="mono" href="#/dialog/${encodeURIComponent(a.dialog_id)}">${esc(a.dialog_id)}</a>
      <span>· ${esc(c?.number ?? "")}. ${esc(c?.title ?? a.criterion_id)}</span>
      <span class="badge ${answerTone(c, orig.answer)}">ИИ: ${esc(answerLabel(c, orig.answer))}</span>
      <span class="grow"></span>
      <span class="small muted">Итог оценки: ${fmtScore(finalScore)} · подана ${fmtDate(a.created_at)}</span>
    </div>
    <div class="card-grid" style="margin-top:10px;grid-template-columns:1fr 1fr">
      <div class="stack" style="gap:6px">
        <div><div class="small muted">Обоснование модели</div>${esc(orig.explanation)}</div>
        ${orig.na_reason ? `<div class="small"><b>Причина N/A:</b> ${esc(orig.na_reason)}</div>` : ""}
        <div><div class="small muted">Комментарий заявителя (${esc(a.author)})</div><b>«${esc(a.comment)}»</b></div>
        ${a.decision ? `<div class="appeal-box resolved"><b>${a.decision.action === "confirm" ? "Оценка подтверждена" :
          `Изменено: ${esc(answerLabel(c, a.decision.old_answer))} → ${esc(answerLabel(c, a.decision.new_answer))}`}</b><div>${esc(a.decision.reason)}</div>
          <div class="small muted">${esc(a.decision.reviewer)}, ${fmtDate(a.decided_at)}</div></div>` :
          isReviewer ? `<div class="stack" data-decide="${a.id}" style="gap:6px">
            <div class="row">
              <select name="action"><option value="confirm">Подтвердить оценку</option><option value="change">Изменить оценку</option></select>
              <select name="new_answer" class="hidden">${c ? allowedAnswers(c).filter((x) => x !== orig.answer).map((x) => `<option value="${x}">${esc(answerLabel(c, x))}</option>`).join("") : ""}</select>
            </div>
            <textarea name="reason" rows="2" placeholder="Причина решения (обязательно)"></textarea>
            <div><button class="primary">Сохранить решение</button></div>
          </div>` : `<div class="small muted">Ожидает решения проверяющего</div>`}
      </div>
      <div>
        <div class="small muted" style="margin-bottom:4px">Фрагмент диалога (подсвечены доказательства)</div>
        <div class="transcript" style="max-height:300px">${fragment || `<div class="muted small">Доказательства не указаны</div>`}</div>
      </div>
    </div>
  </div>`;
}
