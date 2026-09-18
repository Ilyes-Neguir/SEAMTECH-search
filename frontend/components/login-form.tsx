"use client"

import { useEffect, useState, type FormEvent } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { SeamtechLogo } from "@/components/seamtech-logo"
import { resolveNextPath } from "@/lib/authed-fetch"

/**
 * Single shared-password sign-in form (audit issue #5, Option B).
 *
 * Posts to /api/auth/login, which sets an httpOnly session cookie. The cookie
 * is never readable from JavaScript, so this component only has to react to
 * the response status and let the router re-render the gated pages.
 */
export function LoginForm() {
  const router = useRouter()
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [next, setNext] = useState("/")

  // Read ?next= from the URL in an effect rather than useSearchParams(): that
  // hook forces a Suspense boundary on the prerendered login page, and the
  // value is only needed after a successful sign-in anyway.
  useEffect(() => {
    if (typeof window === "undefined") return
    setNext(resolveNextPath(new URLSearchParams(window.location.search).get("next")))
  }, [])

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
        cache: "no-store",
      })
      if (response.ok) {
        setPassword("")
        router.replace(next)
        router.refresh()
        return
      }
      const body = await response.json().catch(() => ({}))
      setError(body?.detail ?? `Sign-in failed (${response.status}).`)
    } catch {
      setError("Could not reach the sign-in endpoint.")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex justify-center">
          <SeamtechLogo className="h-auto w-44" />
        </div>
        <form
          onSubmit={onSubmit}
          className="rounded-xl border border-border bg-card p-5 shadow-sm"
          aria-label="Sign in to SEAMTECH Search"
        >
          <h1 className="text-base font-semibold text-foreground">Sign in</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            SEAMTECH Search is an internal tool. Enter the shared workshop password to continue.
          </p>

          <label htmlFor="password" className="mt-4 block text-xs font-medium text-muted-foreground">
            Password
          </label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            autoFocus
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Shared workshop password"
            className="mt-1"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "login-error" : undefined}
          />

          {error ? (
            <p id="login-error" role="alert" className="mt-3 text-xs text-destructive">
              {error}
            </p>
          ) : null}

          <Button type="submit" size="lg" className="mt-4 w-full" disabled={submitting || password.length === 0}>
            {submitting ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </div>
    </main>
  )
}
