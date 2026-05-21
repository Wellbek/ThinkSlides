/**
 * API client for communicating with the orchestrator and AI service.
 */

const ORCHESTRATOR_URL =
  typeof window !== "undefined"
    ? "/api/orchestrator"
    : process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

const AI_URL =
  typeof window !== "undefined"
    ? "/api/ai"
    : process.env.NEXT_PUBLIC_AI_URL || "http://localhost:8000";

async function request(
  baseUrl: string,
  path: string,
  options: RequestInit = {}
): Promise<Response> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers,
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "Unknown error");
    throw new Error(`API error ${res.status}: ${body}`);
  }

  return res;
}

export const api = {
  // ── Auth ──────────────────────────────────────
  login: (email: string, password: string) =>
    request(ORCHESTRATOR_URL, "/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }).then((r) => r.json()),

  // ── Sessions ──────────────────────────────────
  listSessions: () =>
    request(ORCHESTRATOR_URL, "/sessions").then((r) => r.json()),

  getSession: (id: string) =>
    request(ORCHESTRATOR_URL, `/sessions/${id}`).then((r) => r.json()),

  createSession: (data: { title?: string; templateId?: string; brief?: string }) =>
    request(ORCHESTRATOR_URL, "/sessions", {
      method: "POST",
      body: JSON.stringify(data),
    }).then((r) => r.json()),

  deleteSession: (id: string) =>
    request(ORCHESTRATOR_URL, `/sessions/${id}`, { method: "DELETE" }),

  // ── Messages ──────────────────────────────────
  sendMessage: (sessionId: string, data: { content: string; attachments?: unknown[] }) =>
    request(ORCHESTRATOR_URL, `/sessions/${sessionId}/messages`, {
      method: "POST",
      body: JSON.stringify(data),
    }).then((r) => r.json()),

  // ── Templates ─────────────────────────────────
  listTemplates: () =>
    request(AI_URL, "/templates").then((r) => r.json()),

  uploadTemplate: (formData: FormData) =>
    fetch(`${AI_URL}/templates`, {
      method: "POST",
      headers: { Authorization: `Bearer ${getToken()}` },
      body: formData,
    }).then((r) => r.json()),

  // ── SSE Stream ────────────────────────────────
  sessionEvents: (sessionId: string): EventSource => {
    const token = getToken();
    const url = `${ORCHESTRATOR_URL}/sessions/${sessionId}/events`;
    const es = new EventSource(url);
    return es;
  },

  // ── Generated Files ───────────────────────────
  downloadFile: (fileId: string) =>
    request(ORCHESTRATOR_URL, `/files/${fileId}`).then((r) => r.blob()),
};

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("slideagent_token");
}

export function setToken(token: string) {
  if (typeof window !== "undefined") {
    localStorage.setItem("slideagent_token", token);
  }
}

export function clearToken() {
  if (typeof window !== "undefined") {
    localStorage.removeItem("slideagent_token");
  }
}