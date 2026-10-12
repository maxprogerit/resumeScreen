import { h, icon, demoBadge, emptyState, banner, fmt, storage, replace } from "../ui.js";
import { jobPicker, pageHead, legend, breakdownBar } from "./common.js";

const store = storage();

export async function render(view, params, { api, navigate }) {
  const picker = await jobPicker(api, params[0], (id) => navigate(`#/ranking/${id}`));
  if (!picker.jobId) {
    replace(view, pageHead("Ranking"), emptyState("No data yet. Create a job first."), h("a", { class: "btn btn-primary", href: "#/jobs" }, "Go to jobs"));
    return;
  }
  const jobId = picker.jobId;
  const job = picker.jobs.find((j) => j.id === jobId);

  const minScore = h("input", { type: "number", id: "min-score", min: 0, max: 100, step: 1, value: 0 });
  const skill = h("input", { type: "text", id: "skill", placeholder: "e.g. kubernetes, python", autocomplete: "off" });
  const blind = h("input", { type: "checkbox", id: "blind", checked: store.get("rs_blind") === "1" });
  const stats = h("div", { class: "grid cols-4" });
  const tableHost = h("div", {});
  const live = h("p", { class: "hint", role: "status" });

  let timer = null;
  const schedule = () => { clearTimeout(timer); timer = setTimeout(load, 250); };
  for (const el of [minScore, skill]) el.addEventListener("input", schedule);
  blind.addEventListener("change", () => { store.set("rs_blind", blind.checked ? "1" : "0"); load(); });

  async function load() {
    const q = new URLSearchParams({ min_score: String(Number(minScore.value) || 0) });
    if (skill.value.trim()) q.set("skill", skill.value.trim());
    if (blind.checked) q.set("blind", "true");
    const [data, s] = await Promise.all([api(`/jobs/${jobId}/ranking?${q}`), api(`/jobs/${jobId}/stats`)]);
    drawStats(s);
    drawTable(data);
  }

  function stat(text, value) {
    return h("div", { class: "card stat" }, h("span", { class: "label" }, text), h("div", { class: "value" }, value));
  }

  function drawStats(s) {
    const max = Math.max(1, ...s.score_histogram.map((b) => b.count));
    const hist = h("div", { class: "hist", role: "img", "aria-label": `Score distribution: ${s.score_histogram.map((b) => `${b.from}-${b.to}: ${b.count}`).join(", ")}` });
    for (const b of s.score_histogram) {
      const bar = h("span", { title: `${b.from}-${b.to}: ${b.count}` });
      bar.style.height = `${Math.round((b.count / max) * 100)}%`;
      hist.append(bar);
    }
    replace(stats, 
      stat("Candidates", String(s.candidates_total)),
      stat("Ranked", String(s.scored)),
      stat("Needs review", String(s.by_status.NEEDS_REVIEW)),
      stat("Median score", fmt(s.median_score)),
      h("div", { class: "card stat" }, h("span", { class: "label" }, "Distribution"), hist),
      s.most_missing_must_have.length
        ? h("div", { class: "card stat" }, h("span", { class: "label" }, "Most missing must-have"),
            h("div", { class: "chips" }, ...s.most_missing_must_have.map((m) => h("span", { class: "chip warn" }, `${m.skill} (${m.candidates})`))))
        : null,
    );
  }

  function drawTable(data) {
    live.textContent = `Showing ${data.returned} of ${data.total_candidates} ranked candidates.`;
    if (!data.items.length) {
      replace(tableHost, emptyState(data.total_candidates ? "No candidates match these filters." : "No data yet. Upload resumes to see a ranking."));
      return;
    }
    replace(tableHost, 
      h("div", { class: "table-wrap" }, h("table", {},
        h("caption", { class: "sr-only" }, "Candidates ranked by score with breakdown"),
        h("thead", {}, h("tr", {},
          h("th", { scope: "col" }, "Rank"), h("th", { scope: "col" }, "Candidate"),
          h("th", { scope: "col", class: "num" }, "Score"), h("th", { scope: "col" }, "Breakdown"),
          h("th", { scope: "col" }, "Must-have"))),
        h("tbody", {}, ...data.items.map((it) =>
          h("tr", {},
            h("td", { class: "rank" }, `#${it.rank}`),
            h("td", {}, h("a", { href: `#/candidate/${it.candidate_id}/${jobId}` }, it.display_name || `Candidate ${it.candidate_id.slice(0, 8)}`), " ", demoBadge(it.is_demo)),
            h("td", { class: "num score" }, fmt(it.total_score)),
            h("td", {}, breakdownBar(it.components)),
            h("td", {}, h("span", { class: "mono" }, `${it.must_have_matched}/${it.must_have_total}`),
              it.missing_must_have.length ? h("span", { class: "hint" }, ` missing: ${it.missing_must_have.join(", ")}`) : null)))))),
    );
  }

  replace(view, 
    pageHead(`Ranking: ${job.title}`, "Scores come from the deterministic scorer. Open a candidate to see the evidence.", picker.el,
      h("a", { class: "btn", href: `#/candidates/${jobId}` }, icon("upload"), "Upload resumes"),
      h("a", { class: "btn", href: `#/jobs/${jobId}` }, "Edit requirements")),
    banner(),
    stats,
    h("div", { class: "card" },
      h("div", { class: "toolbar" },
        h("div", { class: "field" }, h("label", { class: "label", for: "min-score" }, "Minimum score"), minScore),
        h("div", { class: "field" }, h("label", { class: "label", for: "skill" }, "Has skill"), skill),
        h("label", { class: "check" }, blind, "Hide names (reduces anchoring)")),
      legend(), live),
    tableHost,
  );
  await load();
  // Candidates may still be processing in the background: refresh while the page is open.
  const interval = setInterval(() => load().catch(() => {}), 6000);
  return () => { clearInterval(interval); clearTimeout(timer); };
}
