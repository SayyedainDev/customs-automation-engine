import { FileSearch } from "lucide-react";
import type { ReactNode } from "react";

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <FileSearch aria-hidden="true" size={24} />
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
