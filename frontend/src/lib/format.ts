import type { ComplianceStatus, ExtractedField } from "../api/types";

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDate(value?: string | null): string {
  if (!value) return "Not available";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("en-PK", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

export function labelize(value?: string | null): string {
  if (!value) return "Not available";
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function fieldValue(field: unknown): unknown {
  if (
    field &&
    typeof field === "object" &&
    "value" in (field as Record<string, unknown>)
  ) {
    return (field as ExtractedField).value;
  }
  return null;
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "Not extracted";
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function statusTone(
  status?: string | null,
): "success" | "warning" | "danger" | "info" | "neutral" {
  if (!status) return "neutral";
  if (
    ["passed", "completed", "extracted", "uploaded", "ok"].includes(status)
  ) {
    return "success";
  }
  if (
    ["manual_review", "awaiting_human_review", "warning"].includes(status)
  ) {
    return "warning";
  }
  if (["failed", "rejected", "error", "critical"].includes(status)) {
    return "danger";
  }
  if (["running", "resuming", "processing", "created"].includes(status)) {
    return "info";
  }
  return "neutral";
}

export function complianceRank(status?: ComplianceStatus): number {
  if (status === "failed") return 3;
  if (status === "manual_review") return 2;
  if (status === "passed") return 1;
  return 0;
}
