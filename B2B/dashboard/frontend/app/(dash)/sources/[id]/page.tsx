"use client"

import * as React from "react"
import Link from "next/link"
import { useParams, useRouter } from "next/navigation"
import useSWR, { mutate as globalMutate } from "swr"
import { toast } from "sonner"
import { api, swrFetcher } from "@/lib/api"
import type {
  ColumnDescriptor,
  FacetsResponse,
  GrabLeadRow,
  LeadsResponse,
  SourceDetail,
} from "@/lib/sources"
import { getCell } from "@/lib/sources"
import { MarcelPipelinePanel } from "@/components/marcel-pipeline-panel"
import { PageHeader } from "@/components/page-header"
import { EmptyState } from "@/components/empty-state"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Search,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  ChevronDown,
  Database,
  ExternalLink,
  ArrowLeft,
  CheckCircle2,
  XCircle,
  User,
  X as XIcon,
  Send,
} from "lucide-react"
import { fmt, relTime, cn } from "@/lib/utils"

const ALL_SENTINEL = "__all"

type FilterState = {
  search: string
  hasEmail: boolean | null
  batch: string
  industry: string
  stage: string
  teamMin: string
  teamMax: string
  topOnly: boolean
  hiringOnly: boolean
  excludeExported: boolean
  starredOnly: boolean
  attentionOnly: boolean
}

const EMPTY_FILTERS: FilterState = {
  search: "",
  hasEmail: null,
  batch: "",
  industry: "",
  stage: "",
  teamMin: "",
  teamMax: "",
  topOnly: false,
  hiringOnly: false,
  excludeExported: false,
  starredOnly: false,
  attentionOnly: false,
}

const SIGNAL_LABELS: Record<string, { label: string; tone: string }> = {
  yc_active_hiring: { label: "Hiring", tone: "emerald" },
  yc_recent_batch: { label: "Recent batch", tone: "violet" },
  yc_portfolio: { label: "Portfolio", tone: "zinc" },
}

type SortKey = "id" | "company" | "team_size" | "batch" | "scraped_at"

