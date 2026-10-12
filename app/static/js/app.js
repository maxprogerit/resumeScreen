import { api, checkSession, login, logout } from "./api.js";
import { h, icon, announce, toast, storage, replace } from "./ui.js";
import * as jobs from "./pages/jobs.js";
import * as candidates from "./pages/candidates.js";
import * as ranking from "./pages/ranking.js";
import * as candidate from "./pages/candidate.js";
import * as settings from "./pages/settings.js";

const store = storage();
const view = document.getElementById("view");
const nav = document.getElementById("nav");
const signout = document.getElementById("signout");

const NAV = [
  { key: "jobs", text: "Jobs", icon: "jobs", href: () => "#/jobs" },
  { key: "candidates", text: "Candidates", icon: "users", href: () => `#/candidates${suffix()}` },
  { key: "ranking", text: "Ranking", icon: "ranking", href: () => `#/ranking${suffix()}` },
  { key: "settings", text: "Settings", icon: "settings", href: () => "#/settings" },
];
const suffix = () => (store.get("rs_job") ? `/${store.get("rs_job")}` : "");

const ROUTES = {
  jobs: { module: jobs, title: "Jobs", section: "jobs" },
  candidates: { module: candidates, title: "Candidates", section: "candidates" },
  ranking: { module: ranking, title: "Ranking", section: "ranking" },
  candidate: { module: candidate, title: "Candidate", section: "ranking" },
  settings: { module: settings, title: "Settings", section: "settings" },
};

let authed = null; // null = not checked yet
let cleanup = null;
let renderToken = 0;

function drawNav(section) {
  nav.replaceChildren(
    ...NAV.map((item) =>
      h("a", { href: item.href(), "aria-current": item.key === section ? "page" : null }, icon(item.icon), item.text),
    ),
  );
}

function authPanel(message) {
  const input = h("input", { type: "password", id: "key", autocomplete: "current-password", required: true });
  const error = h("p", { class: "error-text", role: "alert" }, message || "");
  const form = h(
    "form",
    {
      class: "card stack",
      onsubmit: async (e) => {
        e.preventDefault();
        try {
          await login(input.value.trim());
          authed = true;
          route();
        } catch (err) {
          error.textContent = err.status === 429 ? "Too many attempts. Wait a minute and try again." : "The API key was rejected. Check API_KEY_ADMIN and try again.";
          input.select();
        }
      },
    },
    h("h1", {}, "Sign in"),
    h("p", { class: "muted" }, "Enter the key configured as API_KEY_ADMIN. The server answers with an HttpOnly session cookie; the key is not stored in the page."),
    error,
    h("div", { class: "field" }, h("label", { class: "label", for: "key" }, "API key"), input),
    h("button", { class: "btn btn-primary", type: "submit" }, "Sign in"),
  );
  replace(view, form);
  input.focus();
}

async function route() {
  const token = ++renderToken;
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }

  const [name = "jobs", ...params] = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const def = ROUTES[name] || ROUTES.jobs;
  if (name === "ranking" || name === "candidates") {
    if (params[0]) store.set("rs_job", params[0]);
  }
  drawNav(def.section);
  if (authed === null) authed = await checkSession();
  signout.hidden = !authed;
  document.title = `${def.title} - Resume Screener`;

  if (!authed) { authPanel(); return; }

  replace(view, h("p", { class: "muted" }, "Loading"));
  try {
    const result = await def.module.render(view, params, { api, navigate });
    if (token !== renderToken) { if (typeof result === "function") result(); return; }
    cleanup = typeof result === "function" ? result : null;
  } catch (err) {
    if (token !== renderToken) return;
    if (err.status === 401) return; // auth-required handler shows the key form
    replace(view, 
      h("div", { class: "card stack" }, h("h1", {}, "Something went wrong"), h("p", { class: "error-text", role: "alert" }, err.message)),
    );
  }
  const main = document.getElementById("main");
  main.focus({ preventScroll: true });
  announce(`${def.title} page loaded`);
}

export function navigate(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

window.addEventListener("hashchange", route);
window.addEventListener("auth-required", () => {
  authed = false;
  signout.hidden = true;
  authPanel("Your session ended. Sign in again.");
});
signout.addEventListener("click", async () => {
  await logout();
  authed = false;
  store.remove("rs_job");
  route();
  toast("Signed out");
});

route();
