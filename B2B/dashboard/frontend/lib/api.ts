import { clearSession, getToken } from "./auth"

const BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

function authHeaders(): Record<string, string> {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}` } : {}
}

/** Public helper for callers that build their own fetch (e.g. multipart). */
export async function getAuthHeaders(): Promise<Record<string, string>> {
  return authHeaders()
}

async function handle(res: Response) {
  if (res.status === 401) {
    clearSession()
    if (typeof window !== "undefined") {
      // Bounce back to login. The LoginGate will pick up the cleared
      // session on next render.
      window.dispatchEvent(new Event("dashboard:logout"))
    }
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "")
    throw new Error(`${res.status} ${res.statusText}${text ? ` — ${text}` : ""}`)
  }
  const ct = res.headers.get("content-type") || ""
  if (ct.includes("application/json")) return res.json()
  return res.text()
}

export const api = {
  base: BASE,
  get: async <T = unknown>(path: string): Promise<T> => {
    const res = await fetch(`${BASE}${path}`, {
      method: "GET",
      cache: "no-store",
      headers: { Accept: "application/json", ...authHeaders() },
    })
    return handle(res) as Promise<T>
  },
  post: async <T = unknown>(path: string, body?: unknown): Promise<T> => {
    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...authHeaders(),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
    return handle(res) as Promise<T>
  },
  delete: async <T = unknown>(path: string): Promise<T> => {
    const res = await fetch(`${BASE}${path}`, {
      method: "DELETE",
      cache: "no-store",
      headers: { Accept: "application/json", ...authHeaders() },
    })
    return handle(res) as Promise<T>
  },
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const swrFetcher = (path: string) => api.get(path) as Promise<any>
