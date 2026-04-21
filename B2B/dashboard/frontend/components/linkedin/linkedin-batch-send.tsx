"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import { Rocket, StopCircle, Loader2, Mail } from "lucide-react"
import { cn } from "@/lib/utils"
import { api, swrFetcher } from "@/lib/api"

type BatchState = {
  running: boolean
  total: number
  sent: number
  failed: number
  skipped: number
  started_at: string | null
  finished_at: string | null
  current_lead_id: number | null
  current_email: string | null
  last_error: string | null
  source: "manual" | "autopilot" | null
}

const STATE_URL = "/api/linkedin/send/batch/status"

export function LinkedInBatchSend() {
  const { data } = useSWR<BatchState>(STATE_URL, swrFetcher, {
    refreshInterval: 2_000,
  })
  const running = !!data?.running

  const [count, setCount] = React.useState(5)
  const [busy, setBusy] = React.useState(false)
  const [msg, setMsg] = React.useState<string | null>(null)

  async function onStart() {
    setBusy(true)
    setMsg(null)
    try {
      const res = await api.post<{ started: boolean; total: number }>(
        "/api/linkedin/send/batch",
        { count, source: "manual" },
      )
      setMsg(`Started · ${res.total} leads queued (60-90s jitter between sends)`)
      mutate(STATE_URL)
    } catch (err) {
      setMsg((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function onStop() {
    setBusy(true)
    try {
      await api.post("/api/linkedin/send/batch/stop")
      mutate(STATE_URL)
    } finally {
      setBusy(false)
    }
  }

  const doneCount =
    (data?.sent ?? 0) + (data?.failed ?? 0) + (data?.skipped ?? 0)
  const pct =
    data && data.total > 0 ? Math.round((doneCount / data.total) * 100) : 0

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Rocket className="size-4 text-zinc-400" />
          <div className="text-sm font-medium text-zinc-200">Batch send</div>
        </div>
        {running ? (
          <span className="inline-flex items-center gap-1.5 rounded-md bg-violet-500/15 px-2 py-0.5 text-[11px] font-medium text-violet-300">
            <Loader2 className="size-3 animate-spin" />
            Running
          </span>
        ) : (
          <span className="inline-flex items-center rounded-md bg-zinc-700/30 px-2 py-0.5 text-[11px] font-medium text-zinc-400">
            Idle
          </span>
        )}
      </div>

      {!running ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-zinc-500">Send up to</label>
            <input
              type="number"
              min={1}
              max={20}
              value={count}
              onChange={(e) =>
                setCount(Math.max(1, Math.min(20, parseInt(e.target.value, 10) || 1)))
              }
              className="w-16 rounded border border-zinc-800 bg-zinc-900/60 px-1.5 py-0.5 text-sm text-zinc-100 tnum focus:outline-none focus:border-[hsl(250_80%_62%)]"
            />
            <span className="text-[11px] text-zinc-500">drafted leads</span>
            <button
              onClick={onStart}
              disabled={busy}
              className="ml-auto inline-flex items-center gap-1.5 rounded-md bg-[hsl(250_80%_62%)] px-3 py-1 text-xs text-white hover:brightness-110 disabled:opacity-50"
            >
              {busy ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <Rocket className="size-3" />
              )}
              Start batch
            </button>
          </div>
          {data?.finished_at && (
            <div className="text-[11px] text-zinc-500">
              Last run · sent {data.sent} · failed {data.failed} · skipped{" "}
              {data.skipped} · finished {fmtRelative(data.finished_at)}
              {data.source && <> · {data.source}</>}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          <div className="h-1.5 bg-zinc-800 rounded overflow-hidden">
            <div
              className="h-full bg-[hsl(250_80%_62%)] transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-xs text-zinc-400 tnum">
            <span>
              {doneCount} / {data?.total ?? 0} ({pct}%)
            </span>
            <span className="flex items-center gap-3">
              <span className="text-emerald-400">✓ {data?.sent ?? 0}</span>
              <span className="text-rose-400">× {data?.failed ?? 0}</span>
              <span className="text-zinc-500">↷ {data?.skipped ?? 0}</span>
            </span>
          </div>
          <div className="flex items-center justify-between text-[11px] text-zinc-500 tnum">
            <span>Elapsed {fmtDuration(elapsedSec(data?.started_at))}</span>
            <span>
              ETA {fmtDuration(etaSec(data, doneCount))}
              {data?.total ? ` (${((data.total - doneCount) * 75)}s worst case)` : ""}
            </span>
          </div>
          {data?.current_email && (
            <div className="flex items-center gap-1.5 text-[11px] text-zinc-500 font-mono truncate">
              <Mail className="size-3" />
              Sending to {data.current_email}…
            </div>
          )}
          <button
            onClick={onStop}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-md border border-rose-500/40 bg-rose-500/10 px-2.5 py-1 text-xs text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
          >
            <StopCircle className="size-3" />
            Stop
          </button>
        </div>
      )}

      {msg && (
        <div
          className={cn(
            "mt-3 rounded-md px-2.5 py-1.5 text-[11px]",
            msg.toLowerCase().includes("started")
              ? "bg-emerald-500/10 text-emerald-200"
              : "bg-rose-500/10 text-rose-200",
          )}
        >
          {msg}
        </div>
      )}

      {data?.last_error && !running && (
        <div className="mt-2 text-[11px] text-rose-300 truncate">
          Last error: {data.last_error}
        </div>
      )}
    </div>
  )
}

function elapsedSec(iso: string | null | undefined): number {
  if (!iso) return 0
  return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000))
}

function etaSec(data: BatchState | undefined, done: number): number {
  if (!data || !data.started_at || done <= 0 || data.total === 0) return 0
  // Mean jitter between sends is ~75s; we amortize elapsed/done over
  // remaining to smooth out as we go.
  const elapsed = elapsedSec(data.started_at)
  const avgPerLead = elapsed / done
  const remaining = data.total - done
  return Math.max(0, Math.floor(avgPerLead * remaining))
}

function fmtDuration(sec: number): string {
  if (!sec) return "—"
  const m = Math.floor(sec / 60)
  const s = sec % 60
  if (m === 0) return `${s}s`
  if (m < 60) return `${m}m ${s.toString().padStart(2, "0")}s`
  const h = Math.floor(m / 60)
  return `${h}h ${(m % 60).toString().padStart(2, "0")}m`
}

function fmtRelative(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  if (diff < 0) return "—"
  const m = Math.floor(diff / 60_000)
  if (m < 1) return "just now"
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}
