"use client"

import { PageHeader } from "@/components/page-header"
import { LinkedInRepliesPanel } from "@/components/linkedin/linkedin-replies-panel"

export default function LinkedInRepliesPage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Replies"
        subtitle="All inbound replies, triage by sentiment, open a reply to draft a response."
      />
      <LinkedInRepliesPanel />
    </div>
  )
}
