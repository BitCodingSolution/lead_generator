"use client"

import useSWR from "swr"
import * as React from "react"
import { swrFetcher } from "@/lib/api"
import type { IndustryRow, HotLead, RecentSent } from "@/lib/types"
import { PageHeader } from "@/components/page-header"
import { IndustryBar } from "@/components/charts/industry-bar"
import { EmptyState } from "@/components/empty-state"
import { fmt, pct, truncate } from "@/lib/utils"
import { BarChart3, Trophy, Timer } from "lucide-react"
import {
  BarChart,
  Bar,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

export default function AnalyticsPage() {
  const { data: industries } = useSWR<IndustryRow[]>("/api/industries", swrFetcher)
  const { data: replies } = useSWR<HotLead[]>("/api/hot-leads?limit=500", swrFetcher)
  const { data: recent } = useSWR<RecentSent[]>("/api/recent-sent?limit=500", swrFetcher)

  // Reply rate by industry = replies / sent per industry
  const byIndustry = React.useMemo(() => {
    const replyMap = new Map<string, number>()
    ;(replies || []).forEach((r) => {
      const k = r.industry || "Unknown"
      replyMap.set(k, (replyMap.get(k) || 0) + 1)
    })
    return (industries || [])
      .filter((i) => i.sent > 0)
      .map((i) => {
        const reps = replyMap.get(i.industry) || 0
        return {
          industry: i.industry,
          sent: i.sent,
          replies: reps,
          rate: i.sent ? (reps / i.sent) * 100 : 0,
          tier: i.tier,
        }
      })
      .sort((a, b) => b.rate - a.rate)
  }, [industries, replies])

  // Subject hall of fame: subjects from recent-sent that appear in replies (by lead_id)
  const replyIds = React.useMemo(
    () => new Set((replies || []).map((r) => String(r.lead_id))),
    [replies],
  )

  const subjectWinners = React.useMemo(() => {
    const map = new Map<string, { subject: string; replies: number; sent: number }>()
    ;(recent || []).forEach((s) => {
      if (!s.subject) return
      const key = s.subject.trim().slice(0, 100)
      const entry = map.get(key) || { subject: key, replies: 0, sent: 0 }
      entry.sent += 1
      if (replyIds.has(String(s.lead_id))) entry.replies += 1
      map.set(key, entry)
    })
    return Array.from(map.values())
      .filter((e) => e.replies > 0)
      .sort((a, b) => b.replies - a.replies || b.sent - a.sent)
      .slice(0, 12)
  }, [recent, replyIds])

  // Time-to-first-reply histogram (hours since sent -> reply)
  const ttfr = React.useMemo(() => {
    const bySentLead = new Map<string, string>()
    ;(recent || []).forEach((r) => {
      if (r.sent_at) bySentLead.set(String(r.lead_id), r.sent_at)
    })
    const buckets = [
      { label: "<1h", min: 0, max: 1, count: 0 },
      { label: "1-4h", min: 1, max: 4, count: 0 },
      { label: "4-24h", min: 4, max: 24, count: 0 },
      { label: "1-3d", min: 24, max: 72, count: 0 },
      { label: "3-7d", min: 72, max: 168, count: 0 },
      { label: "7d+", min: 168, max: 100000, count: 0 },
    ]
    ;(replies || []).forEach((r) => {
      const sent = bySentLead.get(String(r.lead_id))
      if (!sent || !r.reply_at) return
      const h =
        (new Date(r.reply_at).getTime() - new Date(sent).getTime()) / 3_600_000
      if (!isFinite(h) || h < 0) return
      const b = buckets.find((b) => h >= b.min && h < b.max)
      if (b) b.count += 1
    })
    return buckets
  }, [recent, replies])

  const totalTtfr = ttfr.reduce((s, b) => s + b.count, 0)

  return (
    <div className="space-y-6">
      <PageHeader
        title="Analytics"
        subtitle="What's working. Industries, subject lines, and response speed."
      />

      <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <div className="text-xs uppercase tracking-[0.1em] text-zinc-500 flex items-center gap-2">
              <BarChart3 className="size-3.5" />
              Reply rate by industry
            </div>
            <div className="text-sm text-zinc-300 mt-0.5">
              Replies ÷ sent. Bars colored by tier.
            </div>
          </div>
        </div>
        {byIndustry.length ? (
          <IndustryBar
            data={byIndustry as unknown as Array<Record<string, unknown>>}
            dataKey="rate"
            nameKey="industry"
            colorMap={(row) => {
              const t = Number(row.tier) || 2
              return t === 1 ? "hsl(250 80% 62%)" : "hsl(250 40% 42%)"
            }}
          />
        ) : (
          <EmptyState
            icon={<BarChart3 className="size-5" />}
            title="Not enough data yet"
            hint="Send a few batches and let replies trickle in."
          />
        )}
        {byIndustry.length > 0 && (
          <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-2">
            {byIndustry.slice(0, 4).map((r) => (
              <div
                key={r.industry}
                className="rounded-md border border-zinc-800/80 bg-zinc-900/40 px-3 py-2"
              >
                <div className="text-[10px] uppercase tracking-[0.1em] text-zinc-500 truncate">
                  {r.industry}
                </div>
                <div className="text-lg font-semibold tnum text-zinc-100 mt-0.5">
                  {pct(r.rate)}
                </div>
                <div className="text-[11px] text-zinc-500 tnum">
                  {fmt(r.replies)} / {fmt(r.sent)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-xl border border-zinc-800/80 bg-[#18181b]">
          <div className="px-5 py-4 border-b border-zinc-800/70">
            <div className="text-xs uppercase tracking-[0.1em] text-zinc-500 flex items-center gap-2">
              <Trophy className="size-3.5" />
              Subject line hall of fame
            </div>
            <div className="text-sm text-zinc-300 mt-0.5">
              Subjects that earned replies
            </div>
          </div>
          {subjectWinners.length ? (
            <div className="divide-y divide-zinc-800/60">
              {subjectWinners.map((s, i) => (
                <div
                  key={i}
                  className="px-5 py-2.5 flex items-center gap-3 hover:bg-zinc-800/30 transition-colors"
                >
                  <div className="size-6 rounded-md bg-zinc-800/60 text-zinc-400 text-xs flex items-center justify-center tnum shrink-0">
                    {i + 1}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm text-zinc-200 truncate">
                      {truncate(s.subject, 90)}
                    </div>
                    <div className="text-[11px] text-zinc-500 tnum">
                      {fmt(s.replies)} replies · {fmt(s.sent)} sent
                    </div>
                  </div>
                  <div className="text-sm font-medium tnum text-[hsl(250_80%_78%)]">
                    {pct(s.sent ? (s.replies / s.sent) * 100 : 0, 0)}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="p-6">
              <EmptyState
                icon={<Trophy className="size-5" />}
                title="No winning subjects yet"
                hint="Once replies come in, the top performers will rank here."
              />
            </div>
          )}
        </div>

        <div className="rounded-xl border border-zinc-800/80 bg-[#18181b]">
          <div className="px-5 py-4 border-b border-zinc-800/70">
            <div className="text-xs uppercase tracking-[0.1em] text-zinc-500 flex items-center gap-2">
              <Timer className="size-3.5" />
              Time to first reply
            </div>
            <div className="text-sm text-zinc-300 mt-0.5">
              {fmt(totalTtfr)} reply{totalTtfr === 1 ? "" : "ies"} bucketed by response time
            </div>
          </div>
          <div className="p-4">
            {totalTtfr ? (
              <div className="h-[260px]">
                <ResponsiveContainer>
                  <BarChart data={ttfr} margin={{ top: 10, right: 8, left: -20, bottom: 0 }}>
                    <CartesianGrid stroke="#27272a" strokeDasharray="2 4" vertical={false} />
                    <XAxis dataKey="label" stroke="#52525b" fontSize={11} tickLine={false} axisLine={false} />
                    <YAxis stroke="#52525b" fontSize={11} tickLine={false} axisLine={false} allowDecimals={false} />
                    <Tooltip
                      cursor={{ fill: "rgba(255,255,255,0.04)" }}
                      contentStyle={{
                        background: "#18181b",
                        border: "1px solid #27272a",
                        borderRadius: 6,
                        fontSize: 12,
                        color: "#e7e7ea",
                      }}
                    />
                    <Bar dataKey="count" fill="hsl(250 80% 62%)" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <EmptyState
                icon={<Timer className="size-5" />}
                title="No response timing yet"
                hint="The first few replies will populate this chart."
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
