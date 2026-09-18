import { redirect } from "next/navigation"
import { SearchApp } from "@/components/search-app"
import { isAuthenticated } from "@/lib/auth"

// Server component: the auth gate for the whole UI (audit issue #5, Option B).
// Without a valid session cookie the operator never reaches the search screen,
// and — more importantly — every app/api/* route refuses to forward
// SEAMTECH_AUTH_TOKEN to the backend on their behalf.
export const dynamic = "force-dynamic"

export default async function Page() {
  if (!(await isAuthenticated())) {
    redirect("/login")
  }
  return <SearchApp />
}
