"use client"

import * as React from "react"
import { usePendingReplies } from "@/lib/use-pending-replies"

/**
 * Mirrors the LinkedIn pending-reply count into document.title so a glance
 * at any browser tab tells Jaydip whether someone is waiting on a reply
 * even when the dashboard isn't focused. Strips and re-applies the
 * "(N) " prefix idempotently so it survives Next route changes that
 * rewrite the title.
 */
export function PendingTitleUpdater() {
  const pending = usePendingReplies()
  // Capture the page's "natural" title (without our prefix) so we can
  // restore it cleanly. Re-derives whenever the underlying title
  // changes (route navigation typically resets it).
  const baseRef = React.useRef<string>("")

  React.useEffect(() => {
    if (typeof document === "undefined") return
    const stripped = document.title.replace(/^\(\d+\)\s+/, "")
    baseRef.current = stripped
    document.title = pending > 0 ? `(${pending}) ${stripped}` : stripped
  }, [pending])

  // Re-watch the title in case Next or another component rewrites it
  // mid-session — without this, navigating to a new page would leave the
  // prefix off until the next poll cycle.
  React.useEffect(() => {
    if (typeof document === "undefined") return
    const observer = new MutationObserver(() => {
      const current = document.title
      const stripped = current.replace(/^\(\d+\)\s+/, "")
      if (stripped !== baseRef.current) {
        baseRef.current = stripped
        const wanted = pending > 0 ? `(${pending}) ${stripped}` : stripped
        if (current !== wanted) document.title = wanted
      }
    })
    const titleEl = document.querySelector("title")
    if (titleEl) {
      observer.observe(titleEl, { childList: true })
    }
    return () => observer.disconnect()
  }, [pending])

  return null
}