export default function SourceDetailPage() {
  const params = useParams<{ id: string }>()
  const id = params?.id

  // View mode: "simple" hides noise (founders stats, enrich button, per-step
  // pipeline, granular filters). "advanced" is the full kitchen sink.
  const [simpleMode, setSimpleMode] = React.useState<boolean>(true)
  React.useEffect(() => {
    const saved = typeof window !== "undefined" ? localStorage.getItem("src_simple_mode") : null
    if (saved !== null) setSimpleMode(saved === "1")
  }, [])
  React.useEffect(() => {
    if (typeof window !== "undefined")
      localStorage.setItem("src_simple_mode", simpleMode ? "1" : "0")
  }, [simpleMode])

  const [filters, setFilters] = React.useState<FilterState>(EMPTY_FILTERS)
  const [searchDebounced, setSearchDebounced] = React.useState("")
  const [page, setPage] = React.useState(0)
  const [expanded, setExpanded] = React.useState<number | null>(null)
  const [selected, setSelected] = React.useState<Set<number>>(new Set())
  const [sort, setSort] = React.useState<SortKey>("id")
  const [order, setOrder] = React.useState<"asc" | "desc">("desc")
  const limit = 50

  React.useEffect(() => {
    const t = setTimeout(() => setSearchDebounced(filters.search.trim()), 300)
    return () => clearTimeout(t)
  }, [filters.search])

  // Reset page whenever any filter (other than search being typed) changes
  const filterSig = JSON.stringify({
    ...filters,
    search: searchDebounced,
    sort,
    order,
  })
  React.useEffect(() => {
    setPage(0)
  }, [filterSig])

  const { data: detail } = useSWR<SourceDetail>(
    id ? `/api/sources/${id}` : null,
    swrFetcher,
  )

  const { data: facets } = useSWR<FacetsResponse>(
    id && detail?.type === "grab" ? `/api/sources/${id}/facets` : null,
    swrFetcher,
  )

  // In simple mode, we always restrict to mailable + not-already-exported.
  const effectiveHasEmail = simpleMode ? true : filters.hasEmail
  const effectiveExcludeExported = simpleMode ? true : filters.excludeExported

  const qs = new URLSearchParams()
  qs.set("limit", String(limit))
  qs.set("offset", String(page * limit))
  qs.set("sort", sort)
  qs.set("order", order)
  if (searchDebounced) qs.set("search", searchDebounced)
  if (effectiveHasEmail !== null) qs.set("has_email", String(effectiveHasEmail))
  if (filters.batch) qs.set("batch", filters.batch)
  if (filters.industry) qs.set("industry", filters.industry)
  if (filters.stage) qs.set("stage", filters.stage)
  if (filters.teamMin) qs.set("team_min", filters.teamMin)
  if (filters.teamMax) qs.set("team_max", filters.teamMax)
  if (filters.topOnly) qs.set("top_only", "true")
  if (filters.hiringOnly) qs.set("hiring_only", "true")
  if (effectiveExcludeExported) qs.set("exclude_exported", "true")
  if (filters.starredOnly) qs.set("starred_only", "true")
  if (filters.attentionOnly) qs.set("attention_only", "true")

  const leadsKey =
    id && detail?.type === "grab"
      ? `/api/sources/${id}/leads?${qs.toString()}`
      : null
  const { data: leadsData, isLoading } = useSWR<LeadsResponse>(
    leadsKey,
    swrFetcher,
    { keepPreviousData: true },
  )

  if (!id) return null

  const display = detail?.schema?.display || {}
  const columns = display.table_columns || []
  const rows = leadsData?.rows || []
  const total = leadsData?.total ?? 0
  const mailable = leadsData?.mailable ?? 0
  const pages = Math.max(1, Math.ceil(total / limit))
  const summary = detail?.summary || {}
  const isGrab = detail?.type === "grab"
  const anyFilter =
    filters.search !== "" ||
    filters.hasEmail !== null ||
    filters.batch !== "" ||
    filters.industry !== "" ||
    filters.stage !== "" ||
    filters.teamMin !== "" ||
    filters.teamMax !== "" ||
    filters.topOnly ||
    filters.hiringOnly ||
    filters.excludeExported

  const visibleIds = rows.map((r) => r.id)
  const selectedOnPage = visibleIds.filter((id) => selected.has(id)).length
  const allOnPageSelected =
    visibleIds.length > 0 && selectedOnPage === visibleIds.length

  function toggleOne(leadId: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(leadId)) next.delete(leadId)
      else next.add(leadId)
      return next
    })
  }

  function togglePage() {
    setSelected((prev) => {
      const next = new Set(prev)
      if (allOnPageSelected) visibleIds.forEach((id) => next.delete(id))
      else visibleIds.forEach((id) => next.add(id))
      return next
    })
  }

  function clearSelection() {
    setSelected(new Set())
  }

  function setSortColumn(key: SortKey) {
    if (sort === key) {
      setOrder((o) => (o === "asc" ? "desc" : "asc"))
    } else {
      setSort(key)
      setOrder("asc")
    }
  }

  function toggleHeaderSort(col: ColumnDescriptor) {
    if (col.key === "company_name") setSortColumn("company")
    else if (col.key === "extra.team_size") setSortColumn("team_size")
    else if (col.key === "extra.batch") setSortColumn("batch")
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs text-zinc-500">
        <Link
          href="/sources"
          className="flex items-center gap-1 hover:text-zinc-300 transition-colors"
        >
          <ArrowLeft className="size-3" /> Sources
        </Link>
        <span>/</span>
        <span className="text-zinc-300">{display.label || id}</span>
      </div>

      <div className="flex items-center justify-between gap-3 flex-wrap">
        <PageHeader title={display.label || id} subtitle={display.description} />
        {isGrab && (
          <div className="flex items-center gap-1 rounded-md border border-zinc-800/80 bg-zinc-900/40 p-0.5 text-[11px]">
            <button
              onClick={() => setSimpleMode(true)}
              className={cn(
                "px-2.5 py-1 rounded transition-colors",
                simpleMode
                  ? "bg-[hsl(250_80%_62%/0.2)] text-[hsl(250_80%_82%)]"
                  : "text-zinc-500 hover:text-zinc-300",
              )}
              title="Essentials only"
            >
              Simple
            </button>
            <button
              onClick={() => setSimpleMode(false)}
              className={cn(
                "px-2.5 py-1 rounded transition-colors",
                !simpleMode
                  ? "bg-[hsl(250_80%_62%/0.2)] text-[hsl(250_80%_82%)]"
                  : "text-zinc-500 hover:text-zinc-300",
              )}
              title="Full controls, all stats, granular buttons"
            >
              Advanced
            </button>
          </div>
        )}
      </div>

      {/* =================== LEAD POOL =================== */}
      {isGrab && (
        <SectionHeader
          title={simpleMode ? "Find Leads" : "Lead Pool"}
          subtitle={
            simpleMode
              ? "Collect ready-to-mail companies and pick which ones go to a campaign. Batches land in the Campaigns tab."
              : "Scrape fresh companies, enrich their founders, filter to your ICP."
          }
          icon={<Database className="size-4" />}
        />
      )}

      {/* Stats row */}
      {isGrab ? (
        simpleMode ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatBox
              label="Companies ready"
              value={fmt(summary.companies_mailable || 0)}
              emphasis
              hint="Companies with at least one verified founder email"
            />
            <StatBox
              label="Emails total"
              value={fmt(summary.verified_emails || 0)}
              hint="Total mailable founders across all ready companies (= actual emails if you pick everyone)"
            />
            <StatBox label="Collected" value={fmt(summary.leads_count || 0)} />
            <StatBox
              label="Last collect"
              value={summary.last_scrape ? relTime(summary.last_scrape) : "—"}
            />
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <StatBox label="Leads" value={fmt(summary.leads_count || 0)} />
            <StatBox label="Founders" value={fmt(summary.founders_count || 0)} />
            <StatBox
              label="Verified Emails"
              value={fmt(summary.verified_emails || 0)}
              emphasis
            />
            <StatBox
              label="Last Scrape"
              value={summary.last_scrape ? relTime(summary.last_scrape) : "—"}
            />
            <StatBox
              label="Last Enrichment"
              value={summary.last_enrichment ? relTime(summary.last_enrichment) : "—"}
            />
          </div>
        )
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <StatBox label="Leads" value={fmt(summary.leads_count || 0)} />
          <StatBox label="Emailed" value={fmt(summary.emailed || 0)} />
          <StatBox label="Replies" value={fmt(summary.replies || 0)} />
          <StatBox
            label="Last Sent"
            value={summary.last_sent ? relTime(summary.last_sent) : "—"}
          />
          <StatBox label="" value="" />
        </div>
      )}

      {isGrab && <JobProgressBanner sourceId={id} />}

      {/* Action bar */}
      {isGrab && (
        <ActionBar
          sourceId={id}
          selected={selected}
          simpleMode={simpleMode}
          onClearSelection={clearSelection}
          onMutate={() => {
            globalMutate(`/api/sources/${id}`)
            globalMutate(
              (k) =>
                typeof k === "string" && k.startsWith(`/api/sources/${id}/leads?`),
            )
            globalMutate(`/api/sources/${id}/facets`)
            globalMutate(`/api/sources/${id}/batches`)
          }}
        />
      )}

      {!isGrab && <MarcelPipelinePanel />}

      {/* Filters + table */}
      {isGrab && (
        <div className="grid grid-cols-1 lg:grid-cols-[250px_minmax(0,1fr)] gap-6">
          <aside className="space-y-5 lg:sticky lg:top-20 h-fit">
            <FilterSection label="Search">
              <div className="relative">
                <Search className="size-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500" />
                <Input
                  value={filters.search}
                  onChange={(e) =>
                    setFilters((f) => ({ ...f, search: e.target.value }))
                  }
                  placeholder="Company, domain, tagline…"
                  className="pl-8 bg-zinc-900/40 border-zinc-800"
                />
              </div>
            </FilterSection>

            {!simpleMode && (
              <FilterSection label="Has verified email">
                <div className="flex gap-1.5">
                  <TogglePill
                    label="Any"
                    active={filters.hasEmail === null}
                    onClick={() =>
                      setFilters((f) => ({ ...f, hasEmail: null }))
                    }
                  />
                  <TogglePill
                    label="Yes"
                    active={filters.hasEmail === true}
                    onClick={() =>
                      setFilters((f) => ({ ...f, hasEmail: true }))
                    }
                  />
                  <TogglePill
                    label="No"
                    active={filters.hasEmail === false}
                    onClick={() =>
                      setFilters((f) => ({ ...f, hasEmail: false }))
                    }
                  />
                </div>
              </FilterSection>
            )}

            <FilterSection label="Team size (ICP 50–500)">
              <div className="flex gap-2">
                <Input
                  type="number"
                  placeholder="Min"
                  value={filters.teamMin}
                  onChange={(e) =>
                    setFilters((f) => ({ ...f, teamMin: e.target.value }))
                  }
                  className="bg-zinc-900/40 border-zinc-800 tnum"
                />
                <Input
                  type="number"
                  placeholder="Max"
                  value={filters.teamMax}
                  onChange={(e) =>
                    setFilters((f) => ({ ...f, teamMax: e.target.value }))
                  }
                  className="bg-zinc-900/40 border-zinc-800 tnum"
                />
              </div>
              <div className="mt-1.5 flex gap-1">
                <button
                  onClick={() =>
                    setFilters((f) => ({ ...f, teamMin: "50", teamMax: "500" }))
                  }
                  className="text-[10px] text-zinc-500 hover:text-zinc-200 underline-offset-2 hover:underline"
                >
                  ICP 50–500
                </button>
                <span className="text-[10px] text-zinc-700">·</span>
                <button
                  onClick={() =>
                    setFilters((f) => ({ ...f, teamMin: "20", teamMax: "200" }))
                  }
                  className="text-[10px] text-zinc-500 hover:text-zinc-200 underline-offset-2 hover:underline"
                >
                  Small 20–200
                </button>
              </div>
            </FilterSection>

            <FacetDropdown
              label="Batch"
              value={filters.batch}
              buckets={facets?.facets?.["extra.batch"] || []}
              onChange={(v) => setFilters((f) => ({ ...f, batch: v }))}
            />
            <FacetDropdown
              label="Industry"
              value={filters.industry}
              buckets={facets?.facets?.["extra.industry"] || []}
              onChange={(v) => setFilters((f) => ({ ...f, industry: v }))}
            />
            <FacetDropdown
              label="Stage"
              value={filters.stage}
              buckets={facets?.facets?.["extra.stage"] || []}
              onChange={(v) => setFilters((f) => ({ ...f, stage: v }))}
            />

            <FilterSection label="Signals">
              <div className="flex flex-col gap-1.5">
                <CheckToggle
                  checked={filters.attentionOnly}
                  onChange={(v) =>
                    setFilters((f) => ({ ...f, attentionOnly: v }))
                  }
                  label="🆕 Needs attention (new / changed)"
                />
                <CheckToggle
                  checked={filters.hiringOnly}
                  onChange={(v) =>
                    setFilters((f) => ({ ...f, hiringOnly: v }))
                  }
                  label="Hiring only"
                />
                <CheckToggle
                  checked={filters.starredOnly}
                  onChange={(v) =>
                    setFilters((f) => ({ ...f, starredOnly: v }))
                  }
                  label="⭐ Starred only"
                />
                <CheckToggle
                  checked={filters.topOnly}
                  onChange={(v) => setFilters((f) => ({ ...f, topOnly: v }))}
                  label="Top companies only"
                />
                {!simpleMode && (
                  <CheckToggle
                    checked={filters.excludeExported}
                    onChange={(v) =>
                      setFilters((f) => ({ ...f, excludeExported: v }))
                    }
                    label="Exclude already-exported"
                  />
                )}
              </div>
            </FilterSection>
            {simpleMode && (
              <div className="text-[10px] text-zinc-600 italic">
                Showing only leads with a verified founder email, not yet in a
                campaign.
              </div>
            )}

            {anyFilter && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setFilters(EMPTY_FILTERS)}
                className="w-full text-zinc-400 hover:text-zinc-200"
              >
                Clear filters
              </Button>
            )}
          </aside>

          <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] min-w-0">
            {selected.size > 0 && (
              <SelectionBar
                sourceId={id}
                selected={selected}
                simpleMode={simpleMode}
                selectedEmailCount={rows.reduce((acc, r) => {
                  if (!selected.has(r.id)) return acc
                  const verified = (r.founders || []).filter(
                    (f) => f.email_status === "ok",
                  ).length
                  return acc + verified
                }, 0)}
                onClear={clearSelection}
                onDone={() => {
                  clearSelection()
                  globalMutate(
                    (k) =>
                      typeof k === "string" &&
                      k.startsWith(`/api/sources/${id}/leads?`),
                  )
                  globalMutate(`/api/sources/${id}`)
                }}
              />
            )}
            <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800/70">
              <div className="text-sm text-zinc-400 flex items-center gap-2">
                {isLoading ? (
                  <span className="text-zinc-500">Loading…</span>
                ) : simpleMode ? (
                  <span className="flex items-center gap-2 flex-wrap">
                    <span>
                      <span className="text-zinc-200 tnum font-medium">
                        {fmt(total)}
                      </span>
                      <span className="text-zinc-500">
                        {" "}compan{total === 1 ? "y" : "ies"} ready
                      </span>
                    </span>
                    <span className="text-zinc-700">·</span>
                    <span
                      title="Actual emails that will be sent if you pick all these companies"
                      className="inline-flex items-center gap-1"
                    >
                      <span className="size-1.5 rounded-full bg-[hsl(250_80%_70%)]" />
                      <span className="text-[hsl(250_80%_82%)] tnum font-medium">
                        {fmt(mailable ? (summary.verified_emails || 0) : 0)}
                      </span>
                      <span className="text-zinc-500">emails</span>
                    </span>
                  </span>
                ) : (
                  <>
                    <span>
                      <span className="text-zinc-200 tnum font-medium">
                        {fmt(total)}
                      </span>
                      <span className="text-zinc-500">
                        {" "}lead{total === 1 ? "" : "s"}
                      </span>
                    </span>
                    <span className="text-zinc-700">·</span>
                    <span
                      title="Leads with at least one verified founder email (mailable now)"
                      className="inline-flex items-center gap-1"
                    >
                      <span className="size-1.5 rounded-full bg-emerald-500" />
                      <span className="text-emerald-400 tnum font-medium">
                        {fmt(mailable)}
                      </span>
                      <span className="text-zinc-500">mailable</span>
                    </span>
                    {total > mailable && (
                      <>
                        <span className="text-zinc-700">·</span>
                        <span
                          title="Leads without a verified founder email — run Enrich Missing"
                          className="inline-flex items-center gap-1"
                        >
                          <span className="size-1.5 rounded-full bg-zinc-600" />
                          <span className="text-zinc-400 tnum">
                            {fmt(total - mailable)}
                          </span>
                          <span className="text-zinc-500">need enrich</span>
                        </span>
                      </>
                    )}
                  </>
                )}
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                  aria-label="Previous page"
                  className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/60 disabled:opacity-40 disabled:pointer-events-none"
                >
                  <ChevronLeft className="size-4" />
                </button>
                <span className="text-xs text-zinc-400 tnum px-2">
                  {page + 1} / {pages}
                </span>
                <button
                  onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}
                  disabled={page >= pages - 1}
                  aria-label="Next page"
                  className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/60 disabled:opacity-40 disabled:pointer-events-none"
                >
                  <ChevronRight className="size-4" />
                </button>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-zinc-900/40 text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                  <tr className="border-b border-zinc-800/70">
                    <th className="w-[36px] px-3 py-2.5">
                      <Checkbox
                        checked={allOnPageSelected}
                        onChange={togglePage}
                        aria-label="Select all on page"
                      />
                    </th>
                    <th className="w-[30px] px-1 py-2.5 text-center" title="Star high-value leads">
                      <span className="text-zinc-600">★</span>
                    </th>
                    {!simpleMode && (
                      <th className="w-[64px] px-2 py-2.5 text-center font-medium">
                        People
                      </th>
                    )}
                    {columns.map((c) => {
                      const sortable =
                        c.key === "company_name" ||
                        c.key === "extra.team_size" ||
                        c.key === "extra.batch"
                      const active =
                        (c.key === "company_name" && sort === "company") ||
                        (c.key === "extra.team_size" && sort === "team_size") ||
                        (c.key === "extra.batch" && sort === "batch")
                      return (
                        <th
                          key={c.key}
                          style={c.width ? { width: c.width } : undefined}
                          onClick={() => sortable && toggleHeaderSort(c)}
                          className={cn(
                            "font-medium px-4 py-2.5 text-left",
                            c.align === "right" && "text-right",
                            c.align === "center" && "text-center",
                            sortable &&
                              "cursor-pointer select-none hover:text-zinc-300",
                          )}
                        >
                          <span className="inline-flex items-center gap-1">
                            {c.label}
                            {sortable && active && (
                              order === "asc" ? (
                                <ChevronUp className="size-3" />
                              ) : (
                                <ChevronDown className="size-3" />
                              )
                            )}
                          </span>
                        </th>
                      )
                    })}
                    <th className="w-[56px] px-3 py-2.5 text-right">Links</th>
                  </tr>
                </thead>
                <tbody>
                  {isLoading && rows.length === 0
                    ? Array.from({ length: 8 }).map((_, i) => (
                        <tr key={i} className="border-b border-zinc-800/60">
                          <td
                            colSpan={columns.length + (simpleMode ? 3 : 4)}
                            className="px-4 py-3"
                          >
                            <div className="skeleton h-4 w-full" />
                          </td>
                        </tr>
                      ))
                    : rows.map((row) => {
                        const isSel = selected.has(row.id)
                        const isExp = row.already_exported
                        return (
                          <React.Fragment key={row.id}>
                            <tr
                              onClick={() =>
                                setExpanded((e) =>
                                  e === row.id ? null : row.id,
                                )
                              }
                              className={cn(
                                "border-b border-zinc-800/60 hover:bg-zinc-800/40 cursor-pointer transition-colors",
                                isSel && "bg-[hsl(250_80%_62%/0.08)]",
                                isExp && "opacity-60",
                              )}
                            >
                              <td
                                className="px-3 py-2.5"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  toggleOne(row.id)
                                }}
                              >
                                <Checkbox
                                  checked={isSel}
                                  onChange={() => toggleOne(row.id)}
                                />
                              </td>
                              <td
                                className="px-1 py-2.5 text-center"
                                onClick={(e) => e.stopPropagation()}
                              >
                                <StarButton
                                  sourceId={id}
                                  leadId={row.id}
                                  starred={!!row.is_high_value}
                                />
                              </td>
                              {!simpleMode && (
                                <td className="px-2 py-2.5 text-center">
                                  <PeopleChip row={row} />
                                </td>
                              )}
                              {columns.map((c) => (
                                <td
                                  key={c.key}
                                  className={cn(
                                    "px-4 py-2.5",
                                    c.align === "right" && "text-right",
                                    c.align === "center" && "text-center",
                                    c.truncate && "max-w-0 truncate",
                                  )}
                                >
                                  <Cell row={row} col={c} />
                                </td>
                              ))}
                              <td className="px-3 py-2.5 text-right">
                                <RowLinks row={row} />
                              </td>
                            </tr>
                            {expanded === row.id && (
                              <tr className="border-b border-zinc-800/60 bg-zinc-900/30">
                                <td
                                  colSpan={columns.length + (simpleMode ? 3 : 4)}
                                  className="px-4 py-3"
                                >
                                  <FoundersPanel row={row} />
                                </td>
                              </tr>
                            )}
                          </React.Fragment>
                        )
                      })}
                </tbody>
              </table>
              {!isLoading && rows.length === 0 && (
                <div className="p-8">
                  <EmptyState
                    icon={<Database className="size-5" />}
                    title="No leads match"
                    hint={
                      anyFilter
                        ? "Try removing some filters."
                        : "Run the scraper to collect data."
                    }
                  />
                </div>
              )}
            </div>
          </div>
        </div>
      )}

    </div>
  )
}

