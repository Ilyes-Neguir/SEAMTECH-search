import { redirect } from "next/navigation"
import { NotificationsApp } from "@/components/notifications-app"
import { isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

export default async function Page() {
  if (!(await isAuthenticated())) redirect("/login?next=/notifications")
  return <NotificationsApp />
}
