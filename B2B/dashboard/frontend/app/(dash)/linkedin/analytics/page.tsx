"use client"

import * as React from "react"
import useSWR from "swr"
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip,
  CartesianGrid, BarChart, Bar, Legend,
} from "recharts"
import { PageHeader } from "@/components/page-header"
import { KpiCard } from "@/components/kpi-card"
import { swrFetcher } from "@/lib/api"
import { Send, MessageSquareReply, Ban, Inbox, Percent } from "lucide-react"

type AnalyticsResponse = {
  days: number
  start: string
  end: string
  series: {
    day: string
    drafted: number
    sent: number
    replied: number
    bounced: number
  }[]
  totals: {
    total_leads: number
    sent: number
    replied: number
    bounced: number
    recyclebin: number
  }
  reply_rate_pct: number
  bounce_rate_pct: number
}

const WINDOWS = [7, 14, 30, 60, 90]

export default function LinkedInAnalyticsPage() {
  const [days, setDays] = React.useState(30)
  const { data } = useSWR<AnalyticsResponse>(
    `/api/linkedin/analytics?days=${days}`,
    swrFetcher,
    { refreshInterval: 60_000 },
  )

  const series = (data?.series ?? []).map((d) => ({
    ...d,
    day: d.day.slice(5),   // MM-DD
  }))

  return (
    <div className="space-y-6">
      <PageHeader
        title="LinkedIn Analytics"
        subtitle="Day-by-day sends, replies, and bounces with totals and reply/bounce rates."
        actions={
          <div className="flex items-center gap-1 rounded-md border border-zinc-800 bg-zinc-900/60 p-0.5">
            {WINDOWS.map((w) => (
              <button
                key={w}
                onClick={() => setDays(w)}
                className={`px-2.5 py-1 text-xs rounded transition-colors ${
                  w === days
                    ? "bg-[hsl(250_80%_62%/0.18)] text-zinc-100"
                    : "text-zinc-500 hover:text-zinc-300"
                }`}
              >
                {w}d
              </button>
            ))}
          </div>
        }
      />

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <KpiCard
          label="Total leads"
          value={data?.totals.total_leads ?? 0}
          icon={<Inbox className="size-4" />}
          accent="violet"
        />
        <KpiCard
          label="Sent (all time)"
          value={data?.totals.sent ?? 0}
          icon={<Send className="size-4" />}
          accent="emerald"
        />
        <KpiCard
          label="Replied"
          value={data?.totals.replied ?? 0}
          icon={<MessageSquareReply className="size-4" />}
          accent="amber"
          hint={`${data?.reply_rate_pct ?? 0}% reply rate`}
        />
        <KpiCard
          label="Bounced"
          value={data?.totals.bounced ?? 0}
          icon={<Ban className="size-4" />}
          accent="rose"
          hint={`${data?.bounce_rate_pct ?? 0}% bounce rate`}
        />
        <KpiCard
          label="Reply rate"
          value={data?.reply_rate_pct != null ? `${data.reply_rate_pct}%` : "0%"}
          icon={<Percent className="size-4" />}
          accent="sky"
        />
      </div>

      <section className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
        <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-500 mb-3">
          Daily activity ({days}d)
        </div>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
              <XAxis dataKey="day" stroke="#71717a" fontSize={11} />
              <YAxis stroke="#71717a" fontSize={11} allowDecimals={false} />
              <Tooltip
                contentStyle={{
                  background: "#18181b",
                  border: "1px solid #3f3f46",
                  fontSize: 12,
                }}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Line dataKey="sent" stroke="hsl(160 70% 50%)" strokeWidth={2} dot={false} />
              <Line dataKey="replied" stroke="hsl(40 90% 55%)" strokeWidth={2} dot={false} />
              <Line dataKey="bounced" stroke="hsl(0 70% 55%)" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
        <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-500 mb-3">
          Drafts generated per day ({days}d)
        </div>
        <div className="h-52">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
              <XAxis dataKey="day" stroke="#71717a" fontSize={11} />
              <YAxis stroke="#71717a" fontSize={11} allowDecimals={false} />
              <Tooltip
                contentStyle={{
                  background: "#18181b",
                  border: "1px solid #3f3f46",
                  fontSize: 12,
                }}
              />
              <Bar dataKey="drafted" fill="hsl(250 80% 62%)" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      <OutcomeBreakdown />
    </div>
  )
}

