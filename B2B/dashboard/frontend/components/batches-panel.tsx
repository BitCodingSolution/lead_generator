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
import { CheckCircle2, X as XIcon } from "lucide-react"
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

  const batches = (data?.batches || []).filter(
    (b) => !sourceFilter || b.source === sourceFilter,
  )
  if (isLoading && batches.length === 0) return null
  if (batches.length === 0) {
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
      <div className="px-5 py-3 border-b border-zinc-800/70 flex items-center justify-between">
        <div className="text-sm text-zinc-200 font-medium tracking-tight">
          {scope.kind === "all" ? "All Campaign Batches" : "Campaign Batches"}{" "}
          <span className="text-zinc-500 font-normal">· {batches.length}</span>
        </div>
        <div className="text-[11px] text-zinc-500">
          {scope.kind === "all"
            ? "Cross-source · auto-updating"
            : "Exported from this source · auto-updating"}
        </div>
      </div>
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
  onMutate,
}: {
  sourceId: string
  batch: CampaignBatch
  showSource?: boolean
  highlight?: boolean
  onMutate: () => void
}) {
  const [busy, setBusy] = React.useState<
    null | "drafts" | "outlook" | "send" | "delete"
  >(null)
  const stateMeta = STATE_META[batch.state] || STATE_META.fresh

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
      const res = await fetch(
        `${api.base}/api/sources/${sourceId}/batches/${encodeURIComponent(
          batch.name,
        )}`,
        { method: "DELETE" },
      )
      if (!res.ok) {
        // 404/500 etc — extract server detail if JSON, else text.
        let detail = ""
        try {
          detail = JSON.stringify(await res.json())
        } catch {
          detail = await res.text()
        }
        throw new Error(`${res.status} ${res.statusText} — ${detail}`)
      }
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
    batch.total ? Math.round((n / batch.total) * 100) : 0
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
          {batch.in_outlook < batch.total && batch.total > 0 && (
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
          {batch.drafted < batch.total && (
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
          {batch.in_outlook >= batch.total &&
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
        </div>
      </div>
      {batch.total > 0 && (
        <div className="mt-2 grid grid-cols-3 gap-2 text-[10px] text-zinc-500">
          <MiniBar label="Drafts" percent={pct(batch.drafted)} />
          <MiniBar label="Outlook" percent={pct(batch.in_outlook)} />
          <MiniBar label="Sent" percent={pct(batch.sent)} />
        </div>
      )}
    </div>
  )
}

function MiniBar({ label, percent }: { label: string; percent: number }) {
  return (
    <div>
      <div className="flex items-center justify-between">
        <span>{label}</span>
        <span className="tnum">{percent}%</span>
      </div>
      <div className="h-1 rounded bg-zinc-900 overflow-hidden">
        <div
          className={cn(
            "h-full",
            percent === 100
              ? "bg-emerald-500"
              : percent > 0
                ? "bg-[hsl(250_80%_62%)]"
                : "bg-zinc-800",
          )}
          style={{ width: `${percent}%` }}
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
