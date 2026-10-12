import { h, icon, toast, banner, emptyState, fmt, storage, replace } from "../ui.js";
import { logout } from "../api.js";
import { jobPicker, pageHead, COMPONENT_LABELS } from "./common.js";

const store = storage();

export async function render(view, params, { api, navigate }) {
  const w = await api("/settings/weights");
  const picker = await jobPicker(api, null, () => preview());
  let current = { ...w.weights };
  let timer = null;
  let seq = 0;

  const rows = {};
  const sliders = h("div", { class: "weights-grid" });
  for (const key of Object.keys(COMPONENT_LABELS)) {
    const range = h("input", { type: "range", id: `w-${key}`, min: 0, max: 100, step: 1, value: current[key] });
    const out = h("output", { class: "mono", for: `w-${key}` }, String(current[key]));
    range.addEventListener("input", () => {
      current[key] = Number(range.value);
      out.textContent = range.value;
      schedule();
    });
    rows[key] = { range, out };
    sliders.append(h("div", { class: "weight-row" }, h("label", { class: "label", for: `w-${key}` }, COMPONENT_LABELS[key]), range, out));
  }

  const status = h("p", { class: "hint", role: "status" });
  const tableHost = h("div", {});
  const normalized = h("p", { class: "hint" });

  function schedule() { clearTimeout(timer); timer = setTimeout(preview, 200); }

  async function preview() {
    const total = Object.values(current).reduce((a, b) => a + b, 0);
    normalized.textContent = total > 0
      ? "Effective shares: " + Object.keys(current).map((k) => `${COMPONENT_LABELS[k]} ${Math.round((current[k] / total) * 100)}%`).join(", ") + ". Components that do not apply to the job are excluded and the rest renormalised."
      : "";
    if (total <= 0) { status.textContent = "At least one weight must be above zero."; return; }
    const jobId = document.getElementById("job-select")?.value;
    if (!jobId) { replace(tableHost, emptyState("No data yet. Create a job to preview weights.")); return; }
    const mine = ++seq;
    try {
      const data = await api(`/jobs/${jobId}/ranking/preview?blind=${store.get("rs_blind") === "1"}`, { method: "POST", body: current });
      if (mine !== seq) return;
      draw(data);
    } catch (err) { status.textContent = err.message; }
  }

  function draw(data) {
    if (!data.length) { replace(tableHost, emptyState("No data yet. Upload resumes to preview a ranking.")); status.textContent = ""; return; }
    const changed = data.filter((r) => r.rank_current !== r.rank_preview).length;
    status.textContent = `${changed} of ${data.length} candidates change rank with these weights. Nothing is saved until you press Save.`;
    const sorted = [...data].sort((a, b) => a.rank_preview - b.rank_preview);
    replace(tableHost, h("div", { class: "table-wrap" }, h("table", {},
      h("caption", { class: "sr-only" }, "Ranking preview with the weights above"),
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "New rank"), h("th", { scope: "col" }, "Candidate"), h("th", { scope: "col" }, "Change"), h("th", { scope: "col", class: "num" }, "Score now"), h("th", { scope: "col", class: "num" }, "Score preview"))),
      h("tbody", {}, ...sorted.map((r) => {
        const delta = r.rank_current - r.rank_preview;
        const cls = delta > 0 ? "delta-up" : delta < 0 ? "delta-down" : "delta-same";
        return h("tr", {},
          h("td", { class: "rank" }, `#${r.rank_preview}`),
          h("td", {}, h("a", { href: `#/candidate/${r.candidate_id}` }, r.display_name || `Candidate ${r.candidate_id.slice(0, 8)}`), r.is_demo ? " " : null, r.is_demo ? h("span", { class: "chip demo" }, "demo") : null),
          h("td", { class: cls }, delta > 0 ? icon("up", "moved up") : delta < 0 ? icon("down", "moved down") : null, " ", delta === 0 ? "same" : `${Math.abs(delta)} ${delta > 0 ? "up" : "down"}`),
          h("td", { class: "num" }, fmt(r.total_current)),
          h("td", { class: "num" }, fmt(r.total_preview)));
      })))));
  }

  const save = h("button", { class: "btn btn-primary", type: "button", onclick: async () => {
    try {
      await api("/settings/weights", { method: "PUT", body: current });
      toast("Weights saved. All stored scores were recomputed.");
      navigate("#/settings");
    } catch (err) { toast(err.message, "error"); }
  } }, icon("check"), "Save weights");
  const reset = h("button", { class: "btn", type: "button", onclick: async () => {
    await api("/settings/weights", { method: "DELETE" });
    toast("Weights reset to defaults");
    navigate("#/settings");
  } }, "Reset to defaults");

  const retention = h("button", { class: "btn btn-sm", type: "button", onclick: async () => {
    const r = await api("/admin/retention/run", { method: "POST" });
    toast(r.retention_days ? `Retention (${r.retention_days} days): deleted ${r.deleted_candidates} candidate(s)` : "Retention is disabled (RETENTION_DAYS=0)");
  } }, "Run retention purge now");

  const blind = h("input", { type: "checkbox", id: "blind-pref", checked: store.get("rs_blind") === "1", onchange: (e) => { store.set("rs_blind", e.target.checked ? "1" : "0"); preview(); } });

  replace(view, 
    pageHead("Settings", "Scoring weights decide how much each component counts. Preview changes before saving."),
    banner("Weights change the ranking order, never who is considered. Nobody is rejected automatically."),
    h("div", { class: "grid cols-2" },
      h("section", { class: "card stack", "aria-label": "Scoring weights" },
        h("h2", {}, "Scoring weights"),
        w.is_override ? h("span", { class: "chip accent" }, "custom weights active") : h("span", { class: "chip" }, "defaults from configuration"),
        sliders, normalized, h("div", { class: "row" }, save, reset)),
      h("section", { class: "card stack", "aria-label": "Preferences" },
        h("h2", {}, "Preferences and data"),
        h("label", { class: "check" }, blind, "Hide candidate names in previews"),
        h("p", { class: "hint" }, "Candidates are deleted automatically after RETENTION_DAYS. Seeded demo rows are exempt."),
        retention,
        h("button", { class: "btn btn-sm", type: "button", onclick: async () => { await logout(); store.remove("rs_job"); location.hash = "#/jobs"; location.reload(); } }, "Sign out of this browser"))),
    h("section", { class: "stack", "aria-label": "Preview" },
      h("div", { class: "row between" }, h("h2", {}, "Live preview"), picker.el), status, tableHost),
  );
  await preview();
  return () => { clearTimeout(timer); seq++; };
}

