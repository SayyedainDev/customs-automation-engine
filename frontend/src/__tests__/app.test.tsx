import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { MultiLineShipmentResponse } from "../api/types";
import { ComplianceReviewResult } from "../components/ComplianceReviewResult";
import { StatusBadge } from "../components/StatusBadge";
import { formatBytes, labelize } from "../lib/format";
import { NewReviewPage } from "../pages/NewReviewPage";
import { OverviewPage } from "../pages/OverviewPage";
import { SessionProvider } from "../state/SessionContext";

describe("format helpers", () => {
  it("formats byte sizes cleanly", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1_572_864)).toBe("1.5 MB");
  });

  it("humanizes internal snake_case status tokens", () => {
    expect(labelize("manual_review")).toBe("Manual Review");
    expect(labelize("commercial_invoice")).toBe("Commercial Invoice");
  });
});

describe("StatusBadge", () => {
  it("renders deterministic compliance statuses", () => {
    render(
      <div>
        <StatusBadge status="passed" />
        <StatusBadge status="failed" />
        <StatusBadge status="manual_review" />
      </div>,
    );
    expect(screen.getByText(/passed/i)).toBeInTheDocument();
    expect(screen.getByText(/failed/i)).toBeInTheDocument();
    expect(screen.getByText(/manual review|review/i)).toBeInTheDocument();
  });
});

describe("OverviewPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listWorkflows").mockResolvedValue({ workflows: [] });
  });

  it("renders professional workspace copy and sample shipment action", async () => {
    render(
      <MemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <SessionProvider>
          <OverviewPage />
        </SessionProvider>
      </MemoryRouter>,
    );

    expect(screen.getByText(/Customs review workspace/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Try sample shipment/i })).toHaveAttribute(
      "href",
      "/review?sample=clean_cotton_tshirts",
    );
    expect(screen.queryByText(/class demonstration/i)).not.toBeInTheDocument();

    await waitFor(() => {
      expect(api.listWorkflows).toHaveBeenCalledWith(6);
    });
  });
});

describe("NewReviewPage sample shipment launcher", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("invokes demo sample loader when clicking Clean Textile Export", async () => {
    const loadSpy = vi
      .spyOn(api, "loadDemoSample")
      .mockRejectedValue(new Error("sample load stopped in unit test"));

    render(
      <MemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <SessionProvider>
          <NewReviewPage />
        </SessionProvider>
      </MemoryRouter>,
    );

    const button = screen.getByRole("button", {
      name: /Clean Textile Export \(PASS\)/i,
    });
    expect(
      screen.getByRole("button", {
        name: /Pending Form-E & COO \(HITL Upload\)/i,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: /Quantity Mismatch \(HITL Audit\)/i,
      }),
    ).toBeInTheDocument();
    fireEvent.click(button);

    await waitFor(() => {
      expect(loadSpy).toHaveBeenCalledWith("clean_cotton_tshirts");
    });
  });
});

describe("ComplianceReviewResult readiness, duty, and telemetry panels", () => {
  it("renders pre-clearance readiness score, hold risk, duty summary, and hybrid telemetry", () => {
    const mockResult: MultiLineShipmentResponse = {
      review_revision_id: "11111111-1111-1111-1111-111111111111",
      overall_status: "failed",
      is_compliant: false,
      rule_data_version: "textile-2026.1",
      commercial_invoice_document_id: "22222222-2222-2222-2222-222222222222",
      packing_list_document_id: "33333333-3333-3333-3333-333333333333",
      invoice: {},
      packing_list: {},
      page_reviews: [],
      shipment_level_checks: [],
      items: [],
      fields_requiring_manual_review: [],
      supporting_documents: [],
      document_review_status: "failed",
      outstanding_documents: [],
      clearance_readiness: {
        readiness_score: 50,
        readiness_band: "blocked_document_defect",
        port_hold_risk: "high",
        headline:
          "High port-hold risk — fix 1 in-house document discrepancy before filing on PSW.",
        in_house_fixes_count: 1,
        external_documents_count: 1,
        manual_confirmations_count: 0,
        remediation_steps: [
          {
            priority: 1,
            owner: "exporter_in_house",
            category: "document_discrepancy",
            title: "Line quantity match",
            action: "Reconcile invoice (100) and packing list (99).",
            score_impact: 35,
          },
        ],
      },
      duty_estimation_summary: {
        total_assessed_value: "1000.00",
        total_customs_duty: "200.00",
        total_additional_customs_duty: "60.00",
        total_regulatory_duty: "0.00",
        total_sales_tax: "226.80",
        total_value_addition_tax: "37.80",
        total_advance_income_tax: "83.85",
        total_duties_and_taxes: "608.45",
        total_landed_cost: "1608.45",
        effective_rate_pct: "60.85",
        lines_estimated: 1,
        total_lines: 1,
        currency: "USD",
        basis_note: "FY2026-27 statutory cascading rates.",
      },
      pipeline_telemetry: {
        extraction_mode: "hybrid",
        processing_time_ms: 8.4,
        documents_processed: 2,
        cache_hits: 0,
        fields_from_deterministic: 18,
        fields_from_llm: 0,
        llm_calls: 0,
        tokens_used: 0,
        estimated_tokens_saved: 900,
      },
    };

    render(<ComplianceReviewResult result={mockResult} />);

    expect(screen.getByText(/Readiness score: 50\/100/i)).toBeInTheDocument();
    expect(screen.getByText(/Port hold risk: HIGH/i)).toBeInTheDocument();
    expect(screen.getByText(/Exporter In House · \+35 pts/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Estimated statutory duties & landed cost/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/1608\.45 USD/i)).toBeInTheDocument();
    expect(screen.getByText(/~900/i)).toBeInTheDocument();
  });
});
