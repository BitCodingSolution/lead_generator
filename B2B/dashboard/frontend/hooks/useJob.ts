"use client"

import useSWR from "swr"
import { swrFetcher } from "@/lib/api"
import type { Job } from "@/lib/types"

export function useJob(jobId: string | null | undefined) {
  const { data, error, isLoading, mutate } = useSWR<Job>(
    jobId ? `/api/jobs/${jobId}` : null,
    swrFetcher,
    {
      refreshInterval: (latest) => {
        if (!latest) return 2000
        if (latest.status === "done" || latest.status === "error") return 0
        return 2000
      },
      revalidateOnFocus: false,
    },
  )
  return { job: data, error, isLoading, mutate }
}
