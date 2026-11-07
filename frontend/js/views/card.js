import { api } from "../api.js";
import {
  $, $$, CONF, SPEAKER, allowedAnswers, answerLabel, answerTone, channelLabel, esc, fmtCost, fmtDate, fmtScore,
  role, scoreTone, toast,
} from "../ui.js";

const selectedEval = {}; // dialogId → evaluationId
let goldMode = false;

export async function renderCard(main, dialogId) {
  const [dialog, { checklists }] = await Promise.all([api.dialog(dialogId), api.checklists()]);
  const evals = dialog.evaluations;
  let evalId = selectedEval[dialogId] ?? evals[0]?.id;
  if (evalId && !evals.some((e) => e.id === evalId)) evalId = evals[0]?.id;
  const ev = evalId ? await api.evaluation(evalId) : null;
  const active = checklists.find((c) => c.is_active) || checklists[0];
  const checklist = ev ? ev.checklist.data : (await api.checklist(active.id)).data;
  let gold = {};
  if (goldMode) gold = (await api.gold(dialogId, checklist.slug)).labels;
  const isCall = (dialog.channel || "").includes("call");

  main.innerHTML = `
    <div class="stack">
      <div class="panel">
        <div class="row">
          <a href="#/dialogs">← Диалоги</a>
          <h1 class="mono" style="font-size:18px">${esc(dialog.id)}</h1>
          <span class="badge">${esc(dialog.language)}</span>
          <span class="badge">${channelLabel(dialog.channel)}</span>
          <span class="badge">${esc(dialog.dataset)}</span>
          <span class="muted small">${dialog.turns.length} реплик</span>
          <span class="grow"></span>
          <label class="row small"><input type="checkbox" id="c-gold" ${goldMode ? "checked" : ""}> Режим эталонной разметки</label>
        </div>
        <div class="row" style="margin-top:10px">
          <label class="field">Чек-лист для новой оценки
            <select id="c-checklist">${checklists.map((c) => `<option value="${c.id}" ${c.id === active.id ? "selected" : ""}>${esc(c.name)} · v${c.version}${c.is_active ? " (активный)" : ""}</option>`).join("")}</select>
          </label>
          <label class="field">Прогонов
            <select id="c-votes"><option value="1">1</option><option value="3">3</option><option value="5">5</option></select>
          </label>
          <button class="primary" id="c-run" style="margin-top:16px">${evals.length ? "Переоценить" : "Оценить"}</button>
          <span id="c-run-status" class="small muted" style="margin-top:16px"></span>
          <span class="grow"></span>
          ${evals.length ? `<label class="field">История оценок
            <select id="c-history">${evals.map((e) => `<option value="${e.id}" ${e.id === evalId ? "selected" : ""}>#${e.id} · ${fmtDate(e.created_at)} · v${e.checklist_version} · ${e.status === "done" ? fmtScore(e.final_total) : "ошибка"}</option>`).join("")}</select>
          </label>` : ""}
        </div>
      </div>

      <div class="card-grid">
        <div class="panel sticky" style="padding:10px">
          <div class="row" style="padding:4px 6px 8px">
            <h2 style="margin:0">Транскрипт</h2><span class="grow"></span>
            <span class="small muted">Клик по ссылке в обосновании ведёт к реплике</span>
          </div>
          ${isCall ? audioBlock(dialog) : ""}
          <div class="transcript" id="c-transcript">${dialog.turns.map(turnHtml(isCall)).join("")}</div>
        </div>
        <div class="stack" id="c-result">${resultHtml(ev, checklist, dialog, gold)}</div>
      </div>
    </div>`;

  bind(main, dialog, ev, checklist, gold);
}

function audioBlock(dialog) {
  if (!dialog.audio_url) return `<div class="small muted" style="padding:0 6px 8px">Запись звонка недоступна</div>`;
  return `<div style="padding:0 6px">
    <audio class="audio" id="c-audio" controls preload="metadata" src="${esc(dialog.audio_url)}"></audio>
    <div class="row small muted" style="margin-bottom:8px">
      <a href="${esc(dialog.audio_url)}" target="_blank" rel="noopener">Открыть запись</a>
      <span class="grow"></span>
      <label class="row" title="Если запись начинается раньше первой реплики">Сдвиг записи, сек <input id="c-shift" type="number" value="0" step="0.5" style="width:70px"></label>
    </div>
  </div>`;
}

