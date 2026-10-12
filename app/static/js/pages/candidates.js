import { h, icon, demoBadge, emptyState, statusChip, toast, fmt, storage, replace } from "../ui.js";
import { uploadFile } from "../api.js";
import { jobPicker, pageHead } from "./common.js";

const ACCEPT = ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const store = storage();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function render(view, params, { api, navigate }) {
  let timer = null;
  let alive = true;
  const stop = () => { alive = false; clearTimeout(timer); };

  const picker = await jobPicker(api, params[0], (id) => navigate(`#/candidates/${id}`));
  if (!picker.jobId) {
    replace(view, pageHead("Candidates"), emptyState("No data yet. Create a job first."), h("a", { class: "btn btn-primary", href: "#/jobs" }, "Go to jobs"));
    return stop;
  }
  const jobId = picker.jobId;
  const queue = h("div", { class: "stack", "aria-label": "Upload queue" });
  const tableHost = h("div", {});
  const input = h("input", { type: "file", id: "file-input", multiple: true, accept: ACCEPT, class: "sr-only", tabindex: -1 });
  const blind = h("input", { type: "checkbox", id: "blind", checked: store.get("rs_blind") === "1", onchange: () => { store.set("rs_blind", blind.checked ? "1" : "0"); refresh(); } });

  const zone = h(
    "button",
    { type: "button", class: "dropzone", onclick: () => input.click(), "aria-describedby": "dz-hint" },
    icon("upload"),
    h("p", {}, h("strong", {}, "Drop PDF or DOCX resumes here"), " or press to browse"),
    h("p", { class: "hint", id: "dz-hint" }, "Files are checked by content, not by name. Synthetic or consented data only."),
  );
  for (const ev of ["dragenter", "dragover"]) zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("dragover"); });
  for (const ev of ["dragleave", "drop"]) zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("dragover"); });
  zone.addEventListener("drop", (e) => handleFiles([...e.dataTransfer.files]));
  input.addEventListener("change", () => { handleFiles([...input.files]); input.value = ""; });

  async function handleFiles(files) {
    for (const file of files) {
      const status = h("span", { class: "chip accent" }, "UPLOADING");
      const bar = h("span", {});
      bar.style.width = "0%";
      const row = h("div", { class: "card row between" }, h("span", { class: "mono" }, file.name), h("div", { class: "progress", role: "progressbar", "aria-label": `Upload ${file.name}`, "aria-valuemin": 0, "aria-valuemax": 100 }, bar), status);
      queue.prepend(row);
      await sendWithRetry(file, bar, status);
    }
    poll();
  }

  async function sendWithRetry(file, bar, status) {
    for (let attempt = 0; attempt < 4; attempt++) {
      try {
        const result = await uploadFile(jobId, file, (p) => { bar.style.width = `${Math.round(p * 100)}%`; });
        bar.style.width = "100%";
        const rejected = result.rejected?.[0];
        status.className = "chip ok";
        status.textContent = rejected ? `REJECTED: ${rejected.reason}` : "PROCESSING";
        return;
      } catch (err) {
        if (err.status === 429 && err.retryAfter) {
          status.textContent = `RATE LIMITED, RETRY IN ${err.retryAfter}S`;
          await sleep(err.retryAfter * 1000);
          continue;
        }
        status.className = "chip danger";
        status.textContent = `FAILED: ${err.message}`;
        return;
      }
    }
  }

  async function refresh() {
    const rows = await api(`/jobs/${jobId}/candidates${blind.checked ? "?blind=true" : ""}`);
    if (!alive) return rows;
    replace(tableHost, 
      rows.length
        ? h("div", { class: "table-wrap" }, h("table", {},
            h("thead", {}, h("tr", {}, ...["File", "Name", "Status", "Score", ""].map((t, i) => h("th", { scope: "col", class: i === 3 ? "num" : "" }, t)))),
            h("tbody", {}, ...rows.map(rowFor))))
        : emptyState("No data yet. Upload resumes above."),
    );
    return rows;
  }

  function rowFor(c) {
    return h("tr", {},
      h("td", { class: "mono" }, c.source_filename, " ", demoBadge(c.is_demo)),
      h("td", {}, c.display_name || (blind.checked ? h("span", { class: "hint" }, "hidden") : h("span", { class: "hint" }, "n/a"))),
      h("td", {}, statusChip(c.status, c.status_reason)),
      h("td", { class: "num" }, fmt(c.total_score)),
      h("td", {}, h("div", { class: "row" },
        c.status === "READY" ? h("a", { class: "btn btn-sm", href: `#/candidate/${c.id}` }, "Open") : null,
        c.status === "NEEDS_REVIEW"
          ? h("button", {
              class: "btn btn-sm", type: "button",
              title: "Re-run extraction from the stored text",
              onclick: async () => {
                try {
                  await api(`/candidates/${c.id}/reprocess`, { method: "POST" });
                  toast("Retrying extraction");
                  poll();
                } catch (err) { toast(err.message, "error"); }
              },
            }, "Retry")
          : null,
        h("button", {
          class: "btn btn-sm btn-danger", type: "button", "aria-label": `Delete candidate ${c.source_filename}`,
          onclick: async () => {
            if (!confirm("Delete this candidate and all derived data? This cannot be undone.")) return;
            await api(`/candidates/${c.id}`, { method: "DELETE" });
            toast("Candidate deleted");
            refresh();
          },
        }, icon("trash")))));
  }

  async function poll() {
    if (!alive) return;
    try {
      const rows = await refresh();
      if (rows.some((r) => r.status === "PROCESSING")) timer = setTimeout(poll, 1500);
    } catch { timer = setTimeout(poll, 4000); }
  }

  replace(view, 
    pageHead("Candidates", "Upload resumes for a job. Parsing and scoring run in the background.", picker.el,
      h("button", { class: "btn", type: "button", title: "Score every READY candidate already in the system against this job",
        onclick: async () => {
          try {
            const r = await api(`/jobs/${jobId}/match`, { method: "POST", body: { candidate_ids: null } });
            toast(`${r.matched} existing candidate(s) matched to this job`);
            poll();
          } catch (err) { toast(err.message, "error"); }
        } }, "Match existing candidates"),
      h("a", { class: "btn btn-primary", href: `#/ranking/${jobId}` }, icon("ranking"), "View ranking")),
    zone, input,
    h("section", { "aria-label": "Uploads" }, queue),
    h("div", { class: "row between" }, h("h2", {}, "Uploaded resumes"),
      h("label", { class: "check" }, blind, "Hide names")),
    tableHost,
  );
  await poll();
  return stop;
}
