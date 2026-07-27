import { AlertTriangle, CheckCircle2, Circle, LoaderCircle, XCircle } from "lucide-react";
import { labelize, statusTone } from "../lib/format";

export function StatusBadge({ status }: { status?: string | null }) {
  const tone = statusTone(status);
  const Icon =
    tone === "success"
      ? CheckCircle2
      : tone === "warning"
        ? AlertTriangle
        : tone === "danger"
          ? XCircle
          : tone === "info"
            ? LoaderCircle
            : Circle;

  return (
    <span className={`status-badge status-badge--${tone}`}>
      <Icon aria-hidden="true" size={13} />
      {labelize(status ?? "pending")}
    </span>
  );
}
