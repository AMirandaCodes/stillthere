import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import FullScreenSpinner from "./ui/FullScreenSpinner";

export default function ProtectedRoute() {
  const { user, isLoading } = useAuth();

  if (isLoading) return <FullScreenSpinner />;
  return user ? <Outlet /> : <Navigate to="/login" replace />;
}
