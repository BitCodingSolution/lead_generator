"use client"

import useSWR from "swr"
import Link from "next/link"
import { swrFetcher } from "@/lib/api"
import type { DailyActivity, HotLead, RecentSent } from "@/lib/types"
import { BatchesSummary } from "@/components/batches-panel"
import { KpiCard } from "@/components/kpi-card"
import { PageHeader } from "@/components/page-header"
import { StatusChip } from "@/components/status-chip"
import { EmptyState } from "@/components/empty-state"
import { ActivityArea } from "@/components/charts/activity-area"
import {
  Mail,
  MessageSquareReply,
  Users,
  Send,
  Flame,
  Target,
  Inbox,
} from "lucide-react"
import { fmt, pct, relTime, truncate } from "@/lib/utils"

type OverviewStats = {
  total_leads: number
  leads_by_source: Record<string, number>
  drafted: number
  total_sent: number
  sent_today: number
  total_replies: number
  hot_pending: number
  reply_rate_pct: number
  positive_rate_pct: number
  daily_quota: number
  remaining_today: number
  has_replies: boolean
}

export default function OverviewPage() {
  const { data: stats, isLoading: statsLoading } = useSWR<OverviewStats>(
    "/api/overview",
    swrFetcher,
    { refreshInterval: 30000 },
  )
  const { data: daily } = useSWR<DailyActivity[]>(
    "/api/daily-activity?days=30",
    swrFetcher,
    { refreshInterval: 60000 },
  )
  const { data: hot } = useSWR<HotLead[]>("/api/hot-leads?limit=8", swrFetcher, {
    refreshInterval: 30000,
  })
  const { data: recent } = useSWR<RecentSent[]>(
    "/api/recent-sent?limit=10",
    swrFetcher,
    { refreshInterval: 30000 },
  )

  const quotaUsed = stats ? stats.daily_quota - stats.remaining_today : 0
  const quotaPct =
    stats && stats.daily_quota > 0
      ? Math.min(100, (quotaUsed / stats.daily_quota) * 100)
      : 0

  return (
    <div className="space-y-8">
      <PageHeader
        title="Outreach Overview"
        subtitle="Daily state of the pipeline across all sources — drafts, sends, and replies at a glance."
      />

      {/* Campaign activity (cross-source batch counters) */}
      <div className="space-y-2">
        <div className="flex items-end justify-between">
          <div>
            <div className="text-xs font-medium uppercase tracking-[0.1em] text-zinc-500">
              Campaign activity
            </div>
            <div className="text-sm text-zinc-300 mt-0.5">
              Batches currently in flight across all sources
            </div>
          </div>
          <Link
            href="/campaigns"
            className="text-xs text-zinc-400 hover:text-zinc-200"
          >
            Manage →
          </Link>
        </div>
        <BatchesSummary scope={{ kind: "all" }} />
      </div>

      {/* KPI grid — cross-source */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <KpiCard
          label="Total leads"
          value={stats?.total_leads}
          hint="All sources combined"
          icon={<Users className="size-4" />}
          accent="violet"
          loading={statsLoading}
          index={0}
        />
        <KpiCard
          label="Drafted"
          value={stats?.drafted}
          hint="Ready in batches"
          icon={<Mail className="size-4" />}
          accent="sky"
          loading={statsLoading}
          index={1}
        />
        <KpiCard
          label="Sent today"
          value={stats?.sent_today}
          hint={`${fmt(stats?.total_sent ?? 0)} all time`}
          icon={<Send className="size-4" />}
          accent="violet"
          loading={statsLoading}
          index={2}
        />
        <KpiCard
          label="Replies"
          value={stats?.total_replies}
          hint={`${pct(stats?.reply_rate_pct)} rate`}
          icon={<MessageSquareReply className="size-4" />}
          accent="emerald"
          loading={statsLoading}
          index={3}
        />
        <KpiCard
          label="Positive rate"
          value={pct(stats?.positive_rate_pct)}
          hint="Of all sends"
          icon={<Target className="size-4" />}
          accent="amber"
          loading={statsLoading}
          index={4}
        />
        <KpiCard
          label="Hot pending"
          value={stats?.hot_pending}
          hint="Needs your reply"
          icon={<Flame className="size-4" />}
          accent="rose"
          loading={statsLoading}
          index={5}
        />
      </div>

      {/* Daily quota (cross-source) + Daily activity chart */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-5">
          <div className="flex items-center justify-between mb-3">
            <div>
              <div className="text-xs font-medium uppercase tracking-[0.1em] text-zinc-500">
                Daily send quota
              </div>
              <div className="mt-1 text-2xl font-semibold tracking-tight tnum text-zinc-50">
                {fmt(quotaUsed)}
                <span className="text-zinc-500 text-lg">
                  {" / "}
                  {fmt(stats?.daily_quota ?? 0)}
                </span>
              </div>
              <div className="text-[11px] text-zinc-500 mt-1">
                All sources combined
              </div>
            </div>
            <div className="text-right">
              <div className="text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                Remaining
              </div>
              <div className="text-xl font-semibold tracking-tight tnum text-[hsl(250_80%_75%)]">
                {fmt(stats?.remaining_today ?? 0)}
              </div>
            </div>
          </div>
          <div className="h-2 rounded-full bg-zinc-800 overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-[hsl(250_80%_55%)] to-[hsl(270_80%_68%)]"
              style={{ width: `${quotaPct}%` }}
            />
          </div>
        </div>

        <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-5 lg:col-span-2">
          <div className="flex items-center justify-between mb-2">
            <div>
              <div className="text-xs font-medium uppercase tracking-[0.1em] text-zinc-500">
                Daily activity
              </div>
              <div className="text-sm text-zinc-300 mt-0.5">Last 30 days</div>
            </div>
            <div className="flex items-center gap-3 text-[11px]">
              <span className="inline-flex items-center gap-1.5 text-zinc-400">
                <span className="size-2 rounded-full bg-[hsl(250_80%_62%)]" />{" "}
                Sent
              </span>
              <span className="inline-flex items-center gap-1.5 text-zinc-400">
                <span className="size-2 rounded-full bg-emerald-400" /> Replies
              </span>
            </div>
          </div>
          {daily?.length ? (
            <ActivityArea data={daily} />
          ) : (
            <div className="h-[240px] flex items-center justify-center text-xs text-zinc-500">
              No activity yet.
            </div>
          )}
        </div>
      </div>

      {/* Hot + Recent tables */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="rounded-xl border border-zinc-800/80 bg-[#18181b]">
          <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800/70">
            <div>
              <div className="text-xs font-medium uppercase tracking-[0.1em] text-zinc-500">
                Hot leads
              </div>
              <div className="text-sm text-zinc-300 mt-0.5">
                Unhandled replies needing attention
              </div>
            </div>
            <Link
              href="/replies"
              className="text-xs text-zinc-400 hover:text-zinc-200"
            >
              View all →
            </Link>
          </div>
          <div className="divide-y divide-zinc-800/60">
            {hot && hot.length > 0 ? (
              hot.map((h) => (
                <div
                  key={h.id}
                  className="px-5 py-3 hover:bg-zinc-800/30 transition-colors"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <div className="text-sm font-medium text-zinc-100 truncate">
                          {h.name || h.company || "—"}
                        </div>
                        <StatusChip value={h.sentiment} />
                      </div>
                      <div className="text-[11px] text-zinc-500 mt-0.5 truncate">
                        {h.company}
                        {h.industry ? ` · ${h.industry}` : ""}
                        {h.city ? ` · ${h.city}` : ""}
                      </div>
                      <div className="text-xs text-zinc-400 mt-1.5 line-clamp-2">
                        {truncate(h.snippet, 160)}
                      </div>
                    </div>
                    <div className="text-[11px] text-zinc-500 tnum whitespace-nowrap">
                      {relTime(h.reply_at)}
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <div className="p-6">
                <EmptyState
                  icon={<Inbox className="size-5" />}
                  title="No hot leads right now"
                  hint="Positive or objection replies will surface here first."
                />
              </div>
            )}
          </div>
        </div>

        <div className="rounded-xl border border-zinc-800/80 bg-[#18181b]">
          <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800/70">
            <div>
              <div className="text-xs font-medium uppercase tracking-[0.1em] text-zinc-500">
                Recently sent
              </div>
              <div className="text-sm text-zinc-300 mt-0.5">
                Latest outgoing emails
              </div>
            </div>
            <Link
              href="/leads"
              className="text-xs text-zinc-400 hover:text-zinc-200"
            >
              View leads →
            </Link>
          </div>
          <div className="divide-y divide-zinc-800/60">
            {recent && recent.length > 0 ? (
              recent.map((r, i) => (
                <div
                  key={i}
                  className="px-5 py-3 hover:bg-zinc-800/30 transition-colors"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <div className="text-sm font-medium text-zinc-100 truncate">
                          {r.company || r.name || "—"}
                        </div>
                        <StatusChip value={r.status} />
                      </div>
                      <div className="text-[11px] text-zinc-500 mt-0.5 truncate">
                        {r.industry}
                        {r.city ? ` · ${r.city}` : ""}
                      </div>
                      <div className="text-xs text-zinc-300 mt-1.5 truncate">
                        {truncate(r.subject, 90)}
                      </div>
                    </div>
                    <div className="text-[11px] text-zinc-500 tnum whitespace-nowrap">
                      {relTime(r.sent_at)}
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <div className="p-6">
                <EmptyState
                  icon={<Send className="size-5" />}
                  title="Nothing sent yet"
                  hint="Use Campaigns → Send to deliver drafted emails."
                />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
