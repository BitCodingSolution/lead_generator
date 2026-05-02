"use client"

import * as React from "react"
import useSWR from "swr"
import { toast } from "sonner"
import { api, swrFetcher } from "@/lib/api"
import type {
  CampaignBatch,
  CampaignBatchesResponse,
} from "@/lib/sources"
import type { Job } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Archive, CheckCircle2, RotateCcw, X as XIcon } from "lucide-react"
import { fmt, relTime, cn } from "@/lib/utils"

// ---- Types ----

type Scope = { kind: "source"; sourceId: string } | { kind: "all" }

type CrossResponse = { batches: CampaignBatch[]; count: number }

const STATE_META: Record<
  CampaignBatch["state"],
  { label: string; tone: string }
> = {
  fresh: { label: "Ready for drafts", tone: "violet" },
  partial: { label: "Partial", tone: "amber" },
  drafted: { label: "Drafts ready", tone: "violet" },
  in_outlook: { label: "In Outlook", tone: "blue" },
  sent: { label: "Sent", tone: "emerald" },
}

// ---- Manual archive (user-controlled, persisted in localStorage) ----
const DISMISSED_KEY = "batches-panel.dismissed"

function useDismissedBatches() {
  // Start empty on both server and first client render so the hydrated
  // tree matches SSR. The real saved set is loaded in a mount effect
  // below, which then triggers a re-render. Reading localStorage in the
  // useState initializer would cause a hydration mismatch (server sees
  // empty, client sees the stored items).
  const [dismissed, setDismissed] = React.useState<Set<string>>(() => new Set())
  React.useEffect(() => {
    try {
      const raw = window.localStorage.getItem(DISMISSED_KEY)
      if (raw) setDismissed(new Set<string>(JSON.parse(raw)))
    } catch {
      /* corrupted JSON or storage blocked — keep the empty default */
    }
  }, [])
  const persist = React.useCallback((next: Set<string>) => {
    setDismissed(next)
    try {
      window.localStorage.setItem(
        DISMISSED_KEY,
        JSON.stringify(Array.from(next)),
      )
    } catch {
      /* ignore quota errors */
    }
  }, [])
  const dismiss = React.useCallback(
    (name: string) => {
      const next = new Set(dismissed)
      next.add(name)
      persist(next)
    },
    [dismissed, persist],
  )
  const restore = React.useCallback(
    (name: string) => {
      const next = new Set(dismissed)
      next.delete(name)
      persist(next)
    },
    [dismissed, persist],
  )
  return { dismissed, dismiss, restore }
}

// ---- Summary ----

export function BatchesSummary({
  scope,
  sourceFilter,
}: {
  scope: Scope
  sourceFilter?: string
}) {
  const url =
    scope.kind === "source"
      ? `/api/sources/${scope.sourceId}/batches`
      : `/api/campaigns/batches`
  const { data } = useSWR<CampaignBatchesResponse | CrossResponse>(
    url,
    swrFetcher,
    { refreshInterval: 5000 },
  )
  const batches = (data?.batches || []).filter(
    (b) => !sourceFilter || b.source === sourceFilter,
  )
  const totalRows = batches.reduce((s, b) => s + (b.total || 0), 0)
  const drafted = batches.reduce((s, b) => s + (b.drafted || 0), 0)
  const inOutlook = batches.reduce((s, b) => s + (b.in_outlook || 0), 0)
  const sent = batches.reduce((s, b) => s + (b.sent || 0), 0)

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      <StatBox label="Batches" value={fmt(batches.length)} />
      <StatBox label="Rows in flight" value={fmt(totalRows)} />
      <StatBox label="Drafts ready" value={fmt(drafted)} />
      <StatBox label="In Outlook" value={fmt(inOutlook)} />
      <StatBox label="Sent" value={fmt(sent)} emphasis={sent > 0} />
    </div>
  )
}

function StatBox({
  label,
  value,
  emphasis,
}: {
  label: string
  value: string
  emphasis?: boolean
}) {
  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] px-4 py-3">
      <div className="text-[10px] uppercase tracking-[0.15em] text-zinc-500">
        {label}
      </div>
      <div
        className={cn(
          "mt-1 text-lg font-semibold tnum tracking-tight",
          emphasis ? "text-emerald-300" : "text-zinc-100",
        )}
      >
        {value}
      </div>
    </div>
  )
}

