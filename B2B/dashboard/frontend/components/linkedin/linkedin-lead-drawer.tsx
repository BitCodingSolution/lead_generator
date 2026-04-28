"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import {
  X, Sparkles, Archive, Loader2, ExternalLink, Mail, Check, Send, Phone, Eye,
  Clock, XCircle, BellOff,
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
  const [busy, setBusy] = React.useState<"" | "draft" | "save" | "archive" | "send" | "schedule" | "snooze">("")
  const [schedulerOpen, setSchedulerOpen] = React.useState(false)
  const [snoozeOpen, setSnoozeOpen] = React.useState(false)
  const [toast, setToast] = React.useState<string | null>(null)

  // Sync local edits from the server lead. Re-fires when the server-side
  // fields change (e.g. background auto-drafter finishes) so the drawer
  // doesn't display stale blanks, but only if the user hasn't started
  // typing — we never clobber in-progress edits.
  const lastSyncedId = React.useRef<number | null>(null)
  React.useEffect(() => {
    if (!lead) return
    const switchedLead = lastSyncedId.current !== lead.id
    // On a brand-new lead: always sync. On same lead: only sync if the
    // current local state still matches what we'd write (i.e., user
    // hasn't typed yet).
    const pristine =
      subject === "" && body === "" ||
      subject === (lead.gen_subject ?? "") &&
        body === (lead.gen_body ?? "") &&
        note === (lead.jaydip_note ?? "")
    if (switchedLead || pristine) {
      setSubject(lead.gen_subject ?? "")
      setBody(lead.gen_body ?? "")
      setNote(lead.jaydip_note ?? "")
      lastSyncedId.current = lead.id
    }
  }, [lead?.id, lead?.gen_subject, lead?.gen_body, lead?.jaydip_note])
  // subject/body/note intentionally omitted from deps — the effect is a
  // one-way server→local sync. Including them would cause loops since it
  // sets them.

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

  async function onSchedule(whenIso: string) {
    if (!lead) return
    if (dirty) {
      setToast("Save your edits before scheduling")
      return
    }
    setBusy("schedule")
    try {
      await api.post(`/api/linkedin/leads/${lead.id}/schedule`, {
        scheduled_send_at: whenIso,
      })
      setSchedulerOpen(false)
      setToast("Scheduled")
      refresh()
      mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/"))
    } catch (e) {
      setToast((e as Error).message)
    } finally {
      setBusy("")
    }
  }

  async function onUnschedule() {
    if (!lead) return
    setBusy("schedule")
    try {
      await api.post(`/api/linkedin/leads/${lead.id}/unschedule`)
      setToast("Schedule cancelled")
      refresh()
      mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/"))
    } catch (e) {
      setToast((e as Error).message)
    } finally {
      setBusy("")
    }
  }

  async function onSnooze(remindAt: string) {
    if (!lead) return
    setBusy("snooze")
    try {
      await api.post(`/api/linkedin/leads/${lead.id}/snooze`, {
        remind_at: remindAt,
      })
      setSnoozeOpen(false)
      setToast("Snoozed")
      refresh()
      mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/"))
    } catch (e) {
      setToast((e as Error).message)
    } finally {
      setBusy("")
    }
  }

  async function onUnsnooze() {
    if (!lead) return
    setBusy("snooze")
    try {
      await api.post(`/api/linkedin/leads/${lead.id}/unsnooze`)
      setToast("Reminder cleared")
      refresh()
      mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/"))
    } catch (e) {
      setToast((e as Error).message)
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
              {(() => {
                // Once the cold mail is on its way (Sent / Replied), the
                // top section is the historical record of what we sent.
                // Regenerating that draft makes no sense — the new draft
                // would never go out, and the user might think it had.
                // Reply-thread regenerate lives in RepliesSection below.
                const alreadySent =
                  lead.status === "Sent" || lead.status === "Replied"
                return (
                  <div className="flex items-center justify-between mb-2">
                    <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-500">
                      {alreadySent ? "Sent message" : "Draft"}
                    </div>
                    {alreadySent ? (
                      <span className="inline-flex items-center gap-1 rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-300">
                        <Check className="size-2.5" />
                        Sent {lead.sent_at ? fmtRelativeTs(lead.sent_at) : ""}
                      </span>
                    ) : (
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
                    )}
                  </div>
                )
              })()}
              <input
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                readOnly={lead.status === "Sent" || lead.status === "Replied"}
                placeholder="Subject line…"
                className={cn(
                  "w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-[hsl(250_80%_62%)]",
                  (lead.status === "Sent" || lead.status === "Replied") &&
                    "opacity-70 cursor-default focus:border-zinc-800",
                )}
              />
              <textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                readOnly={lead.status === "Sent" || lead.status === "Replied"}
                rows={10}
                placeholder="Email body…"
                className={cn(
                  "mt-2 w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 font-mono leading-relaxed focus:outline-none focus:border-[hsl(250_80%_62%)]",
                  (lead.status === "Sent" || lead.status === "Replied") &&
                    "opacity-70 cursor-default focus:border-zinc-800",
                )}
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
                  {(() => {
                    const alreadySent =
                      lead.status === "Sent" || lead.status === "Replied"
                    return (
                      <>
                        <button
                          onClick={onSend}
                          disabled={
                            !!busy ||
                            dirty ||
                            !lead.email ||
                            !subject.trim() ||
                            !body.trim() ||
                            alreadySent
                          }
                          className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-2.5 py-1 text-xs text-white hover:bg-emerald-500 disabled:opacity-40"
                          title={
                            lead.status === "Replied"
                              ? "Lead has already replied — use the reply draft below"
                              : lead.status === "Sent"
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
                          {lead.status === "Replied"
                            ? "Replied"
                            : lead.status === "Sent"
                              ? "Sent"
                              : "Send"}
                        </button>
                        {!alreadySent && lead.email && (
                          <SchedulePicker
                            lead={lead}
                            open={schedulerOpen}
                            busy={busy === "schedule"}
                            onOpen={() => setSchedulerOpen(true)}
                            onClose={() => setSchedulerOpen(false)}
                            onSchedule={onSchedule}
                            onUnschedule={onUnschedule}
                          />
                        )}
                        <SnoozePicker
                          lead={lead}
                          open={snoozeOpen}
                          busy={busy === "snooze"}
                          onOpen={() => setSnoozeOpen(true)}
                          onClose={() => setSnoozeOpen(false)}
                          onSnooze={onSnooze}
                          onUnsnooze={onUnsnooze}
                        />
                      </>
                    )
                  })()}
                </div>
              </div>
            </section>

            {lead.status === "Replied" && (
              <RepliesSection
                leadId={lead.id}
                setToast={setToast}
                onAfterSend={onClose}
              />
            )}

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

            <TimelineSection leadId={lead.id} />
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

const EVENT_ICONS: Record<string, string> = {
  ingest: "+",
  draft: "✏",
  draft_fallback: "⚠",
  draft_skipped: "⤫",
  send: "→",
  send_error: "×",
  inbox_reply: "⇐",
  inbox_bounce: "✗",
  inbox_auto_reply: "•",
  followup_send: "↻",
  archive: "⎚",
  restore: "↩",
}

function TimelineSection({ leadId }: { leadId: number }) {
  const { data } = useSWR<{
    rows: { id: number; at: string; kind: string; meta: unknown }[]
  }>(`/api/linkedin/leads/${leadId}/events`, swrFetcher)

  const rows = data?.rows ?? []
  if (rows.length === 0) return null

  return (
    <section>
      <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-500 mb-1.5">
        Timeline
      </div>
      <ul className="space-y-1.5">
        {rows.map((r) => (
          <li
            key={r.id}
            className="flex items-start gap-2 text-xs text-zinc-300"
          >
            <span className="mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-zinc-800/70 text-[11px] text-zinc-400">
              {EVENT_ICONS[r.kind] ?? "·"}
            </span>
            <div className="min-w-0 flex-1">
              <span className="font-mono text-[11px] text-zinc-500 tnum">
                {fmtTs(r.at)}
              </span>
              <span className="ml-2 text-zinc-200">
                {r.kind.replace(/_/g, " ")}
              </span>
              {r.meta ? (
                <div className="mt-0.5 text-[11px] text-zinc-500 font-mono break-all">
                  {JSON.stringify(r.meta)}
                </div>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}

function fmtTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    })
  } catch { return iso }
}

function fmtRelativeTs(iso: string): string {
  try {
    const diffMs = Date.now() - new Date(iso).getTime()
    if (diffMs < 0) return ""
    const m = Math.floor(diffMs / 60_000)
    if (m < 1) return "just now"
    if (m < 60) return `${m}m ago`
    const h = Math.floor(m / 60)
    if (h < 24) return `${h}h ago`
    const d = Math.floor(h / 24)
    return `${d}d ago`
  } catch { return "" }
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
    {
      k: "Phone",
      v: lead.phone ? (
        <a
          href={`tel:${lead.phone.replace(/\s+/g, "")}`}
          className="inline-flex items-center gap-1 font-mono text-zinc-300 hover:text-zinc-100"
        >
          <Phone className="size-3" />
          {lead.phone}
        </a>
      ) : (
        "—"
      ),
    },
    { k: "Status", v: <span className="text-zinc-200">{lead.status}</span> },
  ]
  if (lead.sent_at) {
    items.push({
      k: "Opens",
      v: lead.open_count > 0 ? (
        <span className="inline-flex items-center gap-1 text-emerald-300">
          <Eye className="size-3" />
          {lead.open_count}× · last {lead.last_opened_at?.slice(0, 16).replace("T", " ")}
        </span>
      ) : (
        <span className="text-zinc-500">not yet</span>
      ),
    })
  }
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


function SchedulePicker({
  lead, open, busy, onOpen, onClose, onSchedule, onUnschedule,
}: {
  lead: LinkedInLead
  open: boolean
  busy: boolean
  onOpen: () => void
  onClose: () => void
  onSchedule: (whenIso: string) => void
  onUnschedule: () => void
}) {
  const [custom, setCustom] = React.useState("")
  const isScheduled = !!lead.scheduled_send_at

  function presetDate(preset: "in2h" | "tomorrow9" | "nextMon9"): Date {
    const d = new Date()
    if (preset === "in2h") {
      d.setHours(d.getHours() + 2)
      return d
    }
    if (preset === "tomorrow9") {
      d.setDate(d.getDate() + 1)
      d.setHours(9, 0, 0, 0)
      return d
    }
    // Next Monday 9am local
    const daysUntilMon = (1 + 7 - d.getDay()) % 7 || 7
    d.setDate(d.getDate() + daysUntilMon)
    d.setHours(9, 0, 0, 0)
    return d
  }

  function submitPreset(p: "in2h" | "tomorrow9" | "nextMon9") {
    onSchedule(presetDate(p).toISOString())
  }

  function submitCustom() {
    if (!custom) return
    // datetime-local gives 'YYYY-MM-DDTHH:MM' in local TZ. Append local offset.
    onSchedule(new Date(custom).toISOString())
  }

  if (isScheduled) {
    return (
      <div className="relative inline-flex items-center gap-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-300">
        <Clock className="size-3" />
        <span title={`Sends at ${lead.scheduled_send_at}`}>
          Scheduled {fmtWhen(lead.scheduled_send_at!)}
        </span>
        <button
          onClick={onUnschedule}
          disabled={busy}
          className="ml-1 text-amber-200 hover:text-white disabled:opacity-50"
          title="Cancel schedule"
        >
          {busy ? <Loader2 className="size-3 animate-spin" /> : <XCircle className="size-3" />}
        </button>
      </div>
    )
  }

  return (
    <div className="relative">
      <button
        onClick={onOpen}
        disabled={busy}
        className="inline-flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800/60 px-2.5 py-1 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-40"
        title="Schedule this send for later"
      >
        {busy ? <Loader2 className="size-3 animate-spin" /> : <Clock className="size-3" />}
        Schedule
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={onClose} />
          <div className="absolute right-0 top-full mt-1 z-50 w-64 rounded-md border border-zinc-700 bg-zinc-900 p-3 text-xs shadow-lg">
            <div className="mb-2 text-[10px] font-medium uppercase tracking-[0.1em] text-zinc-500">
              Send later
            </div>
            <div className="flex flex-col gap-1 mb-3">
              <PresetBtn label="In 2 hours" onClick={() => submitPreset("in2h")} />
              <PresetBtn label="Tomorrow 9am" onClick={() => submitPreset("tomorrow9")} />
              <PresetBtn label="Next Monday 9am" onClick={() => submitPreset("nextMon9")} />
            </div>
            <div className="mb-2 text-[10px] font-medium uppercase tracking-[0.1em] text-zinc-500">
              Or pick exact time
            </div>
            <input
              type="datetime-local"
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
              className="w-full rounded border border-zinc-800 bg-zinc-900/60 px-2 py-1 text-xs text-zinc-200"
            />
            <button
              onClick={submitCustom}
              disabled={!custom}
              className="mt-2 w-full rounded bg-[hsl(250_80%_62%)] px-2 py-1 text-xs text-white hover:bg-[hsl(250_80%_70%)] disabled:opacity-40"
            >
              Schedule custom time
            </button>
          </div>
        </>
      )}
    </div>
  )
}

function SnoozePicker({
  lead, open, busy, onOpen, onClose, onSnooze, onUnsnooze,
}: {
  lead: LinkedInLead
  open: boolean
  busy: boolean
  onOpen: () => void
  onClose: () => void
  onSnooze: (remindAt: string) => void
  onUnsnooze: () => void
}) {
  const [custom, setCustom] = React.useState("")
  const snoozed = !!lead.remind_at

  function submitCustom() {
    if (!custom) return
    onSnooze(new Date(custom).toISOString())
  }

  if (snoozed) {
    return (
      <div className="relative inline-flex items-center gap-1 rounded-md border border-sky-500/30 bg-sky-500/10 px-2 py-1 text-[11px] text-sky-300">
        <BellOff className="size-3" />
        <span title={`Reminds ${lead.remind_at}`}>
          Snoozed {fmtWhen(lead.remind_at!)}
        </span>
        <button
          onClick={onUnsnooze}
          disabled={busy}
          className="ml-1 text-sky-200 hover:text-white disabled:opacity-50"
          title="Clear reminder"
        >
          {busy ? <Loader2 className="size-3 animate-spin" /> : <XCircle className="size-3" />}
        </button>
      </div>
    )
  }

  return (
    <div className="relative">
      <button
        onClick={onOpen}
        disabled={busy}
        className="inline-flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800/60 px-2.5 py-1 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-40"
        title="Hide until a later time"
      >
        {busy ? <Loader2 className="size-3 animate-spin" /> : <BellOff className="size-3" />}
        Snooze
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={onClose} />
          <div className="absolute right-0 top-full mt-1 z-50 w-64 rounded-md border border-zinc-700 bg-zinc-900 p-3 text-xs shadow-lg">
            <div className="mb-2 text-[10px] font-medium uppercase tracking-[0.1em] text-zinc-500">
              Remind me in
            </div>
            <div className="flex flex-col gap-1 mb-3">
              <PresetBtn label="2 hours" onClick={() => onSnooze("2h")} />
              <PresetBtn label="1 day" onClick={() => onSnooze("1d")} />
              <PresetBtn label="3 days" onClick={() => onSnooze("3d")} />
              <PresetBtn label="1 week" onClick={() => onSnooze("1w")} />
            </div>
            <div className="mb-2 text-[10px] font-medium uppercase tracking-[0.1em] text-zinc-500">
              Or pick a date
            </div>
            <input
              type="datetime-local"
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
              className="w-full rounded border border-zinc-800 bg-zinc-900/60 px-2 py-1 text-xs text-zinc-200"
            />
            <button
              onClick={submitCustom}
              disabled={!custom}
              className="mt-2 w-full rounded bg-[hsl(210_80%_55%)] px-2 py-1 text-xs text-white hover:bg-[hsl(210_80%_62%)] disabled:opacity-40"
            >
              Snooze until that time
            </button>
          </div>
        </>
      )}
    </div>
  )
}


function PresetBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="w-full text-left rounded border border-zinc-800 bg-zinc-900/60 px-2 py-1 text-zinc-200 hover:bg-zinc-800 hover:border-zinc-700"
    >
      {label}
    </button>
  )
}

