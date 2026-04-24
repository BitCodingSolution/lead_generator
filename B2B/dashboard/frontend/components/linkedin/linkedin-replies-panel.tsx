"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import {
  MessageSquareReply, AlertTriangle, BotMessageSquare, RefreshCw, Loader2, Sparkles,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { api, swrFetcher } from "@/lib/api"
import { LinkedInLeadDrawer } from "./linkedin-lead-drawer"

type ReplyRow = {
  id: number
  lead_id: number | null
  from_email: string
  subject: string
  snippet: string
  received_at: string
  kind: "reply" | "bounce" | "auto_reply" | string
  sentiment: "positive" | "question" | "ooo" | "not_interested" | "referral" | null
  handled_at: string | null
  company: string | null
  posted_by: string | null
  role: string | null
  lead_email: string | null
  source?: "email" | "manual"
  call_status?: "green" | "yellow" | "red" | null
  auto_draft_body?: string | null
  auto_draft_at?: string | null
}

const SENTIMENT_STYLES: Record<string, { label: string; badge: string }> = {
  positive:       { label: "🟢 interested",     badge: "bg-emerald-500/15 text-emerald-300" },
  question:       { label: "❓ question",       badge: "bg-sky-500/15 text-sky-300" },
  ooo:            { label: "🏖 out of office",   badge: "bg-zinc-700/40 text-zinc-400" },
  not_interested: { label: "🔴 not interested", badge: "bg-rose-500/15 text-rose-300" },
  referral:       { label: "↗ referral",        badge: "bg-violet-500/15 text-violet-300" },
}

const KIND_STYLES: Record<string, { label: string; badge: string; icon: React.ReactNode }> = {
  reply: {
    label: "reply",
    badge: "bg-amber-500/15 text-amber-300",
    icon: <MessageSquareReply className="size-3" />,
  },
  bounce: {
    label: "bounce",
    badge: "bg-rose-500/15 text-rose-300",
    icon: <AlertTriangle className="size-3" />,
  },
  auto_reply: {
    label: "auto reply",
    badge: "bg-zinc-700/40 text-zinc-400",
    icon: <BotMessageSquare className="size-3" />,
  },
  manual: {
    label: "manual tag",
    badge: "bg-violet-500/15 text-violet-300",
    icon: <MessageSquareReply className="size-3" />,
  },
}

const ENDPOINT_BASE = "/api/linkedin/replies"

