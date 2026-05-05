"use client"

import * as React from "react"

import {
  type AuthUser,
  clearSession,
  fetchMe,
  getStoredUser,
  getToken,
  login as apiLogin,
  logout as apiLogout,
} from "@/lib/auth"

type AuthState =
  | { status: "loading"; user: null }
  | { status: "unauthenticated"; user: null }
  | { status: "authenticated"; user: AuthUser }

type AuthContextValue = AuthState & {
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = React.createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = React.useState<AuthState>({ status: "loading", user: null })

  // Boot: rehydrate from localStorage, then validate the token against
  // /api/auth/me. If the token is missing, expired, or rejected, drop
  // the session.
  React.useEffect(() => {
    let cancelled = false
    const stored = getStoredUser()
    if (stored) setState({ status: "authenticated", user: stored })
    ;(async () => {
      if (!getToken()) {
        if (!cancelled) setState({ status: "unauthenticated", user: null })
        return
      }
      const me = await fetchMe()
      if (cancelled) return
      if (me) setState({ status: "authenticated", user: me })
      else setState({ status: "unauthenticated", user: null })
    })()
    return () => {
      cancelled = true
    }
  }, [])

  // The api client emits this when a request returns 401, so the gate
  // re-renders and the user lands back at the login form.
  React.useEffect(() => {
    function onLogout() {
      setState({ status: "unauthenticated", user: null })
    }
    window.addEventListener("dashboard:logout", onLogout)
    return () => window.removeEventListener("dashboard:logout", onLogout)
  }, [])

  const value = React.useMemo<AuthContextValue>(
    () => ({
      ...state,
      login: async (username, password) => {
        const user = await apiLogin(username, password)
        setState({ status: "authenticated", user })
      },
      logout: async () => {
        await apiLogout()
        clearSession()
        setState({ status: "unauthenticated", user: null })
      },
    }),
    [state],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>")
  return ctx
}
