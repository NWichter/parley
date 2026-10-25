"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";

export function HealthBadge() {
  const [state, setState] = useState<"loading" | "ok" | "down">("loading");

  useEffect(() => {
    fetch("/api/health")
      .then((r) => setState(r.ok ? "ok" : "down"))
      .catch(() => setState("down"));
  }, []);

  if (state === "loading") return <Badge variant="secondary">API …</Badge>;
  return (
    <Badge variant={state === "ok" ? "default" : "destructive"}>
      API {state}
    </Badge>
  );
}