export function LinkedInRepliesPanel() {
  const [openLeadId, setOpenLeadId] = React.useState<number | null>(null)
  const onOpenLead = (leadId: number) => setOpenLeadId(leadId)
  const [handledFilter, setHandledFilter] = React.useState<"open" | "all">("open")
  const [sentimentFilter, setSentimentFilter] = React.useState<string>("")
  const qs = new URLSearchParams()
  qs.set("kind", "reply")
  qs.set("include_manual", "true")
  if (handledFilter === "open") qs.set("handled", "false")
  if (sentimentFilter) qs.set("sentiment", sentimentFilter)
  const endpoint = `${ENDPOINT_BASE}?${qs.toString()}`

  const { data, isLoading } = useSWR<{ rows: ReplyRow[] }>(
    endpoint,
    swrFetcher,
    { refreshInterval: 30_000 },
  )
  const [polling, setPolling] = React.useState(false)
  const [pollMsg, setPollMsg] = React.useState<string | null>(null)
  const [selected, setSelected] = React.useState<Set<number>>(new Set())
  const [bulkBusy, setBulkBusy] = React.useState(false)

  const rows = data?.rows ?? []
  const allVisibleIds = React.useMemo(() => rows.map((r) => r.id), [rows])
  const allSelected = allVisibleIds.length > 0 &&
    allVisibleIds.every((id) => selected.has(id))
  const someSelected = selected.size > 0

  function toggleOne(id: number) {
    setSelected((cur) => {
      const next = new Set(cur)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(allVisibleIds))
  }

  async function bulkHandle(handled: boolean) {
    if (!selected.size) return
    setBulkBusy(true)
    try {
      const res = await api.post<{ affected: number }>(
        "/api/linkedin/replies/bulk-handle",
        { reply_ids: Array.from(selected), handled },
      )
      setPollMsg(`Marked ${res.affected} ${handled ? "handled" : "unhandled"}`)
      setSelected(new Set())
      mutate((k) => typeof k === "string" && k.startsWith(ENDPOINT_BASE))
      mutate("/api/linkedin/overview")
    } catch (e) {
      setPollMsg((e as Error).message)
    } finally {
      setBulkBusy(false)
    }
  }

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
      mutate((k) => typeof k === "string" && k.startsWith(ENDPOINT_BASE))
      mutate("/api/linkedin/overview")
      mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/leads?"))
    } catch (err) {
      setPollMsg((err as Error).message)
    } finally {
      setPolling(false)
    }
  }

  return (
    <>
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b]">
      {someSelected && (
        <div className="flex items-center justify-between gap-3 p-2 border-b border-zinc-800/70 bg-[hsl(250_80%_62%)]/10">
          <div className="text-xs text-zinc-300">
            {selected.size} selected
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => bulkHandle(true)}
              disabled={bulkBusy}
              className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-2.5 py-1 text-xs text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              {bulkBusy ? <Loader2 className="size-3 animate-spin" /> : null}
              Mark {selected.size} handled
            </button>
            <button
              onClick={() => bulkHandle(false)}
              disabled={bulkBusy}
              className="inline-flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800/60 px-2.5 py-1 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
            >
              Mark unhandled
            </button>
            <button
              onClick={() => setSelected(new Set())}
              className="text-xs text-zinc-500 hover:text-zinc-300 px-1"
            >
              Clear
            </button>
          </div>
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3 p-3 border-b border-zinc-800/70">
        <div className="flex items-center gap-2">
          {rows.length > 0 && (
            <input
              type="checkbox"
              checked={allSelected}
              onChange={toggleAll}
              className="size-3.5 rounded border-zinc-700 bg-zinc-900/60"
              title="Select all visible"
            />
          )}
          <div className="text-sm font-medium text-zinc-200">Inbox feed</div>
          <select
            value={handledFilter}
            onChange={(e) => setHandledFilter(e.target.value as "open" | "all")}
            className="rounded-md border border-zinc-800 bg-zinc-900/60 px-1.5 py-0.5 text-xs text-zinc-200"
          >
            <option value="open">Unhandled only</option>
            <option value="all">All replies</option>
          </select>
          <select
            value={sentimentFilter}
            onChange={(e) => setSentimentFilter(e.target.value)}
            className="rounded-md border border-zinc-800 bg-zinc-900/60 px-1.5 py-0.5 text-xs text-zinc-200"
          >
            <option value="">All sentiments</option>
            <option value="positive">🟢 Interested</option>
            <option value="question">❓ Question</option>
            <option value="ooo">🏖 OOO</option>
            <option value="not_interested">🔴 Not interested</option>
            <option value="referral">↗ Referral</option>
            <option value="none">— Unclassified</option>
          </select>
        </div>
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
            const isManual = r.source === "manual"
            const style = isManual ? KIND_STYLES.manual : (KIND_STYLES[r.kind] ?? KIND_STYLES.reply)
            const sent = r.sentiment ? SENTIMENT_STYLES[r.sentiment] : null
            const clickable = r.lead_id != null
            return (
              <li
                key={r.id}
                className={cn(
                  "p-3 flex gap-3",
                  clickable
                    ? "hover:bg-zinc-800/50"
                    : "hover:bg-zinc-800/30",
                  r.handled_at && "opacity-60",
                  selected.has(r.id) && "bg-[hsl(250_80%_62%)]/10",
                )}
              >
                <input
                  type="checkbox"
                  checked={selected.has(r.id)}
                  onChange={() => toggleOne(r.id)}
                  onClick={(e) => e.stopPropagation()}
                  className="mt-0.5 size-3.5 rounded border-zinc-700 bg-zinc-900/60 shrink-0"
                />
                <div
                  className={cn("flex-1 min-w-0", clickable && "cursor-pointer")}
                  onClick={() => {
                    if (r.lead_id != null) onOpenLead(r.lead_id)
                  }}
                >
                <div className="flex items-center gap-2 flex-wrap">
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider",
                      style.badge,
                    )}
                  >
                    {style.icon}
                    {style.label}
                  </span>
                  {sent && (
                    <span
                      className={cn(
                        "inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-medium",
                        sent.badge,
                      )}
                    >
                      {sent.label}
                    </span>
                  )}
                  {r.auto_draft_body && (
                    <span
                      className="inline-flex items-center gap-0.5 rounded bg-[hsl(250_80%_62%)]/15 px-1.5 py-0.5 text-[10px] text-[hsl(250_80%_78%)]"
                      title="Claude pre-drafted a reply - open the lead to review"
                    >
                      <Sparkles className="size-2.5" />
                      auto-drafted
                    </span>
                  )}
                  {r.handled_at && (
                    <span className="text-[10px] text-emerald-400">✓ handled</span>
                  )}
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
                {isManual ? (
                  <div className="mt-1 text-xs text-zinc-500 italic">
                    {r.snippet
                      ? <>Note: {r.snippet}</>
                      : "Tagged Replied via call signal — no inbound email. Open the lead to draft a message."
                    }
                  </div>
                ) : (
                  r.snippet && (
                    <div className="mt-1 text-xs text-zinc-500 line-clamp-2">
                      {r.snippet}
                    </div>
                  )
                )}
                {(r.company || r.posted_by) && (
                  <div className="mt-1 text-[11px] text-zinc-500">
                    → <span className="text-zinc-400">
                      {r.company || r.posted_by}
                    </span>
                    {r.role && <span className="ml-1 text-zinc-500">· {r.role}</span>}
                  </div>
                )}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
    <LinkedInLeadDrawer leadId={openLeadId} onClose={() => setOpenLeadId(null)} />
    </>
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
