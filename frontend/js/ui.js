// Небольшие помощники для разметки и форматирования.

export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function fmtScore(v) {
  return v === null || v === undefined ? "—" : Number(v).toFixed(2).replace(/\.00$/, "");
}

export function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function fmtCost(v) {
  if (v === null || v === undefined) return "—";
  return v < 0.01 ? `$${Number(v).toFixed(4)}` : `$${Number(v).toFixed(3)}`;
}

export function answerLabel(criterion, answer) {
  if (answer === "na") return "N/A";
  if (!criterion) return answer;
  if (criterion.type === "flag") return answer === "yes" ? "Выявлено" : "Нет";
  if (criterion.type === "binary") return answer === "yes" ? "Да" : "Нет";
  const top = Math.max(...criterion.scale.map((s) => s.value));
  return `${answer} / ${top}`;
}

export function answerTone(criterion, answer) {
  if (answer === "na") return "muted";
  if (!criterion) return "muted";
  if (criterion.type === "flag") return answer === "yes" ? "bad" : "good";
  if (criterion.type === "binary") return answer === "yes" ? "good" : "bad";
  const top = Math.max(...criterion.scale.map((s) => s.value));
  const r = Number(answer) / top;
  return r >= 0.99 ? "good" : r >= 0.5 ? "warn" : "bad";
}

export function allowedAnswers(c) {
  let vals = c.type === "scale" ? [...c.scale].sort((a, b) => b.value - a.value).map((s) => String(s.value)) : ["yes", "no"];
  if (c.na_allowed) vals.push("na");
  return vals;
}

export function scoreTone(total) {
  if (total === null || total === undefined) return "muted";
  return total >= 85 ? "good" : total >= 60 ? "warn" : "bad";
}

export const LANG = { RU: "RU", KZ: "KZ", EN: "EN" };
export const channelLabel = (ch) => (ch && ch.includes("call") ? "Звонок" : "Чат");
export const SPEAKER = { operator: "Оператор", client: "Клиент", bot: "Бот", system: "Система" };
export const CONF = { high: "уверенно", medium: "есть сомнения", low: "нужна проверка" };

let toastTimer;
export function toast(msg, tone = "info") {
  let el = document.getElementById("cqa-toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "cqa-toast";
    el.className = "toast";
    document.getElementById("cqa-root").appendChild(el);
  }
  el.textContent = msg;
  el.dataset.tone = tone;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), tone === "error" ? 6000 : 3000);
}

export function role() {
  try { return localStorage.getItem("cqa-role") || "operator"; } catch { return "operator"; }
}
export function setRole(r) {
  try { localStorage.setItem("cqa-role", r); } catch { /* приватный режим */ }
}

export function download(filename, text, type = "application/json") {
  const blob = new Blob([text], { type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
