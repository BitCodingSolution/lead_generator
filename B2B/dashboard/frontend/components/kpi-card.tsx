"use client"

import Link from "next/link"
import { motion } from "framer-motion"
import { cn, fmt } from "@/lib/utils"

type Props = {
  label: string
  value: number | string | null | undefined
  hint?: string
  delta?: { value: number; dir?: "up" | "down" | "neutral"; suffix?: string } | null
  accent?: "violet" | "emerald" | "amber" | "rose" | "sky" | "zinc"
  icon?: React.ReactNode
  loading?: boolean
  index?: number
  href?: string
  /** Small attention-grabbing pill rendered above the icon. Used to flag
   * unfinished work tied to this metric (e.g. "3 pending action"). */
  badge?: { text: string; tone?: "amber" | "rose" | "emerald" | "sky" } | null
}

const ACCENTS: Record<NonNullable<Props["accent"]>, string> = {
  violet: "from-[hsl(250_80%_62%/0.22)] to-transparent",
  emerald: "from-emerald-500/20 to-transparent",
  amber: "from-amber-500/20 to-transparent",
  rose: "from-rose-500/20 to-transparent",
  sky: "from-sky-500/20 to-transparent",
  zinc: "from-zinc-500/10 to-transparent",
}

export function KpiCard({
  label,
  value,
  hint,
  delta,
  accent = "violet",
  icon,
  loading,
  index = 0,
  href,
  badge,
}: Props) {
  const BADGE_TONE = {
    amber: "bg-amber-500/20 text-amber-200 border-amber-500/40",
    rose: "bg-rose-500/20 text-rose-200 border-rose-500/40",
    emerald: "bg-emerald-500/20 text-emerald-200 border-emerald-500/40",
    sky: "bg-sky-500/20 text-sky-200 border-sky-500/40",
  } as const
  const Root = href ? motion(Link) : motion.div
  const rootProps = href ? ({ href } as { href: string }) : {}
  return (
    <Root
      {...rootProps}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, delay: index * 0.03, ease: "easeOut" }}
      className={cn(
        "relative overflow-hidden rounded-xl border border-zinc-800/80 bg-[#18181b] hover:border-zinc-700/90 transition-colors group",
        href && "cursor-pointer hover:bg-[#1d1d22]",
      )}
    >
      <div
        className={cn(
          "pointer-events-none absolute inset-x-0 top-0 h-20 bg-gradient-to-b opacity-70",
          ACCENTS[accent],
        )}
      />
      <div className="relative p-4">
        <div className="flex items-center justify-between">
          <div className="text-xs font-medium uppercase tracking-[0.1em] text-zinc-500">
            {label}
          </div>
          <div className="flex items-center gap-1.5">
            {badge && (
              <span
                className={cn(
                  "rounded border px-1.5 py-0.5 text-[10px] font-medium tnum animate-pulse",
                  BADGE_TONE[badge.tone ?? "amber"],
                )}
              >
                {badge.text}
              </span>
            )}
            {icon && <div className="text-zinc-500 group-hover:text-zinc-400 transition-colors">{icon}</div>}
          </div>
        </div>

        <div className="mt-3 flex items-baseline gap-2">
          {loading ? (
            <div className="skeleton h-8 w-24" />
          ) : (
            <div className="text-2xl font-semibold tracking-tight tnum text-zinc-50">
              {typeof value === "number" ? fmt(value) : value ?? "—"}
            </div>
          )}
          {delta && !loading && (
            <span
              className={cn(
                "text-xs tnum",
                delta.dir === "down"
                  ? "text-rose-400"
                  : delta.dir === "neutral"
                    ? "text-zinc-500"
                    : "text-emerald-400",
              )}
            >
              {delta.dir === "down" ? "−" : delta.dir === "neutral" ? "" : "+"}
              {fmt(Math.abs(delta.value))}
              {delta.suffix || ""}
            </span>
          )}
        </div>

        {hint && <div className="mt-1.5 text-xs text-zinc-500">{hint}</div>}
      </div>
    </Root>
  )
}
