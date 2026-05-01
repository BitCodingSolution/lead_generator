"use client"

import * as React from "react"
import { usePendingReplies } from "@/lib/use-pending-replies"

const STORAGE_KEY = "reply_notifs_enabled"
const LAST_SEEN_KEY = "reply_notifs_last_seen"

/**
 * Watches the LinkedIn pending-replies counter and, when it grows, fires
 * a desktop notification — even when the dashboard tab isn't focused. A
 * subtle button in the corner asks for permission on first run; once
 * granted, the user can toggle notifications off via localStorage if
 * they get noisy.
 *
 * Storage:
 *   - reply_notifs_enabled: "1" / "0" — user-level on/off, defaults to
 *     "1" once permission is granted so we don't keep nagging.
 *   - reply_notifs_last_seen: last pending count we already notified
 *     for. Stored so a page refresh doesn't re-fire stale alerts.
 */
export function ReplyNotifications() {
  const pending = usePendingReplies()
  // Start as "unsupported" on both server and first client render so the
  // hydrated tree matches SSR. The real permission state is read after
  // mount in the effect below, which then triggers a re-render.
  const [permission, setPermission] = React.useState<NotificationPermission | "unsupported">("unsupported")

  React.useEffect(() => {
    if (typeof Notification === "undefined") return
    setPermission(Notification.permission)
  }, [])

  React.useEffect(() => {
    if (typeof window === "undefined" || typeof Notification === "undefined") return
    if (permission !== "granted") return
    const enabled = window.localStorage.getItem(STORAGE_KEY) !== "0"
    if (!enabled) return

    // Compare against the last count we already alerted for. We only
    // notify on a strict increase — equal or lower means the user has
    // either already seen it or has handled some.
    const prev = Number(window.localStorage.getItem(LAST_SEEN_KEY) || "0")
    if (pending > prev) {
      const delta = pending - prev
      try {
        const n = new Notification(
          delta === 1
            ? "1 new reply needs action"
            : `${delta} new replies need action`,
          {
            body: `Open the dashboard to triage. ${pending} pending in total.`,
            icon: "/favicon.ico",
            tag: "linkedin-replies",
            // Re-using the same tag means a fresh batch replaces the old
            // one in the OS notification tray instead of stacking.
          },
        )
        n.onclick = () => {
          window.focus()
          window.location.assign("/linkedin/replies")
          n.close()
        }
      } catch {
        /* notification failed silently — nothing useful to recover */
      }
    }
    if (pending !== prev) {
      window.localStorage.setItem(LAST_SEEN_KEY, String(pending))
    }
  }, [pending, permission])

  if (permission === "unsupported" || permission === "granted") return null
  if (permission === "denied") return null

  return (
    <button
      type="button"
      onClick={async () => {
        try {
          const result = await Notification.requestPermission()
          setPermission(result)
          if (result === "granted") {
            window.localStorage.setItem(STORAGE_KEY, "1")
            window.localStorage.setItem(LAST_SEEN_KEY, String(pending))
          }
        } catch {
          /* ignore — older browsers, etc */
        }
      }}
      className="fixed bottom-4 right-4 z-30 rounded-md border border-zinc-700 bg-zinc-900/90 backdrop-blur px-3 py-2 text-xs text-zinc-200 shadow-lg hover:bg-zinc-800"
      title="Get a desktop alert when a new reply arrives"
    >
      🔔 Enable reply notifications
    </button>
  )
}
