import { h, icon, banner, demoBadge, statusChip, toast, fmt, storage, replace, chipEditor } from "../ui.js";
import { pageHead, COMPONENT_LABELS, breakdownBar, legend } from "./common.js";

const store = storage();
const EDU = [["", "Not stated"], ["high_school", "High school"], ["bachelor", "Bachelor"], ["master", "Master"], ["phd", "PhD"]];
const NOTE_TEXT = {
  ocr: "Text was recovered by OCR and may contain errors",
  hidden_text_removed: "Hidden text (white, tiny or hidden-attribute) was removed",
};
const KIND_LABEL = {
  must_have_skill: "Must-have",
  nice_to_have_skill: "Nice-to-have",
  language: "Language",
  education: "Education",
  experience: "Experience",
};

/** Merge [start,end) spans so overlapping evidence quotes produce one <mark>. */
function mergeSpans(items) {
  const spans = items
    .filter((i) => i.quote && i.start !== null && i.end !== null)
    .map((i) => ({ start: i.start, end: i.end, reqs: [i.requirement] }))
    .sort((a, b) => a.start - b.start);
  const merged = [];
  for (const s of spans) {
    const last = merged[merged.length - 1];
    if (last && s.start <= last.end) {
      last.end = Math.max(last.end, s.end);
      last.reqs.push(...s.reqs);
    } else merged.push({ ...s });
  }
  return merged;
}

