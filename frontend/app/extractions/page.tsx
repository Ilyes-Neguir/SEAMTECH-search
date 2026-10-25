import { redirect } from "next/navigation"
import { ExtractionsApp } from "@/components/extractions-app"
import { isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

export default async function Page() {
  if (!(await isAuthenticated())) redirect("/login?next=/extractions")
  return <ExtractionsApp />
}
