"use client"

import * as React from "react"
import { useSearchParams } from "next/navigation"
import useSWR, { mutate } from "swr"
import { Inbox, Mail, Send, Clock, Trash2, Loader2, Sparkles } from "lucide-react"
import { PageHeader } from "@/components/page-header"
import { LinkedInLeadsTable } from "@/components/linkedin/linkedin-leads-table"
import { LinkedInBatchSend } from "@/components/linkedin/linkedin-batch-send"
import { LinkedInRepliesPanel } from "@/components/linkedin/linkedin-replies-panel"
import { LinkedInRecyclebinList } from "@/components/linkedin/linkedin-recyclebin-list"
import { ExportCsvButton } from "@/components/linkedin/linkedin-export-csv"
import { api, swrFetcher } from "@/lib/api"
import { cn } from "@/lib/utils"

type Tab = "all" | "drafts" | "sent" | "followups" | "recyclebin"

const VALID_TABS: readonly Tab[] = ["all", "drafts", "sent", "followups", "recyclebin"] as const

type Counts = {
  new: number
  drafted: number
  queued: number
  sent_today: number
  replied: number
  bounced: number
  total: number
}

export default function LinkedInLeadsPage() {
  const searchParams = useSearchParams()
  const urlTab = searchParams?.get("tab") as Tab | null
  const initialTab: Tab = urlTab && VALID_TABS.includes(urlTab) ? urlTab : "all"
  const [tab, setTab] = React.useState<Tab>(initialTab)
  // Keep state in sync if user clicks another KPI while on this page
  React.useEffect(() => {
    if (urlTab && VALID_TABS.includes(urlTab) && urlTab !== tab) {
      setTab(urlTab)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlTab])

  const { data: overview } = useSWR<Counts>("/api/linkedin/overview", swrFetcher, {
    refreshInterval: 15_000,
  })
  const { data: bin } = useSWR<{ rows: unknown[] }>(
    "/api/linkedin/recyclebin",
    swrFetcher,
    { refreshInterval: 30_000 },
  )
  const { data: followups } = useSWR<{ rows: unknown[] }>(
    "/api/linkedin/followups",
    swrFetcher,
    { refreshInterval: 60_000 },
  )

  const counts: Record<Tab, number> = {
    all: overview?.total ?? 0,
    drafts: overview?.drafted ?? 0,
    sent: (overview?.replied ?? 0) + (overview?.bounced ?? 0) + (overview?.sent_today ?? 0),
    followups: followups?.rows.length ?? 0,
    recyclebin: bin?.rows.length ?? 0,
  }

  const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "all", label: "All", icon: <Inbox className="size-3.5" /> },
    { id: "drafts", label: "Drafts", icon: <Mail className="size-3.5" /> },
    { id: "sent", label: "Sent & Replies", icon: <Send className="size-3.5" /> },
    { id: "followups", label: "Follow-ups", icon: <Clock className="size-3.5" /> },
    { id: "recyclebin", label: "Recyclebin", icon: <Trash2 className="size-3.5" /> },
  ]

  return (
    <div className="space-y-6">
      <PageHeader
        title="Leads"
        subtitle="Everything the extension has captured — filter by lifecycle stage."
        actions={
          <ExportCsvButton
            href={
              tab === "recyclebin"
                ? "/api/linkedin/recyclebin/export"
                : "/api/linkedin/leads/export"
            }
            filename={
              tab === "recyclebin"
                ? "linkedin_recyclebin.csv"
                : "linkedin_leads.csv"
            }
          />
        }
      />

      <div className="flex items-center gap-1 rounded-lg border border-zinc-800 bg-[#18181b] p-1 overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs transition-colors whitespace-nowrap",
              tab === t.id
                ? "bg-[hsl(250_80%_62%/0.18)] text-zinc-100"
                : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50",
            )}
          >
            {t.icon}
            <span>{t.label}</span>
            <span
              className={cn(
                "ml-1 rounded px-1.5 py-0.5 text-[10px] font-medium tnum",
                tab === t.id
                  ? "bg-zinc-900/60 text-zinc-300"
                  : "bg-zinc-800/60 text-zinc-500",
              )}
            >
              {counts[t.id]}
            </span>
          </button>
        ))}
      </div>

      {(tab === "all" || tab === "drafts") && (
        <GenerateDraftsInline newCount={overview?.new ?? 0} />
      )}

      {tab === "drafts" && <LinkedInBatchSend />}

      {tab === "all" && <LinkedInLeadsTable />}
      {tab === "drafts" && <LinkedInLeadsTable initialStatus="Drafted" />}
      {tab === "sent" && (
        <div className="space-y-4">
          <LinkedInRepliesPanel />
          <div>
            <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-500 mb-2">
              Sent leads
            </div>
            <LinkedInLeadsTable initialStatus="Sent" />
          </div>
        </div>
      )}
      {tab === "followups" && <FollowupsInline />}
      {tab === "recyclebin" && <RecyclebinInline />}
    </div>
  )
}

// ---------- follow-ups inline ----------

