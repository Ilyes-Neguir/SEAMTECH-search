import { redirect } from "next/navigation"
import { LoginForm } from "@/components/login-form"
import { SeamtechLogo } from "@/components/seamtech-logo"
import { isAuthConfigured, isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

export const metadata = {
  title: "Sign in — SEAMTECH Search",
}

export default async function LoginPage() {
  if (await isAuthenticated()) {
    redirect("/")
  }

  if (!isAuthConfigured()) {
    // Fail closed and say exactly why, instead of showing a form that can
    // never succeed.
    return (
      <main className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
        <div className="w-full max-w-sm">
          <div className="mb-6 flex justify-center">
            <SeamtechLogo className="h-auto w-44" />
          </div>
          <div className="rounded-xl border border-destructive/40 bg-card p-5" role="alert">
            <h1 className="text-base font-semibold text-foreground">Sign-in is not configured</h1>
            <p className="mt-2 text-xs text-muted-foreground">
              This server has no <code className="text-foreground">SEAMTECH_UI_PASSWORD</code>. Every login attempt is
              refused rather than allowed, so the archive stays closed.
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              Set <code className="text-foreground">SEAMTECH_UI_PASSWORD</code> and{" "}
              <code className="text-foreground">SEAMTECH_SESSION_SECRET</code> in <code>.env</code>, then restart the
              frontend container.
            </p>
          </div>
        </div>
      </main>
    )
  }

  return <LoginForm />
}
