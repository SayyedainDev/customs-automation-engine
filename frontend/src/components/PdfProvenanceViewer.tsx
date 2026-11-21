import { useEffect, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  FileSearch,
  ZoomIn,
  ZoomOut,
  X,
} from "lucide-react";
import { api } from "../api/client";
import type { DocumentPagePreviewResponse } from "../api/types";

export interface PdfInspectTarget {
  documentId: string;
  documentLabel: string;
  pageNumber: number;
  highlightValue?: string | null;
  fieldName?: string | null;
  fieldLabel?: string | null;
}

export function PdfProvenanceViewer({
  target,
  availableTargets,
  selectedTargetIndex,
  onSelectTarget,
  onClose,
}: {
  target: PdfInspectTarget;
  availableTargets?: PdfInspectTarget[];
  selectedTargetIndex?: number;
  onSelectTarget?: (index: number) => void;
  onClose?: () => void;
}) {
  const [page, setPage] = useState<number>(Math.max(1, target.pageNumber || 1));
  const [zoomed, setZoomed] = useState(false);
  const [preview, setPreview] = useState<DocumentPagePreviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPage(Math.max(1, target.pageNumber || 1));
  }, [target.documentId, target.pageNumber, target.highlightValue, target.fieldName]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    api
      .getDocumentPagePreview(target.documentId, page, {
        highlight: target.highlightValue,
        fieldName: target.fieldName,
      })
      .then((res) => {
        if (!active) return;
        setPreview(res);
      })
      .catch((err) => {
        if (!active) return;
        setPreview(null);
        setError(
          err instanceof Error
            ? err.message
            : "The source PDF page could not be rendered.",
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [target.documentId, page, target.highlightValue, target.fieldName]);

  let statusText = "Showing source document page.";
  if (loading) {
    statusText = "Rendering page preview…";
  } else if (preview) {
    if (preview.word_count === 0) {
      statusText =
        "Scanned page — visual preview only (no embedded text coordinates).";
    } else if (preview.highlight_query && preview.highlights.length > 0) {
      const count = preview.highlights.length;
      const prefix = target.fieldLabel ? `${target.fieldLabel}: ` : "";
      statusText = `Highlighting ${prefix}${preview.highlight_query} (${count} ${count === 1 ? "match" : "matches"})`;
    } else if (preview.highlight_query) {
      statusText = `Could not pinpoint “${preview.highlight_query}” on page ${preview.page_number}; showing full page.`;
    }
  }

  return (
    <aside className="pdf-viewer" aria-label="Source PDF provenance preview">
      <div className="pdf-viewer__header">
        <div className="pdf-viewer__title">
          <FileSearch size={16} aria-hidden="true" />
          <div>
            <strong>{target.documentLabel}</strong>
            {preview?.original_filename ? (
              <span className="pdf-viewer__filename">
                {preview.original_filename}
              </span>
            ) : null}
          </div>
        </div>

        <div className="pdf-viewer__controls">
          {preview && preview.page_count > 1 ? (
            <div className="pdf-viewer__pager">
              <button
                type="button"
                className="icon-button"
                aria-label="Previous page"
                disabled={page <= 1 || loading}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                <ChevronLeft size={14} aria-hidden="true" />
              </button>
              <span className="pdf-viewer__page-label">
                Page {page} / {preview.page_count}
              </span>
              <button
                type="button"
                className="icon-button"
                aria-label="Next page"
                disabled={page >= preview.page_count || loading}
                onClick={() => setPage((p) => Math.min(preview.page_count, p + 1))}
              >
                <ChevronRight size={14} aria-hidden="true" />
              </button>
            </div>
          ) : null}

          <button
            type="button"
            className="button button--secondary button--compact"
            onClick={() => setZoomed((prev) => !prev)}
            disabled={!preview}
          >
            {zoomed ? (
              <ZoomOut size={14} aria-hidden="true" />
            ) : (
              <ZoomIn size={14} aria-hidden="true" />
            )}
            {zoomed ? "Fit" : "1.5x"}
          </button>

          {onClose ? (
            <button
              type="button"
              className="icon-button"
              aria-label="Close preview"
              onClick={onClose}
            >
              <X size={14} aria-hidden="true" />
            </button>
          ) : null}
        </div>
      </div>

      {availableTargets && availableTargets.length > 1 && onSelectTarget ? (
        <div className="pdf-viewer__tabs" role="tablist" aria-label="Source documents">
          {availableTargets.map((item, idx) => (
            <button
              key={`${item.documentId}-${item.fieldName ?? idx}`}
              type="button"
              role="tab"
              aria-selected={selectedTargetIndex === idx}
              className={`pdf-viewer__tab ${
                selectedTargetIndex === idx ? "pdf-viewer__tab--active" : ""
              }`}
              onClick={() => onSelectTarget(idx)}
            >
              {item.documentLabel}
              {item.pageNumber ? ` (p.${item.pageNumber})` : ""}
            </button>
          ))}
        </div>
      ) : null}

      <p className="pdf-viewer__status" role="status">
        {statusText}
      </p>

      {error ? (
        <p className="pdf-viewer__error" role="alert">
          Preview unavailable: {error}
        </p>
      ) : null}

      {preview ? (
        <div className="pdf-viewer__viewport">
          <div
            className="pdf-viewer__stage"
            style={{ width: zoomed ? "150%" : "100%" }}
          >
            <img
              className="pdf-viewer__image"
              src={api.documentPageImageUrl(preview.image_url)}
              alt={`${target.documentLabel} page ${preview.page_number}`}
            />
            {preview.highlights.map((box, index) => (
              <div
                key={`${box.x0}-${box.y0}-${index}`}
                className={`pdf-viewer__box ${
                  index === 0
                    ? "pdf-viewer__box--primary"
                    : "pdf-viewer__box--secondary"
                }`}
                style={{
                  left: `${box.left_pct}%`,
                  top: `${box.top_pct}%`,
                  width: `${box.width_pct}%`,
                  height: `${box.height_pct}%`,
                }}
                title={box.text}
                aria-label={`Matched text: ${box.text}`}
              />
            ))}
          </div>
        </div>
      ) : null}
    </aside>
  );
}
