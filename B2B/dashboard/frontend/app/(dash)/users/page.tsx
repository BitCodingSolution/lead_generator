"use client"

import * as React from "react"
import useSWR, { mutate as swrMutate } from "swr"
import { toast } from "sonner"
import { Plus, Pencil, Trash2, Loader2, Users as UsersIcon } from "lucide-react"

import { api, getAuthHeaders, swrFetcher } from "@/lib/api"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useAuth } from "@/components/auth/auth-provider"
import { absTime, relTime } from "@/lib/utils"

type ManagedUser = {
  id: number
  username: string
  created_at: string | null
  last_login_at: string | null
}

const ENDPOINT = "/api/auth/users"

export default function UsersPage() {
  const { user: currentUser } = useAuth()
  const { data, error, isLoading } = useSWR<{ users: ManagedUser[] }>(
    ENDPOINT,
    swrFetcher,
    { revalidateOnFocus: false },
  )

  const [createOpen, setCreateOpen] = React.useState(false)
  const [editing, setEditing] = React.useState<ManagedUser | null>(null)
  const [deleting, setDeleting] = React.useState<ManagedUser | null>(null)

  const refresh = () => swrMutate(ENDPOINT)

  return (
    <div>
      <PageHeader
        title="Users"
        subtitle="Manage who can sign into the dashboard. Every user has equal access — there's no role tier yet."
        actions={
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="size-3.5 mr-1.5" />
            Add user
          </Button>
        }
      />

      <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] overflow-hidden">
        {isLoading ? (
          <div className="px-4 py-12 text-center text-sm text-zinc-500">Loading…</div>
        ) : error ? (
          <div className="px-4 py-8 text-center text-sm text-red-400">
            Failed to load: {String((error as Error).message || error)}
          </div>
        ) : !data || data.users.length === 0 ? (
          <div className="px-4 py-12 text-center text-sm text-zinc-500">
            <UsersIcon className="mx-auto mb-2 size-6 opacity-40" />
            No users yet.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/60 text-[11px] uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium">Username</th>
                <th className="text-left px-4 py-2.5 font-medium">Created</th>
                <th className="text-left px-4 py-2.5 font-medium">Last login</th>
                <th className="text-right px-4 py-2.5 font-medium w-32">Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.users.map((u) => {
                const isMe = currentUser?.id === u.id
                return (
                  <tr key={u.id} className="border-t border-zinc-800/60 hover:bg-zinc-900/40">
                    <td className="px-4 py-2.5 font-medium text-zinc-100">
                      {u.username}
                      {isMe && (
                        <span className="ml-2 rounded bg-blue-500/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-blue-300">
                          you
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-zinc-400 tnum" title={absTime(u.created_at)}>
                      {relTime(u.created_at)}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-zinc-400 tnum" title={absTime(u.last_login_at)}>
                      {u.last_login_at ? relTime(u.last_login_at) : <span className="text-zinc-600">never</span>}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <button
                        type="button"
                        onClick={() => setEditing(u)}
                        title="Edit user"
                        className="inline-flex items-center gap-1 rounded p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
                      >
                        <Pencil className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => setDeleting(u)}
                        disabled={isMe}
                        title={isMe ? "You can't delete your own account" : "Delete user"}
                        className="ml-1 inline-flex items-center gap-1 rounded p-1.5 text-zinc-500 hover:bg-rose-500/15 hover:text-rose-300 disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:bg-transparent"
                      >
                        <Trash2 className="size-3.5" />
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      <CreateUserDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={refresh}
      />
      <EditUserDialog
        user={editing}
        onOpenChange={(open) => !open && setEditing(null)}
        onUpdated={refresh}
      />
      <DeleteUserDialog
        user={deleting}
        onOpenChange={(open) => !open && setDeleting(null)}
        onDeleted={refresh}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Dialogs
// ---------------------------------------------------------------------------

function CreateUserDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: () => void
}) {
  const [username, setUsername] = React.useState("")
  const [password, setPassword] = React.useState("")
  const [busy, setBusy] = React.useState(false)

  React.useEffect(() => {
    if (!open) {
      setUsername("")
      setPassword("")
      setBusy(false)
    }
  }, [open])

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!username.trim() || password.length < 8) {
      toast.error("Username required, password ≥ 8 chars.")
      return
    }
    setBusy(true)
    try {
      await api.post(ENDPOINT, { username: username.trim(), password })
      toast.success(`Created ${username}`)
      onOpenChange(false)
      onCreated()
    } catch (err) {
      toast.error("Create failed", { description: (err as Error).message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add user</DialogTitle>
          <DialogDescription>
            They&apos;ll be able to sign in immediately with these credentials.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-3">
          <label className="block">
            <span className="mb-1 block text-xs text-zinc-400">Username</span>
            <Input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
              required
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-zinc-400">
              Password <span className="text-zinc-600">(min 8 chars)</span>
            </span>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
            />
          </label>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onOpenChange(false)}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={busy}>
              {busy ? <Loader2 className="size-3.5 mr-1.5 animate-spin" /> : null}
              Create user
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function EditUserDialog({
  user,
  onOpenChange,
  onUpdated,
}: {
  user: ManagedUser | null
  onOpenChange: (open: boolean) => void
  onUpdated: () => void
}) {
  const [username, setUsername] = React.useState("")
  const [password, setPassword] = React.useState("")
  const [busy, setBusy] = React.useState(false)

  React.useEffect(() => {
    if (user) {
      setUsername(user.username)
      setPassword("")
      setBusy(false)
    }
  }, [user])

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!user) return

    const payload: { username?: string; password?: string } = {}
    if (username.trim() && username.trim() !== user.username) {
      payload.username = username.trim()
    }
    if (password) {
      if (password.length < 8) {
        toast.error("Password must be at least 8 characters.")
        return
      }
      payload.password = password
    }
    if (Object.keys(payload).length === 0) {
      toast.message("Nothing to update.")
      return
    }
    setBusy(true)
    try {
      // api wrapper exposes get/post/delete; PATCH built manually here.
      const res = await fetch(`${api.base}${ENDPOINT}/${user.id}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          ...(await getAuthHeaders()),
        },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`${res.status} ${res.statusText}${text ? ` — ${text}` : ""}`)
      }
      toast.success(`Updated ${user.username}`)
      onOpenChange(false)
      onUpdated()
    } catch (err) {
      toast.error("Update failed", { description: (err as Error).message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={!!user} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit user</DialogTitle>
          <DialogDescription>
            Leave the password blank to keep the current one.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-3">
          <label className="block">
            <span className="mb-1 block text-xs text-zinc-400">Username</span>
            <Input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
              required
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-zinc-400">
              New password{" "}
              <span className="text-zinc-600">(optional, min 8 chars)</span>
            </span>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={8}
              placeholder="Leave blank to keep current"
            />
          </label>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onOpenChange(false)}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={busy}>
              {busy ? <Loader2 className="size-3.5 mr-1.5 animate-spin" /> : null}
              Save changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function DeleteUserDialog({
  user,
  onOpenChange,
  onDeleted,
}: {
  user: ManagedUser | null
  onOpenChange: (open: boolean) => void
  onDeleted: () => void
}) {
  const [busy, setBusy] = React.useState(false)

  async function onConfirm() {
    if (!user) return
    setBusy(true)
    try {
      await api.delete(`${ENDPOINT}/${user.id}`)
      toast.success(`Deleted ${user.username}`)
      onOpenChange(false)
      onDeleted()
    } catch (err) {
      toast.error("Delete failed", { description: (err as Error).message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={!!user} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete {user?.username}?</DialogTitle>
          <DialogDescription>
            This is permanent. They&apos;ll be signed out of any active session and
            won&apos;t be able to log in again unless you re-create the account.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button
            type="button"
            size="sm"
            variant="destructive"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? <Loader2 className="size-3.5 mr-1.5 animate-spin" /> : null}
            Delete user
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
