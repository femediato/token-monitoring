"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  { href: "/", label: "Universe" },
  { href: "/discovery", label: "Discovery" },
  { href: "/health", label: "Collection health" },
];

export default function Nav() {
  const path = usePathname();
  return (
    <nav className="nav">
      <Link href="/" className="brand">
        Auto LPing<span>explorer</span>
      </Link>
      {ITEMS.map((i) => {
        const active = i.href === "/" ? path === "/" || path.startsWith("/tokens") : path.startsWith(i.href);
        return (
          <Link key={i.href} href={i.href} className={`item${active ? " active" : ""}`}>
            {i.label}
          </Link>
        );
      })}
      <div className="spacer" />
      <span className="note">Read-only · paper research data · refreshes every minute</span>
    </nav>
  );
}
