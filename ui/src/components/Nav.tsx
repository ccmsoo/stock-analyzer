"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "매수 후보" },
  { href: "/radar", label: "레이더" },
  { href: "/opportunities", label: "오르기 전" },
  { href: "/themes", label: "테마" },
  { href: "/chains", label: "밸류체인" },
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
