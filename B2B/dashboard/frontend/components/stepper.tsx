"use client"

import * as React from "react"
import { Check, X } from "lucide-react"
import { cn } from "@/lib/utils"

export type StepState = "done" | "active" | "pending" | "error"

export type StepperNode = {
  key: string
  label: string
  state: StepState
  count?: { done: number; total: number }
}

export function Stepper({ nodes }: { nodes: StepperNode[] }) {
  return (
    <div className="w-full flex items-start justify-center">
      <div className="flex items-center gap-0 w-full max-w-xl">
        {nodes.map((node, i) => {
          const isLast = i === nodes.length - 1
          const next = nodes[i + 1]
          const connectorDone =
            node.state === "done" ||
            (node.state === "active" && next && next.state !== "pending")
          return (
            <React.Fragment key={node.key}>
              <div className="flex flex-col items-center shrink-0 min-w-[72px]">
                <div className="relative flex items-center justify-center">
                  {node.state === "active" && (
                    <span className="absolute inset-0 rounded-full ring-2 ring-[hsl(250_80%_62%)] animate-pulse" />
                  )}
                  <div
                    className={cn(
                      "size-8 rounded-full flex items-center justify-center border transition-colors",
                      node.state === "done" &&
                        "bg-emerald-500 border-emerald-400 text-white",
                      node.state === "active" &&
                        "bg-[hsl(250_80%_62%/0.18)] border-[hsl(250_80%_62%)] text-[hsl(250_80%_85%)]",
                      node.state === "pending" &&
                        "bg-transparent border-zinc-700 text-zinc-600",
                      node.state === "error" &&
                        "bg-rose-500 border-rose-400 text-white",
                    )}
                  >
                    {node.state === "done" ? (
                      <Check className="size-4" strokeWidth={3} />
                    ) : node.state === "error" ? (
                      <X className="size-4" strokeWidth={3} />
                    ) : (
                      <span className="size-1.5 rounded-full bg-current opacity-80" />
                    )}
                  </div>
                </div>
                <div
                  className={cn(
                    "mt-2 text-[11px] tracking-tight font-medium",
                    node.state === "done" && "text-emerald-300",
                    node.state === "active" && "text-[hsl(250_80%_85%)]",
                    node.state === "pending" && "text-zinc-500",
                    node.state === "error" && "text-rose-300",
                  )}
                >
                  {node.label}
                </div>
                <div
                  className={cn(
                    "mt-0.5 text-[10px] uppercase tracking-[0.12em]",
                    node.state === "done" && "text-emerald-500/70",
                    node.state === "active" && "text-[hsl(250_80%_62%)]",
                    node.state === "pending" && "text-zinc-600",
                    node.state === "error" && "text-rose-400",
                  )}
                >
                  {node.state === "active"
                    ? "running"
                    : node.state === "done"
                      ? "done"
                      : node.state === "error"
                        ? "failed"
                        : "waiting"}
                </div>
                {node.count && node.count.total > 0 && (
                  <div
                    className={cn(
                      "mt-0.5 text-[10px] font-mono tabular-nums",
                      node.state === "done" && "text-emerald-400",
                      node.state === "active" && "text-[hsl(250_80%_80%)]",
                      node.state === "pending" && "text-zinc-600",
                      node.state === "error" && "text-rose-300",
                    )}
                  >
                    {node.count.done}/{node.count.total}
                  </div>
                )}
              </div>
              {!isLast && (
                <div className="flex-1 h-px min-w-[24px] relative top-[-18px]">
                  <div
                    className={cn(
                      "h-px w-full transition-colors",
                      connectorDone
                        ? "bg-gradient-to-r from-emerald-500/70 to-[hsl(250_80%_62%/0.7)]"
                        : "bg-zinc-800",
                    )}
                  />
                </div>
              )}
            </React.Fragment>
          )
        })}
      </div>
    </div>
  )
}
