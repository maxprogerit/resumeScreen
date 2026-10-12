import { h, icon, demoBadge, emptyState, toast, chipEditor, storage, replace } from "../ui.js";
import { pageHead } from "./common.js";

const store = storage();
const EDU = [
  ["", "Not required"],
  ["high_school", "High school"],
  ["bachelor", "Bachelor"],
  ["master", "Master"],
  ["phd", "PhD"],
];

export async function render(view, params, { api, navigate }) {
  if (params[0]) return renderJob(view, params[0], { api, navigate });
  return renderList(view, { api, navigate });
}

// ------------------------------------------------------------------ list + new job

async function renderList(view, { api, navigate }) {
  const jobs = await api("/jobs");

  const title = h("input", { type: "text", id: "job-title", required: true, maxlength: 200 });
  const description = h("textarea", { id: "job-desc", required: true, minlength: 20, "aria-describedby": "job-desc-hint" });
  const error = h("p", { class: "error-text", role: "alert" });
  const submit = h("button", { class: "btn btn-primary", type: "submit" }, icon("plus"), "Create job");
  const form = h(
    "form",
    {
      class: "card stack",
      onsubmit: async (e) => {
        e.preventDefault();
        error.textContent = "";
        submit.disabled = true;
        try {
          const job = await api("/jobs", { method: "POST", body: { title: title.value, description_text: description.value } });
          store.set("rs_job", job.id);
          toast(
            job.requirements_source === "llm"
              ? "Requirements extracted. Review and edit them before ranking."
              : "Job created. Requirements could not be extracted automatically: please enter them.",
          );
          navigate(`#/jobs/${job.id}`);
        } catch (err) {
          error.textContent = err.message;
          submit.disabled = false;
        }
      },
    },
    h("h2", {}, "New job"),
    h("div", { class: "field" }, h("label", { class: "label", for: "job-title" }, "Title"), title),
    h("div", { class: "field" },
      h("label", { class: "label", for: "job-desc" }, "Job description"), description,
      h("p", { class: "hint", id: "job-desc-hint" }, "Paste the posting. Requirements are extracted by the language model and shown as editable chips next."),
    ),
    error,
    submit,
  );

  const list = jobs.length
    ? h("div", { class: "grid" }, ...jobs.map(jobCard))
    : emptyState("No data yet. Create your first job.");

  function jobCard(job) {
    const req = job.requirements;
    return h(
      "article",
      { class: "card" },
      h("div", { class: "row between" },
        h("h3", {}, job.title), demoBadge(job.is_demo)),
      h("div", { class: "chips" },
        ...req.must_have_skills.map((s) => h("span", { class: "chip accent" }, s)),
        ...req.nice_to_have_skills.map((s) => h("span", { class: "chip" }, s)),
        !req.must_have_skills.length && !req.nice_to_have_skills.length ? h("span", { class: "hint" }, "No requirements yet") : null),
      h("p", { class: "hint" }, `${job.candidate_count} candidate${job.candidate_count === 1 ? "" : "s"}`),
      h("div", { class: "row" },
        h("a", { class: "btn btn-sm", href: `#/jobs/${job.id}` }, "Requirements"),
        h("a", { class: "btn btn-sm", href: `#/candidates/${job.id}` }, "Upload"),
        h("a", { class: "btn btn-sm btn-primary", href: `#/ranking/${job.id}` }, "Ranking")),
    );
  }

  replace(view, 
    pageHead("Jobs", "Create a job, review its requirements, upload resumes, review the ranking."),
    h("div", { class: "grid cols-2" }, form, h("section", { "aria-label": "Jobs" }, h("div", { class: "stack" }, h("h2", {}, "Your jobs"), list))),
  );
}

// ------------------------------------------------------------------ one job: edit requirements

async function renderJob(view, jobId, { api, navigate }) {
  const job = await api(`/jobs/${jobId}`);
  store.set("rs_job", job.id);
  const req = job.requirements;

  const must = chipEditor(req.must_have_skills, { id: "must", ariaLabel: "Add must-have skill" });
  const nice = chipEditor(req.nice_to_have_skills, { id: "nice", ariaLabel: "Add nice-to-have skill" });
  const langs = chipEditor(req.languages, { id: "langs", ariaLabel: "Add required language" });
  const years = h("input", { type: "number", id: "years", min: 0, max: 50, step: "any", value: req.min_years_experience ?? "" });
  const edu = h("select", { id: "edu" }, ...EDU.map(([v, t]) => h("option", { value: v, selected: (req.education_level || "") === v }, t)));
  const error = h("p", { class: "error-text", role: "alert" });
  const save = h("button", { class: "btn btn-primary", type: "submit" }, icon("check"), "Save and re-rank");

  const form = h(
    "form",
    {
      class: "card stack",
      onsubmit: async (e) => {
        e.preventDefault();
        error.textContent = "";
        save.disabled = true;
        try {
          await api(`/jobs/${jobId}/requirements`, {
            method: "PATCH",
            body: {
              must_have_skills: must.get(),
              nice_to_have_skills: nice.get(),
              min_years_experience: years.value === "" ? null : Number(years.value),
              languages: langs.get(),
              education_level: edu.value || null,
            },
          });
          toast("Requirements saved. Candidates are being re-scored.");
          navigate(`#/ranking/${jobId}`);
        } catch (err) {
          error.textContent = err.message;
          save.disabled = false;
        }
      },
    },
    h("h2", {}, "Requirements"),
    h("p", { class: "hint" }, "Skills are normalised on save (for example k8s becomes kubernetes). Scoring uses only these fields."),
    field("must", "Must-have skills", must.el),
    field("nice", "Nice-to-have skills", nice.el),
    h("div", { class: "grid cols-2" }, field("years", "Minimum years of experience", years), field("edu", "Minimum education", edu)),
    field("langs", "Required spoken languages", langs.el),
    error,
    h("div", { class: "row" }, save, h("a", { class: "btn", href: "#/jobs" }, "Back")),
  );

  const del = h("button", {
    class: "btn btn-danger btn-sm", type: "button",
    onclick: async () => {
      if (!confirm("Delete this job and every candidate uploaded for it? This cannot be undone.")) return;
      await api(`/jobs/${jobId}`, { method: "DELETE" });
      store.remove("rs_job");
      toast("Job and its candidates deleted");
      navigate("#/jobs");
    },
  }, icon("trash"), "Delete job and its candidates");

  replace(view, 
    pageHead(job.title, null, demoBadge(job.is_demo)),
    form,
    h("details", { class: "card" }, h("summary", {}, "Job description"), h("p", { class: "muted" }, job.description_text)),
    del,
  );
}

function field(id, text, control) {
  return h("div", { class: "field" }, h("label", { class: "label", for: id }, text), control);
}

