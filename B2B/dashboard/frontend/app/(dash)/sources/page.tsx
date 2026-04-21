"use client"

import Link from "next/link"
import useSWR from "swr"
import { swrFetcher } from "@/lib/api"
import type { SourceCard } from "@/lib/sources"
import { PageHeader } from "@/components/page-header"
import { EmptyState } from "@/components/empty-state"
import { Database, Mail, Rocket, ArrowRight, CheckCircle2 } from "lucide-react"
import { fmt, relTime, cn } from "@/lib/utils"

const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  Mail,
  Rocket,
  Database,
}

export default function SourcesPage() {
  const { data, isLoading, error } = useSWR<{ sources: SourceCard[] }>(
    "/api/sources",
    swrFetcher,
  )
  const sources = data?.sources || []

  return (
    <div className="space-y-6">
      <PageHeader
        title="Sources"
        subtitle="Each dataset the system can draw leads from. Different fields per source — explore, scrape fresh data, then add to a campaign."
      />

      {error && (
        <div className="rounded-md border border-red-900/40 bg-red-950/30 text-red-200 text-sm px-4 py-3">
          Failed to load sources: {String((error as Error).message)}
        </div>
      )}

      {isLoading && sources.length === 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={i}
              className="h-48 rounded-xl border border-zinc-800/80 bg-[#18181b]"
            >
              <div className="skeleton h-full w-full rounded-xl" />
            </div>
          ))}
        </div>
      ) : sources.length === 0 ? (
        <EmptyState
          icon={<Database className="size-5" />}
          title="No sources registered"
          hint="Register sources in the backend via register_source() in main.py."
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {sources.map((s) => (
            <SourceCardView key={s.id} s={s} />
          ))}
        </div>
      )}
    </div>
  )
}

function SourceCardView({ s }: { s: SourceCard }) {
  const Icon = ICONS[s.icon] || Database
  const isGrab = s.type === "grab"
  const summary = s.summary || {}

  return (
    <Link
      href={`/sources/${s.id}`}
      className={cn(
        "group relative rounded-xl border border-zinc-800/80 bg-[#18181b]",
        "hover:border-[hsl(250_80%_62%/0.4)] hover:bg-zinc-900/60 transition-colors",
        "p-5 flex flex-col gap-4",
      )}
    >
      <div className="flex items-center gap-3">
        <div className="size-9 rounded-md bg-gradient-to-br from-[hsl(250_80%_62%/0.2)] to-[hsl(270_90%_65%/0.1)] flex items-center justify-center border border-[hsl(250_80%_62%/0.2)]">
          <Icon className="size-4 text-[hsl(250_80%_78%)]" />
        </div>
        <div className="min-w-0">
          <div className="text-sm font-semibold tracking-tight text-zinc-100 truncate">
            {s.label}
          </div>
          <div className="text-[11px] uppercase tracking-[0.12em] text-zinc-500">
            {s.type}
          </div>
        </div>
        <ArrowRight className="ml-auto size-4 text-zinc-600 group-hover:text-zinc-300 transition-colors" />
      </div>

      <div className="text-xs text-zinc-400 leading-relaxed min-h-[34px]">
        {s.description || <span className="text-zinc-600">—</span>}
      </div>

      <div className="grid grid-cols-3 gap-2 text-center">
        {isGrab ? (
          <>
            <Stat label="Leads" value={fmt(summary.leads_count || 0)} />
            <Stat label="Founders" value={fmt(summary.founders_count || 0)} />
            <Stat
              label="Verified"
              value={fmt(summary.verified_emails || 0)}
              emphasis={(summary.verified_emails || 0) > 0}
            />
          </>
        ) : (
          <>
            <Stat label="Leads" value={fmt(summary.leads_count || 0)} />
            <Stat label="Emailed" value={fmt(summary.emailed || 0)} />
            <Stat label="Replies" value={fmt(summary.replies || 0)} />
          </>
        )}
      </div>

      <div className="pt-3 border-t border-zinc-800/70 text-[11px] text-zinc-500 flex items-center justify-between">
        <span>
          {isGrab
            ? summary.last_scrape
              ? `Scraped ${relTime(summary.last_scrape)}`
              : "Never scraped"
            : summary.last_sent
              ? `Last sent ${relTime(summary.last_sent)}`
              : "No sends yet"}
        </span>
        {summary.exists ? (
          <span className="flex items-center gap-1 text-emerald-500/80">
            <CheckCircle2 className="size-3" />
            ready
          </span>
        ) : (
          <span className="text-zinc-600">empty</span>
        )}
      </div>
    </Link>
  )
}

function Stat({
  label,
  value,
  emphasis,
}: {
  label: string
  value: string
  emphasis?: boolean
}) {
  return (
    <div className="rounded-md border border-zinc-800/70 bg-zinc-900/40 px-2 py-2">
      <div
        className={cn(
          "text-base font-semibold tnum tracking-tight",
          emphasis ? "text-[hsl(250_80%_78%)]" : "text-zinc-100",
        )}
      >
        {value}
      </div>
      <div className="text-[10px] uppercase tracking-[0.1em] text-zinc-500 mt-0.5">
        {label}
      </div>
    </div>
  )
}
