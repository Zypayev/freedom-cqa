import { api } from "../api.js";
import { $, $$, download, esc, fmtDate, toast } from "../ui.js";

let draft = null;       // редактируемая копия
let draftBase = null;   // id версии, с которой начата правка
let dirty = false;

const TYPE_LABEL = { binary: "Да / Нет", scale: "Шкала", flag: "Флаг (без баллов)" };

function normalize(data) {
  const d = structuredClone(data);
  d.sections ||= [];
  d.criteria = (d.criteria || []).map((c) => ({
    channels: [], na_condition: "", scale: [], ...c,
    instructions: typeof c.instructions === "string" ? { default: c.instructions } : { default: "", ...(c.instructions || {}) },
  }));
  return d;
}

function setPath(obj, path, value) {
  const keys = path.split(".");
  let o = obj;
  keys.slice(0, -1).forEach((k) => { o = o[k]; });
  o[keys.at(-1)] = value;
}

export async function renderChecklists(main, selectedId) {
  const [{ checklists }, { datasets }] = await Promise.all([api.checklists(), api.dialogs()]);
  const sel = Number(selectedId) || draftBase || (checklists.find((c) => c.is_active) || checklists[0])?.id;
  if (!draft || draftBase !== sel) {
    if (dirty && draftBase !== sel && !confirm("Есть несохранённые изменения. Отменить их?")) {
      location.hash = `#/checklists/${draftBase}`;
      return;
    }
    const cl = await api.checklist(sel);
    draft = normalize(cl.data);
    draftBase = sel;
    dirty = false;
  }
  const current = checklists.find((c) => c.id === draftBase);

  main.innerHTML = `
    <div class="editor-grid">
      <div class="panel stack" style="gap:4px">
        <h2>Версии</h2>
        ${checklists.map((c) => `<div class="ver ${c.id === draftBase ? "sel" : ""}" data-ver="${c.id}">
          <div class="row"><b>${esc(c.name)}</b></div>
          <div class="row small muted">v${c.version} · ${fmtDate(c.created_at)} ${c.is_active ? '<span class="badge good">активный</span>' : ""}</div>
          ${c.comment ? `<div class="small muted">${esc(c.comment)}</div>` : ""}
        </div>`).join("")}
        <div class="row" style="margin-top:10px">
          <label class="btn small">Импорт JSON <input type="file" id="e-import" accept=".json" class="hidden"></label>
          <button class="small" id="e-export">Экспорт JSON</button>
        </div>
      </div>

      <div class="stack" id="e-form">
        <div class="panel">
          <div class="row">
            <h2 style="margin:0">Редактор · основа v${esc(current?.version ?? "?")}</h2>
            ${dirty ? '<span class="badge warn">есть изменения</span>' : ""}
            <span class="grow"></span>
            ${current && !current.is_active ? `<button id="e-activate">Сделать активным</button>` : ""}
          </div>
          <div class="grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin-top:10px">
            <label class="field">Название <input data-bind="name" value="${esc(draft.name)}"></label>
            <label class="field">Код (slug) <input data-bind="slug" value="${esc(draft.slug)}" title="Новый slug = новый чек-лист, тот же = новая версия"></label>
            <label class="field">Округление, знаков <input type="number" data-bind="rounding" data-num value="${esc(draft.rounding ?? 2)}"></label>
            <label class="field">Порог первой реакции, сек <input type="number" data-bind="first_response_threshold_sec" data-num value="${esc(draft.first_response_threshold_sec ?? 180)}"></label>
            <label class="field">Порог «долгого ожидания», сек <input type="number" data-bind="wait_threshold_sec" data-num value="${esc(draft.wait_threshold_sec ?? "")}" placeholder="чат 120, звонок 30"></label>
            <label class="field">Язык объяснений <select data-bind="explanation_language">${["ru", "kz", "en"].map((l) => `<option ${draft.explanation_language === l ? "selected" : ""}>${l}</option>`).join("")}</select></label>
          </div>
          <label class="field" style="margin-top:10px">Общие правила оценки <textarea rows="3" data-bind="global_rules">${esc(draft.global_rules || "")}</textarea></label>
          <label class="field" style="margin-top:10px">База знаний (продукты, тарифы, регламенты — для проверки корректности консультации)
            <textarea rows="3" data-bind="knowledge_base" placeholder="Вставьте текст справочника">${esc(draft.knowledge_base || "")}</textarea></label>
        </div>

        <div class="panel">
          <div class="row"><h2 style="margin:0">Секции</h2><span class="grow"></span><button id="e-add-sec">+ Секция</button></div>
          <div class="stack" style="margin-top:10px">${draft.sections.map((s, i) => `
            <div class="row"><input data-bind="sections.${i}.id" value="${esc(s.id)}" style="width:140px" placeholder="id">
              <input data-bind="sections.${i}.title" value="${esc(s.title)}" class="grow" placeholder="Название">
              <button class="danger" data-del-sec="${i}">✕</button></div>`).join("")}</div>
        </div>

        <div class="row"><h2 style="margin:0">Критерии (${draft.criteria.length})</h2>
          <span class="small muted">Сумма весов: ${draft.criteria.filter((c) => c.type !== "flag").reduce((s, c) => s + Number(c.weight || 0), 0)}</span>
          <span class="grow"></span><button id="e-add-crit">+ Критерий</button></div>
        ${draft.criteria.map(critEditor).join("")}

        <div class="panel stack">
          <div class="row">
            <input id="e-comment" class="grow" placeholder="Комментарий к версии, например «Заменён критерий 7»">
            <button class="primary" id="e-save">Сохранить как новую версию</button>
            <button id="e-reset" ${dirty ? "" : "disabled"}>Отменить изменения</button>
          </div>
          <div class="row small">
            <span class="muted">Применить сохранённую версию к набору:</span>
            <select id="e-ds">${datasets.map((d) => `<option>${esc(d)}</option>`).join("")}</select>
            <select id="e-votes"><option value="1">1 прогон</option><option value="3">3 прогона</option></select>
            <button id="e-apply" ${datasets.length ? "" : "disabled"}>Переоценить набор</button>
            <span class="muted">Старые оценки остаются в истории.</span>
          </div>
        </div>
      </div>
    </div>`;

  bind(main, checklists);
}

