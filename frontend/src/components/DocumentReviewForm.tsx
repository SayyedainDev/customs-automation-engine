import { FilePlus2, Info, LoaderCircle, Upload, X } from "lucide-react";
import { type FormEvent, useState } from "react";
import type { DocumentReviewController } from "../hooks/useDocumentReview";
import {
  SUPPORTING_DOCUMENT_TYPES,
  supportingDocumentLabel,
} from "../lib/supportingDocuments";
import { FileDropzone } from "./FileDropzone";
import { ProcessingTimeline } from "./ProcessingTimeline";

function UploadProgress({
  label,
  value,
}: {
  label: string;
  value: number;
}) {
  if (value <= 0 || value >= 100) return null;
  return (
    <div className="progress-row">
      <div className="progress-row__meta">
        <span>{label}</span>
        <span>{value}%</span>
      </div>
      <div
        className="progress-bar"
        role="progressbar"
        aria-label={`${label} progress`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value}
      >
        <span style={{ width: `${value}%` }} />
      </div>
    </div>
  );
}

function SupportingDocumentsSection({
  review,
}: {
  review: DocumentReviewController;
}) {
  const [pendingType, setPendingType] = useState(
    SUPPORTING_DOCUMENT_TYPES[0].value,
  );
  const locked = review.reviewBusy || Boolean(review.compliance);
  const usedTypes = new Set(review.supportingSlots.map((slot) => slot.documentType));

  return (
    <section className="panel" aria-labelledby="supporting-heading">
      <div className="panel__header">
        <div>
          <p className="eyebrow">Step 2 · optional</p>
          <h2 id="supporting-heading">Supporting documents</h2>
          <p>
            Attach any customs documents you already have. Each one is read
            and cross-checked against the invoice before it can count as
            present — adding it here is what lets a requirement pass instead
            of showing up as outstanding.
          </p>
        </div>
      </div>

      <div className="panel__body">
        <div className="supporting-add-row">
          <label className="form-field supporting-add-row__select">
            <span>Document type</span>
            <select
              value={pendingType}
              onChange={(event) => setPendingType(event.target.value)}
              disabled={locked}
            >
              {SUPPORTING_DOCUMENT_TYPES.map((type) => (
                <option key={type.value} value={type.value}>
                  {type.label}
                  {usedTypes.has(type.value) ? " (added)" : ""}
                </option>
              ))}
            </select>
          </label>
          <button
            className="button button--secondary"
            type="button"
            disabled={locked}
            onClick={() => review.addSupportingSlot(pendingType)}
          >
            <FilePlus2 aria-hidden="true" size={16} />
            Add document
          </button>
        </div>

        {review.supportingSlots.length ? (
          <div className="supporting-slot-list">
            {review.supportingSlots.map((slot) => (
              <div className="supporting-slot" key={slot.id}>
                <div className="supporting-slot__head">
                  <strong>{supportingDocumentLabel(slot.documentType)}</strong>
                  <button
                    className="icon-button"
                    type="button"
                    aria-label={`Remove ${supportingDocumentLabel(slot.documentType)}`}
                    onClick={() => review.removeSupportingSlot(slot.id)}
                    disabled={locked}
                  >
                    <X size={16} />
                  </button>
                </div>
                <FileDropzone
                  label={supportingDocumentLabel(slot.documentType)}
                  helper="PDF of the document as issued"
                  file={slot.file}
                  disabled={locked}
                  onFile={(file, validationError) =>
                    review.chooseSupportingFile(slot.id, file, validationError)
                  }
                />
                {slot.error ? (
                  <p className="supporting-slot__error">{slot.error}</p>
                ) : null}
                <UploadProgress
                  label={`Uploading ${supportingDocumentLabel(slot.documentType).toLowerCase()}`}
                  value={slot.progress}
                />
              </div>
            ))}
          </div>
        ) : (
          <p className="supporting-empty">
            No supporting documents attached yet. The review still runs
            without them — missing ones will show up as outstanding
            requirements.
          </p>
        )}
      </div>
    </section>
  );
}

