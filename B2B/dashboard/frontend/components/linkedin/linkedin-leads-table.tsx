"use client"

import * as React from "react"
import useSWR from "swr"
import { Search, Inbox } from "lucide-react"
import { cn } from "@/lib/utils"
import { swrFetcher } from "@/lib/api"
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
  const [status, setStatus] = React.useState<string>(initialStatus ?? "")
  const [q, setQ] = React.useState("")
  const [debounced, setDebounced] = React.useState("")
  const [openId, setOpenId] = React.useState<number | null>(null)

  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 250)
    return () => clearTimeout(t)
  }, [q])

  const params = new URLSearchParams()
  if (status) params.set("status", status)
  if (debounced) params.set("q", debounced)
  params.set("limit", "200")

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
                <Th>Company</Th>
                <Th>Posted by</Th>
                <Th>Role</Th>
                <Th>Email</Th>
                <Th>Status</Th>
                <Th>First seen</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.id}
                  onClick={() => setOpenId(r.id)}
                  className="border-b border-zinc-800/50 hover:bg-zinc-800/40 transition-colors cursor-pointer"
                >
                  <Td>{r.company || "—"}</Td>
                  <Td className="text-zinc-400">{r.posted_by || "—"}</Td>
                  <Td className="text-zinc-400">{r.role || "—"}</Td>
                  <Td className="font-mono text-xs text-zinc-300">
                    {r.email || "—"}
                  </Td>
                  <Td>
                    <span
                      className={cn(
                        "inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium",
                        STATUS_STYLES[r.status] ?? "bg-zinc-700/40 text-zinc-300",
                      )}
                    >
                      {r.status}
                    </span>
                  </Td>
                  <Td className="text-xs text-zinc-500 tnum">
                    {fmtDate(r.first_seen_at)}
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

function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-3 py-2 font-medium">{children}</th>
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