function critEditor(c, i) {
  const p = `criteria.${i}`;
  return `<div class="crit-edit" data-ci="${i}">
    <div class="row">
      <b class="mono">${esc(c.number ?? i + 1)}</b>
      <input data-bind="${p}.title" value="${esc(c.title)}" class="grow" placeholder="Название критерия" style="font-weight:600">
      <button data-move="${i}:-1" ${i === 0 ? "disabled" : ""} title="Выше">↑</button>
      <button data-move="${i}:1" ${i === draft.criteria.length - 1 ? "disabled" : ""} title="Ниже">↓</button>
      <button class="danger" data-del="${i}" title="Удалить">✕</button>
    </div>
    <div class="grid" style="margin-top:8px">
      <label class="field">id <input data-bind="${p}.id" value="${esc(c.id)}"></label>
      <label class="field">№ <input type="number" data-num data-bind="${p}.number" value="${esc(c.number ?? "")}"></label>
      <label class="field">Секция <select data-bind="${p}.section"><option value="">—</option>${draft.sections.map((s) => `<option value="${esc(s.id)}" ${s.id === c.section ? "selected" : ""}>${esc(s.title)}</option>`).join("")}</select></label>
      <label class="field">Тип ответа <select data-bind="${p}.type" data-rerender>${Object.entries(TYPE_LABEL).map(([k, v]) => `<option value="${k}" ${k === c.type ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label class="field">Вес <input type="number" data-num data-bind="${p}.weight" value="${esc(c.weight)}" ${c.type === "flag" ? "disabled" : ""}></label>
      <label class="field">Каналы
        <span class="row" style="gap:8px;padding-top:6px">
          <label class="row small"><input type="checkbox" data-chan="${i}:chat" ${!c.channels?.length || c.channels.some((x) => !x.includes("call")) ? "checked" : ""}> чат</label>
          <label class="row small"><input type="checkbox" data-chan="${i}:call" ${!c.channels?.length || c.channels.some((x) => x.includes("call")) ? "checked" : ""}> звонок</label>
        </span>
      </label>
    </div>
    <div class="row" style="margin-top:8px">
      <label class="row small"><input type="checkbox" data-bind="${p}.na_allowed" data-bool data-rerender ${c.na_allowed ? "checked" : ""}> Разрешить N/A</label>
      ${c.na_allowed ? `<input data-bind="${p}.na_condition" value="${esc(c.na_condition)}" class="grow" placeholder="Условие, при котором ставится N/A">` : ""}
    </div>
    ${c.type === "scale" ? `<div style="margin-top:8px"><div class="small muted">Ступени шкалы (верхняя даёт полный вес)</div>
      ${c.scale.map((s, j) => `<div class="scale-row"><input type="number" data-num data-bind="${p}.scale.${j}.value" value="${esc(s.value)}">
        <input data-bind="${p}.scale.${j}.label" value="${esc(s.label)}" placeholder="Описание ступени"><button class="danger" data-del-step="${i}:${j}">✕</button></div>`).join("")}
      <button class="small" data-add-step="${i}">+ Ступень</button></div>` : ""}
    <label class="field" style="margin-top:8px">Формулировка для оценщика (общая)
      <textarea rows="3" data-bind="${p}.instructions.default">${esc(c.instructions.default || "")}</textarea></label>
    <details style="margin-top:6px"><summary class="small muted">Отдельные формулировки для чата и звонка ${c.instructions.chat || c.instructions.call ? "(заданы)" : ""}</summary>
      <label class="field" style="margin-top:6px">Для чатов (если пусто — общая) <textarea rows="2" data-bind="${p}.instructions.chat">${esc(c.instructions.chat || "")}</textarea></label>
      <label class="field" style="margin-top:6px">Для звонков (если пусто — общая) <textarea rows="2" data-bind="${p}.instructions.call">${esc(c.instructions.call || "")}</textarea></label>
    </details>
  </div>`;
}

function bind(main, checklists) {
  const rerender = () => { dirty = true; renderChecklists(main, draftBase); };
  const form = $("#e-form", main);

  form.addEventListener("input", (e) => {
    const el = e.target;
    if (!el.dataset.bind) return;
    let v = el.dataset.bool !== undefined ? el.checked : el.value;
    if (el.dataset.num !== undefined) v = v === "" ? null : Number(v);
    setPath(draft, el.dataset.bind, v);
    if (el.dataset.bind.endsWith(".type") && v === "flag") setPath(draft, el.dataset.bind.replace(".type", ".weight"), 0);
    if (el.dataset.bind.endsWith(".type") && v === "scale") {
      const c = draft.criteria[Number(el.dataset.bind.split(".")[1])];
      if (!c.scale.length) c.scale = [{ value: 10, label: "Полностью" }, { value: 5, label: "Частично" }, { value: 0, label: "Нет" }];
    }
    if (el.dataset.rerender !== undefined) return rerender();
    if (!dirty) { dirty = true; }
  });
  form.addEventListener("change", (e) => {
    const ch = e.target.dataset.chan;
    if (ch) {
      const [i, kind] = ch.split(":");
      const c = draft.criteria[Number(i)];
      const chat = kind === "chat" ? e.target.checked : $(`[data-chan="${i}:chat"]`, form).checked;
      const call = kind === "call" ? e.target.checked : $(`[data-chan="${i}:call"]`, form).checked;
      c.channels = chat && call ? [] : [...(chat ? ["agent chat"] : []), ...(call ? ["voice call"] : [])];
      dirty = true;
    }
  });

  form.addEventListener("click", async (e) => {
    const t = e.target;
    if (t.id === "e-add-crit") {
      const n = draft.criteria.length + 1;
      draft.criteria.push({ id: `criterion_${n}`, number: n, title: "Новый критерий", type: "binary", weight: 10, section: draft.sections[0]?.id || "",
        na_allowed: false, na_condition: "", channels: [], scale: [], instructions: { default: "Да — ..." } });
      rerender();
      setTimeout(() => $(".crit-edit:last-of-type input")?.focus(), 50);
    } else if (t.dataset.del !== undefined) {
      if (confirm(`Удалить критерий «${draft.criteria[t.dataset.del].title}»?`)) { draft.criteria.splice(Number(t.dataset.del), 1); rerender(); }
    } else if (t.dataset.move) {
      const [i, d] = t.dataset.move.split(":").map(Number);
      const [c] = draft.criteria.splice(i, 1);
      draft.criteria.splice(i + d, 0, c);
      rerender();
    } else if (t.dataset.addStep !== undefined) {
      draft.criteria[Number(t.dataset.addStep)].scale.push({ value: 0, label: "" });
      rerender();
    } else if (t.dataset.delStep) {
      const [i, j] = t.dataset.delStep.split(":").map(Number);
      draft.criteria[i].scale.splice(j, 1);
      rerender();
    } else if (t.id === "e-add-sec") {
      draft.sections.push({ id: `section_${draft.sections.length + 1}`, title: "Новая секция" });
      rerender();
    } else if (t.dataset.delSec !== undefined) {
      draft.sections.splice(Number(t.dataset.delSec), 1);
      rerender();
    } else if (t.id === "e-save") {
      const clean = structuredClone(draft);
      delete clean.version;
      clean.criteria.forEach((c) => {
        if (c.type !== "scale") delete c.scale;
        if (!c.channels?.length) delete c.channels;
        for (const k of ["chat", "call"]) if (!c.instructions[k]) delete c.instructions[k];
      });
      try {
        const saved = await api.saveChecklist(clean, $("#e-comment").value.trim());
        toast(`Сохранена версия v${saved.version} и сделана активной`, "good");
        draft = null; dirty = false; draftBase = saved.id;
        location.hash = `#/checklists/${saved.id}`;
        renderChecklists(main, saved.id);
      } catch (err) { toast(err.message, "error"); }
    } else if (t.id === "e-reset") {
      draft = null; dirty = false;
      renderChecklists(main, draftBase);
    } else if (t.id === "e-activate") {
      await api.activateChecklist(draftBase);
      toast("Версия сделана активной", "good");
      renderChecklists(main, draftBase);
    } else if (t.id === "e-apply") {
      if (dirty) return toast("Сначала сохраните изменения как новую версию", "error");
      try {
        const job = await api.createJob({ dataset: $("#e-ds").value, checklist_id: draftBase, votes: Number($("#e-votes").value) });
        toast(`Запущена переоценка ${job.total} диалогов версией v${checklists.find((c) => c.id === draftBase)?.version}`, "good");
      } catch (err) { toast(err.message, "error"); }
    }
  });

  $$("[data-ver]", main).forEach((el) => el.addEventListener("click", () => { location.hash = `#/checklists/${el.dataset.ver}`; }));
  $("#e-export", main).addEventListener("click", () => download(`${draft.slug}-v${draft.version ?? "draft"}.json`, JSON.stringify(draft, null, 2)));
  $("#e-import", main).addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      draft = normalize(JSON.parse(await f.text()));
      dirty = true;
      toast("Чек-лист загружен в редактор. Проверьте и сохраните.", "good");
      renderChecklists(main, draftBase);
    } catch (err) { toast("Не удалось прочитать JSON: " + err.message, "error"); }
  });
}
