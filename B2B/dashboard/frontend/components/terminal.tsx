"use client"

import * as React from "react"
import { cn } from "@/lib/utils"

export function Terminal({
  logs,
  status,
  className,
  height = 260,
  label,
}: {
  logs: string | string[] | null | undefined
  status?: string
  className?: string
  height?: number
  label?: string
}) {
  const scrollerRef = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    const el = scrollerRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [logs])

  const lines = Array.isArray(logs)
    ? logs
    : (typeof logs === "string" ? logs : "").split("\n")

  return (
    <div
      className={cn(
        "terminal rounded-md border border-zinc-800/90 overflow-hidden",
        className,
      )}
    >
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-zinc-800/90 bg-[#0b0b0f]">
        <div className="flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-rose-500/60" />
          <span className="size-2 rounded-full bg-amber-500/60" />
          <span className="size-2 rounded-full bg-emerald-500/60" />
          <span className="ml-2 text-[11px] text-zinc-500 tracking-tight">
            {label || "job.log"}
          </span>
        </div>
        {status && (
          <span
            className={cn(
              "text-[10px] uppercase tracking-[0.15em]",
              status === "done"
                ? "text-emerald-400"
                : status === "error"
                  ? "text-rose-400"
                  : status === "running"
                    ? "text-[hsl(250_80%_75%)]"
                    : "text-zinc-500",
            )}
          >
            {status}
          </span>
        )}
      </div>
      <div
        ref={scrollerRef}
        className="overflow-y-auto px-3 py-2"
        style={{ height }}
      >
        {!logs ? (
          <div className="text-zinc-600 text-[12px]">
            Waiting for job output…
          </div>
        ) : (
          <pre className="whitespace-pre-wrap break-words leading-[1.5]">
            {lines.map((line, i) => {
              const low = line.toLowerCase()
              let cls = ""
              if (low.includes("error") || low.includes("traceback") || low.includes("failed")) cls = "err"
              else if (low.includes("warn")) cls = "warn"
              else if (low.includes("ok") || low.includes("success") || low.includes("done")) cls = "ok"
              return (
                <span key={i} className={cls}>
                  {line}
                  {"\n"}
                </span>
              )
            })}
          </pre>
        )}
      </div>
    </div>
  )
}
