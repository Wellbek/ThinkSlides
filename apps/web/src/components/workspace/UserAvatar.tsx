"use client";

import { useEffect, useState } from "react";
import { initAuth } from "@/lib/auth";

function initialsOf(name: string): string {
  const parts = name.trim().split(/[\s@._-]+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts[0][0] + (parts[1]?.[0] ?? "")).toUpperCase();
}

/** The signed-in user's initials. Read after mount: the token lives in localStorage. */
export function UserAvatar({ title = "You" }: { title?: string }) {
  const [initials, setInitials] = useState("");

  useEffect(() => {
    const user = initAuth().user;
    setInitials(user ? initialsOf(user.displayName || user.email) : "?");
  }, []);

  return <span className="ws-av indigo" title={title}>{initials}</span>;
}
