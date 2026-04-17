"use client"

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Cell,
} from "recharts"

export function IndustryBar({
  data,
  dataKey = "sent",
  nameKey = "industry",
  colorMap,
}: {
  data: Array<Record<string, unknown>>
  dataKey?: string
  nameKey?: string
  colorMap?: (row: Record<string, unknown>, i: number) => string
}) {
  return (
    <div className="h-[280px] w-full">
      <ResponsiveContainer>
        <BarChart
          data={data}
          margin={{ top: 10, right: 8, left: -20, bottom: 0 }}
          barCategoryGap={"18%"}
        >
          <CartesianGrid stroke="#27272a" strokeDasharray="2 4" vertical={false} />
          <XAxis
            dataKey={nameKey}
            stroke="#52525b"
            fontSize={11}
            tickLine={false}
            axisLine={false}
            interval={0}
            angle={-15}
            height={50}
            textAnchor="end"
          />
          <YAxis
            stroke="#52525b"
            fontSize={11}
            tickLine={false}
            axisLine={false}
            allowDecimals={false}
          />
          <Tooltip
            cursor={{ fill: "rgba(255,255,255,0.04)" }}
            contentStyle={{
              background: "#18181b",
              border: "1px solid #27272a",
              borderRadius: 6,
              fontSize: 12,
              color: "#e7e7ea",
            }}
            itemStyle={{ color: "#e7e7ea" }}
          />
          <Bar dataKey={dataKey} radius={[4, 4, 0, 0]}>
            {data.map((row, i) => (
              <Cell
                key={i}
                fill={colorMap ? colorMap(row, i) : "hsl(250 80% 62%)"}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
