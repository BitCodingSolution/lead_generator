"use client"

import * as React from "react"
import { useSearchParams } from "next/navigation"
import useSWR, { mutate } from "swr"
import {
  Search, Inbox, Send, Loader2, Eye, Clock, FileWarning,
  ArrowUp, ArrowDown, ArrowUpDown,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { api, swrFetcher } from "@/lib/api"
import type { LinkedInLead, LinkedInLeadsResponse } from "@/lib/types"
import { LinkedInLeadDrawer } from "./linkedin-lead-drawer"

const STATUS_STYLES: Record<string, string> = {
  New: "bg-zinc-700/40 text-zinc-300",
  Drafted: "bg-sky-500/15 text-sky-300",
  Queued: "bg-violet-500/15 text-violet-300",
  Sending: "bg-violet-500/25 text-violet-200",
  Sent: "bg-emerald-500/15 text-emerald-300",
  Replied: "bg-amber-500/15 text-amber-300",
  Bounced: "bg-rose-500/15 text-rose-300",
  Skipped: "bg-zinc-700/30 text-zinc-500",
}

export function LinkedInLeadsTable({
  initialStatus,
}: {
  initialStatus?: LinkedInLead["status"]
}) {
  const searchParams = useSearchParams()
  const urlStatus = searchParams?.get("status") ?? ""
  const [status, setStatus] = React.useState<string>(initialStatus ?? urlStatus)
  // Keep local state in sync when the URL changes (e.g. user clicks another KPI)
  React.useEffect(() => {
    if (initialStatus) return
    setStatus(urlStatus)
  }, [urlStatus, initialStatus])
  const [callFilter, setCallFilter] = React.useState<string>("")
  // Sort accepts:
  //   "recent" (default), "score" — the legacy global modes
  //   "{col}_asc" / "{col}_desc" — set by clicking a column header
  const [sort, setSort] = React.useState<string>("recent")
  const [q, setQ] = React.useState("")
  const [debounced, setDebounced] = React.useState("")
  const [openId, setOpenId] = React.useState<number | null>(null)
  const [sendingId, setSendingId] = React.useState<number | null>(null)

  async function quickSend(e: React.MouseEvent, lead: LinkedInLead) {
    e.stopPropagation()
    if (!lead.email) return
    if (lead.cv_missing) {
      alert(
        `No CV uploaded for cluster "${lead.cv_cluster}". ` +
        `Upload it in CV library before sending — a role-matched CV is ` +
        `required.`,
      )
      return
    }
    if (!confirm(`Send email to ${lead.email}?`)) return
    setSendingId(lead.id)
    try {
      await api.post(`/api/linkedin/send/lead/${lead.id}`)
      mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/"))
    } catch (err) {
      alert((err as Error).message)
    } finally {
      setSendingId(null)
    }
  }

  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 250)
    return () => clearTimeout(t)
  }, [q])

  const params = new URLSearchParams()
  if (status) params.set("status", status)
  if (callFilter) params.set("call_status", callFilter)
  if (debounced) params.set("q", debounced)
  // "recent" is the backend default — omit to keep URLs tidy.
  if (sort && sort !== "recent") params.set("sort", sort)
  params.set("limit", "200")

  // Click a column header → toggle asc/desc on that column. Clicking any
  // other column starts at asc. Switching back to the dropdown modes
  // ("recent" / "score") is still handled by the select element.
  function cycleSort(col: string) {
    setSort((prev) => {
      if (prev === `${col}_asc`)  return `${col}_desc`
      if (prev === `${col}_desc`) return "recent"  // third click clears
      return `${col}_asc`
    })
  }
  function sortIcon(col: string) {
    if (sort === `${col}_asc`)  return <ArrowUp   className="size-3 text-violet-300" />
    if (sort === `${col}_desc`) return <ArrowDown className="size-3 text-violet-300" />
    return <ArrowUpDown className="size-3 text-zinc-600 opacity-60 group-hover:opacity-100" />
  }

  const { data, isLoading } = useSWR<LinkedInLeadsResponse>(
    `/api/linkedin/leads?${params.toString()}`,
    swrFetcher,
    { refreshInterval: 20_000 },
  )

  const rows = data?.rows ?? []

  return (
    <>
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b]">
      <div className="flex items-center gap-3 p-3 border-b border-zinc-800/70">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-zinc-500" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search company, role, email..."
            className="w-full rounded-md border border-zinc-800 bg-zinc-900/60 pl-8 pr-3 py-1.5 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-[hsl(250_80%_62%)]"
          />
        </div>
        {!initialStatus && (
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-md border border-zinc-800 bg-zinc-900/60 px-2 py-1.5 text-sm text-zinc-200 focus:outline-none focus:border-[hsl(250_80%_62%)]"
          >
            <option value="">All statuses</option>
            {Object.keys(STATUS_STYLES).map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        )}
        <select
          value={callFilter}
          onChange={(e) => setCallFilter(e.target.value)}
          title="Filter by call signal"
          className="rounded-md border border-zinc-800 bg-zinc-900/60 px-2 py-1.5 text-sm text-zinc-200 focus:outline-none focus:border-[hsl(250_80%_62%)]"
        >
          <option value="">All signals</option>
          <option value="any">Any signal set</option>
          <option value="green">🟢 Interested</option>
          <option value="yellow">🟡 Maybe</option>
          <option value="red">🔴 Not a fit</option>
          <option value="none">— No signal</option>
        </select>
        <select
          value={sort === "recent" || sort === "score" ? sort : "column"}
          onChange={(e) => {
            const v = e.target.value
            if (v === "column") return  // placeholder for custom col sorts
            setSort(v)
          }}
          title="Sort order"
          className="rounded-md border border-zinc-800 bg-zinc-900/60 px-2 py-1.5 text-sm text-zinc-200 focus:outline-none focus:border-[hsl(250_80%_62%)]"
        >
          <option value="recent">Sort: recent</option>
          <option value="score">Sort: fit score</option>
          {sort !== "recent" && sort !== "score" && (
            <option value="column" disabled>
              Sort: {sort.replace("_", " ")}
            </option>
          )}
        </select>
        <div className="ml-auto text-xs text-zinc-500 tnum">
          {isLoading ? "…" : `${data?.total ?? 0} rows`}
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyRows loading={isLoading} />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-[0.08em] text-zinc-500 border-b border-zinc-800/70">
                <Th sortKey="fit"        active={sort} onSort={cycleSort} icon={sortIcon("fit")}>Fit</Th>
                <Th sortKey="company"    active={sort} onSort={cycleSort} icon={sortIcon("company")}>Company</Th>
                <Th sortKey="posted_by"  active={sort} onSort={cycleSort} icon={sortIcon("posted_by")}>Posted by</Th>
                <Th sortKey="role"       active={sort} onSort={cycleSort} icon={sortIcon("role")}>Role</Th>
                <Th sortKey="email"      active={sort} onSort={cycleSort} icon={sortIcon("email")}>Email</Th>
                <Th sortKey="phone"      active={sort} onSort={cycleSort} icon={sortIcon("phone")}>Phone</Th>
                <Th sortKey="status"     active={sort} onSort={cycleSort} icon={sortIcon("status")}>Status</Th>
                <Th sortKey="call"       active={sort} onSort={cycleSort} icon={sortIcon("call")}>Call</Th>
                <Th>Notes</Th>
                <Th sortKey="first_seen" active={sort} onSort={cycleSort} icon={sortIcon("first_seen")}>First seen</Th>
                <Th></Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.id}
                  onClick={() => setOpenId(r.id)}
                  className={cn(
                    "border-b border-zinc-800/50 hover:bg-zinc-800/40 transition-colors cursor-pointer",
                    r.reviewed_at && "opacity-60",
                  )}
                >
                  <Td>
                    <ScorePill score={r.fit_score} reasons={r.fit_score_reasons} />
                  </Td>
                  <Td>
                    <div className="flex items-center gap-2">
                      <span>{r.company || "—"}</span>
                      {r.open_count > 0 && (
                        <span
                          className="inline-flex items-center gap-0.5 rounded bg-emerald-500/15 px-1 py-0.5 text-[10px] text-emerald-300"
                          title={`Opened ${r.open_count}x, last ${r.last_opened_at ?? ""}`}
                        >
                          <Eye className="size-2.5" />
                          {r.open_count}
                        </span>
                      )}
                    </div>
                  </Td>
                  <Td className="text-zinc-400">{r.posted_by || "—"}</Td>
                  <Td className="text-zinc-400">{r.role || "—"}</Td>
                  <Td className="font-mono text-xs text-zinc-300">
                    {r.email || "—"}
                  </Td>
                  <Td className="font-mono text-xs text-zinc-400">
                    {r.phone || "—"}
                  </Td>
                  <Td>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span
                        className={cn(
                          "inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium",
                          STATUS_STYLES[r.status] ?? "bg-zinc-700/40 text-zinc-300",
                        )}
                      >
                        {r.status}
                      </span>
                      {r.scheduled_send_at && (
                        <span
                          className="inline-flex items-center gap-0.5 rounded bg-amber-500/15 px-1 py-0.5 text-[10px] text-amber-300"
                          title={`Scheduled to send at ${r.scheduled_send_at}`}
                        >
                          <Clock className="size-2.5" />
                          {fmtScheduled(r.scheduled_send_at)}
                        </span>
                      )}
                      {r.ooo_nudge_at && !r.ooo_nudge_sent_at && (
                        <span
                          className="inline-flex items-center gap-0.5 rounded bg-sky-500/15 px-1 py-0.5 text-[10px] text-sky-300"
                          title={`OOO nudge auto-scheduled for ${r.ooo_nudge_at}`}
                        >
                          <Clock className="size-2.5" />
                          nudge {fmtScheduled(r.ooo_nudge_at)}
                        </span>
                      )}
                      {r.cv_missing && (
                        <span
                          className="inline-flex items-center gap-0.5 rounded bg-amber-500/15 px-1 py-0.5 text-[10px] text-amber-300"
                          title={`No CV uploaded for cluster "${r.cv_cluster}" — send will be blocked until you upload one`}
                        >
                          <FileWarning className="size-2.5" />
                          CV
                        </span>
                      )}
                    </div>
                  </Td>
                  <Td>
                    <CallStatusCell lead={r} />
                  </Td>
                  <Td>
                    <NotesCell lead={r} />
                  </Td>
                  <Td className="text-xs text-zinc-500 tnum whitespace-nowrap">
                    <div>{fmtDate(r.first_seen_at)}</div>
                    <div className="text-[10px] text-zinc-600">
                      {fmtTime(r.first_seen_at)}
                    </div>
                  </Td>
                  <Td className="text-right pr-3">
                    {r.status === "Drafted" && r.email ? (
                      <button
                        onClick={(e) => quickSend(e, r)}
                        disabled={sendingId === r.id || r.cv_missing}
                        className={cn(
                          "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-medium text-white disabled:opacity-50",
                          r.cv_missing
                            ? "bg-amber-600/70 hover:bg-amber-600/70 cursor-not-allowed"
                            : "bg-emerald-600/90 hover:bg-emerald-500",
                        )}
                        title={
                          r.cv_missing
                            ? `Blocked — upload a CV for "${r.cv_cluster}"`
                            : "Send now"
                        }
                      >
                        {sendingId === r.id ? (
                          <Loader2 className="size-3 animate-spin" />
                        ) : (
                          <Send className="size-3" />
                        )}
                        Send
                      </button>
                    ) : null}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
    <LinkedInLeadDrawer leadId={openId} onClose={() => setOpenId(null)} />
    </>
  )
}

