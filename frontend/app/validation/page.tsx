import { redirect } from "next/navigation"
import { ValidationApp } from "@/components/validation-app"
import { isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

export default async function Page() {
  if (!(await isAuthenticated())) redirect("/login?next=/validation")
  return <ValidationApp />
}
