"use client"

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts"
import type { DailyActivity } from "@/lib/types"

export function ActivityArea({ data }: { data: DailyActivity[] }) {
  const rows = (data || []).map((d) => ({
    day: d.day?.slice(5) || d.day,
    sent: d.sent,
    replies: d.replies,
  }))
  return (
    <div className="h-[240px] w-full">
      <ResponsiveContainer>
        <AreaChart data={rows} margin={{ top: 10, right: 8, left: -20, bottom: 0 }}>
          <defs>
            <linearGradient id="gSent" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="hsl(250 80% 62%)" stopOpacity={0.4} />
              <stop offset="100%" stopColor="hsl(250 80% 62%)" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="gRep" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#34d399" stopOpacity={0.35} />
              <stop offset="100%" stopColor="#34d399" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="#27272a" strokeDasharray="2 4" vertical={false} />
          <XAxis
            dataKey="day"
            stroke="#52525b"
            fontSize={11}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            stroke="#52525b"
            fontSize={11}
            tickLine={false}
            axisLine={false}
            allowDecimals={false}
          />
          <Tooltip
            cursor={{ stroke: "#3f3f46", strokeWidth: 1 }}
            contentStyle={{
              background: "#18181b",
              border: "1px solid #27272a",
              borderRadius: 6,
              fontSize: 12,
              color: "#e7e7ea",
            }}
            itemStyle={{ color: "#e7e7ea" }}
          />
          <Area
            type="monotone"
            dataKey="sent"
            name="Sent"
            stroke="hsl(250 80% 65%)"
            strokeWidth={1.5}
            fill="url(#gSent)"
            dot={false}
          />
          <Area
            type="monotone"
            dataKey="replies"
            name="Replies"
            stroke="#34d399"
            strokeWidth={1.5}
            fill="url(#gRep)"
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