function ScorePill({
  score, reasons,
}: {
  score: number | null
  reasons: string | null
}) {
  if (score == null) {
    return <span className="text-zinc-600 text-[11px]">—</span>
  }
  const tone =
    score >= 75 ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
    : score >= 50 ? "bg-amber-500/15 text-amber-300 border-amber-500/30"
    : "bg-zinc-700/40 text-zinc-400 border-zinc-700"
  let parsed: string[] = []
  try {
    parsed = reasons ? JSON.parse(reasons) : []
  } catch { /* ignore */ }
  const title = parsed.length
    ? parsed.join("\n")
    : `Fit score ${score}`
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center justify-center rounded border px-1.5 py-0.5 text-[11px] font-semibold tnum",
        tone,
      )}
    >
      {score}
    </span>
  )
}


const CALL_DOT: Record<string, string> = {
  green:  "bg-emerald-500",
  yellow: "bg-amber-400",
  red:    "bg-rose-500",
}

const CALL_LABEL: Record<string, string> = {
  green:  "Interested",
  yellow: "Maybe",
  red:    "Not a fit",
}

function CallStatusCell({ lead }: { lead: LinkedInLead }) {
  const [busy, setBusy] = React.useState(false)
  const cur = lead.call_status ?? ""

  async function change(next: string) {
    if (next === cur) return
    setBusy(true)
    try {
      const res = await api.post<{ auto_replied?: boolean }>(
        `/api/linkedin/leads/${lead.id}`,
        { call_status: next },
      )
      mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/"))
      if (res.auto_replied) {
        // Non-blocking hint — status already flipped in the DB.
        console.info(`[lead ${lead.id}] auto-moved to Replied (call signal)`)
      }
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="flex items-center gap-1"
      onClick={(e) => e.stopPropagation()}
    >
      {cur && (
        <span className={cn("size-2 rounded-full", CALL_DOT[cur])} />
      )}
      <select
        value={cur}
        disabled={busy}
        onChange={(e) => change(e.target.value)}
        className="rounded border border-zinc-800 bg-zinc-900/60 px-1 py-0.5 text-[11px] text-zinc-300 focus:outline-none focus:border-[hsl(250_80%_62%)]"
      >
        <option value="">—</option>
        <option value="green">🟢 {CALL_LABEL.green}</option>
        <option value="yellow">🟡 {CALL_LABEL.yellow}</option>
        <option value="red">🔴 {CALL_LABEL.red}</option>
      </select>
    </div>
  )
}

function NotesCell({ lead }: { lead: LinkedInLead }) {
  const [value, setValue] = React.useState(lead.jaydip_note ?? "")
  const [busy, setBusy] = React.useState(false)
  const [dirty, setDirty] = React.useState(false)

  React.useEffect(() => {
    // Sync external updates (e.g. from drawer edits) back into the input
    // only when user hasn't typed something new.
    if (!dirty) setValue(lead.jaydip_note ?? "")
  }, [lead.jaydip_note, dirty])

  async function save() {
    if (value === (lead.jaydip_note ?? "")) {
      setDirty(false)
      return
    }
    setBusy(true)
    try {
      await api.post(`/api/linkedin/leads/${lead.id}`, {
        jaydip_note: value,
      })
      // Also refresh overview KPIs — a note on a Sent lead promotes to
      // Replied and bumps the Replied counter.
      mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/"))
      setDirty(false)
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <input
      value={value}
      onChange={(e) => { setValue(e.target.value); setDirty(true) }}
      onBlur={save}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur() }}
      onClick={(e) => e.stopPropagation()}
      placeholder="Add note…"
      disabled={busy}
      className="w-32 rounded border border-zinc-800 bg-zinc-900/60 px-1.5 py-0.5 text-[11px] text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-[hsl(250_80%_62%)]"
    />
  )
}