// ---- Panel ----

export function BatchesPanel({
  scope,
  highlight,
  sourceFilter,
}: {
  scope: Scope
  highlight?: string | null
  sourceFilter?: string
}) {
  const url =
    scope.kind === "source"
      ? `/api/sources/${scope.sourceId}/batches`
      : `/api/campaigns/batches`
  const { data, isLoading, mutate } = useSWR<
    CampaignBatchesResponse | CrossResponse
  >(url, swrFetcher, { refreshInterval: 5000 })

  // "Archive" = fully sent OR stale (>7 days) OR user-dismissed.
  // Hidden by default; summary cards stay lifetime.
  const [showArchive, setShowArchive] = React.useState(false)
  const STALE_DAYS = 7
  const { dismissed, dismiss, restore } = useDismissedBatches()

  const isArchived = React.useCallback(
    (b: CampaignBatch) => {
      if (dismissed.has(b.name)) return true
      if (b.state === "sent") return true
      const created = b.created_at ? Date.parse(b.created_at) : 0
      if (!created) return false
      const ageDays = (Date.now() - created) / 86400000
      return ageDays > STALE_DAYS
    },
    [dismissed],
  )

  const allMatchingSource = (data?.batches || []).filter(
    (b) => !sourceFilter || b.source === sourceFilter,
  )
  const archivedCount = allMatchingSource.filter(isArchived).length
  const batches = showArchive
    ? allMatchingSource
    : allMatchingSource.filter((b) => !isArchived(b))

  if (isLoading && allMatchingSource.length === 0) return null
  if (allMatchingSource.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-zinc-800/80 bg-zinc-900/20 px-4 py-4 text-xs text-zinc-500">
        No campaign batches yet.{" "}
        {scope.kind === "all"
          ? "Open a source and send leads to Campaign to create one."
          : "Select leads below and click Add to Campaign."}
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b]">
      <div className="px-5 py-3 border-b border-zinc-800/70 flex items-center justify-between gap-3 flex-wrap">
        <div className="text-sm text-zinc-200 font-medium tracking-tight">
          {scope.kind === "all" ? "Active Batches" : "Campaign Batches"}{" "}
          <span className="text-zinc-500 font-normal">· {batches.length}</span>
        </div>
        <div className="flex items-center gap-3">
          {archivedCount > 0 && (
            <button
              onClick={() => setShowArchive((v) => !v)}
              className="text-[11px] text-zinc-400 hover:text-zinc-200 underline-offset-2 hover:underline"
              title={`Archive = sent or older than ${STALE_DAYS} days`}
            >
              {showArchive
                ? `Hide archive (${archivedCount})`
                : `Show archive (${archivedCount})`}
            </button>
          )}
          <div className="text-[11px] text-zinc-500">
            {scope.kind === "all"
              ? "Cross-source · auto-updating"
              : "Exported from this source · auto-updating"}
          </div>
        </div>
      </div>
      {!showArchive && batches.length === 0 && archivedCount > 0 && (
        <div className="px-5 py-4 text-xs text-zinc-500">
          No active batches. Click{" "}
          <button
            onClick={() => setShowArchive(true)}
            className="text-zinc-300 underline-offset-2 hover:underline"
          >
            Show archive
          </button>{" "}
          to review history.
        </div>
      )}
      <div className="divide-y divide-zinc-800/60">
        {batches.map((b) => {
          const rowSourceId =
            scope.kind === "source" ? scope.sourceId : b.source || ""
          if (!rowSourceId) return null
          return (
            <BatchRow
              key={b.name}
              sourceId={rowSourceId}
              batch={b}
              showSource={scope.kind === "all"}
              highlight={highlight === b.name}
              isDismissed={dismissed.has(b.name)}
              onDismiss={() => dismiss(b.name)}
              onRestore={() => restore(b.name)}
              onMutate={() => mutate()}
            />
          )
        })}
      </div>
    </div>
  )
}

