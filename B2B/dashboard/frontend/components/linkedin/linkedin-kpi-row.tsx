"use client"

import useSWR from "swr"
import { Inbox, Mail, Send, MessageSquareReply, Ban, Sparkles } from "lucide-react"
import { KpiCard } from "@/components/kpi-card"
import { swrFetcher } from "@/lib/api"
import type { LinkedInOverview } from "@/lib/types"

export function LinkedInKpiRow() {
  const { data, isLoading } = useSWR<LinkedInOverview>(
    "/api/linkedin/overview",
    swrFetcher,
    { refreshInterval: 15_000 },
  )

  const cards: {
    label: string
    value: number | undefined
    icon: React.ReactNode
    accent?: "violet" | "emerald" | "amber" | "rose" | "sky"
    hint?: string
    href?: string
  }[] = [
    {
      label: "Total leads",
      value: data?.total,
      icon: <Inbox className="size-4" />,
      accent: "violet",
      hint: "All LinkedIn rows",
      href: "/linkedin/leads?tab=all",
    },
    {
      label: "Drafted",
      value: data?.drafted,
      icon: <Mail className="size-4" />,
      accent: "sky",
      hint: "Ready to send",
      href: "/linkedin/leads?tab=drafts",
    },
    {
      label: "Sent today",
      value: data?.sent_today,
      icon: <Send className="size-4" />,
      accent: "emerald",
      hint: `of ${data?.quota_cap ?? 20} daily cap`,
      href: "/linkedin/leads?tab=sent",
    },
    {
      label: "Replied",
      value: data?.replied,
      icon: <MessageSquareReply className="size-4" />,
      accent: "amber",
      href: "/linkedin/replies",
    },
    {
      label: "Bounced",
      value: data?.bounced,
      icon: <Ban className="size-4" />,
      accent: "rose",
      // Bounced leads live within Sent & Replies; filter via status
      href: "/linkedin/leads?tab=all&status=Bounced",
    },
    {
      label: "Queued",
      value: data?.queued,
      icon: <Sparkles className="size-4" />,
      accent: "violet",
      hint: "Waiting in send queue",
      href: "/linkedin/leads?tab=all&status=Queued",
    },
  ]

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      {cards.map((c, i) => (
        <KpiCard
          key={c.label}
          label={c.label}
          value={c.value ?? 0}
          hint={c.hint}
          icon={c.icon}
          accent={c.accent}
          loading={isLoading}
          index={i}
          href={c.href}
        />
      ))}
    </div>
  )
}
