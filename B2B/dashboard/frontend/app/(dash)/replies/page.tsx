"use client"

import * as React from "react"
import { useSearchParams } from "next/navigation"
import useSWR from "swr"
import { toast } from "sonner"
import { api, swrFetcher } from "@/lib/api"
import type { HotLead, ReplyRow } from "@/lib/types"
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
import { Button } from "@/components/ui/button"
import { Check, Inbox, RefreshCw } from "lucide-react"
import { absTime, cn, relTime, truncate } from "@/lib/utils"

const FILTERS: { key: string; label: string }[] = [
  { key: "all", label: "All" },
  { key: "hot", label: "Hot" },
  { key: "positive", label: "Positive" },
  { key: "objection", label: "Objection" },
  { key: "neutral", label: "Neutral" },
  { key: "negative", label: "Negative" },
  { key: "ooo", label: "OOO" },
  { key: "bounce", label: "Bounce" },
]

const VALID_FILTERS = FILTERS.map((f) => f.key)

export default function RepliesPage() {
  const searchParams = useSearchParams()
  const urlFilter = (searchParams?.get("filter") ?? "").toLowerCase()
  const [filter, setFilter] = React.useState<string>(
    VALID_FILTERS.includes(urlFilter) ? urlFilter : "hot",
  )
  React.useEffect(() => {
    if (VALID_FILTERS.includes(urlFilter) && urlFilter !== filter) {
      setFilter(urlFilter)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlFilter])
  const [openId, setOpenId] = React.useState<number | null>(null)

  const { data, mutate, isLoading } = useSWR<HotLead[]>(
    "/api/hot-leads?limit=200",
    swrFetcher,
    { refreshInterval: 30000 },
  )

  const rows = React.useMemo(() => {
    const base = data || []
    if (filter === "all") return base
    if (filter === "hot") return base.filter((r) => !r.handled)
    return base.filter(
      (r) => (r.sentiment || "").toLowerCase() === filter,
    )
  }, [data, filter])

  const counts = React.useMemo(() => {
    const b = data || []
    return {
      all: b.length,
      hot: b.filter((r) => !r.handled).length,
      positive: b.filter((r) => r.sentiment?.toLowerCase() === "positive").length,
      objection: b.filter((r) => r.sentiment?.toLowerCase() === "objection").length,
      neutral: b.filter((r) => r.sentiment?.toLowerCase() === "neutral").length,
      negative: b.filter((r) => r.sentiment?.toLowerCase() === "negative").length,
      ooo: b.filter((r) => r.sentiment?.toLowerCase() === "ooo").length,
      bounce: b.filter((r) => r.sentiment?.toLowerCase() === "bounce").length,
    } as Record<string, number>
  }, [data])

  async function markHandled(id: number, handled: boolean) {
    try {
      await api.post("/api/replies/handle", { reply_id: id, handled })
      mutate()
      toast.success(handled ? "Marked handled" : "Re-opened")
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      toast.error("Failed", { description: msg })
    }
  }

  async function scanNow() {
    try {
      const r = await api.post<{ job_id: string }>("/api/actions/scan-replies")
      toast.success("Scanning inbox", { description: r.job_id })
      setTimeout(() => mutate(), 4000)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      toast.error("Failed", { description: msg })
    }
  }

  const openRow = React.useMemo(
    () => (data || []).find((r) => r.id === openId) || null,
    [data, openId],
  )

  return (
    <div className="space-y-6">
      <PageHeader
        title="Replies"
        subtitle="Everything that came back. Mark handled as you clear your inbox."
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={scanNow}
            className="border-zinc-800 hover:bg-zinc-800/60"
          >
            <RefreshCw className="size-3.5" />
            Scan inbox
          </Button>
        }
      />

      {/* Filter tabs */}
      <div className="flex flex-wrap gap-1.5">
        {FILTERS.map((f) => {
          const active = filter === f.key
          return (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={cn(
                "group inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border text-xs transition-colors",
                active
                  ? "bg-[hsl(250_80%_62%/0.15)] border-[hsl(250_80%_62%/0.35)] text-[hsl(250_80%_78%)]"
                  : "border-zinc-800 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40",
              )}
            >
              <span>{f.label}</span>
              <span
                className={cn(
                  "tnum text-[10px] rounded px-1.5",
                  active
                    ? "bg-[hsl(250_80%_62%/0.25)] text-white"
                    : "bg-zinc-800 text-zinc-400",
                )}
              >
                {counts[f.key] ?? 0}
              </span>
            </button>
          )
        })}
      </div>

      <div className="rounded-xl border border-zinc-800/80 bg-[#18181b]">
        {isLoading ? (
          <div className="p-6 space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="skeleton h-14 w-full" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <div className="p-8">
            <EmptyState
              icon={<Inbox className="size-5" />}
              title="No replies in this view"
              hint="Try the All tab or scan the inbox again."
            />
          </div>
        ) : (
          <div className="divide-y divide-zinc-800/60">
            {rows.map((r) => (
              <div
                key={r.id}
                className="grid grid-cols-[minmax(0,1fr)_auto] gap-4 px-5 py-3.5 hover:bg-zinc-800/30 transition-colors cursor-pointer"
                onClick={() => setOpenId(r.id)}
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <StatusChip value={r.sentiment} />
                    <div className="text-sm font-medium text-zinc-100 truncate">
                      {r.company || r.name || "—"}
                    </div>
                    <span className="text-[11px] text-zinc-500">{r.name}</span>
                    {r.handled ? (
                      <StatusChip tone="zinc">handled</StatusChip>
                    ) : null}
                  </div>
                  <div className="text-[11px] text-zinc-500 mt-0.5 truncate">
                    {r.industry}
                    {r.city ? ` · ${r.city}` : ""}
                  </div>
                  <div className="text-xs text-zinc-400 mt-1.5 line-clamp-2">
                    {truncate(r.snippet, 220)}
                  </div>
                </div>
                <div className="flex flex-col items-end gap-2 shrink-0">
                  <span className="text-[11px] text-zinc-500 tnum">{relTime(r.reply_at)}</span>
                  <Button
                    variant="outline"
                    size="xs"
                    onClick={(e) => {
                      e.stopPropagation()
                      markHandled(r.id, !r.handled)
                    }}
                    className={cn(
                      "border-zinc-800 hover:bg-zinc-800/60",
                      r.handled && "text-emerald-400 border-emerald-500/20",
                    )}
                  >
                    <Check className="size-3" />
                    {r.handled ? "Handled" : "Mark"}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <Sheet open={!!openId} onOpenChange={(o) => !o && setOpenId(null)}>
        <SheetContent
          side="right"
          className="!max-w-[560px] sm:!max-w-[560px] w-full bg-[#101012] border-zinc-800 overflow-y-auto"
        >
          {openRow && (
            <>
              <SheetHeader className="border-b border-zinc-800/70">
                <div className="flex items-center gap-2">
                  <StatusChip value={openRow.sentiment} />
                  {openRow.handled ? <StatusChip tone="zinc">handled</StatusChip> : null}
                </div>
                <SheetTitle className="text-zinc-100 tracking-tight text-lg">
                  {openRow.company || openRow.name || "Reply"}
                </SheetTitle>
                <SheetDescription className="text-zinc-500">
                  {openRow.name}
                  {openRow.industry ? ` · ${openRow.industry}` : ""}
                  {openRow.city ? ` · ${openRow.city}` : ""}
                </SheetDescription>
              </SheetHeader>
              <div className="p-4 space-y-4">
                <div className="rounded-md border border-zinc-800/80 bg-zinc-900/40 p-3">
                  <div className="text-[11px] text-zinc-500">
                    Received {absTime(openRow.reply_at)}
                  </div>
                  <pre className="mt-2 text-sm text-zinc-200 whitespace-pre-wrap font-sans leading-relaxed">
                    {(openRow as unknown as ReplyRow).body || openRow.snippet}
                  </pre>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    onClick={() => markHandled(openRow.id, !openRow.handled)}
                    className={cn(
                      openRow.handled
                        ? "bg-zinc-800 hover:bg-zinc-700"
                        : "bg-[hsl(250_80%_62%)] hover:bg-[hsl(250_80%_58%)] text-white",
                    )}
                    size="sm"
                  >
                    <Check className="size-3.5" />
                    {openRow.handled ? "Re-open" : "Mark handled"}
                  </Button>
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  )
}
