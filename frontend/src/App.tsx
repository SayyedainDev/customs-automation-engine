import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { CustomsChatPage } from "./pages/CustomsChatPage";
import { NewReviewPage } from "./pages/NewReviewPage";
import { PrepareExportPage } from "./pages/PrepareExportPage";
import { OverviewPage } from "./pages/OverviewPage";
import { SearchPage } from "./pages/SearchPage";

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<OverviewPage />} />
        <Route path="prepare" element={<PrepareExportPage />} />
        {/* Superseded RegulatoryAssistantPage (textile-only, template-first
            answers) - see docs/CUSTOMS_CHATBOT_ARCHITECTURE.md. The old page
            component and its backend route are left in place, unrouted,
            rather than deleted outright. */}
        <Route path="ask" element={<CustomsChatPage />} />
        <Route path="customs-chat" element={<CustomsChatPage />} />
        <Route path="review" element={<NewReviewPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
