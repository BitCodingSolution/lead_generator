"use client"

import * as React from "react"
import useSWR from "swr"
import { useSearchParams } from "next/navigation"
import { swrFetcher } from "@/lib/api"
import { BatchesPanel, BatchesSummary } from "@/components/batches-panel"
import { PageHeader } from "@/components/page-header"
import { cn } from "@/lib/utils"

// Campaigns = cross-source batch hub. Source-specific pipeline controls
// (Marcel "Run a campaign", YC "Collect & Prepare") now live on their own
// source pages under /sources/<id>.

export default function CampaignsPage() {
  return (
    <React.Suspense fallback={null}>
      <CampaignsPageInner />
    </React.Suspense>
  )
}

function CampaignsPageInner() {
  const searchParams = useSearchParams()
  const highlightBatch = searchParams?.get("batch") || null

  return (
    <div className="space-y-6">
      <PageHeader
        title="Campaigns"
        subtitle="Batches exported from any source — run drafts → Outlook → send from here."
      />
      <BatchesSection highlightBatch={highlightBatch} />
    </div>
  )
}

// ---------- Cross-source batches with source tabs ----------

type CrossBatchesResponse = {
  batches: Array<{ name: string; source?: string; sent?: number; total?: number }>
  count: number
}

function BatchesSection({
  highlightBatch,
}: {
  highlightBatch: string | null
}) {
  const [selected, setSelected] = React.useState<string>("all")

  const { data } = useSWR<CrossBatchesResponse>(
    "/api/campaigns/batches",
    swrFetcher,
    { refreshInterval: 5000 },
  )
  const batches = data?.batches || []

  const perSource = React.useMemo(() => {
    const m = new Map<string, number>()
    for (const b of batches) {
      const s = b.source || "unknown"
      m.set(s, (m.get(s) || 0) + 1)
    }
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1])
  }, [batches])

  const tabs: Array<{ id: string; label: string; count: number }> = [
    { id: "all", label: "All", count: batches.length },
    ...perSource.map(([id, count]) => ({
      id,
      label: id.charAt(0).toUpperCase() + id.slice(1),
      count,
    })),
  ]

  React.useEffect(() => {
    if (selected !== "all" && !tabs.some((t) => t.id === selected)) {
      setSelected("all")
    }
  }, [tabs, selected])

  const activeFilter = selected === "all" ? undefined : selected

  return (
    <div className="space-y-3">
      <div className="flex items-end justify-between gap-3 pb-2 border-b border-zinc-800/60">
        <div className="min-w-0 flex-1">
          <div className="text-base font-semibold tracking-tight text-zinc-100">
            All Batches
          </div>
          <div className="text-xs text-zinc-500 mt-0.5">
            One list per source — run drafts → Outlook → send for each batch.
          </div>
        </div>
        {tabs.length > 1 && (
          <div className="flex items-center gap-1 rounded-lg border border-zinc-800 bg-zinc-900/40 p-1">
            {tabs.map((t) => (
              <button
                key={t.id}
                onClick={() => setSelected(t.id)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors",
                  selected === t.id
                    ? "bg-[hsl(250_80%_62%/0.18)] text-[hsl(250_80%_85%)] border border-[hsl(250_80%_62%/0.3)]"
                    : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 border border-transparent",
                )}
              >
                {t.label}
                <span
                  className={cn(
                    "tnum text-[10px] rounded px-1 py-0.5",
                    selected === t.id
                      ? "bg-[hsl(250_80%_62%/0.2)] text-[hsl(250_80%_85%)]"
                      : "bg-zinc-800 text-zinc-500",
                  )}
                >
                  {t.count}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
      <BatchesSummary scope={{ kind: "all" }} sourceFilter={activeFilter} />
      <BatchesPanel
        scope={{ kind: "all" }}
        sourceFilter={activeFilter}
        highlight={highlightBatch}
      />
    </div>
  )
}
