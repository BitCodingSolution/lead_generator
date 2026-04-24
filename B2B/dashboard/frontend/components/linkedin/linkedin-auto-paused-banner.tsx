"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import Link from "next/link"
import { AlertOctagon, Play, Loader2 } from "lucide-react"
import { api, swrFetcher } from "@/lib/api"

type AutoPausedAccount = { id: number; email: string; reason: string }
type Overview = { auto_paused_accounts?: AutoPausedAccount[] }

export function LinkedInAutoPausedBanner() {
  const { data } = useSWR<Overview>("/api/linkedin/overview", swrFetcher, {
    refreshInterval: 15_000,
  })
  const paused = data?.auto_paused_accounts ?? []
  const [busy, setBusy] = React.useState<number | null>(null)

  if (paused.length === 0) return null

  async function resume(id: number) {
    setBusy(id)
    try {
      await api.post(`/api/linkedin/gmail/accounts/${id}/resume`)
      mutate("/api/linkedin/overview")
      mutate("/api/linkedin/gmail/accounts")
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3">
      <div className="flex items-start gap-2">
        <AlertOctagon className="size-4 text-rose-300 mt-0.5" />
        <div className="flex-1 space-y-2">
          <div className="text-sm font-medium text-rose-100">
            {paused.length === 1
              ? "A Gmail account was auto-paused due to deliverability issues"
              : `${paused.length} Gmail accounts were auto-paused due to deliverability issues`}
          </div>
          <div className="space-y-1.5">
            {paused.map((a) => (
              <div
                key={a.id}
                className="flex items-center justify-between gap-3 rounded-md bg-rose-500/5 px-2.5 py-1.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-xs text-rose-100 truncate">
                    {a.email}
                  </div>
                  <div className="text-[11px] text-rose-300/80 truncate">
                    {a.reason}
                  </div>
                </div>
                <button
                  onClick={() => resume(a.id)}
                  disabled={busy === a.id}
                  className="inline-flex items-center gap-1 rounded-md border border-rose-400/50 bg-rose-500/20 px-2 py-1 text-[11px] text-rose-100 hover:bg-rose-500/30 disabled:opacity-50"
                >
                  {busy === a.id ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    <Play className="size-3" />
                  )}
                  Resume
                </button>
              </div>
            ))}
          </div>
          <div className="text-[11px] text-rose-300/80">
            Review deliverability before resuming. See{" "}
            <Link
              href="/linkedin/settings"
              className="underline hover:text-rose-200"
            >
              Settings → Gmail
            </Link>{" "}
            for details.
          </div>
        </div>
      </div>
    </div>
  )
}
