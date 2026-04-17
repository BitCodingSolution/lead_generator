"use client"

import * as React from "react"
import useSWR from "swr"
import { motion } from "framer-motion"
import { toast } from "sonner"
import { api, swrFetcher } from "@/lib/api"
import { useJob } from "@/hooks/useJob"
import type { BatchFile, IndustryRow, Stats } from "@/lib/types"
import { PageHeader } from "@/components/page-header"
import { StatusChip } from "@/components/status-chip"
import { Terminal } from "@/components/terminal"
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  PackageSearch,
  PenLine,
  FileOutput,
  Send,
  RefreshCw,
  Play,
  FileText,
} from "lucide-react"
import { cn, fmt } from "@/lib/utils"

type StepKey = "pick" | "drafts" | "outlook" | "send" | "sync"

type Step = {
  key: StepKey
  num: number
  title: string
  subtitle: string
  icon: React.ComponentType<{ className?: string }>
}

const STEPS: Step[] = [
  { key: "pick", num: 1, title: "Pick a batch", subtitle: "Select leads by industry + tier", icon: PackageSearch },
  { key: "drafts", num: 2, title: "Generate drafts", subtitle: "Claude writes tailored emails", icon: PenLine },
  { key: "outlook", num: 3, title: "Write to Outlook", subtitle: "Push to your Outbox folder", icon: FileOutput },
  { key: "send", num: 4, title: "Send", subtitle: "Real sends via SMTP (manual confirm)", icon: Send },
  { key: "sync", num: 5, title: "Sync & scan replies", subtitle: "Pull sent items + inbox replies", icon: RefreshCw },
]