// ---- Section header ----
function SectionHeader({
  title,
  subtitle,
  icon,
}: {
  title: string
  subtitle: string
  icon: React.ReactNode
}) {
  return (
    <div className="flex items-start gap-3 pb-2 border-b border-zinc-800/60">
      <div className="flex items-center gap-2">
        <span className="text-[hsl(250_80%_82%)]">{icon}</span>
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-base font-semibold tracking-tight text-zinc-100">
          {title}
        </div>
        <div className="text-xs text-zinc-500 mt-0.5">{subtitle}</div>
      </div>
    </div>
  )
}

// ---- JobProgressBanner: shows live scrape/enrich progress at top of page ----
type LastRun = {
  exists: boolean
  kind?: string
  label?: string
  job_id?: string
  status?: string
  started_at?: string
  progress?: {
    current: number | null
    total: number | null
    percent: number | null
    unit: string
    last_line: string
  } | null
}

type JobDetail = {
  id: string
  label: string
  status: string
  logs: string[]
  step_total?: number
  step_index?: number
  step_label?: string
}

// Rough typical duration (seconds) per step label — used to interpolate a
// smooth progress bar between discrete step transitions when the backend
// doesn't emit sub-progress. Tunable; err on the long side so the bar
// reaches ~95% of its step right as real completion arrives.
function _typicalStepSecs(label: string): number {
  const l = label.toLowerCase()
  if (l.includes("enrich")) return 90
  if (l.includes("scrape") || l.includes("collect")) return 25
  if (l.includes("draft") || l.includes("claude")) return 30
  if (l.includes("outlook")) return 15
  if (l.includes("export")) return 5
  return 20
}