function fmtWhen(iso: string): string {
  try {
    const d = new Date(iso)
    const now = new Date()
    const sameDay = d.toDateString() === now.toDateString()
    if (sameDay) {
      return `today ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`
    }
    return d.toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    })
  } catch {
    return iso
  }
}


// Quick templates — edit-in-place before sending. Kept short and matching
// the cold-email style rules (no em-dashes, 60-120 words, minimal signoff).
const REPLY_TEMPLATES: { label: string; body: string }[] = [
  {
    label: "📅 Book call",
    body:
      "Thanks for getting back. Happy to hop on a quick 20-min call so I can show you two relevant builds and answer anything specific about fit.\n\n" +
      "Does Tue or Wed afternoon IST work? Or share your calendar link and I'll grab a slot.\n\n" +
      "Jaydip",
  },
  {
    label: "📄 Send portfolio",
    body:
      "Appreciate the reply. Sending over a short portfolio doc covering two recent builds - one AI automation pipeline and one SaaS backend - so you have something concrete to review.\n\n" +
      "Let me know if any of them looks close to what you're hiring for, I can dig in with more detail.\n\n" +
      "Jaydip",
  },
  {
    label: "💬 Rate question",
    body:
      "Thanks for asking. My rate depends on scope and duration - typically $60-$90/hr for contract work, flexible on longer commitments or milestone-based projects.\n\n" +
      "Happy to share a fixed quote once I understand the deliverables better. Want to share the JD or hop on a 15-min scoping call?\n\n" +
      "Jaydip",
  },
  {
    label: "🔄 Referral ask",
    body:
      "Appreciate the forward. Would it help if I send you a 1-pager you can pass along? Summarises my stack (Python / AI-ML / SaaS backend), availability, and recent results.\n\n" +
      "Just reply 'yes' and I'll send it over.\n\n" +
      "Jaydip",
  },
  {
    label: "⏳ Nudge (OOO)",
    body:
      "Thanks for the heads up. I'll circle back next week when you're back in.\n\n" +
      "Jaydip",
  },
]

