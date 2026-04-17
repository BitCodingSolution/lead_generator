const BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8900"

async function handle(res: Response) {
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
      headers: { Accept: "application/json" },
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
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
    return handle(res) as Promise<T>
  },
}

export const swrFetcher = (path: string) => api.get(path)