function JobProgressBanner({ sourceId }: { sourceId: string }) {
  const { data: last } = useSWR<LastRun>(
    `/api/sources/${sourceId}/last-run`,
    swrFetcher,
    { refreshInterval: 1500 },
  )
  const jobId = last?.job_id
  // Pull the full job so we can show step_index/total for chains.
  const { data: job } = useSWR<JobDetail>(
    jobId ? `/api/jobs/${jobId}` : null,
    swrFetcher,
    { refreshInterval: 1200 },
  )
  const isRunning =
    last?.exists && (last.status === "running" || last.status === "queued")

  const stepTotal = job?.step_total || 0
  const stepIdx = job?.step_index || 0
  const stepLabel = job?.step_label || ""
  const lastLog =
    (job?.logs || []).slice(-1)[0] || last?.progress?.last_line || ""
  const p = last?.progress
  const subPercent = p?.percent ?? null

  // --- Smooth time-based interpolation ---
  // When a step transitions, snap base then tick toward the next step's
  // boundary at (elapsed / typical). Capped at 95% of the step so the bar
  // doesn't overshoot before the real completion arrives.
  const [smoothedPct, setSmoothedPct] = React.useState(0)
  const stepStartRef = React.useRef<{ idx: number; at: number } | null>(null)

  React.useEffect(() => {
    if (!isRunning || stepTotal === 0) {
      stepStartRef.current = null
      return
    }
    if (!stepStartRef.current || stepStartRef.current.idx !== stepIdx) {
      stepStartRef.current = { idx: stepIdx, at: Date.now() }
    }
    const tick = () => {
      const sz = 100 / stepTotal
      const base = Math.max(0, stepIdx - 1) * sz
      const startedAt = stepStartRef.current?.at ?? Date.now()
      const elapsed = (Date.now() - startedAt) / 1000
      const typical = _typicalStepSecs(stepLabel)
      // Prefer backend sub-progress when available; else time-based guess.
      const frac =
        subPercent !== null
          ? Math.min(1, subPercent / 100)
          : Math.min(0.95, elapsed / typical)
      setSmoothedPct(Math.min(100, base + frac * sz))
    }
    tick()
    const iv = setInterval(tick, 150)
    return () => clearInterval(iv)
  }, [isRunning, stepTotal, stepIdx, stepLabel, subPercent])

  if (!isRunning) return null

  const kindLabel =
    last?.kind === "collect"
      ? "Collecting"
      : last?.kind === "campaign"
        ? "Sending to campaign"
        : last?.kind === "scrape"
          ? "Scraping"
          : last?.kind === "enrich"
            ? "Enriching"
            : last?.kind || "Running"

  const chainPercent = stepTotal > 0 ? Math.round(smoothedPct) : subPercent

  async function stop() {
    if (!jobId) return
    try {
      await api.post(`/api/jobs/${jobId}/stop`, {})
      toast.info("Stop requested")
    } catch (e) {
      toast.error("Stop failed", { description: String((e as Error).message) })
    }
  }

  return (
    <div className="rounded-xl border border-[hsl(250_80%_62%/0.4)] bg-gradient-to-r from-[hsl(250_80%_62%/0.08)] to-[hsl(270_90%_65%/0.05)] px-5 py-4 shadow-[0_0_0_1px_rgba(160,120,255,0.08)]">
      <div className="flex items-center gap-3">
        <div className="size-2 rounded-full bg-[hsl(250_80%_70%)] animate-pulse" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="text-sm text-zinc-100 font-medium tracking-tight">
              {kindLabel}…
              {stepTotal > 0 && (
                <span className="ml-2 text-[hsl(250_80%_82%)] tnum font-normal">
                  step {stepIdx}/{stepTotal}
                  {stepLabel && (
                    <span className="text-zinc-400">: {stepLabel}</span>
                  )}
                </span>
              )}
              {p?.current !== null && p?.total && (
                <span className="ml-2 text-zinc-500 tnum font-normal">
                  ({p.current}/{p.total} {p.unit})
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              {chainPercent !== null && (
                <span className="text-xs text-[hsl(250_80%_82%)] tnum font-medium">
                  {chainPercent}%
                </span>
              )}
              <Button
                size="sm"
                variant="outline"
                onClick={stop}
                className="border-amber-800/50 text-amber-300 hover:bg-amber-950/40 h-7 px-2.5"
              >
                Stop
              </Button>
            </div>
          </div>
          <div className="mt-2 h-1.5 w-full rounded-full bg-zinc-900 overflow-hidden">
            <div
              className={cn(
                "h-full rounded-full transition-[width] duration-200 ease-linear",
                stepTotal > 0
                  ? "bg-gradient-to-r from-[hsl(250_80%_62%)] to-[hsl(270_90%_72%)]"
                  : "bg-[hsl(250_80%_62%)] animate-pulse w-1/3",
              )}
              style={
                stepTotal > 0
                  ? { width: `${smoothedPct.toFixed(2)}%` }
                  : undefined
              }
            />
          </div>
          {lastLog && (
            <div className="mt-1.5 text-[11px] text-zinc-500 font-mono truncate">
              {lastLog}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}




// ---- ActionBar: Scrape / Enrich (Add-to-campaign moved into SelectionBar) ----
type Job = {
  id: string
  label: string
  status: "queued" | "running" | "done" | "error" | "stopped"
  logs: string[]
  returncode?: number
}

function ActionBar({
  sourceId,
  selected,
  simpleMode,
  onClearSelection,
  onMutate,
}: {
  sourceId: string
  selected: Set<number>
  simpleMode: boolean
  onClearSelection: () => void
  onMutate: () => void
}) {
  const [busy, setBusy] = React.useState<null | "scrape" | "enrich">(null)
  const [currentJobId, setCurrentJobId] = React.useState<string | null>(null)

  const { data: lastRun, mutate: mutateLastRun } = useSWR<{
    exists: boolean
    kind?: string
    label?: string
    job_id?: string
    status?: string
    started_at?: string
  }>(`/api/sources/${sourceId}/last-run`, swrFetcher, { refreshInterval: 4000 })

  const canResume =
    lastRun?.exists &&
    (lastRun.status === "stopped" || lastRun.status === "error") &&
    !busy

  async function pollJob(jobId: string, label: string) {
    const started = Date.now()
    const toastId = toast.loading(`${label} starting…`)
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
          onMutate()
          setBusy(null)
        } else {
          stopped = true
          toast.error(`${label} failed`, {
            id: toastId,
            description: last.slice(0, 200),
          })
          setBusy(null)
        }
      } catch (e) {
        stopped = true
        toast.error(`${label} polling error`, {
          id: toastId,
          description: String((e as Error).message),
        })
        setBusy(null)
      }
    }
    setTimeout(tick, 400)
  }

  async function waitForJob(jobId: string, label: string): Promise<string> {
    // Resolves with last log line when job is done; rejects on error or stop.
    setCurrentJobId(jobId)
    const toastId = toast.loading(`${label} starting…`)
    const started = Date.now()
    try {
      return await new Promise<string>((resolve, reject) => {
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
              resolve(last)
            } else if (j.status === "stopped") {
              stopped = true
              toast.warning(`${label} stopped — can resume later`, { id: toastId })
              reject(new Error("stopped"))
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
    } finally {
      setCurrentJobId(null)
      mutateLastRun()
    }
  }

  async function onScrape() {
    if (busy) return
    const limStr = window.prompt(
      "How many companies to collect? (scrape + enrich run as ONE server-side job — browser refresh safe)",
      "100",
    )
    if (limStr === null) return
    const limit = limStr.trim()
      ? Math.max(1, Math.min(2000, parseInt(limStr, 10) || 100))
      : null
    setBusy("scrape")
    try {
      // Server-side pipeline: scrape → enrich chain in ONE job_id.
      const res = await api.post<{ job_id: string; steps: string[] }>(
        `/api/sources/${sourceId}/collect`,
        { args: limit ? { limit } : {} },
      )
      await waitForJob(res.job_id, "Collect")
      onMutate()
      toast.success("Collect complete — ready to campaign")
    } catch {
      // errors already surfaced via toasts; backend job may still be live
    } finally {
      setBusy(null)
    }
  }

  async function onEnrich() {
    if (busy) return
    const limStr = window.prompt(
      "How many missing leads to enrich? (leave blank for all)",
      "50",
    )
    if (limStr === null) return
    const limit = limStr.trim()
      ? Math.max(1, Math.min(500, parseInt(limStr, 10) || 50))
      : null
    setBusy("enrich")
    try {
      const r = await api.post<{ job_id: string }>(
        `/api/sources/${sourceId}/enrich`,
        { args: limit ? { limit } : {} },
      )
      pollJob(r.job_id, "Enrich")
    } catch (e) {
      toast.error("Failed to start enrich", {
        description: String((e as Error).message),
      })
      setBusy(null)
    }
  }

  async function onStop() {
    if (!currentJobId) return
    try {
      await api.post(`/api/jobs/${currentJobId}/stop`, {})
      toast.info("Stop requested — job will terminate shortly")
    } catch (e) {
      toast.error("Stop failed", { description: String((e as Error).message) })
    }
  }

  async function onResume() {
    if (busy) return
    setBusy((lastRun?.kind as any) || "scrape")
    try {
      const r = await api.post<{ job_id: string }>(
        `/api/sources/${sourceId}/resume-last`,
        {},
      )
      await waitForJob(r.job_id, `Resume ${lastRun?.kind || ""}`)
      onMutate()
    } catch {
      // toasts already shown
    } finally {
      setBusy(null)
    }
  }

  async function onResetAll() {
    if (busy) return
    const ok = window.confirm(
      `⚠️ DELETE ALL data for this source?\n\n` +
        `This will permanently remove:\n` +
        `  • Leads DB (companies + founders + exported marks)\n` +
        `  • Raw scrape dumps\n` +
        `  • All campaign batch files from this source\n` +
        `  • Log files\n\n` +
        `Outlook drafts are NOT touched here — use Clear Drafts separately if needed.\n\n` +
        `Continue?`,
    )
    if (!ok) return
    const typed = window.prompt(`Type RESET to confirm.`)
    if (typed !== "RESET") {
      toast.info("Reset cancelled")
      return
    }
    setBusy("scrape") // reuse a busy state
    try {
      const r = await api.post<{ removed: Record<string, unknown> }>(
        `/api/sources/${sourceId}/reset-all`,
      )
      toast.success("Source reset complete", {
        description: JSON.stringify(r.removed),
      })
      onMutate()
    } catch (e) {
      toast.error("Reset failed", {
        description: String((e as Error).message),
      })
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] px-4 py-3 flex flex-wrap gap-2 items-center">
      <span className="text-xs uppercase tracking-[0.12em] text-zinc-500 mr-2">
        Actions
      </span>
      <Button size="sm" onClick={onScrape} disabled={!!busy}>
        {busy === "scrape"
          ? simpleMode ? "Collecting…" : "Scraping…"
          : busy === "enrich"
            ? "Preparing…"
            : simpleMode ? "⚡ Collect & Prepare" : "Scrape Fresh"}
      </Button>
      {!simpleMode && (
        <Button
          size="sm"
          variant="secondary"
          onClick={onEnrich}
          disabled={!!busy}
        >
          {busy === "enrich" ? "Enriching…" : "Enrich Missing"}
        </Button>
      )}
      {currentJobId && (
        <Button
          size="sm"
          variant="outline"
          onClick={onStop}
          className="border-amber-800/50 text-amber-300 hover:bg-amber-950/40"
          title="Stop the running job"
        >
          Stop
        </Button>
      )}
      {canResume && (
        <Button
          size="sm"
          variant="outline"
          onClick={onResume}
          className="border-[hsl(250_80%_62%/0.35)] text-[hsl(250_80%_78%)] hover:bg-[hsl(250_80%_62%/0.1)]"
          title={`Resume last ${lastRun?.kind || "job"} (${lastRun?.status})`}
        >
          Resume {lastRun?.kind}
        </Button>
      )}
      <Button
        size="sm"
        variant="ghost"
        onClick={onResetAll}
        disabled={!!busy}
        title="Delete all data for this source (dev/testing only)"
        className="text-zinc-500 hover:text-red-400"
      >
        Reset all
      </Button>
      <AutoRunToggle sourceId={sourceId} />
      <div className="ml-auto text-xs text-zinc-500">
        {selected.size > 0 ? (
          <span>
            {selected.size} selected{" "}
            <button
              onClick={onClearSelection}
              className="underline-offset-2 hover:underline text-zinc-400 hover:text-zinc-200"
            >
              clear
            </button>
          </span>
        ) : (
          <span className="text-zinc-600">
            Tick rows below to build a campaign batch
          </span>
        )}
      </div>
    </div>
  )
}

// ---- AutoRunToggle: daily auto-scrape switch per source ----
type AutoRun = {
  enabled: boolean
  hour: number
  minute: number
  last_fired_at?: string
  last_fired_date?: string
  next_fire?: string | null
}

function AutoRunToggle({ sourceId }: { sourceId: string }) {
  const { data, mutate, isLoading } = useSWR<AutoRun>(
    `/api/sources/${sourceId}/auto-run`,
    swrFetcher,
    { refreshInterval: 30000 },
  )
  const [saving, setSaving] = React.useState(false)
  const enabled = !!data?.enabled
  const hh = String(data?.hour ?? 2).padStart(2, "0")
  const mm = String(data?.minute ?? 0).padStart(2, "0")

  async function save(next: Partial<AutoRun>) {
    // Guard: avoid clobbering persisted hh/mm with defaults if data hasn't
    // loaded yet. Better to no-op than silently save the wrong values.
    if (!data && next.enabled !== undefined) {
      toast.warning("Still loading auto-run config — try again in a moment")
      return
    }
    setSaving(true)
    try {
      const body = {
        enabled: next.enabled ?? enabled,
        hour: next.hour ?? data?.hour ?? 2,
        minute: next.minute ?? data?.minute ?? 0,
      }
      await api.post(`/api/sources/${sourceId}/auto-run`, body)
      await mutate()
      toast.success(
        body.enabled
          ? `Auto-scrape ON (daily ${String(body.hour).padStart(2, "0")}:${String(body.minute).padStart(2, "0")})`
          : "Auto-scrape OFF",
      )
    } catch (e) {
      toast.error("Save failed", { description: String((e as Error).message) })
    } finally {
      setSaving(false)
    }
  }

  const nextHint = React.useMemo(() => {
    if (!enabled || !data?.next_fire) return ""
    const d = new Date(data.next_fire)
    if (Number.isNaN(d.getTime())) return ""
    const today = new Date()
    const isTomorrow =
      d.getDate() !== today.getDate() || d.getMonth() !== today.getMonth()
    const time = d.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    })
    return isTomorrow ? `next: tomorrow ${time}` : `next: today ${time}`
  }, [enabled, data?.next_fire])

  return (
    <button
      onClick={() => save({ enabled: !enabled })}
      disabled={saving || isLoading}
      title={
        enabled
          ? `Daily auto-scrape runs at ${hh}:${mm}. Click to disable.`
          : `Click to enable daily auto-scrape at ${hh}:${mm}.`
      }
      className={cn(
        "inline-flex items-center gap-2 rounded-md border px-2.5 h-8 text-[11px] transition-colors",
        enabled
          ? "border-emerald-700/50 bg-emerald-950/30 text-emerald-300 hover:bg-emerald-950/50"
          : "border-zinc-800 bg-zinc-900/40 text-zinc-500 hover:text-zinc-300 hover:border-zinc-700",
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-full",
          enabled ? "bg-emerald-400 shadow-[0_0_6px_rgba(16,185,129,0.7)]" : "bg-zinc-600",
        )}
      />
      <span className="font-medium">
        Auto-scrape {enabled ? "ON" : "OFF"}
      </span>
      <span className="text-zinc-500 tnum">
        {hh}:{mm}
      </span>
      {nextHint && <span className="text-zinc-500">· {nextHint}</span>}
    </button>
  )
}

// ---- Selection bar (appears above table when selection > 0) ----
type SelectionCheck = {
  total: number
  ready: number[]
  needs_enrichment: number[]
  no_founders: number[]
}

function SelectionBar({
  sourceId,
  selected,
  simpleMode,
  selectedEmailCount,
  onClear,
  onDone,
}: {
  sourceId: string
  selected: Set<number>
  simpleMode: boolean
  selectedEmailCount: number
  onClear: () => void
  onDone: () => void
}) {
  const [busy, setBusy] = React.useState<
    null | "check" | "enrich" | "export" | "drafts" | "outlook"
  >(null)
  const [pending, setPending] = React.useState<SelectionCheck | null>(null)
  const router = useRouter()

  async function waitJob(jobId: string, label: string, toastId: string | number) {
    const started = Date.now()
    // eslint-disable-next-line no-constant-condition
    while (true) {
      await new Promise((r) => setTimeout(r, 1500))
      const j = await api.get<Job>(`/api/jobs/${jobId}`)
      const last = j.logs?.[j.logs.length - 1] || ""
      if (j.status === "done") {
        const secs = Math.round((Date.now() - started) / 1000)
        toast.loading(`${label} done in ${secs}s`, { id: toastId })
        return
      }
      if (j.status === "stopped") throw new Error("stopped")
      if (j.status === "error") throw new Error(last || "error")
      toast.loading(`${label} — ${last.slice(0, 80) || j.status}`, {
        id: toastId,
      })
    }
  }

  async function doExport(ids: number[]) {
    setBusy("export")
    const toastId = toast.loading(
      simpleMode
        ? "Preparing campaign (export → drafts → Outlook)…"
        : "Exporting…",
    )
    try {
      if (simpleMode) {
        // Server-side pipeline: export → drafts → outlook as ONE job
        const r = await api.post<{ job_id: string; steps: string[] }>(
          `/api/sources/${sourceId}/campaign`,
          { lead_ids: ids, max: ids.length },
        )
        try {
          await waitJob(r.job_id, "Campaign", toastId)
          toast.success(`${ids.length} drafts ready in Outlook`, {
            id: toastId,
            description: "Open Campaigns to manage & send this batch.",
            duration: 10000,
            action: {
              label: "Open Campaigns",
              onClick: () => router.push("/campaigns"),
            },
          })
        } catch (e) {
          toast.error("Pipeline interrupted — job may still be running server-side", {
            id: toastId,
            description: String((e as Error).message),
          })
        }
      } else {
        // Advanced mode: export only, user runs drafts/outlook manually
        const r = await api.post<{
          rows: number
          file_name: string
          next_step: string
        }>(`/api/sources/${sourceId}/export-batch`, {
          lead_ids: ids,
          max: ids.length,
        })
        toast.success(`Exported ${r.rows} leads`, {
          id: toastId,
          description: `${r.file_name} — open Campaigns to run drafts → Outlook → send.`,
          duration: 8000,
          action: {
            label: "Open Campaigns",
            onClick: () => router.push(`/campaigns?batch=${encodeURIComponent(r.file_name)}`),
          },
        })
      }
      setPending(null)
      onDone()
    } catch (e) {
      toast.error("Export failed", {
        id: toastId,
        description: String((e as Error).message),
      })
    } finally {
      setBusy(null)
    }
  }

  async function runEnrichThenExport(ids: number[]) {
    setBusy("enrich")
    const toastId = toast.loading(
      `Enriching ${ids.length} leads…`,
    )
    try {
      const r = await api.post<{ job_id: string }>(
        `/api/sources/${sourceId}/enrich`,
        { args: { limit: ids.length } },
      )
      // Poll the job to completion
      const started = Date.now()
      // eslint-disable-next-line no-constant-condition
      while (true) {
        await new Promise((res) => setTimeout(res, 1500))
        const j = await api.get<Job>(`/api/jobs/${r.job_id}`)
        const last = j.logs?.[j.logs.length - 1] || ""
        if (j.status === "done") break
        if (j.status === "error") {
          toast.error("Enrichment failed", {
            id: toastId,
            description: last.slice(0, 200),
          })
          setBusy(null)
          return
        }
        toast.loading(`Enriching — ${last.slice(0, 80) || j.status}`, {
          id: toastId,
        })
      }
      const secs = Math.round((Date.now() - started) / 1000)
      toast.success(`Enriched in ${secs}s — now exporting`, { id: toastId })
      // Re-check which are now ready, then export
      const chk = await api.post<SelectionCheck>(
        `/api/sources/${sourceId}/selection-check`,
        { lead_ids: ids },
      )
      if (chk.ready.length === 0) {
        toast.error("Still no verified emails found", {
          description:
            "Domains may block pattern guessing. Try a different source or manual research.",
        })
        setBusy(null)
        return
      }
      setPending(null)
      await doExport(chk.ready)
    } catch (e) {
      toast.error("Enrich failed", {
        id: toastId,
        description: String((e as Error).message),
      })
      setBusy(null)
    }
  }

  async function onClickAdd() {
    if (busy || selected.size === 0) return
    setBusy("check")
    try {
      const chk = await api.post<SelectionCheck>(
        `/api/sources/${sourceId}/selection-check`,
        { lead_ids: Array.from(selected) },
      )
      setBusy(null)
      // All ready → export straight away
      if (
        chk.needs_enrichment.length === 0 &&
        chk.no_founders.length === 0 &&
        chk.ready.length > 0
      ) {
        await doExport(chk.ready)
        return
      }
      // Some or all need work → show dialog
      setPending(chk)
    } catch (e) {
      toast.error("Pre-flight check failed", {
        description: String((e as Error).message),
      })
      setBusy(null)
    }
  }

  const emailsSuffix =
    simpleMode && selectedEmailCount > 0
      ? ` (${selectedEmailCount} email${selectedEmailCount === 1 ? "" : "s"})`
      : ""

  const btnLabel =
    busy === "check"
      ? "Checking…"
      : busy === "enrich"
        ? "Enriching…"
        : busy === "export"
          ? "Exporting…"
          : busy === "drafts"
            ? "Writing drafts…"
            : busy === "outlook"
              ? "Putting in Outlook…"
              : simpleMode
                ? `🚀 Send ${selected.size} ${selected.size === 1 ? "company" : "companies"}${emailsSuffix}`
                : `Add ${selected.size} to Campaign`

  return (
    <>
      <div className="flex items-center gap-3 px-5 py-3 border-b border-[hsl(250_80%_62%/0.25)] bg-[hsl(250_80%_62%/0.08)]">
        <div className="text-sm text-zinc-200">
          <span className="tnum font-medium">{selected.size}</span>
          <span className="text-zinc-400"> selected</span>
        </div>
        <button
          onClick={onClear}
          className="text-xs text-zinc-400 hover:text-zinc-200 inline-flex items-center gap-1"
        >
          <XIcon className="size-3" /> clear
        </button>
        <div className="ml-auto" />
        <Button size="sm" onClick={onClickAdd} disabled={!!busy}>
          <Send className="size-3.5 mr-1" />
          {btnLabel}
        </Button>
      </div>

      {pending && (
        <SelectionCheckDialog
          check={pending}
          onCancel={() => setPending(null)}
          onSkipAndExport={() => doExport(pending.ready)}
          onEnrichAndExport={() =>
            runEnrichThenExport([
              ...pending.needs_enrichment,
              ...pending.no_founders,
            ])
          }
          busy={busy !== null}
        />
      )}
    </>
  )
}

function SelectionCheckDialog({
  check,
  busy,
  onCancel,
  onSkipAndExport,
  onEnrichAndExport,
}: {
  check: SelectionCheck
  busy: boolean
  onCancel: () => void
  onSkipAndExport: () => void
  onEnrichAndExport: () => void
}) {
  const ready = check.ready.length
  const needsEnrich = check.needs_enrichment.length + check.no_founders.length
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-xl border border-zinc-800 bg-[#18181b] shadow-xl">
        <div className="px-5 py-4 border-b border-zinc-800/80">
          <div className="text-sm font-semibold tracking-tight text-zinc-100">
            Some leads need enrichment
          </div>
          <div className="text-xs text-zinc-500 mt-1">
            Out of {check.total} selected leads:
          </div>
        </div>
        <div className="px-5 py-4 space-y-2 text-sm">
          <Row dot="emerald" label="Ready to export" value={ready} hint="have a verified founder email" />
          <Row dot="amber" label="Need enrichment" value={needsEnrich} hint="no verified email yet — can try to fetch/guess" />
        </div>
        <div className="px-5 py-4 border-t border-zinc-800/80 flex flex-wrap justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          {ready > 0 && (
            <Button
              size="sm"
              variant="secondary"
              onClick={onSkipAndExport}
              disabled={busy}
            >
              Skip &amp; Export {ready}
            </Button>
          )}
          {needsEnrich > 0 && (
            <Button size="sm" onClick={onEnrichAndExport} disabled={busy}>
              Enrich {needsEnrich} then Export
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

function Row({
  dot,
  label,
  value,
  hint,
}: {
  dot: "emerald" | "amber"
  label: string
  value: number
  hint: string
}) {
  const color =
    dot === "emerald"
      ? "bg-emerald-500"
      : "bg-amber-500"
  return (
    <div className="flex items-start gap-3 rounded-md border border-zinc-800/70 bg-zinc-900/40 px-3 py-2">
      <span className={cn("mt-1 size-2 rounded-full", color)} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between">
          <span className="text-zinc-200">{label}</span>
          <span className="tnum font-semibold text-zinc-100">{value}</span>
        </div>
        <div className="text-xs text-zinc-500 mt-0.5">{hint}</div>
      </div>
    </div>
  )
}

function StarButton({
  sourceId,
  leadId,
  starred,
}: {
  sourceId: string
  leadId: number
  starred: boolean
}) {
  const [busy, setBusy] = React.useState(false)
  const [optimistic, setOptimistic] = React.useState<boolean | null>(null)
  const effective = optimistic !== null ? optimistic : starred

  async function toggle() {
    if (busy) return
    const next = !effective
    setOptimistic(next)
    setBusy(true)
    try {
      await api.post(
        `/api/sources/${sourceId}/leads/${leadId}/star`,
        { value: next },
      )
      // Invalidate the leads query so server state refreshes
      globalMutate(
        (k) =>
          typeof k === "string" && k.startsWith(`/api/sources/${sourceId}/leads?`),
      )
    } catch (e) {
      setOptimistic(starred) // revert on error
      toast.error("Star failed", { description: String((e as Error).message) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <button
      onClick={toggle}
      title={effective ? "High-value lead — click to unstar" : "Mark as high-value"}
      className={cn(
        "text-sm leading-none transition-colors",
        effective
          ? "text-amber-400 hover:text-amber-300"
          : "text-zinc-700 hover:text-amber-400",
      )}
    >
      {effective ? "★" : "☆"}
    </button>
  )
}


function PeopleChip({ row }: { row: GrabLeadRow }) {
  const founders = row.founders || []
  const verified = founders.filter((f) => f.email_status === "ok").length
  if (founders.length === 0) {
    return (
      <span
        title="No founders enriched yet — run Enrich Missing"
        className="inline-flex items-center justify-center rounded-md border border-dashed border-zinc-700 text-zinc-600 text-[10px] px-1.5 py-0.5"
      >
        —
      </span>
    )
  }
  if (verified === 0) {
    return (
      <span
        title={`${founders.length} founder${founders.length === 1 ? "" : "s"} found, 0 verified`}
        className="inline-flex items-center gap-1 rounded-md border border-amber-900/50 bg-amber-950/40 text-amber-300 text-[10px] px-1.5 py-0.5 tnum"
      >
        0/{founders.length}
      </span>
    )
  }
  return (
    <span
      title={`${verified}/${founders.length} founders with verified email`}
      className="inline-flex items-center gap-1 rounded-md border border-emerald-900/50 bg-emerald-950/40 text-emerald-300 text-[10px] px-1.5 py-0.5 tnum"
    >
      <span className="size-1 rounded-full bg-emerald-400" />
      {verified}
      {verified < founders.length && (
        <span className="text-emerald-600">/{founders.length}</span>
      )}
    </span>
  )
}


// ---- Small reusable UI atoms ----
function Checkbox({
  checked,
  onChange,
  ...rest
}: {
  checked: boolean
  onChange: () => void
} & React.HTMLAttributes<HTMLInputElement>) {
  return (
    <input
      type="checkbox"
      checked={checked}
      onChange={onChange}
      onClick={(e) => e.stopPropagation()}
      className="size-4 rounded border-zinc-700 bg-zinc-900 accent-[hsl(250_80%_62%)] cursor-pointer"
      {...rest}
    />
  )
}

function FilterSection({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-[0.15em] text-zinc-500 mb-2">
        {label}
      </div>
      {children}
    </div>
  )
}

function FacetDropdown({
  label,
  value,
  buckets,
  onChange,
}: {
  label: string
  value: string
  buckets: Array<{ value: string; count: number }>
  onChange: (v: string) => void
}) {
  const effectiveValue = value === "" ? ALL_SENTINEL : value
  return (
    <FilterSection label={label}>
      <Select
        value={effectiveValue}
        onValueChange={(v) => onChange(v === ALL_SENTINEL ? "" : v)}
      >
        <SelectTrigger className="!w-full bg-zinc-900/40 border-zinc-800">
          <SelectValue
            placeholder={`All${
              buckets.length ? ` (${buckets.length})` : ""
            }`}
          />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL_SENTINEL}>All</SelectItem>
          {buckets.slice(0, 40).map((b) => (
            <SelectItem key={b.value} value={b.value}>
              {b.value} · {b.count}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </FilterSection>
  )
}

function CheckToggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: string
}) {
  return (
    <label className="flex items-center gap-2 text-xs text-zinc-300 cursor-pointer">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="size-3.5 rounded border-zinc-700 bg-zinc-900 accent-[hsl(250_80%_62%)]"
      />
      {label}
    </label>
  )
}

function Cell({ row, col }: { row: GrabLeadRow; col: ColumnDescriptor }) {
  const raw = getCell(row, col.key)

  if (raw === undefined || raw === null || raw === "") {
    return <span className="text-zinc-600">—</span>
  }

  if (col.type === "bool") {
    return raw ? (
      <CheckCircle2 className="size-4 text-emerald-500" />
    ) : (
      <XCircle className="size-4 text-zinc-600" />
    )
  }

  if (col.type === "url") {
    return (
      <a
        href={String(raw).startsWith("http") ? String(raw) : `https://${raw}`}
        target="_blank"
        rel="noopener noreferrer"
        onClick={(e) => e.stopPropagation()}
        className="text-[hsl(250_80%_78%)] hover:underline inline-flex items-center gap-1"
      >
        {String(raw)}
        <ExternalLink className="size-3 opacity-60" />
      </a>
    )
  }

  // Friendly signal labels
  if (col.key === "signal_type") {
    const info = SIGNAL_LABELS[String(raw)] || {
      label: String(raw),
      tone: "zinc",
    }
    const tones: Record<string, string> = {
      emerald:
        "bg-emerald-900/40 border-emerald-700/40 text-emerald-300",
      violet:
        "bg-[hsl(250_80%_62%/0.15)] border-[hsl(250_80%_62%/0.3)] text-[hsl(250_80%_82%)]",
      zinc: "bg-zinc-800/60 border-zinc-700 text-zinc-300",
    }
    return (
      <span
        className={cn(
          "inline-flex items-center rounded-md border text-[11px] px-2 py-0.5",
          tones[info.tone],
        )}
      >
        {info.label}
      </span>
    )
  }

  if (col.badge) {
    return (
      <span className="inline-flex items-center rounded-md border border-zinc-700 bg-zinc-800/60 text-zinc-300 text-[11px] px-2 py-0.5 tnum">
        {String(raw)}
      </span>
    )
  }

  const val = Array.isArray(raw) ? raw.join(", ") : String(raw)
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5",
        col.primary && "text-zinc-100 font-medium tracking-tight",
        !col.primary && "text-zinc-400",
      )}
    >
      {val}
      {col.primary && !!row.needs_attention && (
        <span
          className="inline-flex items-center rounded-sm border border-[hsl(250_80%_62%/0.4)] bg-[hsl(250_80%_62%/0.12)] text-[hsl(250_80%_85%)] text-[9px] font-semibold uppercase tracking-wider px-1 py-0.5"
          title={
            row.first_seen_at &&
            new Date(row.first_seen_at).getTime() >
              Date.now() - 36 * 3600 * 1000
              ? "New since last scrape"
              : "Changed since last scrape"
          }
        >
          New
        </span>
      )}
    </span>
  )
}

function RowLinks({ row }: { row: GrabLeadRow }) {
  const meta = (row.extra?.company_meta || {}) as Record<string, string | undefined>
  const yc = row.source_url
  const li = meta.linkedin_url
  const tw = meta.twitter_url
  const fb = meta.facebook_url
  const gh = meta.github_url
  const cb = meta.crunchbase_url

  const Link = ({
    href,
    label,
    title,
    hoverClass,
  }: {
    href: string
    label: React.ReactNode
    title: string
    hoverClass: string
  }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={title}
      className={cn(
        "text-[10px] font-semibold text-zinc-500 px-1 leading-none",
        hoverClass,
      )}
    >
      {label}
    </a>
  )

  return (
    <div
      className="inline-flex items-center gap-1.5 justify-end"
      onClick={(e) => e.stopPropagation()}
    >
      {yc && (
        <a
          href={yc}
          target="_blank"
          rel="noopener noreferrer"
          title="YC page"
          className="text-zinc-500 hover:text-zinc-200"
        >
          <ExternalLink className="size-3.5" />
        </a>
      )}
      {li && <Link href={li} label="in" title="LinkedIn" hoverClass="hover:text-[#70b5f9]" />}
      {tw && <Link href={tw} label="𝕏" title="Twitter / X" hoverClass="hover:text-zinc-200" />}
      {gh && <Link href={gh} label="gh" title="GitHub" hoverClass="hover:text-[#a0a0ff]" />}
      {cb && <Link href={cb} label="cb" title="Crunchbase" hoverClass="hover:text-[#146aff]" />}
      {fb && <Link href={fb} label="f" title="Facebook" hoverClass="hover:text-[#4867aa]" />}
    </div>
  )
}

function FoundersPanel({ row }: { row: GrabLeadRow }) {
  const founders = row.founders || []
  if (!founders.length) {
    return (
      <div className="text-xs text-zinc-500">
        No founders enriched yet for this lead.
      </div>
    )
  }
  return (
    <div className="space-y-2">
      <div className="text-[11px] uppercase tracking-[0.15em] text-zinc-500 flex items-center gap-1">
        <User className="size-3" />
        Founders ({founders.length})
      </div>
      <div className="grid gap-2 md:grid-cols-2">
        {founders.map((f, i) => (
          <div
            key={i}
            className="rounded-md border border-zinc-800/80 bg-zinc-950/40 p-3 text-xs"
          >
            <div className="flex items-center justify-between">
              <div className="font-medium text-zinc-100 truncate">
                {f.full_name}
              </div>
              {f.email_status === "ok" ? (
                <span className="text-emerald-500/90 text-[10px] uppercase tracking-wider">
                  verified
                </span>
              ) : f.email_status ? (
                <span className="text-zinc-500 text-[10px] uppercase tracking-wider">
                  {f.email_status}
                </span>
              ) : null}
            </div>
            <div className="text-zinc-400 mt-0.5">{f.title || "—"}</div>
            {f.email && (
              <div className="mt-1.5 font-mono text-[11px] text-[hsl(250_80%_78%)] truncate">
                {f.email}
              </div>
            )}
            {f.linkedin_url && (
              <a
                href={f.linkedin_url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-1 inline-flex items-center gap-1 text-[11px] text-zinc-400 hover:text-zinc-200"
              >
                LinkedIn <ExternalLink className="size-2.5 opacity-60" />
              </a>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function StatBox({
  label,
  value,
  emphasis,
  hint,
}: {
  label: string
  value: string
  emphasis?: boolean
  hint?: string
}) {
  return (
    <div
      className="rounded-xl border border-zinc-800/80 bg-[#18181b] px-4 py-3"
      title={hint}
    >
      <div className="text-[10px] uppercase tracking-[0.12em] text-zinc-500">
        {label}
      </div>
      <div
        className={cn(
          "mt-1 text-xl font-semibold tnum tracking-tight",
          emphasis ? "text-[hsl(250_80%_78%)]" : "text-zinc-100",
        )}
      >
        {value || "\u00A0"}
      </div>
    </div>
  )
}

function TogglePill({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex-1 px-2 py-1 text-xs rounded-md border transition-colors",
        active
          ? "bg-[hsl(250_80%_62%/0.15)] border-[hsl(250_80%_62%/0.3)] text-[hsl(250_80%_78%)]"
          : "border-zinc-800 text-zinc-400 hover:text-zinc-200",
      )}
    >
      {label}
    </button>
  )
}
