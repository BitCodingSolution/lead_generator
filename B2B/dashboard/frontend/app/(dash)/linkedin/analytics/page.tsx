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
    </div>
  )
}
