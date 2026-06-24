import axios from "axios";

const api = axios.create({
  baseURL: "/api",
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("stillthere_access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("stillthere_access_token");
      localStorage.removeItem("stillthere_refresh_token");
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    const message =
      error.response?.data?.detail ?? error.message ?? "An unexpected error occurred";
    return Promise.reject(new Error(message));
  }
);

export default api;
