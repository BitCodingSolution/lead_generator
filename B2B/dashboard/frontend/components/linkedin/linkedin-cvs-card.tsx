"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import {
  FileText, Upload, Trash2, Check, Loader2, AlertCircle,
} from "lucide-react"
import { api, swrFetcher } from "@/lib/api"
import { cn } from "@/lib/utils"

type CV = {
  id: number
  cluster: string
  filename: string
  size_bytes: number | null
  uploaded_at: string
}

const ENDPOINT = "/api/linkedin/cvs"
const CLUSTER_HINT: Record<string, string> = {
  python_ai: "Python / AI-ML / LLM / RAG / multi-agent",
  fullstack: "React / Next.js / Node / TypeScript",
  scraping: "Web scraping / Selenium / Playwright",
  n8n: "n8n / Zapier / workflow automation",
  default: "Fallback when cluster can't be determined",
}

export function LinkedInCVsCard() {
  const { data, isLoading } = useSWR<{
    rows: CV[]
    clusters: string[]
    missing: string[]
  }>(ENDPOINT, swrFetcher)

  const byCluster = new Map<string, CV>()
  for (const r of data?.rows ?? []) byCluster.set(r.cluster, r)

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
      <div className="flex items-center gap-2 mb-1">
        <FileText className="size-4 text-zinc-400" />
        <div className="text-sm font-medium text-zinc-200">CV library</div>
        {data && (
          <span className="ml-auto text-[11px] text-zinc-500">
            {data.rows.length} / {data.clusters.length} configured
          </span>
        )}
      </div>
      <div className="text-[11px] text-zinc-500 mb-3">
        One PDF per specialty. Auto-attached at send time based on Claude's cluster classification. Drag-drop a PDF on any slot to upload.
      </div>

      {data && data.missing.length > 0 && (
        <div className="mb-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-2.5 flex items-start gap-2 text-xs text-amber-200">
          <AlertCircle className="size-3.5 shrink-0 mt-0.5" />
          <div>
            Missing: <span className="font-mono">{data.missing.join(", ")}</span>.
            Sends for these clusters will go without an attachment.
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {data?.clusters.map((c) => (
          <ClusterSlot
            key={c}
            cluster={c}
            existing={byCluster.get(c)}
          />
        ))}
        {isLoading && (
          <div className="col-span-2 p-4 text-xs text-zinc-500">Loading…</div>
        )}
      </div>
    </div>
  )
}

function ClusterSlot({
  cluster,
  existing,
}: {
  cluster: string
  existing: CV | undefined
}) {
  const [busy, setBusy] = React.useState(false)
  const [dragging, setDragging] = React.useState(false)
  const [msg, setMsg] = React.useState<string | null>(null)
  const inputRef = React.useRef<HTMLInputElement>(null)

  async function onUpload(file: File) {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setMsg("PDF only")
      return
    }
    setBusy(true)
    setMsg(null)
    try {
      const form = new FormData()
      form.append("cluster", cluster)
      form.append("file", file)
      const res = await fetch(`${api.base}${ENDPOINT}`, {
        method: "POST",
        body: form,
      })
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
      mutate(ENDPOINT)
      setMsg("Uploaded ✓")
      setTimeout(() => setMsg(null), 2000)
    } catch (err) {
      setMsg((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function onDelete() {
    if (!existing) return
    if (!confirm(`Delete ${existing.filename}?`)) return
    await api.post(`${ENDPOINT}/${existing.id}/delete`)
    mutate(ENDPOINT)
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) onUpload(f)
  }

  return (
    <div
      onClick={() => !busy && inputRef.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      className={cn(
        "rounded-lg border p-3 cursor-pointer transition-colors",
        dragging
          ? "border-[hsl(250_80%_62%)] bg-[hsl(250_80%_62%/0.08)]"
          : existing
            ? "border-zinc-800 bg-zinc-900/40 hover:border-zinc-700"
            : "border-dashed border-zinc-700 bg-zinc-900/20 hover:border-zinc-600",
      )}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="font-mono text-sm font-medium text-zinc-200">
          {cluster}
        </span>
        {existing ? (
          <span className="inline-flex items-center gap-1 rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
            <Check className="size-2.5" /> ready
          </span>
        ) : (
          <span className="rounded bg-zinc-700/40 px-1.5 py-0.5 text-[10px] font-medium text-zinc-500">
            empty
          </span>
        )}
      </div>
      <div className="text-[11px] text-zinc-500 mb-2">{CLUSTER_HINT[cluster]}</div>

      {existing ? (
        <div className="flex items-center justify-between text-[11px]">
          <div className="min-w-0">
            <div className="text-zinc-300 truncate">{existing.filename}</div>
            <div className="text-zinc-500 tnum">
              {fmtSize(existing.size_bytes)}
            </div>
          </div>
          <button
            onClick={(e) => { e.stopPropagation(); onDelete() }}
            className="p-1 rounded hover:bg-rose-500/20 text-zinc-500 hover:text-rose-300"
          >
            <Trash2 className="size-3" />
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-1.5 text-[11px] text-zinc-500">
          {busy ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <Upload className="size-3" />
          )}
          Drag-drop PDF or click
        </div>
      )}

      {msg && (
        <div className="mt-1.5 text-[11px] text-zinc-400">{msg}</div>
      )}

      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) onUpload(f)
          e.target.value = ""
        }}
      />
    </div>
  )
}

function fmtSize(b: number | null): string {
  if (!b) return "—"
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / 1024 / 1024).toFixed(2)} MB`
}
