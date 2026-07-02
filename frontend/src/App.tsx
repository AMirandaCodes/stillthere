import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "@/components/layout/Layout";
import ProtectedRoute from "@/components/ProtectedRoute";
import LoginPage from "@/pages/LoginPage";
import RegisterPage from "@/pages/RegisterPage";
import HomePage from "@/pages/HomePage";
import BatchUploadPage from "@/pages/BatchUploadPage";
import VerificationResultPage from "@/pages/VerificationResultPage";
import SearchHistoryPage from "@/pages/SearchHistoryPage";
import BatchJobsPage from "@/pages/BatchJobsPage";

export default function App() {
  return (
    <Routes>
      {/* Auth pages */}
      <Route path="/login"    element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />

      {/* Guest-accessible pages — still wrapped in Layout for nav */}
      <Route element={<Layout />}>
        <Route path="/"            element={<HomePage />} />
        <Route path="/results/:id" element={<VerificationResultPage />} />
      </Route>

      {/* Auth-required pages */}
      <Route element={<ProtectedRoute />}>
        <Route element={<Layout />}>
          <Route path="/history" element={<SearchHistoryPage />} />
          <Route path="/batch"   element={<BatchUploadPage />} />
          <Route path="/jobs"    element={<BatchJobsPage />} />
        </Route>
      </Route>

      {/* Fallback */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
