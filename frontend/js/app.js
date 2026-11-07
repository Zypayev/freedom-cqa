import { api } from "./api.js";
import { esc, role, setRole, toast } from "./ui.js";
import { renderDialogs } from "./views/dialogs.js";
import { renderCard } from "./views/card.js";
import { renderChecklists } from "./views/checklists.js";
import { renderAppeals } from "./views/appeals.js";
import { renderAccuracy } from "./views/accuracy.js";

const root = document.getElementById("cqa-root");

root.innerHTML = `
  <header class="topbar">
    <div class="brand"><span class="dot"></span> CQA · Оценка качества диалогов</div>
    <nav id="cqa-nav">
      <a href="#/dialogs" data-route="dialogs">Диалоги</a>
      <a href="#/appeals" data-route="appeals">Апелляции <span class="count hidden" id="cqa-appeals-count"></span></a>
      <a href="#/checklists" data-route="checklists">Чек-листы</a>
      <a href="#/accuracy" data-route="accuracy">Точность</a>
    </nav>
    <div class="spacer"></div>
    <span class="meta" id="cqa-job"></span>
    <span class="meta" id="cqa-model"></span>
    <label class="row small muted" title="Подача апелляции и её проверка показаны как отдельные роли">
      Роль
      <select id="cqa-role">
        <option value="operator">Оператор / супервайзер</option>
        <option value="reviewer">Проверяющий</option>
      </select>
    </label>
  </header>
  <main id="cqa-main"></main>`;

const main = document.getElementById("cqa-main");
const roleSel = document.getElementById("cqa-role");
roleSel.value = role();
roleSel.addEventListener("change", () => { setRole(roleSel.value); route(); });

const routes = {
  dialogs: (el) => renderDialogs(el),
  dialog: (el, id) => renderCard(el, decodeURIComponent(id)),
  checklists: (el, id) => renderChecklists(el, id),
  appeals: (el) => renderAppeals(el),
  accuracy: (el) => renderAccuracy(el),
};

export async function route() {
  const [, name = "dialogs", arg] = (location.hash || "#/dialogs").split("/");
  document.querySelectorAll("#cqa-nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === name || (name === "dialog" && a.dataset.route === "dialogs")));
  const fn = routes[name] || routes.dialogs;
  // Каждый переход рисуется в свой контейнер: устаревшая асинхронная отрисовка
  // пишет в отсоединённый элемент и не затирает текущую страницу.
  const view = document.createElement("div");
  view.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  main.replaceChildren(view);
  try {
    await fn(view, arg);
  } catch (e) {
    console.error(e);
    view.innerHTML = `<div class="panel">Ошибка: ${esc(e.message)}</div>`;
  }
  refreshAppealsCount();
}

async function refreshAppealsCount() {
  try {
    const { appeals } = await api.appeals("open");
    const el = document.getElementById("cqa-appeals-count");
    el.textContent = appeals.length;
    el.classList.toggle("hidden", appeals.length === 0);
  } catch { /* backend недоступен */ }
}

// Индикатор фоновых задач оценки
async function pollJobs() {
  try {
    const { jobs } = await api.jobs();
    const running = jobs.filter((j) => j.status === "running");
    const el = document.getElementById("cqa-job");
    if (running.length) {
      const j = running[0];
      el.innerHTML = `<span class="spinner"></span> Оценка: ${j.done.length + j.failed.length}/${j.total}`;
    } else {
      el.textContent = "";
    }
    window.dispatchEvent(new CustomEvent("cqa-jobs", { detail: jobs }));
  } catch { /* игнорируем */ }
  setTimeout(pollJobs, 2500);
}

api.health().then((h) => {
  document.getElementById("cqa-model").textContent = `${h.provider} · ${h.model}${h.votes > 1 ? ` · ×${h.votes}` : ""}`;
}).catch(() => toast("Backend недоступен. Запустите сервер (см. README).", "error"));

window.addEventListener("hashchange", route);
route();
pollJobs();
