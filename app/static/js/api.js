// The API key never lives in JavaScript: the browser logs in once (POST /api/session) and
// keeps an HttpOnly, SameSite=Strict cookie. State-changing requests carry a custom header
// that a cross-site page cannot send.

const CSRF = { "X-Requested-With": "resume-screener" };

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.message || `Request failed (${status})`);
    this.status = status;
    this.code = body?.code || "error";
  }
}

async function readJson(response) {
  try { return await response.json(); } catch { return null; }
}

export async function checkSession() {
  try {
    const response = await fetch("/api/session", { credentials: "same-origin" });
    return response.ok;
  } catch {
    return false;
  }
}

export async function login(key) {
  const response = await fetch("/api/session", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  });
  if (!response.ok) throw new ApiError(response.status, await readJson(response));
}

export async function logout() {
  try {
    await fetch("/api/session", { method: "DELETE", credentials: "same-origin", headers: CSRF });
  } catch { /* ignore */ }
}

export async function api(path, { method = "GET", body } = {}) {
  const headers = { ...CSRF };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (response.status === 204) return null;
  const data = await readJson(response);
  if (response.status === 401) window.dispatchEvent(new Event("auth-required"));
  if (!response.ok) throw new ApiError(response.status, data);
  return data;
}

/** Upload ONE file (own request) so the UI can show per-file progress. */
export function uploadFile(jobId, file, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("files", file, file.name);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/jobs/${jobId}/candidates`);
    for (const [name, value] of Object.entries(CSRF)) xhr.setRequestHeader(name, value);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status === 401) window.dispatchEvent(new Event("auth-required"));
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else {
        const err = new ApiError(xhr.status, data);
        err.retryAfter = Number(xhr.getResponseHeader("Retry-After")) || 0;
        reject(err);
      }
    };
    xhr.onerror = () => reject(new ApiError(0, { message: "Network error" }));
    xhr.send(form);
  });
}
