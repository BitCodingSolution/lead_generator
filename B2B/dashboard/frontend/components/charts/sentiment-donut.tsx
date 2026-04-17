"use client"

import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts"
import { fmt } from "@/lib/utils"

type Slice = { name: string; value: number; color: string }

export function SentimentDonut({
  positive,
  objection,
  neutral,
  negative,
  ooo,
  bounce,
}: {
  positive: number
  objection: number
  neutral: number
  negative: number
  ooo: number
  bounce: number
}) {
  const data: Slice[] = [
    { name: "Positive", value: positive, color: "#34d399" },
    { name: "Objection", value: objection, color: "#fbbf24" },
    { name: "Neutral", value: neutral, color: "#a1a1aa" },
    { name: "Negative", value: negative, color: "#fb7185" },
    { name: "OOO", value: ooo, color: "#38bdf8" },
    { name: "Bounce", value: bounce, color: "#f43f5e" },
  ].filter((d) => d.value > 0)

  const total = data.reduce((s, d) => s + d.value, 0)

  if (total === 0) {
    return (
      <div className="flex items-center justify-center h-[200px] text-xs text-zinc-500">
        No replies yet
      </div>
    )
  }

  return (
    <div className="relative h-[200px]">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            innerRadius={55}
            outerRadius={80}
            paddingAngle={2}
            dataKey="value"
            stroke="none"
          >
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.color} />
            ))}
          </Pie>
          <Tooltip
            cursor={false}
            contentStyle={{
              background: "#18181b",
              border: "1px solid #27272a",
              borderRadius: 6,
              fontSize: 12,
              color: "#e7e7ea",
            }}
            itemStyle={{ color: "#e7e7ea" }}
          />
        </PieChart>
      </ResponsiveContainer>
      <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
        <div className="text-[10px] uppercase tracking-[0.15em] text-zinc-500">Replies</div>
        <div className="text-2xl font-semibold tnum text-zinc-50">{fmt(total)}</div>
      </div>
    </div>
  )
}

export function SentimentLegend({
  items,
}: {
  items: { label: string; value: number; color: string }[]
}) {
  return (
    <div className="grid grid-cols-2 gap-2 mt-3">
      {items.map((i) => (
        <div key={i.label} className="flex items-center justify-between text-xs">
          <div className="flex items-center gap-2">
            <span className="size-2 rounded-sm" style={{ background: i.color }} />
            <span className="text-zinc-400">{i.label}</span>
          </div>
          <span className="tnum text-zinc-300">{fmt(i.value)}</span>
        </div>
      ))}
    </div>
  )
}
