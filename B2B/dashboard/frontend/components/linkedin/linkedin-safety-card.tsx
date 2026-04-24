"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import {
  Shield, Zap, AlertTriangle, Power, Loader2, Check, Clock,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { api, swrFetcher } from "@/lib/api"
import type { LinkedInSafety } from "@/lib/types"

const ENDPOINT = "/api/linkedin/safety"

export function LinkedInSafetyCard() {
  const { data } = useSWR<LinkedInSafety>(ENDPOINT, swrFetcher, {
    refreshInterval: 10_000,
  })

  const mode = data?.safety_mode ?? "max"
  const paused = data?.warning_paused_until
    ? new Date(data.warning_paused_until) > new Date()
    : false

  const [busy, setBusy] = React.useState(false)
  const [hourDraft, setHourDraft] = React.useState<number | null>(null)
  const hour = hourDraft ?? data?.autopilot_hour ?? 10

  async function patch(body: Record<string, unknown>) {
    setBusy(true)
    try {
      await api.post(ENDPOINT, body)
      mutate(ENDPOINT)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium uppercase tracking-[0.1em] text-zinc-500">
          Safety
        </div>
        <div className="flex items-center gap-1 rounded-md bg-zinc-800/60 p-0.5">
          <ModeBtn
            active={mode === "max"}
            onClick={() => patch({ safety_mode: "max" })}
            icon={<Shield className="size-3" />}
            label="Max"
            tone="emerald"
          />
          <ModeBtn
            active={mode === "normal"}
            onClick={() => patch({ safety_mode: "normal" })}
            icon={<Zap className="size-3" />}
            label="Normal"
            tone="amber"
          />
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
        <Row label="Daily sent" value={`${data?.daily_sent_count ?? 0} / 20`} />
        <Row label="Last send" value={fmtRelative(data?.last_send_at)} />
        <Row label="Failures" value={`${data?.consecutive_failures ?? 0}`} />
        <Row label="Mode" value={mode === "max" ? "Maximum safety" : "Normal"} />
      </div>

      <div className="mt-4 pt-3 border-t border-zinc-800/70">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Clock
              className={cn(
                "size-4",
                data?.business_hours_only ? "text-sky-300" : "text-zinc-500",
              )}
            />
            <div>
              <div className="text-sm font-medium text-zinc-200">
                Business hours only
              </div>
              <div className="text-[11px] text-zinc-500">
                {data?.business_hours_only
                  ? "Mon-Fri 09:00-18:00 local"
                  : "Quiet hours 23:00-07:00 (default)"}
              </div>
            </div>
          </div>
          <button
            onClick={() =>
              patch({ business_hours_only: !data?.business_hours_only })
            }
            disabled={busy}
            className={cn(
              "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium",
              data?.business_hours_only
                ? "bg-sky-500/15 text-sky-300"
                : "bg-zinc-700/40 text-zinc-400",
            )}
          >
            {data?.business_hours_only ? "ON" : "OFF"}
          </button>
        </div>
      </div>

      <div className="mt-4 pt-3 border-t border-zinc-800/70">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Power
              className={cn(
                "size-4",
                data?.autopilot_enabled ? "text-emerald-400" : "text-zinc-500",
              )}
            />
            <div className="text-sm font-medium text-zinc-200">Autopilot</div>
          </div>
          <button
            onClick={() =>
              patch({ autopilot_enabled: !data?.autopilot_enabled })
            }
            disabled={busy}
            className={cn(
              "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium",
              data?.autopilot_enabled
                ? "bg-emerald-500/15 text-emerald-300"
                : "bg-zinc-700/40 text-zinc-400",
            )}
          >
            {data?.autopilot_enabled ? "ON" : "OFF"}
          </button>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-[11px] text-zinc-500">Fire daily at</label>
          <input
            type="number"
            min={0}
            max={23}
            value={hour}
            onChange={(e) => setHourDraft(parseInt(e.target.value, 10) || 0)}
            className="w-14 rounded border border-zinc-800 bg-zinc-900/60 px-1.5 py-0.5 text-sm text-zinc-100 tnum focus:outline-none focus:border-[hsl(250_80%_62%)]"
          />
          <span className="text-[11px] text-zinc-500">:00 local</span>
          {hourDraft != null && hourDraft !== data?.autopilot_hour && (
            <button
              onClick={async () => {
                await patch({ autopilot_hour: hourDraft })
                setHourDraft(null)
              }}
              disabled={busy}
              className="ml-auto inline-flex items-center gap-1 rounded bg-[hsl(250_80%_62%)] px-2 py-0.5 text-[11px] text-white"
            >
              {busy ? <Loader2 className="size-3 animate-spin" /> : <Check className="size-3" />}
              Save
            </button>
          )}
        </div>
      </div>

      {paused && (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-rose-500/40 bg-rose-500/10 p-2.5 text-xs text-rose-200">
          <AlertTriangle className="size-4 shrink-0 mt-0.5" />
          <div className="flex-1">
            Account-warning pause active until{" "}
            <span className="font-mono">{data?.warning_paused_until}</span>.
            Sending is blocked.
            <button
              onClick={() => patch({ clear_warning_pause: true })}
              className="mt-1.5 inline-flex items-center gap-1 rounded border border-rose-500/40 bg-rose-500/20 px-2 py-0.5 text-[11px] hover:bg-rose-500/30"
            >
              Clear pause
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function ModeBtn({
  active, onClick, icon, label, tone,
}: {
  active: boolean
  onClick: () => void
  icon: React.ReactNode
  label: string
  tone: "emerald" | "amber"
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-medium transition-colors",
        active
          ? tone === "emerald"
            ? "bg-emerald-500/20 text-emerald-200"
            : "bg-amber-500/20 text-amber-200"
          : "text-zinc-500 hover:text-zinc-300",
      )}
    >
      {icon}
      {label}
    </button>
  )
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

function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "—"
  const diff = Date.now() - new Date(iso).getTime()
  if (diff < 0) return "—"
  const m = Math.floor(diff / 60_000)
  if (m < 1) return "just now"
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}
