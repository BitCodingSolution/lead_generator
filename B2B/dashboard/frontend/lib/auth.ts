/**
 * Lightweight auth client: stores a JWT in localStorage, exposes
 * login/logout/me, and is the single source for the bearer token used
 * by `lib/api.ts`.
 */

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

const TOKEN_KEY = "dashboard_token"
const USER_KEY = "dashboard_user"

export type AuthUser = {
  id: number
  username: string
}

export function getToken(): string {
  if (typeof window === "undefined") return ""
  try {
    return window.localStorage.getItem(TOKEN_KEY) || ""
  } catch {
    return ""
  }
}

export function getStoredUser(): AuthUser | null {
  if (typeof window === "undefined") return null
  try {
    const raw = window.localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as AuthUser) : null
  } catch {
    return null
  }
}

function setSession(token: string, user: AuthUser): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token)
    window.localStorage.setItem(USER_KEY, JSON.stringify(user))
  } catch {
    /* storage blocked — best effort */
  }
}

export function clearSession(): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.removeItem(TOKEN_KEY)
    window.localStorage.removeItem(USER_KEY)
  } catch {
    /* noop */
  }
}

export async function login(username: string, password: string): Promise<AuthUser> {
  const res = await fetch(`${BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => "")
    let detail = `${res.status} ${res.statusText}`
    try {
      const data = JSON.parse(text)
      if (data?.detail) detail = String(data.detail)
    } catch {
      /* keep default */
    }
    throw new Error(detail)
  }
  const data = (await res.json()) as {
    access_token: string
    user: AuthUser
  }
  setSession(data.access_token, data.user)
  return data.user
}

export async function logout(): Promise<void> {
  const token = getToken()
  if (token) {
    try {
      await fetch(`${BASE}/api/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      })
    } catch {
      /* server-side logout is best-effort; we always clear local state */
    }
  }
  clearSession()
}

export async function fetchMe(): Promise<AuthUser | null> {
  const token = getToken()
  if (!token) return null
  const res = await fetch(`${BASE}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (res.status === 401) {
    clearSession()
    return null
  }
  if (!res.ok) return null
  const data = (await res.json()) as AuthUser
  setSession(token, { id: data.id, username: data.username })
  return { id: data.id, username: data.username }
}