type OutcomeBucket = {
  key: string
  sent: number
  replied: number
  positive: number
  reply_rate_pct: number
  positive_rate_pct: number
}

type OutcomeStats = {
  totals: {
    sent: number
    replied: number
    positive: number
    reply_rate_pct: number
    positive_rate_pct: number
  }
  by_cv_cluster: OutcomeBucket[]
  by_body_length: OutcomeBucket[]
  by_subject_first: OutcomeBucket[]
  by_weekday: OutcomeBucket[]
}

function OutcomeBreakdown() {
  const { data } = useSWR<OutcomeStats>(
    "/api/linkedin/outreach-stats",
    swrFetcher,
    { refreshInterval: 120_000 },
  )

  if (!data || data.totals.sent === 0) {
    return (
      <section className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
        <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-500 mb-2">
          What works — reply rate by style
        </div>
        <div className="text-xs text-zinc-500">
          Not enough sent emails yet. Data appears once you start getting
          replies on sent drafts.
        </div>
      </section>
    )
  }

  const avg = data.totals.reply_rate_pct

  return (
    <section className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-500">
          What works — reply rate by style
        </div>
        <div className="text-[11px] text-zinc-500">
          Baseline: {avg}% reply rate ({data.totals.replied}/{data.totals.sent})
        </div>
      </div>
      <div className="grid md:grid-cols-2 gap-4">
        <OutcomeTable title="By CV / pitch"      rows={data.by_cv_cluster}    baseline={avg} />
        <OutcomeTable title="By body length"     rows={data.by_body_length}   baseline={avg} />
        <OutcomeTable title="By subject opener"  rows={data.by_subject_first} baseline={avg} />
        <OutcomeTable title="By weekday sent"    rows={data.by_weekday}       baseline={avg} />
      </div>
    </section>
  )
}

function OutcomeTable({
  title, rows, baseline,
}: {
  title: string
  rows: OutcomeBucket[]
  baseline: number
}) {
  const visible = rows.filter((r) => r.sent >= 1).slice(0, 8)
  return (
    <div className="rounded-md border border-zinc-800/80 bg-zinc-900/40">
      <div className="px-3 py-2 border-b border-zinc-800/80 text-[11px] font-medium text-zinc-400">
        {title}
      </div>
      <table className="w-full text-xs">
        <thead className="text-[10px] uppercase tracking-wide text-zinc-500">
          <tr>
            <th className="text-left py-1.5 px-3 font-normal">Bucket</th>
            <th className="text-right py-1.5 px-2 font-normal">Sent</th>
            <th className="text-right py-1.5 px-2 font-normal">Reply</th>
            <th className="text-right py-1.5 px-3 font-normal">Rate</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((r) => {
            const diff = r.reply_rate_pct - baseline
            const colour =
              r.sent < 3
                ? "text-zinc-500"
                : diff > 2
                  ? "text-emerald-300"
                  : diff < -2
                    ? "text-rose-300"
                    : "text-zinc-300"
            return (
              <tr key={r.key} className="border-t border-zinc-800/60">
                <td className="py-1.5 px-3 text-zinc-300 truncate">{r.key}</td>
                <td className="py-1.5 px-2 text-right text-zinc-400">{r.sent}</td>
                <td className="py-1.5 px-2 text-right text-zinc-400">{r.replied}</td>
                <td className={`py-1.5 px-3 text-right font-mono ${colour}`}>
                  {r.reply_rate_pct}%
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
