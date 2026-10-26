import { redirect } from "next/navigation"
import { ConfianceApp } from "@/components/confiance-app"
import { isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

export default async function Page() {
  if (!(await isAuthenticated())) redirect("/login?next=/confiance")
  return <ConfianceApp />
}