export function DocumentReviewForm({
  review,
}: {
  review: DocumentReviewController;
}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void review.runCompliance();
  }

  return (
    <form className="review-form" onSubmit={submit}>
      <section className="panel" aria-labelledby="documents-heading">
        <div className="panel__header">
          <div>
            <p className="eyebrow">Step 1</p>
            <h2 id="documents-heading">Documents needed to start</h2>
            <p>Select one invoice and its matching packing list.</p>
          </div>
        </div>

        <div className="panel__body">
          <div className="notice notice--info input-scope-note">
            <Info aria-hidden="true" size={18} />
            <div>
              <strong>Only these two PDFs are needed to run the review</strong>
              <p>
                You do not need Form-E, a certificate of origin, or other
                supporting documents at this step. If a customs rule requires
                one, the result will identify it as a missing supporting
                document and explain what to do next.
              </p>
            </div>
          </div>

          <div className="upload-grid">
            <div className="stack">
              <FileDropzone
                label="Commercial invoice"
                helper="Contains value, product, buyer, and PCT details"
                file={review.invoiceFile}
                disabled={review.reviewBusy || Boolean(review.compliance)}
                onFile={review.chooseInvoice}
              />
              <UploadProgress
                label="Uploading invoice"
                value={review.invoiceProgress}
              />
            </div>

            <div className="stack">
              <FileDropzone
                label="Packing list"
                helper="Contains package, quantity, and weight details"
                file={review.packingFile}
                disabled={review.reviewBusy || Boolean(review.compliance)}
                onFile={review.choosePackingList}
              />
              <UploadProgress
                label="Uploading packing list"
                value={review.packingProgress}
              />
            </div>
          </div>
        </div>
      </section>

      <SupportingDocumentsSection review={review} />

      <section className="panel" aria-labelledby="dates-heading">
        <div className="panel__header">
          <div>
            <p className="eyebrow">Step 3</p>
            <h2 id="dates-heading">Shipment context</h2>
            <p>
              Dates are optional, but improve effective-date and
              letter-of-credit checks.
            </p>
          </div>
        </div>
        <div className="panel__body">
          <div className="form-grid">
            <label className="form-field">
              <span>Shipment date</span>
              <input
                type="date"
                value={review.shipmentDate}
                onChange={(event) =>
                  review.setShipmentDate(event.target.value)
                }
                disabled={review.reviewBusy || Boolean(review.compliance)}
              />
              <small>Date the goods are expected to ship.</small>
            </label>
            <label className="form-field">
              <span>Letter of credit date</span>
              <input
                type="date"
                value={review.letterOfCreditDate}
                onChange={(event) =>
                  review.setLetterOfCreditDate(event.target.value)
                }
                disabled={review.reviewBusy || Boolean(review.compliance)}
              />
              <small>Leave blank when the shipment does not use one.</small>
            </label>
          </div>
        </div>
      </section>

      <section
        className="panel processing-panel"
        aria-labelledby="processing-heading"
      >
        <div className="panel__header">
          <div>
            <p className="eyebrow">Step 4</p>
            <h2 id="processing-heading">Processing</h2>
            <p>Each stage reflects a completed backend operation.</p>
          </div>
        </div>
        <div className="panel__body">
          <ProcessingTimeline stages={review.stages} />
          {!review.compliance ? (
            <div className="form-actions">
              <p>
                The review can take several minutes on a cold Railway service
                or when OCR is needed.
              </p>
              <button
                className="button button--primary"
                type="submit"
                disabled={
                  !review.invoiceFile ||
                  !review.packingFile ||
                  review.reviewBusy
                }
              >
                {review.reviewBusy ? (
                  <LoaderCircle
                    className="spinner"
                    aria-hidden="true"
                    size={16}
                  />
                ) : (
                  <Upload aria-hidden="true" size={16} />
                )}
                {review.reviewBusy
                  ? "Processing documents…"
                  : "Check invoice and packing list"}
              </button>
            </div>
          ) : null}
        </div>
      </section>
    </form>
  );
}
