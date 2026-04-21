// Types + helpers for the dynamic-schema source API.

export type ColumnDescriptor = {
  key: string                // "company_name" or "extra.batch"
  label: string
  width?: number
  align?: "left" | "right" | "center"
  type?: "url" | "bool" | "date"
  badge?: boolean
  truncate?: boolean
  primary?: boolean
}

export type FilterDescriptor = {
  key: string
  type: "dropdown" | "toggle"
  label: string
  facet?: boolean
}

export type SourceDisplay = {
  icon?: string
  label?: string
  description?: string
  table_columns?: ColumnDescriptor[]
  filters?: FilterDescriptor[]
  search_fields?: string[]
  founder_display?: {
    enabled?: boolean
    expand_row?: boolean
    columns?: string[]
  }
}

export type SourceSummary = {
  leads_count?: number
  founders_count?: number
  verified_emails?: number
  companies_mailable?: number
  attention_count?: number
  emailed?: number
  replies?: number
  last_scrape?: string | null
  last_enrichment?: string | null
  last_sent?: string | null
  exists?: boolean
}

export type SourceCard = {
  id: string
  label: string
  icon: string
  description: string
  type: "grab" | "outreach"
  summary: SourceSummary
}

export type SourceDetail = {
  id: string
  type: "grab" | "outreach"
  schema: {
    source: string
    type: string
    display?: SourceDisplay
    scraper?: { path: string; default_args?: string[]; option_args?: any[] }
    enricher?: { path: string; default_args?: string[] }
  }
  summary: SourceSummary
}

export type GrabLeadRow = {
  id: number
  source: string
  source_url: string
  company_name: string
  company_domain?: string
  company_size?: string
  location?: string
  signal_type: string
  signal_detail?: string
  signal_date?: string
  person_name?: string
  person_title?: string
  person_email?: string
  scraped_at: string
  first_seen_at?: string | null
  last_seen_at?: string | null
  needs_attention?: number
  already_exported?: boolean
  is_high_value?: number
  extra: Record<string, any>
  founders?: Array<{
    lead_id: number
    full_name: string
    title?: string
    email?: string
    email_status?: string
    linkedin_url?: string
  }>
}

export type LeadsResponse = {
  rows: GrabLeadRow[]
  total: number
  mailable?: number
  limit: number
  offset: number
}

export type FacetsResponse = {
  facets: Record<string, Array<{ value: string; count: number }>>
}

export type CampaignBatch = {
  name: string
  path: string
  source?: string              // set by cross-source aggregator
  size_kb: number
  created_at: string
  total: number
  drafted: number
  in_outlook: number
  sent: number
  state: "fresh" | "drafted" | "in_outlook" | "sent" | "partial"
  error?: string
}

export type CampaignBatchesResponse = {
  source: string
  batches: CampaignBatch[]
  count: number
}

/** Resolve a dot-path key ("extra.batch") against a row. */
export function getCell(row: any, key: string): any {
  if (!row) return undefined
  if (!key.includes(".")) return row[key]
  return key
    .split(".")
    .reduce((acc: any, part) => (acc == null ? acc : acc[part]), row)
}
