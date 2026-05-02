"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import {
  Shield, Zap, AlertTriangle, Power, Loader2, Check, Clock,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { api, swrFetcher } from "@/lib/api"
import { fmtRelative, fmtTimeShort } from "@/lib/datetime"
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
  const [minuteDraft, setMinuteDraft] = React.useState<number | null>(null)
  const [countDraft, setCountDraft] = React.useState<number | null>(null)
  const hour = hourDraft ?? data?.autopilot_hour ?? 10
  const minute = minuteDraft ?? data?.autopilot_minute ?? 0
  // null/undefined/0 from the server → "full cap" mode.
  const serverCount = data?.autopilot_count ?? null
  const count = countDraft ?? serverCount
  const countMode: "full" | "limited" = count && count > 0 ? "limited" : "full"

  const scheduleDirty =
    (hourDraft != null && hourDraft !== data?.autopilot_hour) ||
    (minuteDraft != null && minuteDraft !== data?.autopilot_minute) ||
    (countDraft != null && countDraft !== serverCount)

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

        {/* Auto follow-ups: send touch 2 (3d after first send) and
            touch 3 (7d after touch 2) automatically. Stops on reply,
            bounce, or blocklist hit — same rails as the manual sender. */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Clock
              className={cn(
                "size-4",
                data?.followups_autopilot ? "text-emerald-300" : "text-zinc-500",
              )}
            />
            <div>
              <div className="text-sm font-medium text-zinc-200">
                Auto follow-ups
              </div>
              <div className="text-[11px] text-zinc-500">
                {data?.followups_autopilot
                  ? `Daily at ${data.followups_hour}:00 - touch 2 (+3d), touch 3 (+7d)`
                  : "Off - manually trigger from Followups"}
              </div>
            </div>
          </div>
          <button
            onClick={() =>
              patch({ followups_autopilot: !data?.followups_autopilot })
            }
            disabled={busy}
            className={cn(
              "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium",
              data?.followups_autopilot
                ? "bg-emerald-500/15 text-emerald-300"
                : "bg-zinc-700/40 text-zinc-400",
            )}
          >
            {data?.followups_autopilot ? "ON" : "OFF"}
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
        <div className="space-y-2">
          {/* Row 1: daily fire time, 12h + AM/PM + editable minute. */}
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-zinc-500 shrink-0">Fire at</label>
            {(() => {
              const h24 = hour
              const isAm = h24 < 12
              const h12 = ((h24 + 11) % 12) + 1
              const to24 = (h12Val: number, am: boolean) => {
                if (am) return h12Val === 12 ? 0 : h12Val
                return h12Val === 12 ? 12 : h12Val + 12
              }
              return (
                <>
                  <input
                    type="number"
                    min={1}
                    max={12}
                    value={h12}
                    onChange={(e) => {
                      const raw = parseInt(e.target.value, 10)
                      const clamped = isNaN(raw) ? 1 : Math.max(1, Math.min(12, raw))
                      setHourDraft(to24(clamped, isAm))
                    }}
                    className="w-12 rounded border border-zinc-800 bg-zinc-900/60 px-1.5 py-0.5 text-sm text-zinc-100 tnum text-center focus:outline-none focus:border-[hsl(250_80%_62%)]"
                  />
                  <span className="text-zinc-500">:</span>
                  <input
                    type="number"
                    min={0}
                    max={59}
                    value={minute.toString().padStart(2, "0")}
                    onChange={(e) => {
                      const raw = parseInt(e.target.value, 10)
                      const clamped = isNaN(raw) ? 0 : Math.max(0, Math.min(59, raw))
                      setMinuteDraft(clamped)
                    }}
                    className="w-12 rounded border border-zinc-800 bg-zinc-900/60 px-1.5 py-0.5 text-sm text-zinc-100 tnum text-center focus:outline-none focus:border-[hsl(250_80%_62%)]"
                  />
                  <div className="flex rounded border border-zinc-800 bg-zinc-900/60 overflow-hidden">
                    <button
                      type="button"
                      onClick={() => setHourDraft(to24(h12, true))}
                      className={cn(
                        "px-2 py-0.5 text-[11px] transition-colors",
                        isAm
                          ? "bg-[hsl(250_80%_62%)]/20 text-violet-200"
                          : "text-zinc-500 hover:text-zinc-300",
                      )}
                    >
                      AM
                    </button>
                    <button
                      type="button"
                      onClick={() => setHourDraft(to24(h12, false))}
                      className={cn(
                        "px-2 py-0.5 text-[11px] transition-colors border-l border-zinc-800",
                        !isAm
                          ? "bg-[hsl(250_80%_62%)]/20 text-violet-200"
                          : "text-zinc-500 hover:text-zinc-300",
                      )}
                    >
                      PM
                    </button>
                  </div>
                  <span className="text-[10px] text-zinc-600">local</span>
                </>
              )
            })()}
          </div>

          {/* Row 2: how many mails to send — full effective cap or a
              user-chosen smaller drip. */}
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-zinc-500 shrink-0">Send</label>
            <div className="flex rounded border border-zinc-800 bg-zinc-900/60 overflow-hidden">
              <button
                type="button"
                onClick={() => setCountDraft(-1)}
                className={cn(
                  "px-2 py-0.5 text-[11px] transition-colors",
                  countMode === "full"
                    ? "bg-[hsl(250_80%_62%)]/20 text-violet-200"
                    : "text-zinc-500 hover:text-zinc-300",
                )}
              >
                Full batch
              </button>
              <button
                type="button"
                onClick={() =>
                  setCountDraft(countDraft && countDraft > 0 ? countDraft : 10)
                }
                className={cn(
                  "px-2 py-0.5 text-[11px] transition-colors border-l border-zinc-800",
                  countMode === "limited"
                    ? "bg-[hsl(250_80%_62%)]/20 text-violet-200"
                    : "text-zinc-500 hover:text-zinc-300",
                )}
              >
                Limited
              </button>
            </div>
            {countMode === "limited" && (
              <>
                <input
                  type="number"
                  min={1}
                  max={500}
                  value={count ?? 10}
                  onChange={(e) => {
                    const raw = parseInt(e.target.value, 10)
                    const clamped = isNaN(raw) ? 1 : Math.max(1, Math.min(500, raw))
                    setCountDraft(clamped)
                  }}
                  className="w-16 rounded border border-zinc-800 bg-zinc-900/60 px-1.5 py-0.5 text-sm text-zinc-100 tnum text-center focus:outline-none focus:border-[hsl(250_80%_62%)]"
                />
                <span className="text-[10px] text-zinc-600">mails / day</span>
              </>
            )}
            {countMode === "full" && (
              <span className="text-[10px] text-zinc-600">
                uses the full daily cap
              </span>
            )}
          </div>

          {scheduleDirty && (
            <div className="flex justify-end">
              <button
                onClick={async () => {
                  const patchBody: Record<string, unknown> = {}
                  if (hourDraft != null && hourDraft !== data?.autopilot_hour) {
                    patchBody.autopilot_hour = hourDraft
                  }
                  if (minuteDraft != null && minuteDraft !== data?.autopilot_minute) {
                    patchBody.autopilot_minute = minuteDraft
                  }
                  if (countDraft != null && countDraft !== serverCount) {
                    // -1 tells the backend to revert to full-cap mode.
                    patchBody.autopilot_count = countDraft
                  }
                  await patch(patchBody)
                  setHourDraft(null)
                  setMinuteDraft(null)
                  setCountDraft(null)
                }}
                disabled={busy}
                className="inline-flex items-center gap-1 rounded bg-[hsl(250_80%_62%)] px-2 py-0.5 text-[11px] text-white disabled:opacity-50"
              >
                {busy ? <Loader2 className="size-3 animate-spin" /> : <Check className="size-3" />}
                Save
              </button>
            </div>
          )}

          {data?.autopilot_today && (
            <div className="mt-2 flex items-start gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/10 p-2 text-[11px] text-emerald-200">
              <Check className="size-3.5 shrink-0 mt-0.5" />
              <div className="flex-1">
                Already ran today at{" "}
                <span className="font-mono">
                  {fmtTimeShort(data.autopilot_today.fired_at)}
                </span>
                {" — "}
                {data.autopilot_today.status === "started"
                  ? `${data.autopilot_today.total_queued} queued`
                  : data.autopilot_today.status.replace(/_/g, " ")}
                .
                <div className="mt-1 text-emerald-300/70">
                  Next fire: tomorrow. Changed the time? Reset to re-fire today.
                </div>
              </div>
              <button
                onClick={async () => {
                  if (!confirm("Reset today's autopilot run? It will re-fire at the next tick.")) return
                  setBusy(true)
                  try {
                    await api.post("/api/linkedin/autopilot/reset-today", {})
                    mutate(ENDPOINT)
                  } finally {
                    setBusy(false)
                  }
                }}
                disabled={busy}
                className="shrink-0 rounded border border-emerald-500/40 bg-emerald-500/20 px-1.5 py-0.5 text-[10px] text-emerald-100 hover:bg-emerald-500/30 disabled:opacity-50"
              >
                Reset
              </button>
            </div>
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

