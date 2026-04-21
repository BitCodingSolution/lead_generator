"use client"

import * as React from "react"
import { mutate } from "swr"
import { Wrench, Loader2, RefreshCw, Trash2 } from "lucide-react"
import { api } from "@/lib/api"

type Action = "orphan" | "sweep" | "empty"

export function LinkedInMaintenance() {
  const [busy, setBusy] = React.useState<Action | "">("")
  const [msg, setMsg] = React.useState<string | null>(null)

  async function run(action: Action) {
    let confirmMsg: string | null = null
    if (action === "empty") {
      confirmMsg = "Permanently delete ALL recyclebin entries? This cannot be undone."
    } else if (action === "sweep") {
      confirmMsg = "Sweep junk leads to Recyclebin? Dry-testable: run, then restore from Recyclebin if needed."
    }
    if (confirmMsg && !confirm(confirmMsg)) return

    setBusy(action)
    setMsg(null)
    try {
      let path = ""
      if (action === "orphan") path = "/api/linkedin/maintenance/reset-orphans"
      if (action === "sweep")  path = "/api/linkedin/maintenance/sweep-junk"
      if (action === "empty")  path = "/api/linkedin/recyclebin/empty"
      const res = await api.post<Record<string, number>>(path)
      const summary = Object.entries(res)
        .map(([k, v]) => `${k}: ${v}`)
        .join(" · ")
      setMsg(summary || "Done")
      mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/"))
    } catch (err) {
      setMsg((err as Error).message)
    } finally {
      setBusy("")
    }
  }

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
      <div className="flex items-center gap-2 mb-3">
        <Wrench className="size-4 text-zinc-400" />
        <div className="text-sm font-medium text-zinc-200">Maintenance</div>
      </div>
      <div className="space-y-2">
        <Row
          label="Reset orphan rows"
          hint="Any lead stuck in Sending/Queued beyond 10 min → back to Drafted."
          action={
            <ActionBtn
              busy={busy === "orphan"}
              onClick={() => run("orphan")}
              icon={<RefreshCw className="size-3" />}
              label="Reset"
            />
          }
        />
        <Row
          label="Sweep junk → Recyclebin"
          hint="No email + no phone + no draft + older than 7d → archived."
          action={
            <ActionBtn
              busy={busy === "sweep"}
              onClick={() => run("sweep")}
              icon={<Trash2 className="size-3" />}
              label="Sweep"
            />
          }
        />
        <Row
          label="Empty Recyclebin"
          hint="Permanent delete. Keep for compliance, or clear when bloated."
          danger
          action={
            <ActionBtn
              busy={busy === "empty"}
              onClick={() => run("empty")}
              icon={<Trash2 className="size-3" />}
              label="Empty"
              danger
            />
          }
        />
      </div>
      {msg && <div className="mt-3 text-[11px] text-zinc-400">{msg}</div>}
    </div>
  )
}

function Row({
  label, hint, action, danger,
}: {
  label: string
  hint: string
  action: React.ReactNode
  danger?: boolean
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-zinc-800/70 bg-zinc-900/40 p-2.5">
      <div className="min-w-0">
        <div className={`text-sm ${danger ? "text-rose-200" : "text-zinc-200"}`}>
          {label}
        </div>
        <div className="text-[11px] text-zinc-500">{hint}</div>
      </div>
      {action}
    </div>
  )
}

function ActionBtn({
  busy, onClick, icon, label, danger,
}: {
  busy: boolean
  onClick: () => void
  icon: React.ReactNode
  label: string
  danger?: boolean
}) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs border disabled:opacity-50 ${
        danger
          ? "border-rose-500/40 bg-rose-500/10 text-rose-200 hover:bg-rose-500/20"
          : "border-zinc-700 bg-zinc-800/60 text-zinc-200 hover:bg-zinc-800"
      }`}
    >
      {busy ? <Loader2 className="size-3 animate-spin" /> : icon}
      {label}
    </button>
  )
}
