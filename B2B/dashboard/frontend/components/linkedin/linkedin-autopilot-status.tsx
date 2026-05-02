"use client"

import useSWR from "swr"
import { Power } from "lucide-react"
import { cn } from "@/lib/utils"
import { swrFetcher } from "@/lib/api"
import { fmtDateTime, fmtRelative } from "@/lib/datetime"

type Status = {
  enabled: boolean
  hour: number
  last_fired_at: string | null
  last_fired_date: string | null
  last_queued: number | null
  last_status: string | null
  next_fire_at: string | null
}

export function LinkedInAutopilotStatus() {
  const { data } = useSWR<Status>("/api/linkedin/autopilot/status", swrFetcher, {
    refreshInterval: 30_000,
  })

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Power
            className={cn(
              "size-4",
              data?.enabled ? "text-emerald-400" : "text-zinc-500",
            )}
          />
          <div className="text-sm font-medium text-zinc-200">Autopilot status</div>
        </div>
        <span
          className={cn(
            "inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium",
            data?.enabled
              ? "bg-emerald-500/15 text-emerald-300"
              : "bg-zinc-700/40 text-zinc-500",
          )}
        >
          {data?.enabled ? `ON @ ${data.hour}:00` : "OFF"}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 text-sm">
        <Row label="Last fired" value={fmtRelative(data?.last_fired_at)} />
        <Row
          label="Last status"
          value={prettyStatus(data?.last_status)}
        />
        <Row
          label="Last queued"
          value={data?.last_queued != null ? `${data.last_queued} leads` : "—"}
        />
        <Row
          label="Next fire"
          value={data?.enabled ? fmtDateTime(data.next_fire_at) : "—"}
        />
      </div>
    </div>
  )
}

function prettyStatus(s: string | null | undefined): string {
  if (!s) return "—"
  return s.replace(/_/g, " ")
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.1em] text-zinc-500">
        {label}
      </div>
      <div className="mt-0.5 text-sm text-zinc-200 tnum">{value}</div>
    </div>
  )
}

