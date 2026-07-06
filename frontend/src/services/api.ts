import axios from "axios";
import type { PaginatedResponse } from "@/types/common";
import { ACCESS_KEY, REFRESH_KEY } from "@/services/authService";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api",
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(ACCESS_KEY);
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem(ACCESS_KEY);
      localStorage.removeItem(REFRESH_KEY);
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    const detail = error.response?.data?.detail;
    const message = Array.isArray(detail)
      ? detail.map((e: { msg?: string }) => e.msg ?? String(e)).join("; ")
      : (detail ?? error.message ?? "An unexpected error occurred");
    return Promise.reject(new Error(String(message)));
  }
);

export async function getPaginated<T>(
  path: string,
  page: number,
  pageSize = 20,
): Promise<PaginatedResponse<T>> {
  const res = await api.get<PaginatedResponse<T>>(path, {
    params: { page, page_size: pageSize },
  });
  return res.data;
}

export default api;
