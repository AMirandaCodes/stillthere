import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import FullScreenSpinner from "./ui/FullScreenSpinner";

export default function AdminRoute() {
  const { user, isLoading } = useAuth();

  if (isLoading) return <FullScreenSpinner />;
  if (!user) return <Navigate to="/login" replace />;
  if (!user.is_admin) return <Navigate to="/" replace />;
  return <Outlet />;
}
