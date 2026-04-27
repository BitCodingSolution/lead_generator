"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import { Ban, Plus, Trash2, Loader2 } from "lucide-react"
import { api, swrFetcher } from "@/lib/api"
import { cn } from "@/lib/utils"

type BlockKind = "company" | "domain" | "email"

type BlockRow = {
  id: number
  kind: BlockKind
  value: string
  reason: string | null
  created_at: string
}

const ENDPOINT = "/api/linkedin/blocklist"

export function LinkedInBlocklistCard() {
  const { data, isLoading } = useSWR<{ rows: BlockRow[] }>(ENDPOINT, swrFetcher)
  const [kind, setKind] = React.useState<BlockKind>("domain")
  const [value, setValue] = React.useState("")
  const [reason, setReason] = React.useState("")
  const [busy, setBusy] = React.useState(false)
  const [msg, setMsg] = React.useState<string | null>(null)
  const [filter, setFilter] = React.useState<"all" | "auto" | "manual">("all")

  const allRows = data?.rows ?? []
  const rows = allRows.filter((r) => {
    if (filter === "auto") return (r.reason ?? "").startsWith("auto:")
    if (filter === "manual") return !(r.reason ?? "").startsWith("auto:")
    return true
  })
  const autoCount = allRows.filter((r) =>
    (r.reason ?? "").startsWith("auto:"),
  ).length

  async function onAdd(e: React.FormEvent) {
    e.preventDefault()
    if (!value.trim()) return
    setBusy(true)
    setMsg(null)
    try {
      await api.post(ENDPOINT, {
        kind,
        value: value.trim(),
        reason: reason.trim() || null,
      })
      setValue("")
      setReason("")
      mutate(ENDPOINT)
    } catch (err) {
      setMsg((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function onDelete(id: number) {
    if (!confirm("Remove this entry?")) return
    await api.post(`${ENDPOINT}/${id}/delete`)
    mutate(ENDPOINT)
  }

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
      <div className="flex items-center gap-2 mb-3">
        <Ban className="size-4 text-zinc-400" />
        <div className="text-sm font-medium text-zinc-200">Blocklist</div>
        <span className="ml-auto text-[11px] text-zinc-500">
          {rows.length} entries
        </span>
      </div>
      <div className="text-[11px] text-zinc-500 mb-3">
        Companies and email domains never ingested or sent to. Matches on ingest (silent drop) and at send (400 error).
      </div>

      <form onSubmit={onAdd} className="grid grid-cols-[100px_1fr_1fr_auto] gap-2 mb-3">
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as BlockKind)}
          className="rounded-md border border-zinc-800 bg-zinc-900/60 px-2 py-1.5 text-sm text-zinc-100 focus:outline-none focus:border-[hsl(250_80%_62%)]"
        >
          <option value="domain">Domain</option>
          <option value="email">Email</option>
          <option value="company">Company</option>
        </select>
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={
            kind === "domain"
              ? "upwork.com"
              : kind === "email"
                ? "name@upwork.com"
                : "Deloitte Consulting"
          }
          className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 font-mono focus:outline-none focus:border-[hsl(250_80%_62%)]"
        />
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Reason (optional)"
          className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-[hsl(250_80%_62%)]"
        />
        <button
          type="submit"
          disabled={!value.trim() || busy}
          className="inline-flex items-center justify-center gap-1.5 rounded-md bg-[hsl(250_80%_62%)] px-3 py-1.5 text-sm text-white hover:brightness-110 disabled:opacity-50"
        >
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
          Block
        </button>
      </form>
      {msg && <div className="mb-2 text-[11px] text-rose-300">{msg}</div>}

      {allRows.length > 0 && (
        <div className="mb-2 flex items-center gap-1 text-[11px]">
          {(["all", "auto", "manual"] as const).map((k) => (
            <button
              key={k}
              onClick={() => setFilter(k)}
              className={cn(
                "rounded px-2 py-0.5 capitalize",
                filter === k
                  ? "bg-zinc-700/60 text-zinc-100"
                  : "text-zinc-500 hover:text-zinc-300",
              )}
            >
              {k}
              {k === "auto" && autoCount > 0 && (
                <span className="ml-1 text-amber-400">({autoCount})</span>
              )}
            </button>
          ))}
        </div>
      )}
      {isLoading ? (
        <div className="text-xs text-zinc-500">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="text-xs text-zinc-500">
          {filter === "auto"
            ? "No auto-blocks yet — reply opt-outs land here."
            : "No entries yet."}
        </div>
      ) : (
        <div className="divide-y divide-zinc-800/60 max-h-72 overflow-y-auto">
          {rows.map((r) => (
            <div
              key={r.id}
              className="flex items-center justify-between py-2"
            >
              <div className="min-w-0 flex-1 flex items-center gap-2">
                <span
                  className={cn(
                    "inline-block rounded px-1.5 py-0.5 text-[10px] font-medium uppercase",
                    r.kind === "domain"
                      ? "bg-sky-500/15 text-sky-300"
                      : r.kind === "email"
                        ? "bg-amber-500/15 text-amber-300"
                        : "bg-violet-500/15 text-violet-300",
                  )}
                >
                  {r.kind}
                </span>
                <span className="font-mono text-sm text-zinc-200 truncate">
                  {r.value}
                </span>
                {r.reason && (
                  <span
                    className={cn(
                      "text-[11px] truncate",
                      r.reason.startsWith("auto:")
                        ? "text-amber-400/80"
                        : "text-zinc-500",
                    )}
                  >
                    — {r.reason}
                  </span>
                )}
              </div>
              <button
                onClick={() => onDelete(r.id)}
                className="p-1 rounded hover:bg-rose-500/20 text-zinc-500 hover:text-rose-300"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
