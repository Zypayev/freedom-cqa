import { API_BASE } from "./config.js";

async function request(method, path, body) {
  const res = await fetch(API_BASE + path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const detail = data && data.detail;
    let msg = typeof detail === "string" ? detail : (detail && detail.errors ? detail.errors.join("\n") : `HTTP ${res.status}`);
    const err = new Error(msg);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export const api = {
  health: () => request("GET", "/api/health"),
  dialogs: (dataset) => request("GET", "/api/dialogs" + (dataset ? `?dataset=${encodeURIComponent(dataset)}` : "")),
  dialog: (id) => request("GET", `/api/dialogs/${encodeURIComponent(id)}`),
  fetchDataset: (group_id, url) => request("POST", "/api/datasets/fetch", { group_id, url: url || undefined }),
  uploadDataset: (payload, dataset) => request("POST", "/api/datasets/upload", { payload, dataset: dataset || undefined }),
  checklists: () => request("GET", "/api/checklists"),
  checklist: (id) => request("GET", `/api/checklists/${id}`),
  saveChecklist: (data, comment) => request("POST", "/api/checklists", { data, comment }),
  activateChecklist: (id) => request("POST", `/api/checklists/${id}/activate`),
  createJob: (body) => request("POST", "/api/jobs", body),
  job: (id) => request("GET", `/api/jobs/${id}`),
  jobs: () => request("GET", "/api/jobs"),
  evaluation: (id) => request("GET", `/api/evaluations/${id}`),
  createAppeal: (eid, criterion_id, comment) => request("POST", `/api/evaluations/${eid}/appeals`, { criterion_id, comment }),
  appeals: (status) => request("GET", "/api/appeals" + (status ? `?status=${status}` : "")),
  decide: (aid, body) => request("POST", `/api/appeals/${aid}/decision`, body),
  gold: (dialogId, slug) => request("GET", `/api/gold/${encodeURIComponent(dialogId)}?slug=${encodeURIComponent(slug)}`),
  saveGold: (dialogId, slug, labels) => request("PUT", `/api/gold/${encodeURIComponent(dialogId)}`, { slug, labels }),
  accuracy: (checklistId) => request("GET", "/api/accuracy" + (checklistId ? `?checklist_id=${checklistId}` : "")),
  exportUrl: (dataset, checklistId) => `${API_BASE}/api/export?${new URLSearchParams({ ...(dataset ? { dataset } : {}), ...(checklistId ? { checklist_id: checklistId } : {}) })}`,
};
