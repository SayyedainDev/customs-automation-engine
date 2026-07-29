import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { NewReviewPage } from "./pages/NewReviewPage";
import { PrepareExportPage } from "./pages/PrepareExportPage";
import { OverviewPage } from "./pages/OverviewPage";
import { RegulatoryAssistantPage } from "./pages/RegulatoryAssistantPage";
import { SearchPage } from "./pages/SearchPage";

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<OverviewPage />} />
        <Route path="prepare" element={<PrepareExportPage />} />
        <Route path="ask" element={<RegulatoryAssistantPage />} />
        <Route path="review" element={<NewReviewPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
