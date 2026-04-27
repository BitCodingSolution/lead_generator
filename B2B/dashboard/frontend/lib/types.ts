export type Stats = {
  total_leads: number
  new_leads: number
  picked: number
  drafted: number
  total_sent: number
  sent_today: number
  total_replies: number
  replies_today: number
  positive: number
  objection: number
  neutral: number
  negative: number
  ooo: number
  bounce: number
  hot_pending: number
  reply_rate_pct: number
  positive_rate_pct: number
  daily_quota: number
  remaining_today: number
  tier1: number
  tier2: number
}

export type FunnelStage = { stage: string; count: number }

export type DailyActivity = { day: string; sent: number; replies: number }

export type IndustryRow = {
  industry: string
  total: number
  available: number
  sent: number
  tier: number | string
}

export type HotLead = {
  id: number
  lead_id: string | number
  name: string
  company: string
  industry: string
  city: string
  sentiment: string
  reply_at: string
  snippet: string
  handled: number | boolean
}

export type RecentSent = {
  sent_at: string
  lead_id: string | number
  name: string
  company: string
  industry: string
  city: string
  subject: string
  status: string
}

export type Lead = {
  id?: number
  lead_id: string | number
  name?: string
  company?: string
  industry?: string
  city?: string
  email?: string
  status?: string
  tier?: number | string
  [k: string]: unknown
}

export type LeadsResponse = {
  items: Lead[]
  total: number
}

export type EmailRow = {
  id?: number
  sent_at?: string
  subject?: string
  body?: string
  status?: string
  [k: string]: unknown
}

export type ReplyRow = {
  id: number
  lead_id: string | number
  name?: string
  company?: string
  industry?: string
  sentiment: string
  reply_at: string
  subject?: string
  body?: string
  snippet?: string
  handled: number | boolean
}

export type LeadDetail = {
  lead: Lead
  emails: EmailRow[]
  replies: ReplyRow[]
}

export type BatchRow = {
  id?: number
  created_at?: string
  industry?: string
  count?: number
  file?: string
  [k: string]: unknown
}

export type BatchFile = {
  name: string
  size_kb: number
  modified: string
}

export type Job = {
  id: string
  label: string
  status: "queued" | "running" | "done" | "error" | string
  logs: string | string[]
  returncode?: number | null
  started_at?: string
  ended_at?: string
}

export type Health = { ok: boolean; db: boolean; time: string }

// ---------- LinkedIn source ----------

export type LinkedInLead = {
  id: number
  post_url: string
  posted_by: string | null
  company: string | null
  role: string | null
  tech_stack: string | null
  location: string | null
  email: string | null
  phone: string | null
  status:
    | "New"
    | "Drafted"
    | "Queued"
    | "Sending"
    | "Sent"
    | "Replied"
    | "Bounced"
    | "Skipped"
  gen_subject: string | null
  cv_cluster: string | null
  first_seen_at: string
  last_seen_at: string
  sent_at: string | null
  replied_at: string | null
  needs_attention: 0 | 1
  call_status: "green" | "yellow" | "red" | null
  reviewed_at: string | null
  jaydip_note: string | null
  open_count: number
  first_opened_at: string | null
  last_opened_at: string | null
  scheduled_send_at: string | null
  ooo_nudge_at: string | null
  ooo_nudge_sent_at: string | null
  fit_score: number | null
  fit_score_reasons: string | null
  // True when cv_cluster points at a specialty slot (ml / ai_llm / python
  // / ...) whose CV PDF is not uploaded yet. Send would 400 — UI warns.
  cv_missing?: boolean
  // ISO timestamp the lead is snoozed until. Cleared automatically by
  // the /leads lazy sweep once the time passes.
  remind_at?: string | null
  // True when the same posted_by name has hit 3+ distinct companies in
  // the last 30 days — typical recruiter-spray signal. UI flags it so
  // Jaydip can decide whether to skip / send a different pitch.
  is_recruiter?: boolean
  // 0-100 heat score derived from opens, replies, recency, and call
  // signals. Drives "Hot" badges and an optional sort-by-temperature
  // mode in the leads table.
  temperature?: number
}

export type LinkedInLeadsResponse = {
  rows: LinkedInLead[]
  total: number
}

export type LinkedInOverview = {
  total: number
  new: number
  drafted: number
  queued: number
  sent_today: number
  replied: number
  /** Inbound replies still awaiting Jaydip's action (handled=0). Drives
   * the "X pending" sub-line on the Replied KPI card. */
  replied_pending: number
  bounced: number
  quota_used: number
  quota_cap: number
  gmail_connected: boolean
  autopilot_enabled: boolean
  safety_mode: "max" | "normal"
  warning_paused_until: string | null
}

export type LinkedInSafety = {
  daily_sent_count: number
  daily_sent_date: string | null
  last_send_at: string | null
  consecutive_failures: number
  warning_paused_until: string | null
  autopilot_enabled: boolean
  autopilot_hour: number
  autopilot_minute: number
  /** null = send the full effective cap; number caps the daily drip. */
  autopilot_count: number | null
  autopilot_tz: string
  business_hours_only: boolean
  safety_mode: "max" | "normal"
  autopilot_today: {
    fired_at: string
    total_queued: number
    status: string
  } | null
  followups_autopilot: boolean
  followups_hour: number
}

export type LinkedInGmailStatus = {
  connected: boolean
  email: string | null
  expires_at: string | null
}