const turnHtml = (isCall) => (t) => `
  <div class="turn ${esc(t.speaker)}" id="turn-${t.id}" data-offset="${t.offset_sec ?? ""}">
    <div class="tid">${t.id}</div>
    <div>
      <div class="who">${SPEAKER[t.speaker] || esc(t.speaker)}
        <span class="ts">${esc(t.timestamp || "")}</span>
        ${isCall && t.offset_sec !== null ? `<button class="link seek" data-seek="${t.id}">▶ ${fmtOffset(t.offset_sec)}</button>` : ""}
      </div>
      <div class="txt">${esc(t.text)}</div>
    </div>
  </div>`;

function fmtOffset(s) {
  s = Math.max(0, Math.round(s));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function resultHtml(ev, checklist, dialog, gold) {
  if (!ev) {
    return `<div class="panel empty">Диалог ещё не оценён. Выберите чек-лист и нажмите «Оценить».${goldMode ? goldOnlyHtml(checklist, dialog, gold) : ""}</div>`;
  }
  const res = ev.result;
  if (res.status !== "done") {
    return `<div class="panel"><h2>Оценка не выполнена</h2><div style="color:var(--bad)">${esc(res.error)}</div></div>${logHtml(res)}`;
  }
  const view = ev.view;
  const final = view.final.score;
  const orig = res.score;
  const changed = final.total !== orig.total;
  const critById = Object.fromEntries(checklist.criteria.map((c) => [c.id, c]));
  const meta = res.meta || {};
  const appealsBy = {};
  for (const a of view.appeals) (appealsBy[a.criterion_id] ||= []).push(a);

  const head = `
    <div class="panel">
      <div class="score-head">
        <div>
          <div class="small muted">${changed ? "Финальный итог (после апелляций)" : "Итоговый балл"}</div>
          <div class="score-big ${scoreTone(final.total)}">${fmtScore(final.total)} <small>/ 100</small></div>
          ${changed ? `<div class="small muted">Исходный: <span class="strike">${fmtScore(orig.total)}</span></div>` : ""}
        </div>
        <div class="stack" style="gap:6px">
          ${final.gross_error ? `<span class="badge bad">⚠ Грубая ошибка</span>` : `<span class="badge good">Грубых ошибок нет</span>`}
          ${res.needs_review.length ? `<span class="badge warn">Нужна ручная проверка: ${res.needs_review.length}</span>` : ""}
        </div>
        <span class="grow"></span>
        <div class="small muted" style="text-align:right">
          Чек-лист v${esc(ev.checklist.version)} · ${esc(meta.model || "")}${meta.votes > 1 ? ` · ${meta.votes} прогона` : ""}<br>
          ${meta.calls} вызов(а) · ${(meta.usage?.input_tokens || 0) + (meta.usage?.cache_read_input_tokens || 0) + (meta.usage?.cache_creation_input_tokens || 0)} вх. / ${meta.usage?.output_tokens || 0} вых. токенов · <b>${fmtCost(meta.cost_usd)}</b><br>
          ${fmtDate(meta.finished_at)}
        </div>
      </div>
      ${res.summary ? `<p style="margin:12px 0 0">${esc(res.summary)}</p>` : ""}
      <div class="sections">${final.sections.map((s) => `
        <div class="sec">
          <div class="t">${esc(s.title)}</div>
          ${s.points !== null && s.points !== undefined ? `<div class="v">${fmtScore(s.points)} / ${fmtScore(s.max_points)}</div><div class="bar"><i style="width:${s.max_points ? (s.points / s.max_points * 100) : 0}%"></i></div>`
            : s.flagged !== undefined ? `<div class="v" style="color:${s.flagged ? "var(--bad)" : "var(--good)"}">${s.flagged ? "Выявлены" : "Нет"}</div>` : `<div class="v muted">N/A</div>`}
        </div>`).join("")}
      </div>
      ${final.applicable_weight < 100 ? `<div class="small muted" style="margin-top:8px">N/A исключены, вес ${100 - final.applicable_weight} перераспределён пропорционально между применимыми критериями (применимый вес ${final.applicable_weight} → 100).</div>` : ""}
    </div>`;

  const crits = res.criteria.map((r) => critHtml(r, critById[r.criterion_id], final.criteria[r.criterion_id], view, appealsBy[r.criterion_id] || [], gold)).join("");
  return head + `<div class="stack">${crits}</div>` + factsHtml(res.facts) + logHtml(res) +
    (goldMode ? `<div class="panel row"><span class="grow small muted">Эталон сохраняется для чек-листа «${esc(checklist.slug)}» и используется на странице «Точность».</span><button class="primary" id="c-gold-save">Сохранить эталон</button></div>` : "");
}

function critHtml(r, c, finalEntry, view, appeals, gold) {
  if (!c) return "";
  const finalAnswer = view.final.answers[r.criterion_id];
  const overridden = finalAnswer !== r.answer;
  const tone = answerTone(c, finalAnswer);
  const pts = c.type === "flag" ? `<span class="pts">без баллов</span>` :
    finalAnswer === "na" ? `<span class="pts">вес ${c.weight} перераспределён</span>` :
      `<span class="pts">${fmtScore(finalEntry.points)} / ${fmtScore(finalEntry.max_points)} б.${finalEntry.max_points !== c.weight ? ` <span class="small" title="Базовый вес до перераспределения">(вес ${c.weight})</span>` : ""}</span>`;
  const ev = r.evidence_turn_ids || [];
  const [from, to] = r.checked_range || [];
  const openAppeal = appeals.find((a) => a.status === "open");
  const gross = c.type === "flag" && finalAnswer === "yes";
  return `
    <div class="crit ${r.confidence === "low" ? "low" : ""} ${gross ? "gross-hit" : ""}" data-crit="${esc(c.id)}" data-ev="${esc(ev.join(","))}">
      <div class="crit-head">
        <span class="num">${esc(c.number ?? "")}</span>
        <span class="title">${esc(c.title)}</span>
        ${overridden ? `<span class="badge strike">${esc(answerLabel(c, r.answer))}</span>` : ""}
        <span class="badge ${tone}">${esc(answerLabel(c, finalAnswer))}</span>
        ${pts}
      </div>
      <div class="expl">${esc(r.explanation)}</div>
      ${finalAnswer === "na" && r.na_reason ? `<div class="small"><b>Причина N/A:</b> ${esc(r.na_reason)}</div>` : ""}
      <div class="chips">
        ${ev.length ? `<span class="muted">Доказательства:</span>${ev.map((t) => `<span class="chip" data-go="${t}">${t}</span>`).join("")}` : ""}
        ${r.expected_at_turn_id ? `<span class="muted">Ожидалось в:</span><span class="chip expected" data-go="${r.expected_at_turn_id}">${r.expected_at_turn_id}</span>` : ""}
        ${from && to ? `<span class="muted">Проверено:</span><span class="chip expected" data-range="${from},${to}">${from}–${to}</span>` : ""}
        <span class="grow"></span>
        ${r.confidence !== "high" ? `<span class="badge ${r.confidence === "low" ? "warn" : ""}">${CONF[r.confidence]}</span>` : ""}
        ${r.votes && Object.keys(r.votes).length > 1 ? `<span class="small muted" title="Голоса прогонов">${Object.entries(r.votes).map(([a, n]) => `${esc(answerLabel(c, a))}: ${n}`).join(" · ")}</span>` : ""}
      </div>
      <details class="more"><summary>Разбор модели</summary><div class="analysis">${esc(r.analysis)}</div></details>
      ${appeals.map(appealHtml(c)).join("")}
      ${role() === "operator" && !openAppeal ? `
        <div class="appeal-form hidden" data-form="${esc(c.id)}">
          <textarea rows="2" placeholder="Почему вы не согласны с оценкой? Укажите реплики."></textarea>
          <div class="row" style="margin-top:6px"><button class="primary" data-submit="${esc(c.id)}">Отправить апелляцию</button><button data-cancel="${esc(c.id)}">Отмена</button></div>
        </div>
        <div class="row" style="margin-top:6px"><button class="link" data-appeal="${esc(c.id)}">Оспорить оценку</button></div>` : ""}
      ${goldMode ? `<div class="row small" style="margin-top:8px;border-top:1px dashed var(--border);padding-top:8px">
        <b>Эталон:</b>
        <select data-gold="${esc(c.id)}"><option value="">—</option>${allowedAnswers(c).map((a) => `<option value="${a}" ${gold[c.id] === a ? "selected" : ""}>${esc(answerLabel(c, a))}</option>`).join("")}</select>
        ${gold[c.id] ? (gold[c.id] === r.answer ? `<span class="badge good">совпадает</span>` : `<span class="badge bad">расхождение</span>`) : ""}
      </div>` : ""}
    </div>`;
}

const appealHtml = (c) => (a) => `
  <div class="appeal-box ${a.status}">
    <div class="row"><b>Апелляция #${a.id}</b><span class="badge ${a.status === "open" ? "warn" : "accent"}">${a.status === "open" ? "на рассмотрении" : "рассмотрена"}</span>
      <span class="grow"></span><span class="small muted">${fmtDate(a.created_at)}</span></div>
    <div style="margin-top:4px">«${esc(a.comment)}» <span class="small muted">— ${esc(a.author)}</span></div>
    ${a.decision ? `<div style="margin-top:6px">
      <b>${a.decision.action === "confirm" ? "Оценка подтверждена" : `Оценка изменена: ${esc(answerLabel(c, a.decision.old_answer))} → ${esc(answerLabel(c, a.decision.new_answer))}`}</b>
      <div>${esc(a.decision.reason)} <span class="small muted">— ${esc(a.decision.reviewer)}, ${fmtDate(a.decided_at)}</span></div>
    </div>` : ""}
  </div>`;

function goldOnlyHtml(checklist, dialog, gold) {
  return `<div class="stack" style="text-align:left;margin-top:16px">${checklist.criteria.map((c) => `
    <div class="row"><span class="grow">${esc(c.number)}. ${esc(c.title)}</span>
      <select data-gold="${esc(c.id)}"><option value="">—</option>${allowedAnswers(c).map((a) => `<option value="${a}" ${gold[c.id] === a ? "selected" : ""}>${esc(answerLabel(c, a))}</option>`).join("")}</select></div>`).join("")}
    <button class="primary" id="c-gold-save">Сохранить эталон</button></div>`;
}

function factsHtml(f) {
  if (!f) return "";
  const waits = (f.long_waits || []).map((w) => `${fmtOffset(w.seconds)} (${w.from_turn}→${w.reply_turn}${w.warned_hint ? ", предупредил" : ""})`).join("; ");
  const items = [
    ["Язык клиента (авто)", f.client_language_detected],
    ["Язык 1-го ответа оператора", f.first_operator_language_detected],
    ["Первая реакция", f.first_response_sec !== null ? `${fmtOffset(f.first_response_sec)}${f.first_response_exceeded ? " — превышен порог" : ""}` : "—"],
    ["Долгие ожидания", waits || "нет"],
    ["Реплик по ролям", Object.entries(f.turns_by_speaker || {}).map(([k, v]) => `${SPEAKER[k] || k}: ${v}`).join(", ")],
    ["Длительность", f.duration_sec !== null ? fmtOffset(f.duration_sec) : "—"],
  ];
  if (f.is_call) items.push(["Темп речи оператора", f.operator_words_per_minute ? `${f.operator_words_per_minute} сл/мин` : "—"]);
  return `<details class="panel"><summary><b>Факты, посчитанные кодом</b> <span class="small muted">— передаются модели как точные данные</span></summary>
    <div class="facts" style="margin-top:10px">${items.map(([k, v]) => `<div><div class="k">${esc(k)}</div>${esc(v)}</div>`).join("")}</div></details>`;
}

function logHtml(res) {
  const calls = res.llm_log || [];
  if (!calls.length) return "";
  return `<details class="panel"><summary><b>Журнал оценки</b> <span class="small muted">— каждый вызов модели, токены, стоимость, сырой ответ</span></summary>
    ${calls.map((c, i) => `<div style="margin-top:10px">
      <div class="small"><b>Вызов ${i + 1}</b> · попытка ${c.attempt} · ${esc(c.provider || "")} ${esc(c.model || "")} · ${c.duration_sec ?? "—"} c ·
        ${c.usage ? `${c.usage.input_tokens} вх. (${c.usage.cache_read_input_tokens || 0} из кэша) / ${c.usage.output_tokens} вых.` : ""} · ${fmtCost(c.cost_usd)} · ${esc(c.started_at || "")}</div>
      ${c.error ? `<div style="color:var(--bad)">${esc(c.error)}</div>` : ""}
      ${c.issues && c.issues.length ? `<div class="small" style="color:var(--warn)">Замечания валидации (отправлены на исправление): ${esc(c.issues.join("; "))}</div>` : ""}
      ${c.raw ? `<pre class="raw">${esc(pretty(c.raw))}</pre>` : ""}
    </div>`).join("")}</details>`;
}

function pretty(raw) {
  try { return JSON.stringify(JSON.parse(raw), null, 2); } catch { return raw; }
}

function bind(main, dialog, ev, checklist, gold) {
  const transcript = $("#c-transcript", main);
  const audio = $("#c-audio", main);

  const flash = (ids) => {
    $$(".turn.flash", transcript).forEach((el) => el.classList.remove("flash"));
    const els = ids.map((id) => document.getElementById(`turn-${id}`)).filter(Boolean);
    els.forEach((el) => el.classList.add("flash"));
    if (els[0]) els[0].scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => els.forEach((el) => el.classList.remove("flash")), 2600);
    return els[0];
  };
  const seek = (el) => {
    if (!audio || !el || el.dataset.offset === "") return;
    const shift = Number($("#c-shift", main)?.value || 0);
    audio.currentTime = Math.max(0, Number(el.dataset.offset) + shift);
    audio.play().catch(() => {});
  };

  // Обработчик на корневом элементе этой отрисовки (не на main), чтобы не копились при перерисовке.
  main.firstElementChild.addEventListener("click", async (e) => {
    const go = e.target.closest("[data-go]");
    if (go) { seek(flash([go.dataset.go])); return; }
    const range = e.target.closest("[data-range]");
    if (range) {
      const [a, b] = range.dataset.range.split(",");
      const ids = dialog.turns.map((t) => t.id);
      flash(ids.slice(ids.indexOf(a), ids.indexOf(b) + 1));
      return;
    }
    const sk = e.target.closest("[data-seek]");
    if (sk) { seek(document.getElementById(`turn-${sk.dataset.seek}`)); return; }
    const ap = e.target.closest("[data-appeal]");
    if (ap) {
      $(`[data-form="${ap.dataset.appeal}"]`, main).classList.remove("hidden");
      ap.classList.add("hidden");
      $(`[data-form="${ap.dataset.appeal}"] textarea`, main).focus();
      return;
    }
    const cancel = e.target.closest("[data-cancel]");
    if (cancel) {
      $(`[data-form="${cancel.dataset.cancel}"]`, main).classList.add("hidden");
      $(`[data-appeal="${cancel.dataset.cancel}"]`, main).classList.remove("hidden");
      return;
    }
    const submit = e.target.closest("[data-submit]");
    if (submit) {
      const text = $(`[data-form="${submit.dataset.submit}"] textarea`, main).value.trim();
      if (!text) return toast("Добавьте комментарий", "error");
      try {
        await api.createAppeal(ev.id, submit.dataset.submit, text);
        toast("Апелляция отправлена проверяющему", "good");
        selectedEval[dialog.id] = ev.id;
        renderCard(main, dialog.id);
      } catch (err) { toast(err.message, "error"); }
      return;
    }
    if (e.target.id === "c-gold-save") {
      const labels = {};
      $$("[data-gold]", main).forEach((s) => { if (s.value) labels[s.dataset.gold] = s.value; });
      try {
        await api.saveGold(dialog.id, checklist.slug, labels);
        toast(`Эталон сохранён (${Object.keys(labels).length} критериев)`, "good");
        renderCard(main, dialog.id);
      } catch (err) { toast(err.message, "error"); }
    }
  });

  // Подсветка доказательств при наведении на критерий
  $$(".crit", main).forEach((card) => {
    const ids = (card.dataset.ev || "").split(",").filter(Boolean);
    card.addEventListener("mouseenter", () => ids.forEach((id) => document.getElementById(`turn-${id}`)?.classList.add("evidence")));
    card.addEventListener("mouseleave", () => ids.forEach((id) => document.getElementById(`turn-${id}`)?.classList.remove("evidence")));
  });

  $("#c-history", main)?.addEventListener("change", (e) => {
    selectedEval[dialog.id] = Number(e.target.value);
    renderCard(main, dialog.id);
  });
  $("#c-gold", main).addEventListener("change", (e) => { goldMode = e.target.checked; renderCard(main, dialog.id); });

  $("#c-run", main).addEventListener("click", async () => {
    const btn = $("#c-run", main);
    const status = $("#c-run-status", main);
    btn.disabled = true;
    try {
      const job = await api.createJob({ dialog_ids: [dialog.id], checklist_id: Number($("#c-checklist", main).value), votes: Number($("#c-votes", main).value) });
      const started = Date.now();
      const poll = async () => {
        const j = await api.job(job.id);
        if (j.status === "running") {
          status.innerHTML = `<span class="spinner"></span> Оценка идёт… ${Math.round((Date.now() - started) / 1000)} с`;
          setTimeout(poll, 1500);
          return;
        }
        if (j.failed.length) toast(`Ошибка оценки: ${j.failed[0].error}`, "error");
        else toast(`Готово: ${fmtScore(j.done[0].total)} баллов`, "good");
        selectedEval[dialog.id] = (j.done[0] || j.failed[0])?.evaluation_id;
        if (location.hash.includes(encodeURIComponent(dialog.id)) || location.hash.includes(dialog.id)) renderCard(main, dialog.id);
      };
      poll();
    } catch (err) {
      toast(err.message, "error");
      btn.disabled = false;
    }
  });
}