type ThreadEntry = {
  direction: "out_initial" | "in" | "out_reply"
  at: string
  // Common
  body?: string | null
  // out_initial
  subject?: string | null
  // in
  id?: number
  from_email?: string | null
  kind?: string
  sentiment?: string | null
  intent?: string | null
  handled_at?: string | null
  auto_draft_body?: string | null
  auto_draft_at?: string | null
}

const INTENT_LABEL: Record<string, string> = {
  form_fill: "📋 form fill",
  interview_request: "🎯 interview",
  scheduling: "📅 schedule",
  salary_question: "💰 rate ask",
  referral: "↪ referral",
  info_request: "📎 info ask",
  rejection: "❌ rejection",
}

type RepliesPayload = {
  lead: {
    id: number
    email: string | null
    gen_subject: string | null
    received_on_email: string | null
  }
  replies: Array<{
    id: number
    from_email: string | null
    subject: string | null
    snippet: string | null
    body: string | null
    received_at: string
    kind: string
    handled_at: string | null
    sentiment: string | null
    auto_draft_body: string | null
    auto_draft_at: string | null
  }>
  thread?: ThreadEntry[]
}

function RepliesSection({
  leadId, setToast, onAfterSend,
}: {
  leadId: number
  setToast: (s: string | null) => void
  // Called once a reply has actually been sent — drawer uses this to
  // dismiss itself so the user lands back on the leads list.
  onAfterSend?: () => void
}) {
  const { data, isLoading } = useSWR<RepliesPayload>(
    `/api/linkedin/leads/${leadId}/replies`,
    swrFetcher,
  )
  const [draftBody, setDraftBody] = React.useState("")
  const [busy, setBusy] = React.useState<"draft" | "send" | null>(null)
  const [prefilled, setPrefilled] = React.useState(false)
  // Free-text instruction Jaydip can pass into Regenerate. Cleared after
  // each successful regen so a stale hint doesn't sneak into the next one.
  const [hint, setHint] = React.useState("")

  const inbound = (data?.replies ?? []).filter((r) => r.kind === "reply")
  const latest = inbound[inbound.length - 1]
  // Render the merged conversation (inbound + outbound replies, ordered
  // chronologically). Skip the out_initial since the drawer already shows
  // the original cold mail in the dedicated "Sent message" section above.
  const thread: ThreadEntry[] = (data?.thread ?? []).filter(
    (e) => e.direction !== "out_initial",
  )
  // Reply composer only shows when the most-recent thread entry is
  // inbound — i.e. we actually owe a reply. If Jaydip just sent something
  // and is waiting on the prospect, hide the composer to reduce clutter.
  const lastEntry = thread[thread.length - 1]
  const owesReply = !!lastEntry && lastEntry.direction === "in"

  // Auto-fill textarea with the background-generated draft the first
  // time the drawer has it. Never clobbers what the user has typed.
  React.useEffect(() => {
    if (prefilled) return
    if (latest?.auto_draft_body && !draftBody) {
      setDraftBody(latest.auto_draft_body)
      setPrefilled(true)
    }
  }, [latest?.auto_draft_body, draftBody, prefilled])

  async function onDraft() {
    setBusy("draft")
    try {
      const res = await api.post<{ body: string }>(
        `/api/linkedin/leads/${leadId}/draft-reply`,
        hint.trim() ? { hint: hint.trim() } : undefined,
      )
      setDraftBody(res.body || "")
      if (!res.body) setToast("Bridge returned empty draft")
      // Hint stays — user can tweak it and regenerate again. Caller can
      // explicitly clear it via the Clear hint button below the textarea.
    } catch (e) {
      setToast((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  async function onSend() {
    if (!draftBody.trim()) {
      setToast("Draft body is empty")
      return
    }
    if (!confirm("Send this reply now?")) return
    setBusy("send")
    try {
      await api.post(`/api/linkedin/leads/${leadId}/send-reply`, {
        body: draftBody,
      })
      setToast("Reply sent")
      setDraftBody("")
      mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/"))
      // Dismiss the drawer once the send succeeds — user is done with
      // this lead, no reason to keep them looking at it. Fires after the
      // SWR mutate so the leads list shows the updated state on render.
      onAfterSend?.()
    } catch (e) {
      setToast((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="rounded-md border border-amber-500/20 bg-amber-500/5 p-3">
      <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
        <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-amber-300">
          Conversation thread {thread.length > 0 && `(${thread.length})`}
        </div>
        {data?.lead.received_on_email && (
          <div
            className="inline-flex items-center gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-200"
            title="The inbox this conversation is landing in"
          >
            <Mail className="size-3" />
            <span className="text-amber-300/70">Inbox:</span>
            <span className="font-mono text-amber-100">{data.lead.received_on_email}</span>
          </div>
        )}
      </div>

      {isLoading ? (
        <div className="text-xs text-zinc-500">Loading…</div>
      ) : thread.length === 0 ? (
        <div className="text-xs text-zinc-500">No reply content captured.</div>
      ) : (
        <>
          {/* Merged thread — each message rendered as its own card, oldest
              first. Inbound = amber tint, our replies = teal tint. */}
          <div className="space-y-2 mb-3">
            {thread.map((entry, idx) => (
              <ThreadMessage key={`${entry.direction}-${entry.at}-${idx}`} entry={entry} />
            ))}
          </div>

          {!owesReply && (
            <div className="rounded border border-zinc-800/60 bg-zinc-900/40 px-2.5 py-2 text-[11px] text-zinc-500">
              You replied last — waiting on the prospect. Composer reopens
              when they write back.
            </div>
          )}

          {owesReply && (
          <div className="mt-3">
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center gap-2">
                <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-500">
                  Your reply
                </div>
                {latest?.auto_draft_body && prefilled && (
                  <span
                    className="inline-flex items-center gap-1 rounded bg-[hsl(250_80%_62%)]/15 px-1.5 py-0.5 text-[10px] text-[hsl(250_80%_78%)]"
                    title={`Auto-drafted ${latest.auto_draft_at ?? ""} - edit as needed`}
                  >
                    <Sparkles className="size-2.5" />
                    auto-drafted
                  </span>
                )}
              </div>
              <button
                onClick={onDraft}
                disabled={!!busy}
                className={cn(
                  "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] text-white disabled:opacity-50",
                  hint.trim()
                    ? "bg-[hsl(280_80%_62%)] hover:bg-[hsl(280_80%_70%)]"
                    : "bg-[hsl(250_80%_62%)] hover:bg-[hsl(250_80%_70%)]",
                )}
                title={
                  hint.trim()
                    ? "Regenerate using your hint above"
                    : "Regenerate from the inbound reply (no hint)"
                }
              >
                {busy === "draft" ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : (
                  <Sparkles className="size-3" />
                )}
                {latest?.auto_draft_body ? "Regenerate" : "Draft with Claude"}
              </button>
            </div>

            {/* Hint input — Jaydip types "what to say" here, Claude blends it
                into the regenerated draft. Empty = generic regenerate. */}
            <div className="mb-1.5">
              <textarea
                value={hint}
                onChange={(e) => setHint(e.target.value)}
                placeholder="Optional: tell Claude what to say (e.g. 'thank them, ask for the JD link', 'mention 5 yrs Kubernetes', 'politely decline'). Leave blank for a generic reply."
                rows={2}
                className="w-full resize-none rounded border border-zinc-800 bg-zinc-900/40 px-2 py-1.5 text-[12px] text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-[hsl(280_80%_62%)] leading-relaxed"
              />
              {hint.trim() && (
                <div className="mt-0.5 flex items-center justify-between text-[10px]">
                  <span className="text-[hsl(280_80%_78%)]">
                    ✨ Regenerate will use this hint
                  </span>
                  <button
                    type="button"
                    onClick={() => setHint("")}
                    className="text-zinc-500 hover:text-zinc-300 underline-offset-2 hover:underline"
                  >
                    Clear hint
                  </button>
                </div>
              )}
            </div>

            <div className="flex flex-wrap gap-1 mb-1.5">
              {REPLY_TEMPLATES.map((t) => (
                <button
                  key={t.label}
                  onClick={() => setDraftBody(t.body)}
                  className="rounded border border-zinc-800 bg-zinc-900/40 px-1.5 py-0.5 text-[10px] text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
                  title="Click to fill template — edit before sending"
                >
                  {t.label}
                </button>
              ))}
            </div>
            <textarea
              value={draftBody}
              onChange={(e) => setDraftBody(e.target.value)}
              rows={8}
              placeholder="Click 'Draft with Claude' or type your reply…"
              // Cap visible height so long drafts scroll within the
              // textarea instead of forcing the whole drawer to grow.
              // resize-y lets the user pull the handle bigger if they
              // want; max-h hard-stops at ~half the viewport.
              style={{ maxHeight: "50vh" }}
              className="w-full rounded border border-zinc-800 bg-zinc-900/60 px-2 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 font-mono leading-relaxed focus:outline-none focus:border-[hsl(250_80%_62%)] overflow-y-auto resize-y"
            />
            <div className="mt-2 flex items-center justify-end gap-2">
              {draftBody.trim() && (
                <button
                  onClick={() => {
                    if (!confirm("Discard this draft? Text will be cleared.")) return
                    setDraftBody("")
                    setPrefilled(true)
                  }}
                  disabled={!!busy}
                  className="inline-flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800/60 px-2.5 py-1 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40"
                  title="Clear the draft — keeps the lead open so you can re-draft"
                >
                  <XCircle className="size-3" />
                  Discard
                </button>
              )}
              <button
                onClick={onSend}
                disabled={!!busy || !draftBody.trim()}
                className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-2.5 py-1 text-xs text-white hover:bg-emerald-500 disabled:opacity-40"
              >
                {busy === "send" ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : (
                  <Send className="size-3" />
                )}
                Send reply
              </button>
            </div>
          </div>
          )}
        </>
      )}
    </section>
  )
}

function ThreadMessage({ entry }: { entry: ThreadEntry }) {
  const isInbound = entry.direction === "in"
  const tone = isInbound
    ? "border-amber-500/30 bg-amber-500/5"
    : "border-emerald-500/30 bg-emerald-500/5"
  const labelTone = isInbound ? "text-amber-300" : "text-emerald-300"
  const label = isInbound
    ? `From ${entry.from_email ?? "(unknown)"}`
    : "Your reply"
  const when = entry.at?.slice(0, 16).replace("T", " ")

  return (
    <div className={cn("rounded border p-2.5", tone)}>
      <div className="flex items-center justify-between mb-1 text-[11px]">
        <span className={cn("font-medium", labelTone)}>
          {label}
          {entry.kind && entry.kind !== "reply" && (
            <span className="ml-2 rounded bg-zinc-800 px-1 py-0.5 text-[9px] text-zinc-400 uppercase">
              {entry.kind}
            </span>
          )}
          {entry.intent && INTENT_LABEL[entry.intent] && (
            <span
              className="ml-2 inline-flex items-center gap-0.5 rounded bg-violet-500/15 px-1.5 py-0.5 text-[10px] text-violet-300"
              title={`Intent: ${entry.intent.replace(/_/g, " ")}`}
            >
              {INTENT_LABEL[entry.intent]}
            </span>
          )}
        </span>
        <span className="text-[10px] text-zinc-500 font-mono">{when}</span>
      </div>
      <div
        className="rounded border border-zinc-800/70 bg-zinc-900/50 p-2 text-xs text-zinc-200 whitespace-pre-wrap leading-relaxed overflow-y-auto resize-y"
        style={{ maxHeight: "32rem" }}
      >
        {entry.body || "(empty)"}
      </div>
    </div>
  )
}
