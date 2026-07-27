import { AlertCircle, X } from "lucide-react";

export function ErrorNotice({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss?: () => void;
}) {
  return (
    <div className="notice notice--danger" role="alert">
      <AlertCircle aria-hidden="true" size={18} />
      <div>
        <strong>Request could not be completed</strong>
        <p>{message}</p>
      </div>
      {onDismiss ? (
        <button
          className="icon-button"
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss error"
        >
          <X size={16} />
        </button>
      ) : null}
    </div>
  );
}