function BatchRow({
  sourceId,
  batch,
  showSource,
  highlight,
  isDismissed,
  onDismiss,
  onRestore,
  onMutate,
}: {
  sourceId: string
  batch: CampaignBatch
  showSource?: boolean
  highlight?: boolean
  isDismissed?: boolean
  onDismiss?: () => void
  onRestore?: () => void
  onMutate: () => void
}) {
  const [busy, setBusy] = React.useState<
    null | "drafts" | "outlook" | "send" | "delete"
  >(null)
  const stateMeta = STATE_META[batch.state] || STATE_META.fresh

  // --- Smooth live progress for the active step ---
  // When a step (drafts/outlook/send) is running, the underlying Excel-based
  // counters only refresh on SWR tick — causing a 0→100 jump at the end.
  // Interpolate client-side using elapsed time vs a typical per-row duration
  // so the bar inches forward continuously.
  const [tick, setTickNow] = React.useState(0)
  const busyStartRef = React.useRef<{ kind: string; at: number } | null>(null)
  React.useEffect(() => {
    if (busy === "drafts" || busy === "outlook" || busy === "send") {
      if (!busyStartRef.current || busyStartRef.current.kind !== busy) {
        busyStartRef.current = { kind: busy, at: Date.now() }
      }
      const iv = setInterval(() => setTickNow((n) => n + 1), 300)
      return () => clearInterval(iv)
    }
    busyStartRef.current = null
  }, [busy])

  // Per-row typical durations (seconds per row).
  const typicalSecsPerRow: Record<string, number> = {
    drafts: 10,
    outlook: 3,
    send: 8,
  }

  function smoothedCount(
    step: "drafts" | "outlook" | "send",
    actualDone: number,
  ): number {
    if (busy !== step || !busyStartRef.current) return actualDone
    const perRow = typicalSecsPerRow[step] ?? 10
    const elapsed = (Date.now() - busyStartRef.current.at) / 1000
    // We don't know exact remaining, so advance toward total with 95% cap
    // of (total - actualDone) so the bar never overshoots before SWR confirms.
    const remaining = Math.max(0, batch.total - actualDone)
    const progressed = Math.min(remaining * 0.95, elapsed / perRow)
    // silence unused-var: tick drives re-render
    void tick
    return Math.min(batch.total, actualDone + progressed)
  }

  function waitForJob(jobId: string, label: string): Promise<void> {
    const started = Date.now()
    const toastId = toast.loading(`${label} starting…`)
    return new Promise<void>((resolve, reject) => {
      let stopped = false
      async function tick() {
        if (stopped) return
        try {
          const j = await api.get<Job>(`/api/jobs/${jobId}`)
          const last = j.logs?.[j.logs.length - 1] || ""
          if (j.status === "running" || j.status === "queued") {
            toast.loading(`${label} — ${last.slice(0, 80) || j.status}`, {
              id: toastId,
            })
            setTimeout(tick, 1500)
          } else if (j.status === "done") {
            stopped = true
            const secs = Math.round((Date.now() - started) / 1000)
            toast.success(`${label} done in ${secs}s`, {
              id: toastId,
              description: last.slice(0, 120),
            })
            resolve()
          } else {
            stopped = true
            toast.error(`${label} failed`, {
              id: toastId,
              description: last.slice(0, 200),
            })
            reject(new Error(last || "failed"))
          }
        } catch (e) {
          stopped = true
          toast.error(`${label} polling error`, {
            id: toastId,
            description: String((e as Error).message),
          })
          reject(e)
        }
      }
      setTimeout(tick, 400)
    })
  }

  async function runStep(
    step: "drafts" | "outlook" | "send",
    path: string,
    label: string,
    body?: any,
  ) {
    if (busy) return
    setBusy(step)
    try {
      const r = await api.post<{ job_id: string }>(path, body)
      await waitForJob(r.job_id, label)
      onMutate()
    } catch (e) {
      toast.error(`${label} failed`, {
        description: String((e as Error).message),
      })
    } finally {
      setBusy(null)
    }
  }

  async function runPipeline() {
    if (busy) return
    try {
      if (batch.drafted < batch.total) {
        setBusy("drafts")
        const r = await api.post<{ job_id: string }>(
          `/api/sources/${sourceId}/batches/${encodeURIComponent(batch.name)}/generate-drafts`,
          {},
        )
        await waitForJob(r.job_id, "Generate drafts")
        onMutate()
      }
      setBusy("outlook")
      const r2 = await api.post<{ job_id: string }>(
        `/api/sources/${sourceId}/batches/${encodeURIComponent(batch.name)}/write-outlook`,
        {},
      )
      await waitForJob(r2.job_id, "Write to Outlook")
      onMutate()
      toast.success("Pipeline complete — drafts ready in Outlook")
    } catch {
      // per-step toasts already shown
    } finally {
      setBusy(null)
    }
  }

  async function onDelete() {
    if (busy) return
    if (!window.confirm(`Delete batch "${batch.name}"?`)) return
    setBusy("delete")
    try {
      // api.delete throws on non-2xx with a detail-rich Error message
      // (status + body). No need for a manual fetch + status unwrap.
      await api.delete(
        `/api/sources/${sourceId}/batches/${encodeURIComponent(batch.name)}`,
      )
      toast.success("Batch deleted")
      onMutate()
    } catch (e) {
      toast.error("Delete failed", {
        description: String((e as Error).message),
      })
    } finally {
      setBusy(null)
    }
  }

  const pct = (n: number) =>
    batch.total ? Math.min(100, (n / batch.total) * 100) : 0

  // Smoothed counts — only differ from raw counts while a step is busy.
  const displayDrafted = smoothedCount("drafts", batch.drafted)
  const displayOutlook = smoothedCount("outlook", batch.in_outlook)
  const displaySent = smoothedCount("send", batch.sent)
  const steps = [
    { key: "excel", label: "Excel", done: batch.total > 0, count: batch.total },
    {
      key: "drafts",
      label: "Drafts",
      done: batch.drafted >= batch.total,
      count: batch.drafted,
    },
    {
      key: "outlook",
      label: "Outlook",
      done: batch.in_outlook >= batch.total,
      count: batch.in_outlook,
    },
    {
      key: "sent",
      label: "Sent",
      done: batch.sent >= batch.total,
      count: batch.sent,
    },
  ]

  return (
    <div
      className={cn(
        "px-5 py-4 transition-colors",
        highlight && "bg-[hsl(250_80%_62%/0.06)]",
      )}
    >
      <div className="flex items-start gap-3 flex-wrap">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            {showSource && (
              <span className="inline-flex items-center rounded-md border border-zinc-700/60 bg-zinc-900/80 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-zinc-300">
                {sourceId}
              </span>
            )}
            <span className="font-mono text-xs text-zinc-200 truncate">
              {batch.name}
            </span>
            <StateChip tone={stateMeta.tone} label={stateMeta.label} />
            <span className="text-[11px] text-zinc-500 tnum">
              {batch.total} rows · {relTime(batch.created_at)}
            </span>
          </div>
          <div className="mt-2 flex items-center gap-0.5">
            {steps.map((s, i) => (
              <React.Fragment key={s.key}>
                <div
                  className={cn(
                    "flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] tnum",
                    s.done
                      ? "bg-emerald-950/40 text-emerald-300"
                      : s.count > 0
                        ? "bg-amber-950/40 text-amber-300"
                        : "bg-zinc-900/60 text-zinc-500",
                  )}
                >
                  {s.done ? (
                    <CheckCircle2 className="size-3" />
                  ) : (
                    <span className="size-3 rounded-full border border-current opacity-60" />
                  )}
                  <span>
                    {s.label}
                    {s.count > 0 && (
                      <span className="opacity-75">
                        {" "}
                        {s.count}/{batch.total}
                      </span>
                    )}
                  </span>
                </div>
                {i < steps.length - 1 && (
                  <div className="h-px w-3 bg-zinc-800/80" />
                )}
              </React.Fragment>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          {/* Marcel has its own DB-picked pipeline — per-file actions don't
              apply; we only allow delete for history cleanup. */}
          {sourceId !== "marcel" && batch.in_outlook < batch.total && batch.total > 0 && (
            <Button
              size="sm"
              disabled={!!busy}
              onClick={runPipeline}
              title="Runs Generate Drafts → Write to Outlook in one go"
              className="bg-gradient-to-r from-[hsl(250_80%_62%)] to-[hsl(270_90%_65%)] hover:from-[hsl(250_80%_66%)] hover:to-[hsl(270_90%_68%)]"
            >
              {busy === "drafts"
                ? "Drafts…"
                : busy === "outlook"
                  ? "Outlook…"
                  : batch.drafted >= batch.total
                    ? "→ Outlook"
                    : "Run Pipeline"}
            </Button>
          )}
          {sourceId !== "marcel" && batch.drafted < batch.total && (
            <Button
              size="sm"
              variant="ghost"
              disabled={!!busy}
              onClick={() =>
                runStep(
                  "drafts",
                  `/api/sources/${sourceId}/batches/${encodeURIComponent(
                    batch.name,
                  )}/generate-drafts`,
                  "Generate drafts",
                )
              }
              className="text-zinc-500 hover:text-zinc-200 text-[11px]"
              title="Run only the drafts step"
            >
              drafts only
            </Button>
          )}
          {sourceId !== "marcel" &&
            batch.in_outlook >= batch.total &&
            batch.sent < batch.total &&
            batch.total > 0 && (
              <Button
                size="sm"
                disabled={!!busy}
                onClick={() => {
                  const count = batch.in_outlook - batch.sent
                  if (count <= 0) return
                  if (
                    !window.confirm(
                      `Send ${count} draft${count === 1 ? "" : "s"} from Outlook now?`,
                    )
                  )
                    return
                  runStep(
                    "send",
                    `/api/sources/${sourceId}/batches/${encodeURIComponent(
                      batch.name,
                    )}/send`,
                    `Send ${count} drafts`,
                    { count },
                  )
                }}
              >
                {busy === "send"
                  ? "Sending…"
                  : `Send ${batch.in_outlook - batch.sent}`}
              </Button>
            )}
          {/* Manual archive (user-dismiss). Doesn't touch the file — just
              hides the row from the Active view via localStorage. Available
              for every source including Marcel. */}
          {onDismiss && onRestore && (
            isDismissed ? (
              <Button
                size="sm"
                variant="ghost"
                disabled={!!busy}
                onClick={onRestore}
                title="Restore to active list"
                className="text-zinc-500 hover:text-zinc-200"
              >
                <RotateCcw className="size-3.5" />
              </Button>
            ) : (
              <Button
                size="sm"
                variant="ghost"
                disabled={!!busy}
                onClick={onDismiss}
                title="Archive (hide from active list)"
                className="text-zinc-500 hover:text-zinc-200"
              >
                <Archive className="size-3.5" />
              </Button>
            )
          )}
          {sourceId !== "marcel" && (
            <Button
              size="sm"
              variant="ghost"
              disabled={!!busy}
              onClick={onDelete}
              title="Delete batch"
              className="text-zinc-500 hover:text-red-400"
            >
              <XIcon className="size-3.5" />
            </Button>
          )}
        </div>
      </div>
      {batch.total > 0 && (
        <div className="mt-2 grid grid-cols-3 gap-2 text-[10px] text-zinc-500">
          <MiniBar label="Drafts" percent={pct(displayDrafted)} />
          <MiniBar label="Outlook" percent={pct(displayOutlook)} />
          <MiniBar label="Sent" percent={pct(displaySent)} />
        </div>
      )}
    </div>
  )
}

function MiniBar({ label, percent }: { label: string; percent: number }) {
  // Raw `percent` may be fractional (e.g. 66.666…) from smoothed counts —
  // round for the text label but keep precision for the bar width below.
  const shown = Math.round(percent)
  return (
    <div>
      <div className="flex items-center justify-between">
        <span>{label}</span>
        <span className="tnum">{shown}%</span>
      </div>
      <div className="h-1 rounded bg-zinc-900 overflow-hidden">
        <div
          className={cn(
            "h-full transition-[width] duration-300 ease-linear",
            percent >= 100
              ? "bg-emerald-500"
              : percent > 0
                ? "bg-[hsl(250_80%_62%)]"
                : "bg-zinc-800",
          )}
          style={{ width: `${percent.toFixed(2)}%` }}
        />
      </div>
    </div>
  )
}

function StateChip({ tone, label }: { tone: string; label: string }) {
  const tones: Record<string, string> = {
    emerald: "bg-emerald-900/40 border-emerald-700/40 text-emerald-300",
    violet:
      "bg-[hsl(250_80%_62%/0.15)] border-[hsl(250_80%_62%/0.3)] text-[hsl(250_80%_82%)]",
    blue: "bg-blue-900/30 border-blue-700/40 text-blue-300",
    amber: "bg-amber-950/40 border-amber-800/40 text-amber-300",
    zinc: "bg-zinc-800/60 border-zinc-700 text-zinc-300",
  }
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border text-[10px] uppercase tracking-wider px-2 py-0.5",
        tones[tone] || tones.zinc,
      )}
    >
      {label}
    </span>
  )
}
