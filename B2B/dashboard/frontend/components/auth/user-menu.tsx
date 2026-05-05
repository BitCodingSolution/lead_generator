"use client"

import * as React from "react"
import { LogOut, User2 } from "lucide-react"

import { useAuth } from "./auth-provider"

/** Compact account chip for the topbar. Renders nothing if not signed in. */
export function UserMenu() {
  const { status, user, logout } = useAuth()
  const [pending, setPending] = React.useState(false)

  if (status !== "authenticated" || !user) return null

  async function onLogout() {
    setPending(true)
    try {
      await logout()
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="inline-flex items-center gap-2 rounded-md border border-zinc-800/80 bg-zinc-900/50 px-2 py-1 text-[11px]">
      <User2 className="size-3 text-zinc-400" />
      <span className="max-w-[140px] truncate text-zinc-300" title={user.username}>
        {user.username}
      </span>
      <button
        type="button"
        onClick={onLogout}
        disabled={pending}
        className="ml-1 inline-flex items-center gap-1 rounded border border-zinc-700 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 disabled:opacity-60"
        title="Sign out"
      >
        <LogOut className="size-2.5" />
        <span>{pending ? "…" : "Sign out"}</span>
      </button>
    </div>
  )
}
