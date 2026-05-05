"use client"

import * as React from "react"
import useSWR from "swr"
import Link from "next/link"
import { toast } from "sonner"
import { api, swrFetcher } from "@/lib/api"
import type { Health } from "@/lib/types"
import { Search, Activity, Plug, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { UserMenu } from "@/components/auth/user-menu"

export function Topbar() {
  const { data: health, error } = useSWR<Health>("/api/health", swrFetcher, {
    refreshInterval: 15000,
    revalidateOnFocus: false,
  })
  const { data: bridge, mutate: mutateBridge } = useSWR<{ ok: boolean }>(
    "/api/bridge-health",
    swrFetcher,
    { refreshInterval: 15000, revalidateOnFocus: false },
  )
  const ok = !!health?.ok && !error
  const bridgeOk = !!bridge?.ok
  const [starting, setStarting] = React.useState(false)

  async function startBridge() {
    setStarting(true)
    try {
      const res = await api.post<{ ok: boolean; already_running: boolean; hint?: string }>(
        "/api/actions/start-bridge",
        {},
      )
      if (res.ok) {
        toast.success(res.already_running ? "Bridge already running" : "Bridge started")
      } else {
        toast.error("Bridge launched but not responding", { description: res.hint })
      }
      mutateBridge()
    } catch (e) {
      toast.error("Start failed", {
        description: e instanceof Error ? e.message : String(e),
      })
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="h-14 border-b border-zinc-800/80 bg-[#0a0a0a]/80 backdrop-blur flex items-center px-6 lg:px-8 sticky top-0 z-30">
      <button
        type="button"
        onClick={() => {
          window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }))
        }}
        aria-label="Open command palette"
        className="hidden sm:flex items-center gap-2 w-[340px] max-w-full text-left rounded-md border border-zinc-800/80 bg-zinc-900/50 hover:bg-zinc-900 hover:border-zinc-700 transition-colors px-3 py-1.5 text-sm text-zinc-500"
      >
        <Search className="size-3.5" />
        <span>Search or jump to…</span>
        <kbd className="ml-auto rounded border border-zinc-700 bg-zinc-800 px-1.5 py-0.5 text-[10px] font-mono text-zinc-400">
          ⌘K
        </kbd>
      </button>

      <div className="ml-auto flex items-center gap-4">
        <Link
          href="/campaigns"
          className="hidden md:inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-zinc-200 transition-colors"
        >
          Go to Campaigns
        </Link>
        <div
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md border pl-2 pr-1 py-1 text-[11px] font-medium",
            bridgeOk
              ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-400"
              : "border-amber-500/20 bg-amber-500/10 text-amber-400",
          )}
          title={bridgeOk ? "Bridge reachable on :8766" : "Bridge not responding — drafts/replies will fail"}
        >
          <Plug className="size-3" />
          <span>{bridgeOk ? "Bridge online" : "Bridge offline"}</span>
          {!bridgeOk && (
            <button
              type="button"
              onClick={startBridge}
              disabled={starting}
              className="ml-1 rounded border border-amber-400/30 bg-amber-400/10 hover:bg-amber-400/20 disabled:opacity-60 px-1.5 py-0.5 text-[10px] uppercase tracking-wide inline-flex items-center gap-1"
            >
              {starting ? <Loader2 className="size-2.5 animate-spin" /> : null}
              {starting ? "Starting" : "Start"}
            </button>
          )}
        </div>
        <div
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-medium",
            ok
              ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-400"
              : "border-rose-500/20 bg-rose-500/10 text-rose-400",
          )}
        >
          <Activity className="size-3" />
          <span>{ok ? "API online" : "API offline"}</span>
        </div>
        <UserMenu />
      </div>
    </div>
  )
}
