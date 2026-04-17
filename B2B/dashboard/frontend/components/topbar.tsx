"use client"

import useSWR from "swr"
import Link from "next/link"
import { swrFetcher } from "@/lib/api"
import type { Health } from "@/lib/types"
import { Search, Activity } from "lucide-react"
import { cn } from "@/lib/utils"

export function Topbar() {
  const { data: health, error } = useSWR<Health>("/api/health", swrFetcher, {
    refreshInterval: 15000,
    revalidateOnFocus: false,
  })
  const ok = !!health?.ok && !error

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
            "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-medium",
            ok
              ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-400"
              : "border-rose-500/20 bg-rose-500/10 text-rose-400",
          )}
        >
          <Activity className="size-3" />
          <span>{ok ? "API online" : "API offline"}</span>
        </div>
      </div>
    </div>
  )
}
