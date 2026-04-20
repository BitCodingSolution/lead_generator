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