function Th({
  children, sortKey, active, onSort, icon,
}: {
  children?: React.ReactNode
  sortKey?: string
  active?: string
  onSort?: (col: string) => void
  icon?: React.ReactNode
}) {
  // Plain header when no sort hook provided (e.g. Notes column).
  if (!sortKey || !onSort) {
    return <th className="px-3 py-2 font-medium">{children}</th>
  }
  const isActive = active === `${sortKey}_asc` || active === `${sortKey}_desc`
  return (
    <th className="px-3 py-2 font-medium">
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cn(
          "group inline-flex items-center gap-1 uppercase tracking-[0.08em]",
          "transition-colors hover:text-zinc-300",
          isActive ? "text-violet-200" : "text-zinc-500",
        )}
      >
        {children}
        {icon}
      </button>
    </th>
  )
}
function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return <td className={cn("px-3 py-2 text-zinc-200", className)}>{children}</td>
}

function EmptyRows({ loading }: { loading: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="size-10 rounded-full bg-zinc-800/60 flex items-center justify-center mb-3">
        <Inbox className="size-5 text-zinc-500" />
      </div>
      <div className="text-sm text-zinc-300">
        {loading ? "Loading…" : "No leads yet"}
      </div>
      <div className="mt-1 text-xs text-zinc-500 max-w-sm">
        Install the LinkedIn extension and scan a search page to populate this
        list.
      </div>
    </div>
  )
}

function fmtScheduled(iso: string): string {
  try {
    const d = new Date(iso)
    const now = new Date()
    const sameDay = d.toDateString() === now.toDateString()
    if (sameDay) {
      return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
    }
    return d.toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    })
  } catch {
    return iso
  }
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    })
  } catch {
    return iso
  }
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    })
  } catch {
    return ""
  }
}
