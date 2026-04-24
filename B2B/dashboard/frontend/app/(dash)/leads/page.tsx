"use client"

import * as React from "react"
import { useSearchParams } from "next/navigation"
import useSWR from "swr"
import { swrFetcher } from "@/lib/api"
import type { Lead, LeadsResponse, LeadDetail, IndustryRow } from "@/lib/types"
import { PageHeader } from "@/components/page-header"
import { StatusChip } from "@/components/status-chip"
import { EmptyState } from "@/components/empty-state"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Button } from "@/components/ui/button"
import { Search, ChevronLeft, ChevronRight, Users, Mail, MessageSquareReply } from "lucide-react"
import { fmt, relTime, absTime, truncate, cn } from "@/lib/utils"

const STATUSES = ["new", "picked", "drafted", "sent", "replied"]
const TIERS = ["1", "2"]

export default function LeadsPage() {
  const searchParams = useSearchParams()
  const urlStatus = (searchParams?.get("status") ?? "").toLowerCase()
  const [status, setStatus] = React.useState<string>(
    STATUSES.includes(urlStatus) ? urlStatus : "",
  )
  React.useEffect(() => {
    if (STATUSES.includes(urlStatus) && urlStatus !== status) {
      setStatus(urlStatus)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlStatus])
  const [industry, setIndustry] = React.useState<string>("")
  const [tier, setTier] = React.useState<string>("")
  const [searchRaw, setSearchRaw] = React.useState<string>("")
  const [search, setSearch] = React.useState<string>("")
  const [page, setPage] = React.useState(0)
  const limit = 50

  const [openId, setOpenId] = React.useState<string | number | null>(null)

  // debounce
  React.useEffect(() => {
    const t = setTimeout(() => setSearch(searchRaw.trim()), 300)
    return () => clearTimeout(t)
  }, [searchRaw])

  const qs = new URLSearchParams()
  if (status) qs.set("status", status)
  if (industry) qs.set("industry", industry)
  if (tier) qs.set("tier", tier)
  if (search) qs.set("search", search)
  qs.set("limit", String(limit))
  qs.set("offset", String(page * limit))

  const { data, isLoading } = useSWR<LeadsResponse>(
    `/api/leads?${qs.toString()}`,
    swrFetcher,
    { keepPreviousData: true },
  )

  const { data: industries } = useSWR<IndustryRow[]>("/api/industries", swrFetcher)

  const total = data?.total ?? 0
  const items = data?.items ?? []

  const pages = Math.max(1, Math.ceil(total / limit))

  return (
    <div className="space-y-6">
      <PageHeader
        title="Leads"
        subtitle="Every company the system knows about, searchable and filterable."
      />

      <div className="grid grid-cols-1 lg:grid-cols-[240px_minmax(0,1fr)] gap-6">
        {/* Filters */}
        <aside className="space-y-5 lg:sticky lg:top-20 h-fit">
          <div>
            <div className="text-[11px] uppercase tracking-[0.15em] text-zinc-500 mb-2">
              Search
            </div>
            <div className="relative">
              <Search className="size-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500" />
              <Input
                value={searchRaw}
                onChange={(e) => {
                  setSearchRaw(e.target.value)
                  setPage(0)
                }}
                placeholder="Company, name, city…"
                className="pl-8 bg-zinc-900/40 border-zinc-800"
              />
            </div>
          </div>

          <div>
            <div className="text-[11px] uppercase tracking-[0.15em] text-zinc-500 mb-2">
              Status
            </div>
            <div className="flex flex-wrap gap-1.5">
              <button
                onClick={() => {
                  setStatus("")
                  setPage(0)
                }}
                className={cn(
                  "px-2 py-1 text-xs rounded-md border transition-colors",
                  !status
                    ? "bg-[hsl(250_80%_62%/0.15)] border-[hsl(250_80%_62%/0.3)] text-[hsl(250_80%_78%)]"
                    : "border-zinc-800 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40",
                )}
              >
                All
              </button>
              {STATUSES.map((s) => (
                <button
                  key={s}
                  onClick={() => {
                    setStatus(s)
                    setPage(0)
                  }}
                  className={cn(
                    "px-2 py-1 text-xs rounded-md border transition-colors capitalize",
                    status === s
                      ? "bg-[hsl(250_80%_62%/0.15)] border-[hsl(250_80%_62%/0.3)] text-[hsl(250_80%_78%)]"
                      : "border-zinc-800 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40",
                  )}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="text-[11px] uppercase tracking-[0.15em] text-zinc-500 mb-2">
              Industry
            </div>
            <Select
              value={industry || "__all"}
              onValueChange={(v) => {
                setIndustry(v === "__all" ? "" : v)
                setPage(0)
              }}
            >
              <SelectTrigger className="!w-full bg-zinc-900/40 border-zinc-800">
                <SelectValue placeholder="All industries" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all">All industries</SelectItem>
                {(industries || []).map((i) => (
                  <SelectItem key={i.industry} value={i.industry}>
                    {i.industry}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div>
            <div className="text-[11px] uppercase tracking-[0.15em] text-zinc-500 mb-2">
              Tier
            </div>
            <div className="flex gap-1.5">
              <button
                onClick={() => {
                  setTier("")
                  setPage(0)
                }}
                className={cn(
                  "flex-1 px-2 py-1 text-xs rounded-md border transition-colors",
                  !tier
                    ? "bg-[hsl(250_80%_62%/0.15)] border-[hsl(250_80%_62%/0.3)] text-[hsl(250_80%_78%)]"
                    : "border-zinc-800 text-zinc-400 hover:text-zinc-200",
                )}
              >
                Any
              </button>
              {TIERS.map((t) => (
                <button
                  key={t}
                  onClick={() => {
                    setTier(t)
                    setPage(0)
                  }}
                  className={cn(
                    "flex-1 px-2 py-1 text-xs rounded-md border transition-colors",
                    tier === t
                      ? "bg-[hsl(250_80%_62%/0.15)] border-[hsl(250_80%_62%/0.3)] text-[hsl(250_80%_78%)]"
                      : "border-zinc-800 text-zinc-400 hover:text-zinc-200",
                  )}
                >
                  T{t}
                </button>
              ))}
            </div>
          </div>

          {(status || industry || tier || search) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setStatus("")
                setIndustry("")
                setTier("")
                setSearchRaw("")
                setPage(0)
              }}
              className="w-full text-zinc-400 hover:text-zinc-200"
            >
              Clear filters
            </Button>
          )}
        </aside>

        {/* Table */}
        <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] min-w-0">
          <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800/70">
            <div className="text-sm text-zinc-400">
              {isLoading ? (
                <span className="text-zinc-500">Loading…</span>
              ) : (
                <>
                  <span className="text-zinc-200 tnum font-medium">{fmt(total)}</span>
                  <span className="text-zinc-500"> lead{total === 1 ? "" : "s"} match</span>
                </>
              )}
            </div>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                aria-label="Previous page"
                className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/60 disabled:opacity-40 disabled:pointer-events-none"
              >
                <ChevronLeft className="size-4" />
              </button>
              <span className="text-xs text-zinc-400 tnum px-2">
                {page + 1} / {pages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}
                disabled={page >= pages - 1}
                aria-label="Next page"
                className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/60 disabled:opacity-40 disabled:pointer-events-none"
              >
                <ChevronRight className="size-4" />
              </button>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-zinc-900/40 text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                <tr className="border-b border-zinc-800/70">
                  <th className="text-left font-medium px-4 py-2.5">Company</th>
                  <th className="text-left font-medium px-4 py-2.5">Contact</th>
                  <th className="text-left font-medium px-4 py-2.5">Industry</th>
                  <th className="text-left font-medium px-4 py-2.5">City</th>
                  <th className="text-left font-medium px-4 py-2.5">Tier</th>
                  <th className="text-left font-medium px-4 py-2.5">Status</th>
                </tr>
              </thead>
              <tbody>
                {isLoading && items.length === 0
                  ? Array.from({ length: 8 }).map((_, i) => (
                      <tr key={i} className="border-b border-zinc-800/60">
                        <td colSpan={6} className="px-4 py-3">
                          <div className="skeleton h-4 w-full" />
                        </td>
                      </tr>
                    ))
                  : items.map((lead) => (
                      <tr
                        key={String(lead.lead_id)}
                        onClick={() => setOpenId(lead.lead_id)}
                        className="border-b border-zinc-800/60 hover:bg-zinc-800/40 cursor-pointer transition-colors"
                      >
                        <td className="px-4 py-2.5 text-zinc-100 font-medium tracking-tight">
                          {lead.company || "—"}
                        </td>
                        <td className="px-4 py-2.5 text-zinc-400">
                          {lead.name || <span className="text-zinc-600">—</span>}
                        </td>
                        <td className="px-4 py-2.5 text-zinc-400">
                          {lead.industry || <span className="text-zinc-600">—</span>}
                        </td>
                        <td className="px-4 py-2.5 text-zinc-400">
                          {lead.city || <span className="text-zinc-600">—</span>}
                        </td>
                        <td className="px-4 py-2.5">
                          {lead.tier ? (
                            <span className="text-xs text-zinc-400 tnum">T{lead.tier}</span>
                          ) : (
                            <span className="text-zinc-600">—</span>
                          )}
                        </td>
                        <td className="px-4 py-2.5">
                          <StatusChip value={String(lead.status || "new")} />
                        </td>
                      </tr>
                    ))}
              </tbody>
            </table>
            {!isLoading && items.length === 0 && (
              <div className="p-8">
                <EmptyState
                  icon={<Users className="size-5" />}
                  title="No leads match"
                  hint="Try removing some filters, or pick a new batch from Campaigns."
                />
              </div>
            )}
          </div>
        </div>
      </div>

      <LeadDetailSheet openId={openId} onOpenChange={(o) => !o && setOpenId(null)} />
    </div>
  )
}

function LeadDetailSheet({
  openId,
  onOpenChange,
}: {
  openId: string | number | null
  onOpenChange: (o: boolean) => void
}) {
  const { data, isLoading } = useSWR<LeadDetail>(
    openId ? `/api/lead/${openId}` : null,
    swrFetcher,
  )

  const lead = data?.lead as Lead | undefined
  const emails = data?.emails || []
  const replies = data?.replies || []

  return (
    <Sheet open={!!openId} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="!max-w-[560px] sm:!max-w-[560px] w-full bg-[#101012] border-zinc-800 overflow-y-auto"
      >
        <SheetHeader className="border-b border-zinc-800/70">
          <SheetTitle className="text-zinc-100 tracking-tight text-lg">
            {lead?.company || (isLoading ? "Loading…" : "Lead")}
          </SheetTitle>
          <SheetDescription className="text-zinc-500">
            {lead?.name || "—"}
            {lead?.industry ? ` · ${lead.industry}` : ""}
            {lead?.city ? ` · ${lead.city}` : ""}
          </SheetDescription>
        </SheetHeader>

        <div className="p-4 space-y-5">
          {lead && (
            <div className="grid grid-cols-2 gap-2">
              <Info label="Email" value={lead.email} />
              <Info label="Tier" value={lead.tier ? `T${lead.tier}` : "—"} />
              <Info label="Status" value={<StatusChip value={String(lead.status || "new")} />} />
              <Info label="Lead ID" value={String(lead.lead_id)} mono />
            </div>
          )}

          <section>
            <div className="flex items-center gap-2 text-xs uppercase tracking-[0.15em] text-zinc-500 mb-2">
              <Mail className="size-3.5" />
              Emails ({emails.length})
            </div>
            {emails.length ? (
              <div className="space-y-2">
                {emails.map((e, i) => (
                  <div
                    key={i}
                    className="rounded-md border border-zinc-800/80 bg-zinc-900/40 p-3"
                  >
                    <div className="flex items-center justify-between text-xs text-zinc-400">
                      <span className="truncate pr-2">{e.subject || "(no subject)"}</span>
                      <span className="tnum text-zinc-500">{relTime(e.sent_at)}</span>
                    </div>
                    {e.status && (
                      <div className="mt-1.5">
                        <StatusChip value={String(e.status)} />
                      </div>
                    )}
                    {e.body && (
                      <pre className="mt-2 text-xs text-zinc-300 whitespace-pre-wrap font-sans leading-relaxed">
                        {truncate(String(e.body), 600)}
                      </pre>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-xs text-zinc-500 border border-dashed border-zinc-800 rounded-md px-3 py-3">
                No emails yet.
              </div>
            )}
          </section>

          <section>
            <div className="flex items-center gap-2 text-xs uppercase tracking-[0.15em] text-zinc-500 mb-2">
              <MessageSquareReply className="size-3.5" />
              Replies ({replies.length})
            </div>
            {replies.length ? (
              <div className="space-y-2">
                {replies.map((r) => (
                  <div
                    key={r.id}
                    className="rounded-md border border-zinc-800/80 bg-zinc-900/40 p-3"
                  >
                    <div className="flex items-center justify-between">
                      <StatusChip value={r.sentiment} />
                      <span className="text-[11px] text-zinc-500 tnum">
                        {absTime(r.reply_at)}
                      </span>
                    </div>
                    {r.subject && (
                      <div className="mt-1.5 text-xs text-zinc-300">{r.subject}</div>
                    )}
                    {(r.body || r.snippet) && (
                      <pre className="mt-2 text-xs text-zinc-400 whitespace-pre-wrap font-sans leading-relaxed">
                        {truncate(String(r.body || r.snippet), 600)}
                      </pre>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-xs text-zinc-500 border border-dashed border-zinc-800 rounded-md px-3 py-3">
                No replies yet.
              </div>
            )}
          </section>
        </div>
      </SheetContent>
    </Sheet>
  )
}

function Info({
  label,
  value,
  mono,
}: {
  label: string
  value?: React.ReactNode
  mono?: boolean
}) {
  return (
    <div className="rounded-md border border-zinc-800/80 bg-zinc-900/40 px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.12em] text-zinc-500">{label}</div>
      <div className={cn("mt-0.5 text-sm text-zinc-200 truncate", mono && "font-mono text-xs")}>
        {value || "—"}
      </div>
    </div>
  )
}
