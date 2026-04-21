"use client"

import * as React from "react"
import { mutate } from "swr"
import { Trash2, Loader2 } from "lucide-react"
import { PageHeader } from "@/components/page-header"
import { LinkedInRecyclebinList } from "@/components/linkedin/linkedin-recyclebin-list"
import { api } from "@/lib/api"

export default function LinkedInRecyclebinPage() {
  const [busy, setBusy] = React.useState(false)

  async function onEmpty() {
    if (!confirm("Permanently delete ALL recyclebin entries? Cannot be undone.")) return
    setBusy(true)
    try {
      await api.post("/api/linkedin/recyclebin/empty")
      mutate("/api/linkedin/recyclebin")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Recyclebin"
        subtitle="Auto-skipped, rejected, or archived leads. Restore any row back into the active list."
        actions={
          <button
            onClick={onEmpty}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-sm text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
          >
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}
            Empty bin
          </button>
        }
      />
      <LinkedInRecyclebinList />
    </div>
  )
}
