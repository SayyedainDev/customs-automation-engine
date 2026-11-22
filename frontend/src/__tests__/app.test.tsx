import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
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
    fireEvent.click(button);

    await waitFor(() => {
      expect(loadSpy).toHaveBeenCalledWith("clean_cotton_tshirts");
    });
  });
});