export default function CampaignsPage() {
  const { data: stats } = useSWR<Stats>("/api/stats", swrFetcher, {
    refreshInterval: 30000,
  })
  const { data: industries } = useSWR<IndustryRow[]>("/api/industries", swrFetcher)
  const { data: files, mutate: refetchFiles } = useSWR<BatchFile[]>(
    "/api/batches/files",
    swrFetcher,
    { refreshInterval: 15000 },
  )

  // Per-step job state
  const [jobs, setJobs] = React.useState<Record<StepKey, string | null>>({
    pick: null,
    drafts: null,
    outlook: null,
    send: null,
    sync: null,
  })

  // Step inputs
  const [pickForm, setPickForm] = React.useState({
    industry: "",
    count: 20,
    tier: "",
    city: "",
  })
  const [selectedFile, setSelectedFile] = React.useState<string>("")
  const [draftLimit, setDraftLimit] = React.useState<number>(0)
  const [sendCount, setSendCount] = React.useState<number>(5)
  const [noJitter, setNoJitter] = React.useState(false)
  const [confirmSend, setConfirmSend] = React.useState(false)

  React.useEffect(() => {
    if (!selectedFile && files && files.length > 0) {
      setSelectedFile(files[0].name)
    }
  }, [files, selectedFile])

  async function run(key: StepKey, path: string, body?: unknown) {
    try {
      const res = await api.post<{ job_id: string }>(path, body)
      if (!res.job_id) throw new Error("No job_id returned")
      setJobs((j) => ({ ...j, [key]: res.job_id }))
      toast.success("Job started", { description: res.job_id })
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      toast.error("Failed to start", { description: msg })
    }
  }

  const runPick = () => {
    if (!pickForm.industry) {
      toast.error("Pick an industry first")
      return
    }
    run("pick", "/api/actions/pick-batch", {
      industry: pickForm.industry,
      count: Number(pickForm.count) || 20,
      tier: pickForm.tier || undefined,
      city: pickForm.city || undefined,
    }).then(() => {
      setTimeout(() => refetchFiles(), 4000)
    })
  }

  const runDrafts = () => {
    if (!selectedFile) return toast.error("Pick a batch file")
    run("drafts", "/api/actions/generate-drafts", {
      file: selectedFile,
      limit: draftLimit || undefined,
    })
  }

  const runOutlook = () => {
    if (!selectedFile) return toast.error("Pick a batch file")
    run("outlook", "/api/actions/write-outlook", { file: selectedFile })
  }

  const runSend = () => {
    if (!selectedFile) return toast.error("Pick a batch file")
    const n = Number(sendCount) || 0
    if (n <= 0) return toast.error("Count must be > 0")
    setConfirmSend(false)
    run("send", "/api/actions/send-drafts", {
      file: selectedFile,
      count: n,
      no_jitter: noJitter || undefined,
    })
  }

  const runSync = async () => {
    try {
      const a = await api.post<{ job_id: string }>("/api/actions/sync-sent")
      setJobs((j) => ({ ...j, sync: a.job_id }))
      toast.success("Syncing sent items…")
      // chain scan-replies after sync completes (best-effort — API lets both run)
      await api.post<{ job_id: string }>("/api/actions/scan-replies")
      toast.success("Scanning replies in background")
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      toast.error("Failed", { description: msg })
    }
  }

  const remaining = stats?.remaining_today ?? 0

  return (
    <div className="space-y-6">
      <PageHeader
        title="Campaigns"
        subtitle="The control room. Run the outreach pipeline, one step at a time."
        actions={
          <div className="flex items-center gap-2">
            <div className="rounded-md border border-zinc-800 bg-zinc-900/40 px-2.5 py-1 text-[11px] text-zinc-400">
              Remaining today{" "}
              <span className="ml-1 tnum text-[hsl(250_80%_78%)] font-medium">
                {fmt(remaining)}
              </span>
            </div>
          </div>
        }
      />

      {/* Shared batch picker */}
      <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[260px]">
            <label className="text-[11px] uppercase tracking-[0.15em] text-zinc-500 block mb-1.5">
              Active batch file
            </label>
            <Select
              value={selectedFile || "__none"}
              onValueChange={(v) => setSelectedFile(v === "__none" ? "" : v)}
            >
              <SelectTrigger className="!w-full bg-zinc-900/40 border-zinc-800">
                <SelectValue placeholder="Choose a batch file…" />
              </SelectTrigger>
              <SelectContent>
                {(!files || files.length === 0) && (
                  <SelectItem value="__none">No batch files yet</SelectItem>
                )}
                {files?.map((f) => (
                  <SelectItem key={f.name} value={f.name}>
                    <FileText className="size-3.5 mr-1 text-zinc-500" />
                    {f.name}{" "}
                    <span className="text-zinc-500 ml-2 text-xs">{f.size_kb} KB</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="text-xs text-zinc-500">
            Steps 2–4 operate on this file. Step 1 creates a new one.
          </div>
        </div>
      </div>

      {/* Steps grid */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {STEPS.map((s, i) => (
          <StepCard
            key={s.key}
            step={s}
            index={i}
            jobId={jobs[s.key]}
          >
            {s.key === "pick" && (
              <div className="grid grid-cols-2 gap-2.5">
                <div className="col-span-2">
                  <Label>Industry</Label>
                  <Select
                    value={pickForm.industry || "__none"}
                    onValueChange={(v) =>
                      setPickForm((f) => ({ ...f, industry: v === "__none" ? "" : v }))
                    }
                  >
                    <SelectTrigger className="!w-full bg-zinc-900/40 border-zinc-800">
                      <SelectValue placeholder="Pick industry…" />
                    </SelectTrigger>
                    <SelectContent>
                      {(industries || []).map((i) => (
                        <SelectItem key={i.industry} value={i.industry}>
                          {i.industry}
                          <span className="text-zinc-500 ml-2 text-xs">
                            {fmt(i.available)} avail
                          </span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <Label>Count</Label>
                  <Input
                    type="number"
                    min={1}
                    value={pickForm.count}
                    onChange={(e) =>
                      setPickForm((f) => ({ ...f, count: Number(e.target.value) }))
                    }
                    className="bg-zinc-900/40 border-zinc-800"
                  />
                </div>
                <div>
                  <Label>Tier (opt)</Label>
                  <Select
                    value={pickForm.tier || "__any"}
                    onValueChange={(v) =>
                      setPickForm((f) => ({ ...f, tier: v === "__any" ? "" : v }))
                    }
                  >
                    <SelectTrigger className="!w-full bg-zinc-900/40 border-zinc-800">
                      <SelectValue placeholder="Any" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__any">Any tier</SelectItem>
                      <SelectItem value="1">Tier 1</SelectItem>
                      <SelectItem value="2">Tier 2</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="col-span-2">
                  <Label>City (opt)</Label>
                  <Input
                    value={pickForm.city}
                    onChange={(e) =>
                      setPickForm((f) => ({ ...f, city: e.target.value }))
                    }
                    placeholder="e.g. Berlin"
                    className="bg-zinc-900/40 border-zinc-800"
                  />
                </div>
                <div className="col-span-2 flex justify-end">
                  <RunButton onClick={runPick} />
                </div>
              </div>
            )}

            {s.key === "drafts" && (
              <div className="grid grid-cols-2 gap-2.5">
                <div className="col-span-2">
                  <Label>Batch file</Label>
                  <Input
                    value={selectedFile}
                    readOnly
                    className="bg-zinc-900/40 border-zinc-800 font-mono text-xs"
                  />
                </div>
                <div>
                  <Label>Limit (0 = all)</Label>
                  <Input
                    type="number"
                    min={0}
                    value={draftLimit}
                    onChange={(e) => setDraftLimit(Number(e.target.value))}
                    className="bg-zinc-900/40 border-zinc-800"
                  />
                </div>
                <div className="col-span-2 flex justify-end">
                  <RunButton onClick={runDrafts} />
                </div>
              </div>
            )}

            {s.key === "outlook" && (
              <div className="grid grid-cols-1 gap-2.5">
                <div>
                  <Label>Batch file</Label>
                  <Input
                    value={selectedFile}
                    readOnly
                    className="bg-zinc-900/40 border-zinc-800 font-mono text-xs"
                  />
                </div>
                <div className="text-xs text-zinc-500">
                  Drafts will appear in Outlook → Drafts, ready for a final human review.
                </div>
                <div className="flex justify-end">
                  <RunButton onClick={runOutlook} />
                </div>
              </div>
            )}

            {s.key === "send" && (
              <div className="grid grid-cols-2 gap-2.5">
                <div className="col-span-2">
                  <Label>Batch file</Label>
                  <Input
                    value={selectedFile}
                    readOnly
                    className="bg-zinc-900/40 border-zinc-800 font-mono text-xs"
                  />
                </div>
                <div>
                  <Label>Count to send</Label>
                  <Input
                    type="number"
                    min={1}
                    max={remaining || undefined}
                    value={sendCount}
                    onChange={(e) => setSendCount(Number(e.target.value))}
                    className="bg-zinc-900/40 border-zinc-800"
                  />
                </div>
                <div className="flex items-end">
                  <label className="inline-flex items-center gap-2 text-xs text-zinc-400 select-none">
                    <input
                      type="checkbox"
                      checked={noJitter}
                      onChange={(e) => setNoJitter(e.target.checked)}
                      className="accent-[hsl(250_80%_62%)]"
                    />
                    No jitter delay
                  </label>
                </div>
                <div className="col-span-2 flex items-center justify-between gap-3">
                  <div className="text-[11px] text-amber-400/90">
                    This will send real emails.
                  </div>
                  <Button
                    size="sm"
                    onClick={() => setConfirmSend(true)}
                    className="bg-[hsl(250_80%_62%)] hover:bg-[hsl(250_80%_58%)] text-white"
                  >
                    <Send className="size-3.5" />
                    Send now
                  </Button>
                </div>
              </div>
            )}

            {s.key === "sync" && (
              <div className="space-y-2.5">
                <div className="text-xs text-zinc-400">
                  Pulls sent items from Outlook into the database and scans the inbox for replies.
                  Safe to run anytime.
                </div>
                <div className="flex justify-end">
                  <RunButton onClick={runSync} label="Sync & scan" />
                </div>
              </div>
            )}
          </StepCard>
        ))}
      </div>

      <Dialog open={confirmSend} onOpenChange={setConfirmSend}>
        <DialogContent className="bg-[#18181b] border-zinc-800">
          <DialogHeader>
            <DialogTitle className="text-zinc-100">Send {sendCount} real emails?</DialogTitle>
            <DialogDescription className="text-zinc-400">
              Emails will be sent from{" "}
              <span className="font-mono text-xs text-zinc-300">jaydip@bitcodingsolutions.com</span>{" "}
              via Outlook SMTP. You have{" "}
              <span className="text-zinc-200 tnum font-medium">{fmt(remaining)}</span> in today&apos;s
              safety quota. This action can&apos;t be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmSend(false)}>
              Cancel
            </Button>
            <Button
              onClick={runSend}
              className="bg-[hsl(250_80%_62%)] hover:bg-[hsl(250_80%_58%)] text-white"
            >
              <Send className="size-3.5" />
              Send {sendCount}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[11px] uppercase tracking-[0.12em] text-zinc-500 mb-1.5">
      {children}
    </div>
  )
}

function RunButton({ onClick, label = "Run" }: { onClick: () => void; label?: string }) {
  return (
    <Button
      onClick={onClick}
      size="sm"
      className="bg-[hsl(250_80%_62%)] hover:bg-[hsl(250_80%_58%)] text-white"
    >
      <Play className="size-3.5" />
      {label}
    </Button>
  )
}

function StepCard({
  step,
  index,
  jobId,
  children,
}: {
  step: Step
  index: number
  jobId: string | null
  children: React.ReactNode
}) {
  const { job } = useJob(jobId)
  const Icon = step.icon
  const status = job?.status

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, delay: index * 0.04 }}
      className={cn(
        "rounded-xl border bg-[#18181b] transition-colors",
        status === "running"
          ? "border-[hsl(250_80%_62%/0.35)] shadow-[0_0_0_1px_hsl(250_80%_62%/0.25)]"
          : "border-zinc-800/80",
      )}
    >
      <div className="flex items-start justify-between px-5 pt-5">
        <div className="flex items-start gap-3">
          <div className="size-9 rounded-md bg-gradient-to-br from-[hsl(250_80%_62%/0.15)] to-transparent border border-zinc-800/80 flex items-center justify-center text-[hsl(250_80%_78%)]">
            <Icon className="size-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-[10px] uppercase tracking-[0.15em] text-zinc-500 tnum">
                Step {step.num}
              </span>
              {status && <StatusChip value={status} />}
            </div>
            <div className="text-sm font-semibold tracking-tight text-zinc-100 mt-0.5">
              {step.title}
            </div>
            <div className="text-xs text-zinc-500">{step.subtitle}</div>
          </div>
        </div>
      </div>
      <div className="px-5 py-4">{children}</div>
      <div className="px-5 pb-5">
        <Terminal
          logs={job?.logs || ""}
          status={status}
          label={job?.label || `step-${step.num}.log`}
          height={180}
        />
      </div>
    </motion.div>
  )
}