export async function render(view, params, { api, navigate }) {
  const id = params[0];
  const blind = store.get("rs_blind") === "1";
  const query = new URLSearchParams();
  if (blind) query.set("blind", "true");
  if (params[1]) query.set("job_id", params[1]);
  const c = await api(`/candidates/${id}${query.size ? `?${query}` : ""}`);

  const match = c.match;
  const items = match?.explanation?.items || [];
  const spans = mergeSpans(items);

  // Resume text with highlighted evidence. Only textContent is used, never HTML.
  const pre = h("pre", { class: "resume", tabindex: 0, "aria-label": "Resume text with evidence highlighted" });
  let pos = 0;
  const marks = [];
  for (const s of spans) {
    if (s.start > pos) pre.append(document.createTextNode(c.raw_text.slice(pos, s.start)));
    const mark = h("mark", { title: `Evidence for: ${[...new Set(s.reqs)].join(", ")}`, "data-start": s.start }, c.raw_text.slice(s.start, s.end));
    marks.push({ el: mark, start: s.start, end: s.end });
    pre.append(mark);
    pos = s.end;
  }
  pre.append(document.createTextNode(c.raw_text.slice(pos)));
  if (!c.raw_text) pre.textContent = "No text was extracted.";

  function jumpTo(item) {
    const m = marks.find((x) => item.start >= x.start && item.start < x.end);
    if (!m) return;
    m.el.scrollIntoView({ block: "center", behavior: "smooth" });
    m.el.classList.add("flash");
    setTimeout(() => m.el.classList.remove("flash"), 1500);
  }

  function reqRow(item) {
    return h("li", { class: "req" },
      icon(item.matched ? "check" : "x", item.matched ? "Matched" : "Missing"),
      h("div", {},
        h("div", { class: "row" },
          h("strong", {}, item.requirement),
          h("span", { class: "chip" }, KIND_LABEL[item.kind] || item.kind),
          h("span", { class: `chip ${item.matched ? "ok" : "danger"}` }, item.matched ? "matched" : "missing"),
          item.demonstrated === false ? h("span", { class: "chip warn", title: "Only appears in a bare skills list, not in described work" }, "listed only") : null,
          item.source ? h("span", { class: "hint" }, item.source === "llm" ? "quote: model, verified" : "quote: rule-based") : null),
        item.quote
          ? h("button", { type: "button", class: "quote-link", onclick: () => jumpTo(item), "aria-label": `Show quote for ${item.requirement} in the resume` }, h("blockquote", {}, item.quote))
          : null,
        item.note ? h("p", { class: "hint" }, item.note) : null));
  }

  const checklist = items.length
    ? h("ul", { class: "stack", role: "list", "aria-label": "Requirement checklist" }, ...items.map(reqRow))
    : h("p", { class: "hint" }, "No requirements are defined for this job, or the candidate has not been scored.");

  const breakdown = match
    ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ...["Component", "Fit", "Weight", "Points"].map((t, i) => h("th", { scope: "col", class: i ? "num" : "" }, t)))),
        h("tbody", {}, ...Object.entries(match.breakdown.components).map(([k, comp]) =>
          h("tr", {},
            h("td", {}, COMPONENT_LABELS[k]),
            h("td", { class: "num" }, comp.applicable ? `${Math.round(comp.score * 100)}%` : "n/a"),
            h("td", { class: "num" }, comp.applicable ? `${Math.round(comp.weight * 100)}%` : "-"),
            h("td", { class: "num" }, comp.applicable ? comp.points.toFixed(1) : "-"))))))
    : null;

  const profile = c.profile;
  const profileCard = profile
    ? h("section", { class: "card stack", "aria-label": "Extracted profile" },
        h("h2", {}, "Extracted profile"),
        h("div", {}, h("span", { class: "label" }, "Skills"), h("div", { class: "chips" }, ...profile.skills.map((s) => h("span", { class: "chip" }, s)))),
        h("p", {}, h("span", { class: "label" }, "Experience "), `${fmt(profile.total_years_experience)} years`),
        h("p", {}, h("span", { class: "label" }, "Education "), profile.education.map((e) => e.level).join(", ") || "n/a"),
        h("p", {}, h("span", { class: "label" }, "Languages "), profile.languages.join(", ") || "n/a"),
        profile.certifications.length ? h("p", {}, h("span", { class: "label" }, "Certifications "), profile.certifications.join(", ")) : null)
    : null;

  function field(idv, text, control) {
    return h("div", { class: "field" }, h("label", { class: "label", for: idv }, text), control);
  }

  function profileForm() {
    const p = c.profile || { skills: [], total_years_experience: null, education: [], languages: [], certifications: [] };
    const skills = chipEditor(p.skills, { id: "pf-skills", ariaLabel: "Add skill" });
    const langs = chipEditor(p.languages, { id: "pf-langs", ariaLabel: "Add language" });
    const certs = chipEditor(p.certifications, { id: "pf-certs", ariaLabel: "Add certification" });
    const years = h("input", { type: "number", id: "pf-years", min: 0, max: 60, step: "any", value: p.total_years_experience ?? "" });
    const edu = h("select", { id: "pf-edu" }, ...EDU.map(([v, t]) => h("option", { value: v, selected: (p.education[0]?.level || "") === v }, t)));
    const error = h("p", { class: "error-text", role: "alert" });
    const save = h("button", { class: "btn btn-primary btn-sm", type: "submit" }, icon("check"), "Save and re-score");
    return h("details", { class: "card", open: c.status !== "READY" },
      h("summary", {}, c.profile ? "Correct the extracted profile" : "Enter the profile manually"),
      h("form", {
        class: "stack",
        onsubmit: async (e) => {
          e.preventDefault();
          error.textContent = "";
          save.disabled = true;
          try {
            await api(`/candidates/${id}/profile`, { method: "PATCH", body: {
              skills: skills.get(),
              total_years_experience: years.value === "" ? null : Number(years.value),
              education_level: edu.value || null,
              languages: langs.get(),
              certifications: certs.get(),
            } });
            toast("Profile saved. Every job this candidate is matched to was re-scored.");
            navigate(location.hash);
          } catch (err) {
            error.textContent = err.message;
            save.disabled = false;
          }
        },
      },
      h("p", { class: "hint" }, "Edits are marked as manual. Name and contact data are not editable here and are never used for scoring."),
      field("pf-skills", "Skills", skills.el),
      h("div", { class: "grid cols-2" }, field("pf-years", "Years of experience", years), field("pf-edu", "Highest education", edu)),
      field("pf-langs", "Spoken languages", langs.el),
      field("pf-certs", "Certifications", certs.el),
      error, save));
  }

  const del = h("button", {
    class: "btn btn-danger btn-sm", type: "button",
    onclick: async () => {
      if (!confirm("Delete this candidate and ALL derived data (profile, identity, embedding, matches)? This cannot be undone.")) return;
      await api(`/candidates/${id}`, { method: "DELETE" });
      toast("Candidate deleted");
      navigate(c.job_id ? `#/ranking/${c.job_id}` : "#/jobs");
    },
  }, icon("trash"), "Delete candidate and all data");

  const name = c.identity?.display_name || (blind ? `Candidate ${id.slice(0, 8)}` : c.source_filename);

  replace(view, 
    pageHead(name, c.identity ? [c.identity.email, c.identity.phone].filter(Boolean).join(" | ") + " (shown for contact only, never used in scoring)" : "Identity hidden",
      demoBadge(c.is_demo),
      c.job_id ? h("a", { class: "btn", href: `#/ranking/${c.job_id}` }, "Back to ranking") : null),
    banner(),
    c.parse_notes.length || c.manually_edited
      ? h("div", { class: "row" },
          ...c.parse_notes.map((n) => h("span", { class: "chip warn" }, NOTE_TEXT[n] || n)),
          c.manually_edited ? h("span", { class: "chip accent" }, "profile edited by a human") : null)
      : null,
    c.matched_jobs.length > 1
      ? h("div", { class: "row" }, h("span", { class: "label" }, "Matched jobs"),
          ...c.matched_jobs.map((m) => h("a", { class: "chip", href: `#/candidate/${id}/${m.job_id}`, "aria-current": m.job_id === c.job_id ? "true" : null }, `${m.title}: ${fmt(m.total_score)}`)))
      : null,
    c.status !== "READY"
      ? h("div", { class: "banner warn", role: "alert" }, icon("info"), h("div", {}, statusChip(c.status, c.status_reason), h("p", {}, "This resume needs manual review. It is not ranked and was not rejected.")))
      : null,
    match
      ? h("section", { class: "card stack", "aria-label": "Score" },
          h("div", { class: "row between" }, h("h2", {}, "Score"), h("span", { class: "score" }, `${fmt(match.total_score)} / 100`)),
          breakdownBar(match.breakdown.components), legend(), breakdown,
          h("div", { class: "explain" },
            h("span", { class: "label" }, match.explanation.summary_source === "llm" ? "Explanation (written by the model from the stored breakdown and verified quotes)" : "Explanation (template)"),
            h("p", {}, match.explanation.summary)),
          match.explanation.quotes_discarded
            ? h("p", { class: "hint" }, `${match.explanation.quotes_discarded} model-proposed quote(s) were discarded because they are not in the resume.`)
            : null)
      : null,
    h("div", { class: "split" },
      h("section", { class: "card stack", "aria-label": "Resume" },
        h("div", { class: "row between" }, h("h2", {}, "Resume"), h("span", { class: "hint" }, "Highlighted passages are evidence quotes")), pre),
      h("div", { class: "stack" },
        h("section", { class: "card stack", "aria-label": "Requirements" }, h("h2", {}, "Requirements"), checklist),
        profileCard,
        c.status !== "PROCESSING" ? profileForm() : null)),
    del,
  );
}