function FollowupsInline() {
  const { data, isLoading } = useSWR<{
    rows: {
      id: number
      company: string | null
      posted_by: string | null
      email: string | null
      gen_subject: string | null
      sent_at: string
      next_sequence: number
      days_since_last_touch: number
    }[]
    cadence: number[]
  }>("/api/linkedin/followups", swrFetcher, { refreshInterval: 30_000 })

  const rows = data?.rows ?? []
  const cadence = data?.cadence ?? [3, 7]
  const [busy, setBusy] = React.useState(false)
  const [msg, setMsg] = React.useState<string | null>(null)

  async function runAll() {
    if (rows.length === 0) return
    if (!confirm(`Send ${rows.length} follow-up${rows.length === 1 ? "" : "s"}?`)) return
    setBusy(true)
    try {
      const res = await api.post<{
        sent?: number
        skipped?: number
        errors?: unknown[]
        blocked_by_safety?: string
      }>("/api/linkedin/followups/run", { dry_run: false })
      setMsg(
        res.blocked_by_safety
          ? `Safety blocked: ${res.blocked_by_safety}`
          : `Sent ${res.sent} · skipped ${res.skipped} · errors ${(res.errors ?? []).length}`,
      )
      mutate("/api/linkedin/followups")
      mutate("/api/linkedin/overview")
    } catch (err) {
      setMsg((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-3 flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          <Clock className="size-3.5 text-zinc-500" />
          Cadence: {cadence.join("d, ")}d after last touch · {rows.length} due
        </div>
        <div className="flex items-center gap-2">
          {msg && <span className="text-[11px] text-zinc-500">{msg}</span>}
          <button
            onClick={runAll}
            disabled={busy || rows.length === 0}
            className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1 text-xs text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {busy ? <Loader2 className="size-3 animate-spin" /> : <Send className="size-3" />}
            Send all
          </button>
        </div>
      </div>

      <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] overflow-hidden">
        {isLoading ? (
          <div className="p-6 text-sm text-zinc-500">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="p-10 text-center text-sm text-zinc-400">
            No follow-ups due
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-[0.08em] text-zinc-500 border-b border-zinc-800/70">
                <th className="px-3 py-2 font-medium">Company</th>
                <th className="px-3 py-2 font-medium">Email</th>
                <th className="px-3 py-2 font-medium w-24">Last touch</th>
                <th className="px-3 py-2 font-medium w-24">Sequence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                  <td className="px-3 py-2 text-zinc-200">
                    {r.company || r.posted_by || "—"}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-zinc-300">
                    {r.email || "—"}
                  </td>
                  <td className="px-3 py-2 text-[11px] text-zinc-500 tnum">
                    {r.days_since_last_touch}d ago
                  </td>
                  <td className="px-3 py-2">
                    <span className="inline-flex items-center rounded bg-violet-500/15 px-1.5 py-0.5 text-[11px] font-medium text-violet-300">
                      #{r.next_sequence} of {cadence.length}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ---------- generate drafts inline ----------

type DraftBatchStatus = {
  running: boolean
  total: number
  drafted: number
  skipped: number
  failed: number
  last_error: string | null
}

function GenerateDraftsInline({ newCount }: { newCount: number }) {
  const { data: status } = useSWR<DraftBatchStatus>(
    "/api/linkedin/drafts/generate/status",
    swrFetcher,
    { refreshInterval: 2000 },
  )
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState<string | null>(null)

  const running = status?.running ?? false
  const done = (status?.drafted ?? 0) + (status?.skipped ?? 0) + (status?.failed ?? 0)
  const total = status?.total ?? 0
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0

  const prevRunning = React.useRef(running)
  React.useEffect(() => {
    if (prevRunning.current && !running) {
      mutate("/api/linkedin/overview")
      mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/leads"))
    }
    prevRunning.current = running
  }, [running])

  async function startBatch() {
    setErr(null)
    setBusy(true)
    try {
      await api.post("/api/linkedin/drafts/generate/batch", { max: 100 })
      mutate("/api/linkedin/drafts/generate/status")
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (newCount === 0 && !running) return null

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-3 flex items-center justify-between flex-wrap gap-3">
      <div className="flex items-center gap-2 text-xs text-zinc-400">
        <Sparkles className="size-3.5 text-[hsl(250_80%_62%)]" />
        {running ? (
          <span>
            Drafting {done}/{total} · {pct}% · {status?.drafted ?? 0} drafted · {status?.skipped ?? 0} skipped · {status?.failed ?? 0} failed
          </span>
        ) : (
          <span>
            {newCount} lead{newCount === 1 ? "" : "s"} need drafting. Claude runs 4 in parallel.
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        {err && <span className="text-[11px] text-rose-400">{err}</span>}
        <button
          onClick={startBatch}
          disabled={busy || running || newCount === 0}
          className="inline-flex items-center gap-1.5 rounded-md bg-[hsl(250_80%_62%)] px-3 py-1 text-xs font-medium text-white hover:brightness-110 disabled:opacity-50"
        >
          {busy || running ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <Sparkles className="size-3" />
          )}
          {running ? "Drafting…" : "Generate drafts"}
        </button>
      </div>
    </div>
  )
}

// ---------- recyclebin inline ----------

function RecyclebinInline() {
  const [busy, setBusy] = React.useState(false)

  async function onEmpty() {
    if (!confirm("Permanently delete ALL recyclebin entries?")) return
    setBusy(true)
    try {
      await api.post("/api/linkedin/recyclebin/empty")
      mutate("/api/linkedin/recyclebin")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          <Trash2 className="size-3.5 text-zinc-500" />
          Auto-skipped, rejected, and archived leads. Restore any row back to active.
        </div>
        <button
          onClick={onEmpty}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-1 text-xs text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
        >
          {busy ? <Loader2 className="size-3 animate-spin" /> : <Trash2 className="size-3" />}
          Empty bin
        </button>
      </div>
      <LinkedInRecyclebinList />
    </div>
  )
}
