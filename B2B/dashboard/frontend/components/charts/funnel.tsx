"use client"

import { motion } from "framer-motion"
import { fmt } from "@/lib/utils"
import type { FunnelStage } from "@/lib/types"

const STAGE_COLORS: Record<string, string> = {
  new: "hsl(210 90% 62%)",
  picked: "hsl(250 80% 62%)",
  drafted: "hsl(270 80% 68%)",
  sent: "hsl(155 70% 55%)",
  replied: "hsl(40 90% 60%)",
}

export function FunnelChart({ data }: { data: FunnelStage[] }) {
  if (!data?.length) return null
  const max = Math.max(...data.map((d) => d.count), 1)
  return (
    <div className="space-y-3">
      {data.map((s, i) => {
        const pct = Math.max(4, (s.count / max) * 100)
        const color = STAGE_COLORS[s.stage.toLowerCase()] || "hsl(250 80% 62%)"
        return (
          <div key={s.stage} className="group">
            <div className="flex items-center justify-between text-xs mb-1.5">
              <span className="text-zinc-400 capitalize tracking-tight">{s.stage}</span>
              <span className="text-zinc-300 tnum font-medium">{fmt(s.count)}</span>
            </div>
            <div className="h-2 rounded-full bg-zinc-800/80 overflow-hidden">
              <motion.div
                initial={{ width: 0 }}
                animate={{ width: `${pct}%` }}
                transition={{ duration: 0.5, delay: i * 0.06, ease: "easeOut" }}
                className="h-full rounded-full"
                style={{
                  background: `linear-gradient(90deg, ${color}, ${color}aa)`,
                }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}
