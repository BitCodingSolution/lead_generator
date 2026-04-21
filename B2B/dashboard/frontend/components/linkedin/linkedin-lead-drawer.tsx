"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import {
  X, Sparkles, Archive, Loader2, ExternalLink, Mail, Check, Send,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { api, swrFetcher } from "@/lib/api"
import type { LinkedInLead } from "@/lib/types"

type LeadFull = LinkedInLead & {
  tags: string | null
  post_text: string | null
  phone: string | null
  gen_body: string | null
  email_mode: string | null
  jaydip_note: string | null
  skip_reason: string | null
}

export function LinkedInLeadDrawer({
  leadId,
  onClose,
}: {
  leadId: number | null
  onClose: () => void
}) {
  const open = leadId != null
  const key = open ? `/api/linkedin/leads/${leadId}` : null
  const { data: lead, mutate: refresh } = useSWR<LeadFull>(key, swrFetcher)

  const [subject, setSubject] = React.useState("")
  const [body, setBody] = React.useState("")
  const [note, setNote] = React.useState("")
  const [busy, setBusy] = React.useState<"" | "draft" | "save" | "archive" | "send">("")
  const [toast, setToast] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (!lead) return
    setSubject(lead.gen_subject ?? "")
    setBody(lead.gen_body ?? "")
    setNote(lead.jaydip_note ?? "")
  }, [lead?.id])

  const dirty =
    !!lead &&
    (subject !== (lead.gen_subject ?? "") ||
      body !== (lead.gen_body ?? "") ||
      note !== (lead.jaydip_note ?? ""))

  async function onGenerate() {
    if (!lead) return
    setBusy("draft")
    try {
      const res = await api.post<{
        status: "drafted" | "skipped"
        subject?: string
        body?: string
        skip_reason?: string
      }>(`/api/linkedin/drafts/${lead.id}/generate`)
      if (res.status === "skipped") {
        setToast(`Auto-skipped: ${res.skip_reason}. Moved to Recyclebin.`)
        mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/"))
        onClose()
        return
      }
      setSubject(res.subject ?? "")
      setBody(res.body ?? "")
      setToast("Draft generated")
      refresh()
      mutate("/api/linkedin/overview")
    } catch (err) {
      setToast(`Generate failed: ${(err as Error).message}`)
    } finally {
      setBusy("")
    }
  }

  async function onSave() {
    if (!lead) return
    setBusy("save")
    try {
      await api.post(`/api/linkedin/leads/${lead.id}`, {
        gen_subject: subject,
        gen_body: body,
        jaydip_note: note,
      })
      setToast("Saved")
      refresh()
      mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/leads?"))
    } catch (err) {
      setToast(`Save failed: ${(err as Error).message}`)
    } finally {
      setBusy("")
    }
  }

  async function onSend() {
    if (!lead) return
    if (dirty) {
      setToast("Save your edits before sending")
      return
    }
    if (!lead.email) {
      setToast("This lead has no email address")
      return
    }
    if (!confirm(`Send email to ${lead.email}?`)) return
    setBusy("send")
    try {
      const res = await api.post<{ sent_at: string }>(
        `/api/linkedin/send/lead/${lead.id}`,
      )
      setToast(`Sent at ${res.sent_at}`)
      refresh()
      mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/"))
    } catch (err) {
      setToast((err as Error).message)
    } finally {
      setBusy("")
    }
  }

  async function onArchive() {
    if (!lead) return
    if (!confirm("Move this lead to Recyclebin?")) return
    setBusy("archive")
    try {
      await api.post(`/api/linkedin/leads/${lead.id}/archive`, {
        reason: "manual",
      })
      mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/"))
      onClose()
    } catch (err) {
      setToast(`Archive failed: ${(err as Error).message}`)
      setBusy("")
    }
  }

  React.useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 2500)
    return () => clearTimeout(t)
  }, [toast])

  return (
    <div
      className={cn(
        "fixed inset-0 z-40 transition-opacity",
        open ? "opacity-100" : "opacity-0 pointer-events-none",
      )}
    >
      <div
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
      />
      <aside
        className={cn(
          "absolute right-0 top-0 h-full w-full max-w-[640px] bg-[#0f0f11] border-l border-zinc-800/80 shadow-2xl transition-transform overflow-y-auto",
          open ? "translate-x-0" : "translate-x-full",
        )}
      >
        <header className="sticky top-0 z-10 flex items-center justify-between gap-3 px-5 py-3 border-b border-zinc-800/70 bg-[#0f0f11]/95 backdrop-blur">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-zinc-100 truncate">
              {lead?.company || lead?.posted_by || "Lead"}
            </div>
            <div className="text-[11px] text-zinc-500 truncate">
              {lead?.role || "—"}
              {lead?.location && <> · {lead.location}</>}
            </div>
          </div>
          <div className="flex items-center gap-1">
            {lead?.post_url && (
              <a
                href={lead.post_url}
                target="_blank"
                rel="noreferrer"
                className="p-1.5 rounded hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200"
                title="Open LinkedIn post"
              >
                <ExternalLink className="size-4" />
              </a>
            )}
            <button
              onClick={onArchive}
              disabled={!lead || !!busy}
              className="p-1.5 rounded hover:bg-rose-500/20 text-zinc-400 hover:text-rose-300 disabled:opacity-50"
              title="Archive to Recyclebin"
            >
              <Archive className="size-4" />
            </button>
            <button
              onClick={onClose}
              className="p-1.5 rounded hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200"
            >
              <X className="size-4" />
            </button>
          </div>
        </header>

        {!lead ? (
          <div className="p-8 text-sm text-zinc-500">Loading…</div>
        ) : (
          <div className="p-5 space-y-5">
            <Facts lead={lead} />

            <section>
              <div className="flex items-center justify-between mb-2">
                <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-500">
                  Draft
                </div>
                <button
                  onClick={onGenerate}
                  disabled={!!busy}
                  className="inline-flex items-center gap-1.5 rounded-md bg-[hsl(250_80%_62%)] px-2.5 py-1 text-xs text-white hover:brightness-110 disabled:opacity-50"
                >
                  {busy === "draft" ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    <Sparkles className="size-3" />
                  )}
                  {lead.gen_subject ? "Regenerate" : "Generate"}
                </button>
              </div>
              <input
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                placeholder="Subject line…"
                className="w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-[hsl(250_80%_62%)]"
              />
              <textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                rows={10}
                placeholder="Email body…"
                className="mt-2 w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 font-mono leading-relaxed focus:outline-none focus:border-[hsl(250_80%_62%)]"
              />
              <div className="mt-2 flex items-center justify-between">
                <div className="text-[11px] text-zinc-500">
                  {lead.email_mode && <>Mode: <span className="text-zinc-300">{lead.email_mode}</span></>}
                  {lead.cv_cluster && <> · CV: <span className="text-zinc-300">{lead.cv_cluster}</span></>}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={onSave}
                    disabled={!dirty || !!busy}
                    className="inline-flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800/60 px-2.5 py-1 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-40"
                  >
                    {busy === "save" ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : (
                      <Check className="size-3" />
                    )}
                    Save
                  </button>
                  <button
                    onClick={onSend}
                    disabled={
                      !!busy ||
                      dirty ||
                      !lead.email ||
                      !subject.trim() ||
                      !body.trim() ||
                      lead.status === "Sent"
                    }
                    className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-2.5 py-1 text-xs text-white hover:bg-emerald-500 disabled:opacity-40"
                    title={
                      lead.status === "Sent"
                        ? "Already sent"
                        : !lead.email
                          ? "No email address on this lead"
                          : dirty
                            ? "Save edits first"
                            : "Send now"
                    }
                  >
                    {busy === "send" ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : (
                      <Send className="size-3" />
                    )}
                    {lead.status === "Sent" ? "Sent" : "Send"}
                  </button>
                </div>
              </div>
            </section>

            <section>
              <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-500 mb-1.5">
                Private note
              </div>
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={2}
                placeholder="Any non-empty note will skip this lead from send."
                className="w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-[hsl(250_80%_62%)]"
              />
            </section>

            <section>
              <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-500 mb-1.5">
                Post
              </div>
              <div className="rounded-md border border-zinc-800 bg-zinc-900/40 p-3 text-xs text-zinc-300 whitespace-pre-wrap max-h-72 overflow-y-auto leading-relaxed">
                {lead.post_text || "—"}
              </div>
            </section>
          </div>
        )}

        {toast && (
          <div className="fixed bottom-4 right-6 rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs text-zinc-200 shadow-lg">
            {toast}
          </div>
        )}
      </aside>
    </div>
  )
}

function Facts({ lead }: { lead: LeadFull }) {
  const items: { k: string; v: React.ReactNode }[] = [
    { k: "Posted by", v: lead.posted_by || "—" },
    { k: "Company", v: lead.company || "—" },
    { k: "Role", v: lead.role || "—" },
    { k: "Tech", v: lead.tech_stack || "—" },
    {
      k: "Email",
      v: lead.email ? (
        <a
          href={`mailto:${lead.email}`}
          className="inline-flex items-center gap-1 text-[hsl(250_80%_72%)] hover:underline"
        >
          <Mail className="size-3" />
          {lead.email}
        </a>
      ) : (
        "—"
      ),
    },
    { k: "Status", v: <span className="text-zinc-200">{lead.status}</span> },
  ]
  return (
    <div className="grid grid-cols-2 gap-y-2 gap-x-6 text-xs">
      {items.map((it) => (
        <div key={it.k}>
          <div className="text-[10px] uppercase tracking-[0.1em] text-zinc-500">
            {it.k}
          </div>
          <div className="mt-0.5 text-zinc-300 truncate">{it.v}</div>
        </div>
      ))}
    </div>
  )
}
