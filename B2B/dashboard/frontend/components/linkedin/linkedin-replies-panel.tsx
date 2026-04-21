"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import {
  MessageSquareReply, AlertTriangle, BotMessageSquare, RefreshCw, Loader2,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { api, swrFetcher } from "@/lib/api"

type ReplyRow = {
  id: number
  lead_id: number | null
  from_email: string
  subject: string
  snippet: string
  received_at: string
  kind: "reply" | "bounce" | "auto_reply" | string
  company: string | null
  posted_by: string | null
  lead_email: string | null
}

const ENDPOINT = "/api/linkedin/replies"

const KIND_STYLES: Record<string, { badge: string; icon: React.ReactNode }> = {
  reply: {
    badge: "bg-amber-500/15 text-amber-300",
    icon: <MessageSquareReply className="size-3" />,
  },
  bounce: {
    badge: "bg-rose-500/15 text-rose-300",
    icon: <AlertTriangle className="size-3" />,
  },
  auto_reply: {
    badge: "bg-zinc-700/40 text-zinc-400",
    icon: <BotMessageSquare className="size-3" />,
  },
}

export function LinkedInRepliesPanel() {
  const { data, isLoading } = useSWR<{ rows: ReplyRow[] }>(
    ENDPOINT,
    swrFetcher,
    { refreshInterval: 30_000 },
  )
  const [polling, setPolling] = React.useState(false)
  const [pollMsg, setPollMsg] = React.useState<string | null>(null)

  const rows = data?.rows ?? []

  async function onPoll() {
    setPolling(true)
    setPollMsg(null)
    try {
      const res = await api.post<{
        fetched: number
        replies: number
        bounces: number
        auto_replies: number
        matched: number
      }>("/api/linkedin/replies/poll")
      setPollMsg(
        `Fetched ${res.fetched} · matched ${res.matched} (${res.replies} replies, ${res.bounces} bounces, ${res.auto_replies} auto)`,
      )
      mutate(ENDPOINT)
      mutate("/api/linkedin/overview")
      mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/leads?"))
    } catch (err) {
      setPollMsg((err as Error).message)
    } finally {
      setPolling(false)
    }
  }

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b]">
      <div className="flex items-center justify-between gap-3 p-3 border-b border-zinc-800/70">
        <div className="text-sm font-medium text-zinc-200">Inbox feed</div>
        <div className="flex items-center gap-2">
          {pollMsg && (
            <span className="text-[11px] text-zinc-500">{pollMsg}</span>
          )}
          <button
            onClick={onPoll}
            disabled={polling}
            className="inline-flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800/60 px-2.5 py-1 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
          >
            {polling ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <RefreshCw className="size-3" />
            )}
            Poll now
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="p-6 text-sm text-zinc-500">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="p-10 text-center">
          <div className="mx-auto size-10 rounded-full bg-zinc-800/60 flex items-center justify-center mb-3">
            <MessageSquareReply className="size-5 text-zinc-500" />
          </div>
          <div className="text-sm text-zinc-300">No replies yet</div>
          <div className="mt-1 text-xs text-zinc-500 max-w-sm mx-auto">
            Automatic polling runs every 5 minutes. Use Poll now to trigger
            immediately.
          </div>
        </div>
      ) : (
        <ul className="divide-y divide-zinc-800/60">
          {rows.map((r) => {
            const style = KIND_STYLES[r.kind] ?? KIND_STYLES.reply
            return (
              <li key={r.id} className="p-3 hover:bg-zinc-800/30">
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider",
                      style.badge,
                    )}
                  >
                    {style.icon}
                    {r.kind.replace("_", " ")}
                  </span>
                  <span className="text-sm text-zinc-200 truncate">
                    {r.from_email || "(unknown sender)"}
                  </span>
                  <span className="ml-auto text-[11px] text-zinc-500 tnum">
                    {fmtDate(r.received_at)}
                  </span>
                </div>
                <div className="mt-1 text-sm text-zinc-300 truncate">
                  {r.subject || "(no subject)"}
                </div>
                {r.snippet && (
                  <div className="mt-1 text-xs text-zinc-500 line-clamp-2">
                    {r.snippet}
                  </div>
                )}
                {(r.company || r.posted_by) && (
                  <div className="mt-1 text-[11px] text-zinc-500">
                    → <span className="text-zinc-400">
                      {r.company || r.posted_by}
                    </span>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function fmtDate(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    })
  } catch {
    return iso
  }
}
