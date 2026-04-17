"use client"

import useSWR from "swr"
import { swrFetcher } from "@/lib/api"
import type {
  Stats,
  FunnelStage,
  DailyActivity,
  IndustryRow,
  HotLead,
  RecentSent,
} from "@/lib/types"
import { KpiCard } from "@/components/kpi-card"
import { PageHeader } from "@/components/page-header"
import { StatusChip } from "@/components/status-chip"
import { EmptyState } from "@/components/empty-state"
import { FunnelChart } from "@/components/charts/funnel"
import {
  SentimentDonut,
  SentimentLegend,
} from "@/components/charts/sentiment-donut"
import { ActivityArea } from "@/components/charts/activity-area"
import { IndustryBar } from "@/components/charts/industry-bar"
import {
  Mail,
  MessageSquareReply,
  Users,
  Send,
  Flame,
  Target,
  Inbox,
  Zap,
} from "lucide-react"
import { fmt, pct, relTime, truncate } from "@/lib/utils"
import Link from "next/link"

export default function OverviewPage() {
  const { data: stats, isLoading: statsLoading } = useSWR<Stats>(
    "/api/stats",
    swrFetcher,
    { refreshInterval: 30000 },
  )
  const { data: funnel } = useSWR<FunnelStage[]>("/api/funnel", swrFetcher, {
    refreshInterval: 60000,
  })
  const { data: daily } = useSWR<DailyActivity[]>(
    "/api/daily-activity?days=30",
    swrFetcher,
    { refreshInterval: 60000 },
  )
  const { data: industries } = useSWR<IndustryRow[]>("/api/industries", swrFetcher, {
    refreshInterval: 120000,
  })
  const { data: hot } = useSWR<HotLead[]>("/api/hot-leads?limit=8", swrFetcher, {
    refreshInterval: 30000,
  })
  const { data: recent } = useSWR<RecentSent[]>(
    "/api/recent-sent?limit=10",
    swrFetcher,
    { refreshInterval: 30000 },
  )

  const quotaUsed = stats ? stats.daily_quota - stats.remaining_today : 0
  const quotaPct = stats && stats.daily_quota > 0
    ? Math.min(100, (quotaUsed / stats.daily_quota) * 100)
    : 0

  const topIndustries = (industries || [])
    .slice()
    .sort((a, b) => b.sent - a.sent)
    .slice(0, 8)

  return (
    <div className="space-y-8">
      <PageHeader
        title="Outreach Overview"
        subtitle="Daily state of the pipeline — drafts, sends, and replies at a glance."
        actions={
          <Link
            href="/campaigns"
            className="inline-flex items-center gap-1.5 rounded-md bg-[hsl(250_80%_62%)] hover:bg-[hsl(250_80%_58%)] text-white text-sm px-3 py-1.5 transition-colors shadow-[0_0_0_1px_rgba(255,255,255,0.05)]"
          >
            <Zap className="size-3.5" />
            Run a campaign
          </Link>
        }
      />

      {/* KPI grid */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <KpiCard
          label="Total leads"
          value={stats?.total_leads}
          hint={`${fmt(stats?.new_leads ?? 0)} new · ${fmt(stats?.picked ?? 0)} picked`}
          icon={<Users className="size-4" />}
          accent="violet"
          loading={statsLoading}
          index={0}
        />
        <KpiCard
          label="Drafted"
          value={stats?.drafted}
          hint="Ready to send"
          icon={<Mail className="size-4" />}
          accent="sky"
          loading={statsLoading}
          index={1}
        />
        <KpiCard
          label="Sent (all time)"
          value={stats?.total_sent}
          hint={`${fmt(stats?.sent_today ?? 0)} today`}
          icon={<Send className="size-4" />}
          accent="violet"
          loading={statsLoading}
          index={2}
        />
        <KpiCard
          label="Replies"
          value={stats?.total_replies}
          hint={`${fmt(stats?.replies_today ?? 0)} today`}
          icon={<MessageSquareReply className="size-4" />}
          accent="emerald"
          loading={statsLoading}
          index={3}
        />
        <KpiCard
          label="Reply rate"
          value={pct(stats?.reply_rate_pct)}
          hint={`${pct(stats?.positive_rate_pct)} positive`}
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

      {/* Quota + Activity */}
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
          <div className="mt-4 grid grid-cols-2 gap-3">
            <div className="rounded-md border border-zinc-800/80 bg-zinc-900/40 px-3 py-2">
              <div className="text-[10px] uppercase tracking-[0.1em] text-zinc-500">Tier 1</div>
              <div className="text-sm font-medium tnum text-zinc-200 mt-0.5">{fmt(stats?.tier1 ?? 0)}</div>
            </div>
            <div className="rounded-md border border-zinc-800/80 bg-zinc-900/40 px-3 py-2">
              <div className="text-[10px] uppercase tracking-[0.1em] text-zinc-500">Tier 2</div>
              <div className="text-sm font-medium tnum text-zinc-200 mt-0.5">{fmt(stats?.tier2 ?? 0)}</div>
            </div>
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
                <span className="size-2 rounded-full bg-[hsl(250_80%_62%)]" /> Sent
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

      {/* Funnel + Sentiment */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-5 lg:col-span-2">
          <div className="mb-4">
            <div className="text-xs font-medium uppercase tracking-[0.1em] text-zinc-500">
              Funnel
            </div>
            <div className="text-sm text-zinc-300 mt-0.5">Stage-by-stage throughput</div>
          </div>
          {funnel?.length ? (
            <FunnelChart data={funnel} />
          ) : (
            <EmptyState
              icon={<Target className="size-5" />}
              title="Funnel is empty"
              hint="Pick a batch from Campaigns to start generating drafts."
            />
          )}
        </div>

        <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-5">
          <div className="mb-2">
            <div className="text-xs font-medium uppercase tracking-[0.1em] text-zinc-500">
              Sentiment mix
            </div>
            <div className="text-sm text-zinc-300 mt-0.5">Reply breakdown</div>
          </div>
          <SentimentDonut
            positive={stats?.positive ?? 0}
            objection={stats?.objection ?? 0}
            neutral={stats?.neutral ?? 0}
            negative={stats?.negative ?? 0}
            ooo={stats?.ooo ?? 0}
            bounce={stats?.bounce ?? 0}
          />
          <SentimentLegend
            items={[
              { label: "Positive", value: stats?.positive ?? 0, color: "#34d399" },
              { label: "Objection", value: stats?.objection ?? 0, color: "#fbbf24" },
              { label: "Neutral", value: stats?.neutral ?? 0, color: "#a1a1aa" },
              { label: "Negative", value: stats?.negative ?? 0, color: "#fb7185" },
              { label: "OOO", value: stats?.ooo ?? 0, color: "#38bdf8" },
              { label: "Bounce", value: stats?.bounce ?? 0, color: "#f43f5e" },
            ]}
          />
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
              <div className="text-sm text-zinc-300 mt-0.5">Unhandled replies needing attention</div>
            </div>
            <Link href="/replies" className="text-xs text-zinc-400 hover:text-zinc-200">
              View all →
            </Link>
          </div>
          <div className="divide-y divide-zinc-800/60">
            {hot && hot.length > 0 ? (
              hot.map((h) => (
                <div key={h.id} className="px-5 py-3 hover:bg-zinc-800/30 transition-colors">
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
              <div className="text-sm text-zinc-300 mt-0.5">Latest outgoing emails</div>
            </div>
            <Link href="/leads" className="text-xs text-zinc-400 hover:text-zinc-200">
              View leads →
            </Link>
          </div>
          <div className="divide-y divide-zinc-800/60">
            {recent && recent.length > 0 ? (
              recent.map((r, i) => (
                <div key={i} className="px-5 py-3 hover:bg-zinc-800/30 transition-colors">
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

      {/* Industry breakdown */}
      <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <div className="text-xs font-medium uppercase tracking-[0.1em] text-zinc-500">
              Sent by industry
            </div>
            <div className="text-sm text-zinc-300 mt-0.5">Top performing verticals</div>
          </div>
        </div>
        {topIndustries.length ? (
          <IndustryBar
            data={topIndustries as unknown as Array<Record<string, unknown>>}
            dataKey="sent"
            nameKey="industry"
            colorMap={(row) => {
              const t = Number(row.tier) || 2
              return t === 1 ? "hsl(250 80% 62%)" : "hsl(250 40% 42%)"
            }}
          />
        ) : (
          <EmptyState
            icon={<Users className="size-5" />}
            title="No industries tracked yet"
            hint="Pick a batch to populate industry stats."
          />
        )}
      </div>
    </div>
  )
}
