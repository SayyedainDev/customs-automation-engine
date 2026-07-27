import { Info, LoaderCircle, Upload } from "lucide-react";
import type { FormEvent } from "react";
import type { DocumentReviewController } from "../hooks/useDocumentReview";
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

      <section className="panel" aria-labelledby="dates-heading">
        <div className="panel__header">
          <div>
            <p className="eyebrow">Step 2</p>
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
            <p className="eyebrow">Step 3</p>
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
