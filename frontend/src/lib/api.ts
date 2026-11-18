// Thin fetch wrapper. The token lives in memory and sessionStorage (cleared when the tab closes).
// Error messages surfaced to the UI come from the API's sanitised `detail`; request bodies are never logged.

const TOKEN_KEY = "fl.token";
let token: string | null = null;
try {
  token = sessionStorage.getItem(TOKEN_KEY);
} catch {
  token = null;
}
let onUnauthorized: (() => void) | null = null;

export function setToken(t: string | null) {
  token = t;
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable */
  }
}
export const getToken = () => token;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public errors?: { loc: unknown[]; msg: string }[]) {
    super(message);
  }
}

async function parseError(res: Response): Promise<ApiError> {
  let msg = `Request failed (${res.status})`;
  let errors;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") msg = body.detail;
    if (Array.isArray(body.errors)) {
      errors = body.errors;
      msg = body.errors.map((e: { loc: unknown[]; msg: string }) => `${(e.loc || []).slice(1).join(".")}: ${e.msg}`).join("; ") || msg;
    }
  } catch {
    /* non-JSON */
  }
  return new ApiError(res.status, msg, errors);
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  const res = await fetch(`/api${path}`, { ...init, headers, body });
  if (res.status === 401 && path !== "/auth/login") {
    setToken(null);
    onUnauthorized?.();
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

/** Fetch an authenticated file and trigger a browser download. */
export async function download(path: string, fallbackName = "download") {
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`/api${path}`, { headers });
  if (!res.ok) throw await parseError(res);
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const m = /filename="?([^";]+)"?/.exec(cd);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = m?.[1] ?? fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/** Fetch an authenticated file as an object URL (for inline previews). */
export async function objectUrl(path: string): Promise<string> {
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`/api${path}`, { headers });
  if (!res.ok) throw await parseError(res);
  return URL.createObjectURL(await res.blob());
}

export async function upload<T>(path: string, form: FormData): Promise<T> {
  return api<T>(path, { method: "POST", body: form });
}
