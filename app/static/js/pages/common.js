import { h, label, storage } from "../ui.js";

const store = storage();

/** Select listing all jobs. Returns {el, jobId, jobs}; `onChange(id)` fires on user change. */
export async function jobPicker(api, preferredId, onChange) {
  const jobs = await api("/jobs");
  let current = jobs.find((j) => j.id === preferredId)?.id || jobs.find((j) => j.id === store.get("rs_job"))?.id || jobs[0]?.id || null;
  if (current) store.set("rs_job", current);
  const select = h(
    "select",
    {
      id: "job-select",
      onchange: () => { store.set("rs_job", select.value); onChange(select.value); },
    },
    ...jobs.map((j) => h("option", { value: j.id, selected: j.id === current }, `${j.title}${j.is_demo ? " [demo]" : ""}`)),
  );
  const el = h("div", { class: "field" }, h("label", { class: "label", for: "job-select" }, "Job"), select);
  return { el, jobId: current, jobs };
}

export function pageHead(title, sub, ...actions) {
  return h(
    "div",
    { class: "page-head" },
    h("div", {}, h("h1", {}, title), sub ? h("p", { class: "sub" }, sub) : null),
    actions.length ? h("div", { class: "toolbar" }, ...actions) : null,
  );
}

export const COMPONENT_LABELS = {
  must_have: "Must-have skills",
  nice_to_have: "Nice-to-have skills",
  experience: "Experience",
  semantic: "Semantic similarity",
  language_education: "Language / education",
};

export function legend() {
  return h(
    "div",
    { class: "legend", "aria-label": "Breakdown colours" },
    ...Object.entries(COMPONENT_LABELS).map(([key, text]) =>
      h("span", {}, h("i", { class: `seg-${key}` }), text),
    ),
  );
}

/** Segmented bar: each segment's width is its points out of 100. */
export function breakdownBar(components) {
  const parts = Object.entries(components)
    .filter(([, c]) => c.applicable && c.points > 0)
    .map(([key, c]) => ({ key, points: c.points }));
  const summary = Object.entries(components)
    .map(([key, c]) => `${COMPONENT_LABELS[key]}: ${c.applicable ? c.points.toFixed(1) + " points" : "not applicable"}`)
    .join("; ");
  const bar = h("div", { class: "bar", role: "img", "aria-label": summary, title: summary });
  for (const p of parts) {
    const seg = h("span", { class: `seg-${p.key}` });
    seg.style.width = `${Math.min(p.points, 100)}%`;
    bar.append(seg);
  }
  return bar;
}

export { label };
