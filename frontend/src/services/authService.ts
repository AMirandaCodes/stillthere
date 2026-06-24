import api from "./api";
import type { LoginRequest, RegisterRequest, TokenResponse, UserResponse } from "@/types/auth";

const ACCESS_KEY = "stillthere_access_token";
const REFRESH_KEY = "stillthere_refresh_token";

export const authService = {
  getToken(): string | null {
    return localStorage.getItem(ACCESS_KEY);
  },

  setTokens(tokens: TokenResponse): void {
    localStorage.setItem(ACCESS_KEY, tokens.access_token);
    localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
  },

  clearTokens(): void {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },

  async login(data: LoginRequest): Promise<TokenResponse> {
    const res = await api.post<TokenResponse>("/v1/auth/login", data);
    this.setTokens(res.data);
    return res.data;
  },

  async register(data: RegisterRequest): Promise<UserResponse> {
    const res = await api.post<UserResponse>("/v1/auth/register", data);
    return res.data;
  },

  async getMe(): Promise<UserResponse> {
    const res = await api.get<UserResponse>("/v1/auth/me");
    return res.data;
  },

  async logout(refreshToken: string): Promise<void> {
    try {
      await api.post("/v1/auth/logout", { refresh_token: refreshToken });
    } catch {
      // Ignore errors on logout — clear locally regardless
    } finally {
      this.clearTokens();
    }
  },
};
