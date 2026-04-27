"use client"

import useSWR from "swr"
import { swrFetcher } from "./api"
import type { LinkedInOverview } from "./types"

/**
 * Polls /api/linkedin/overview every 15s and returns the count of inbound
 * replies still awaiting Jaydip's action. Single source of truth for the
 * sidebar badge, the document.title prefix, and any future
 * "you have N pending" affordance — keeps everything in lockstep so the
 * sidebar dot never disagrees with the KPI card.
 */
export function usePendingReplies(): number {
  const { data } = useSWR<LinkedInOverview>(
    "/api/linkedin/overview",
    swrFetcher,
    { refreshInterval: 15_000 },
  )
  return data?.replied_pending ?? 0
}
