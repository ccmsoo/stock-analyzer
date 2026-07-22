"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

// 핵심 3 + 시그널 탐색만 노출 — 구세대 페이지(/, /opportunities, /themes, /chains)는
// 라우트는 살아있지만 메뉴에선 뺌 (검증 안 된 화면이 시선을 분산시키지 않게)
const NAV = [
  { href: "/radar", label: "레이더" },
  { href: "/track", label: "성적표" },
  { href: "/portfolio", label: "보유 종목" },
  { href: "/signals", label: "전체 시그널" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav className="border-b border-border">
      <div className="container">
        <ul className="flex items-baseline gap-4 overflow-x-auto py-2 text-sm sm:gap-6">
          {NAV.map((item) => {
            const active = pathname === item.href;
            return (
              <li key={item.href} className="shrink-0">
                <Link
                  href={item.href}
                  className={cn(
                    "block whitespace-nowrap border-b-2 pb-2 transition-colors",
                    active
                      ? "border-foreground font-medium text-foreground"
                      : "border-transparent text-muted-foreground hover:text-foreground",
                  )}
                >
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </div>
    </nav>
  );
}
