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
type QueueEntry = { resolve: (token: string) => void; reject: (err: unknown) => void };
let pendingQueue: QueueEntry[] = [];

function drainQueue(token: string) {
  pendingQueue.forEach(({ resolve }) => resolve(token));
  pendingQueue = [];
}

function rejectQueue(err: unknown) {
  pendingQueue.forEach(({ reject }) => reject(err));
  pendingQueue = [];
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config as typeof error.config & { _retry?: boolean };

    if (error.response?.status === 401 && !original._retry) {
      // A refresh is already in-flight — queue this request to retry once it completes.
      if (isRefreshing) {
        return new Promise<string>((resolve, reject) => {
          pendingQueue.push({ resolve, reject });
        }).then((token) => {
          original.headers = original.headers ?? {};
          original.headers.Authorization = `Bearer ${token}`;
          return api(original);
        });
      }

      original._retry = true;
      isRefreshing = true;
      try {
        const refreshToken = localStorage.getItem(REFRESH_KEY);
        if (!refreshToken) throw new Error("no refresh token");
        const res = await axios.post<TokenResponse>("/api/v1/auth/refresh", {
          refresh_token: refreshToken,
        });
        const newToken = res.data.access_token;
        localStorage.setItem(ACCESS_KEY, newToken);
        localStorage.setItem(REFRESH_KEY, res.data.refresh_token);
        drainQueue(newToken);
        original.headers = original.headers ?? {};
        original.headers.Authorization = `Bearer ${newToken}`;
        return api(original);
      } catch (err) {
        rejectQueue(err);
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
