import axios from "axios";
import type { PaginatedResponse } from "@/types/common";
import type { TokenResponse } from "@/types/auth";
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

let isRefreshing = false;

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config as typeof error.config & { _retry?: boolean };

    if (error.response?.status === 401 && !original._retry && !isRefreshing) {
      original._retry = true;
      isRefreshing = true;
      try {
        const refreshToken = localStorage.getItem(REFRESH_KEY);
        if (!refreshToken) throw new Error("no refresh token");
        const res = await axios.post<TokenResponse>("/api/v1/auth/refresh", {
          refresh_token: refreshToken,
        });
        localStorage.setItem(ACCESS_KEY, res.data.access_token);
        localStorage.setItem(REFRESH_KEY, res.data.refresh_token);
        original.headers = original.headers ?? {};
        original.headers.Authorization = `Bearer ${res.data.access_token}`;
        return api(original);
      } catch {
        localStorage.removeItem(ACCESS_KEY);
        localStorage.removeItem(REFRESH_KEY);
        if (window.location.pathname !== "/login") window.location.href = "/login";
      } finally {
        isRefreshing = false;
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
