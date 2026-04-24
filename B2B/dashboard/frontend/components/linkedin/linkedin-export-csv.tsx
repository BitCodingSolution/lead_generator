"use client"

import { Download } from "lucide-react"
import { api } from "@/lib/api"

export function ExportCsvButton({
  href,
  filename,
  label = "Export CSV",
}: {
  href: string
  filename: string
  label?: string
}) {
  const url = `${api.base}${href}`
  return (
    <a
      href={url}
      download={filename}
      className="inline-flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800/60 px-3 py-1.5 text-sm text-zinc-200 hover:bg-zinc-800"
    >
      <Download className="size-3.5" />
      {label}
    </a>
  )
}
