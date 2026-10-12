// Tiny DOM helpers. Never parse HTML: all text goes through textContent, so
// uploaded resume content can never be interpreted as HTML.

const SVG_NS = "http://www.w3.org/2000/svg";

export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (key === "value") el.value = value;
    else if (key === "checked" || key === "disabled" || key === "hidden") el[key] = Boolean(value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

const ICONS = {
  jobs: "M3 8h18v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8z M8 8V6a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2 M3 13h18",
  users: "M16 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 18.5V20 M10 12a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z M20 20v-1.5a3.5 3.5 0 0 0-2.5-3.3 M15.5 5.2a3.5 3.5 0 0 1 0 6.6",
  ranking: "M5 20V10 M12 20V4 M19 20v-7",
  settings: "M4 7h10 M18 7h2 M4 17h2 M10 17h10 M16 4v6 M8 14v6",
  upload: "M12 16V4 M7 9l5-5 5 5 M4 20h16",
  check: "M5 12.5l4.5 4.5L19 7.5",
  x: "M6 6l12 12 M18 6L6 18",
  trash: "M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13",
  up: "M12 19V5 M6 11l6-6 6 6",
  down: "M12 5v14 M6 13l6 6 6-6",
  info: "M12 11v6 M12 7.5v.01 M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
  plus: "M12 5v14 M5 12h14",
  arrow: "M5 12h14 M13 6l6 6-6 6",
};

export function icon(name, label) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  if (label) {
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label);
  } else {
    svg.setAttribute("aria-hidden", "true");
  }
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", ICONS[name] || ICONS.info);
  svg.append(path);
  return svg;
}

export const label = (text) => h("span", { class: "label", text });

export function demoBadge(isDemo) {
  return isDemo ? h("span", { class: "chip demo", title: "Synthetic demo data" }, "demo") : null;
}

export function banner(text = "Decision support only. A human makes the hiring decision.") {
  return h("div", { class: "banner", role: "note" }, icon("info"), h("p", {}, text));
}

export function emptyState(text = "No data yet") {
  return h("div", { class: "empty" }, text);
}

export function statusChip(status, reason) {
  const cls = { READY: "ok", PROCESSING: "accent", NEEDS_REVIEW: "warn" }[status] || "";
  const reasons = {
    no_extractable_text: "no readable text, even after OCR",
    unsupported_file_type: "unsupported file type",
    extraction_invalid: "the model's output failed validation twice",
    llm_unavailable: "the language model is unavailable",
    processing_error: "unexpected processing error",
    pdf_encrypted: "PDF is encrypted",
    pdf_unreadable: "PDF could not be read",
    docx_unreadable: "DOCX could not be read",
    docx_too_large: "DOCX is too large",
    ocr_failed: "OCR could not read the scan",
    duplicate_resume: "same text as a resume already uploaded for this job",
    interrupted: "processing was interrupted (server restart); use Retry",
  };
  const chip = h("span", { class: `chip ${cls}` }, status);
  return reason
    ? h("span", { class: "row" }, chip, h("span", { class: "hint" }, reasons[reason] || reason))
    : chip;
}

export function announce(message) {
  const live = document.getElementById("live");
  if (live) live.textContent = message;
}

export function toast(message, kind = "info") {
  const box = document.getElementById("toasts");
  const el = h("div", { class: `toast ${kind}`, role: kind === "error" ? "alert" : "status" }, message);
  box.append(el);
  setTimeout(() => el.remove(), 5000);
}

export const fmt = (n, digits = 1) => (n === null || n === undefined ? "n/a" : Number(n).toFixed(digits));

export function storage() {
  // sessionStorage can throw (private windows, blocked storage): never rely on it.
  return {
    get(key) {
      try { return sessionStorage.getItem(key); } catch { return null; }
    },
    set(key, value) {
      try { sessionStorage.setItem(key, value); } catch { /* ignore */ }
    },
    remove(key) {
      try { sessionStorage.removeItem(key); } catch { /* ignore */ }
    },
  };
}

/** Chips input. Returns {el, get(), set(values)}. */
export function chipEditor(initial, { id, ariaLabel }) {
  let values = [...initial];
  const list = h("div", { class: "chip-editor" });
  const input = h("input", { type: "text", id, "aria-label": ariaLabel, placeholder: "Type and press Enter" });

  function draw() {
    for (const node of [...list.querySelectorAll(".chip")]) node.remove();
    values.forEach((value, i) => {
      const chip = h("span", { class: "chip" }, value,
        h("button", { type: "button", "aria-label": `Remove ${value}`, onclick: () => { values.splice(i, 1); draw(); input.focus(); } }, icon("x")));
      list.insertBefore(chip, input);
    });
  }
  function commit() {
    const v = input.value.trim().replace(/,$/, "");
    if (v && !values.some((x) => x.toLowerCase() === v.toLowerCase())) values.push(v);
    input.value = "";
    draw();
  }
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); commit(); }
    else if (e.key === "Backspace" && !input.value && values.length) { values.pop(); draw(); }
  });
  input.addEventListener("blur", commit);
  list.append(input);
  draw();
  return { el: list, get: () => { commit(); return [...values]; }, set: (v) => { values = [...v]; draw(); } };
}

/** replaceChildren that ignores null/false (the native one would print "null"). */
export function replace(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));
}
