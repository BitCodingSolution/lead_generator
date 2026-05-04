const BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

// API key plumbing. Priority:
//   1. NEXT_PUBLIC_DASHBOARD_API_KEY  (set at build time)
//   2. localStorage[dashboard_api_key] (persisted from the bootstrap probe)
//   3. Loopback bootstrap call (/api/_bootstrap) — runs once on first
//      request that needs auth. Safe because the endpoint itself only
//      answers to 127.0.0.1 / ::1 callers.
//
// If the backend reports auth_required=false the whole mechanism is a
// no-op and we don't set the header.
const BUILD_TIME_KEY =
  (typeof process !== "undefined"
    ? (process.env as Record<string, string | undefined>).NEXT_PUBLIC_DASHBOARD_API_KEY
    : undefined) || ""

const KEY_STORAGE = "dashboard_api_key"

function readStoredKey(): string {
  if (BUILD_TIME_KEY) return BUILD_TIME_KEY
  if (typeof window === "undefined") return ""
  try {
    return window.localStorage.getItem(KEY_STORAGE) || ""
  } catch {
    return ""
  }
}

function writeStoredKey(key: string): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(KEY_STORAGE, key)
  } catch {
    /* storage blocked — use in-memory only */
  }
}

let cachedKey: string = readStoredKey()
let bootstrapPromise: Promise<string> | null = null

async function bootstrapKey(): Promise<string> {
  if (cachedKey) return cachedKey
  if (!bootstrapPromise) {
    bootstrapPromise = fetch(`${BASE}/api/_bootstrap`, {
      method: "GET",
      cache: "no-store",
      headers: { Accept: "application/json" },
    })
      .then(async (r) => {
        if (!r.ok) return ""
        const data = (await r.json().catch(() => ({}))) as {
          api_key?: string
          auth_required?: boolean
        }
        if (data.auth_required === false) return "" // auth disabled
        const k = data.api_key || ""
        if (k) {
          cachedKey = k
          writeStoredKey(k)
        }
        return k
      })
      .catch(() => "")
  }
  return bootstrapPromise
}

async function authHeaders(): Promise<Record<string, string>> {
  if (!cachedKey) await bootstrapKey()
  return cachedKey ? { "X-API-Key": cachedKey } : {}
}

/**
 * Public helper for callers that can't route through api.get/post
 * (e.g. multipart FormData uploads). Returns { "X-API-Key": ... }
 * once the key has been bootstrapped, or {} if auth is disabled.
 */
export async function getAuthHeaders(): Promise<Record<string, string>> {
  return authHeaders()
}

async function handle(res: Response) {
  // 401 likely means our cached key is stale — nuke it so the next
  // call re-bootstraps. Don't loop here; surface the error up.
  if (res.status === 401) {
    cachedKey = ""
    bootstrapPromise = null
    if (typeof window !== "undefined") {
      try { window.localStorage.removeItem(KEY_STORAGE) } catch { /* noop */ }
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
      headers: { Accept: "application/json", ...(await authHeaders()) },
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
        ...(await authHeaders()),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
    return handle(res) as Promise<T>
  },
  delete: async <T = unknown>(path: string): Promise<T> => {
    const res = await fetch(`${BASE}${path}`, {
      method: "DELETE",
      cache: "no-store",
      headers: { Accept: "application/json", ...(await authHeaders()) },
    })
    return handle(res) as Promise<T>
  },
}

// Cast to `any` so `useSWR<T>(key, swrFetcher)` infers the right data type
// at the call site. Without this, SWR's strict overloads reject the
// `unknown`-returning generic helper.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const swrFetcher = (path: string) => api.get(path) as Promise<any>
