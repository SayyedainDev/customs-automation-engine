import { FileText, UploadCloud, X } from "lucide-react";
import { useId, type ChangeEvent, type DragEvent } from "react";
import { formatBytes } from "../lib/format";

const MAX_BYTES = 10 * 1024 * 1024;

export function FileDropzone({
  label,
  helper,
  file,
  disabled,
  onFile,
}: {
  label: string;
  helper: string;
  file: File | null;
  disabled?: boolean;
  onFile: (file: File | null, error?: string) => void;
}) {
  const inputId = useId();

  function acceptFile(candidate?: File) {
    if (!candidate) return;
    if (
      candidate.type !== "application/pdf" &&
      !candidate.name.toLowerCase().endsWith(".pdf")
    ) {
      onFile(null, "Only PDF documents are supported in this review.");
      return;
    }
    if (candidate.size > MAX_BYTES) {
      onFile(null, "The selected PDF exceeds the 10 MB limit.");
      return;
    }
    onFile(candidate);
  }

  function handleInput(event: ChangeEvent<HTMLInputElement>) {
    acceptFile(event.target.files?.[0]);
    event.target.value = "";
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    if (!disabled) acceptFile(event.dataTransfer.files?.[0]);
  }

  if (file) {
    return (
      <div className="selected-file">
        <span className="selected-file__icon">
          <FileText aria-hidden="true" size={19} />
        </span>
        <div>
          <strong>{file.name}</strong>
          <span>{formatBytes(file.size)} · PDF</span>
        </div>
        <button
          className="icon-button"
          type="button"
          aria-label={`Remove ${file.name}`}
          onClick={() => onFile(null)}
          disabled={disabled}
        >
          <X size={17} />
        </button>
      </div>
    );
  }

  return (
    <label
      className={`dropzone ${disabled ? "dropzone--disabled" : ""}`}
      htmlFor={inputId}
      onDragOver={(event) => event.preventDefault()}
      onDrop={handleDrop}
    >
      <input
        id={inputId}
        type="file"
        accept=".pdf,application/pdf"
        onChange={handleInput}
        disabled={disabled}
      />
      <UploadCloud aria-hidden="true" size={22} />
      <strong>{label}</strong>
      <span>{helper}</span>
      <small>PDF only · maximum 10 MB</small>
    </label>
  );
}
