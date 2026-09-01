import Image from "next/image"

export function SeamtechLogo({ className }: { className?: string }) {
  return (
    <Image
      src="/seamtech-logo.png"
      alt="SEAMTECH"
      width={394}
      height={98}
      priority
      className={className}
    />
  )
}
