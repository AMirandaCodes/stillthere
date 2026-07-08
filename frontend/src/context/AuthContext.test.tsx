import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { AuthProvider, useAuth } from "./AuthContext";

vi.mock("@/services/authService", () => ({
  authService: {
    getToken: vi.fn(),
    getRefreshToken: vi.fn(),
    getMe: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(),
    register: vi.fn(),
    setTokens: vi.fn(),
    clearTokens: vi.fn(),
  },
}));

import { authService } from "@/services/authService";

const mock = authService as unknown as Record<string, ReturnType<typeof vi.fn>>;

const wrapper = ({ children }: { children: ReactNode }) => (
  <AuthProvider>{children}</AuthProvider>
);

const fakeUser = {
  id: "user-1",
  email: "alice@example.com",
  full_name: "Alice",
  is_active: true,
  is_admin: false,
  created_at: "2026-01-01T00:00:00Z",
};

describe("AuthProvider / useAuth", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("starts with user=null and isLoading=false when no token stored", async () => {
    mock.getToken.mockReturnValue(null);
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.user).toBeNull();
  });

  it("restores session from stored token on mount", async () => {
    mock.getToken.mockReturnValue("valid-token");
    mock.getMe.mockResolvedValue(fakeUser);
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.user?.email).toBe("alice@example.com");
  });

  it("clears tokens when getMe returns a 401-like error", async () => {
    mock.getToken.mockReturnValue("expired-token");
    mock.getMe.mockRejectedValue(new Error("401 Unauthorized"));
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(mock.clearTokens).toHaveBeenCalled();
    expect(result.current.user).toBeNull();
  });

  it("sets user after successful login", async () => {
    mock.getToken.mockReturnValue(null);
    mock.login.mockResolvedValue({ access_token: "tok", refresh_token: "ref", token_type: "bearer", expires_in: 3600 });
    mock.getMe.mockResolvedValue(fakeUser);

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.login("alice@example.com", "password");
    });
    expect(result.current.user?.email).toBe("alice@example.com");
  });

  it("clears user after logout", async () => {
    mock.getToken.mockReturnValue("tok");
    mock.getMe.mockResolvedValue(fakeUser);
    mock.getRefreshToken.mockReturnValue("refresh-tok");
    mock.logout.mockResolvedValue(undefined);

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.user).not.toBeNull();

    await act(async () => {
      await result.current.logout();
    });
    expect(result.current.user).toBeNull();
    expect(mock.clearTokens).toHaveBeenCalled();
  });

  it("clears user even when logout API call fails", async () => {
    mock.getToken.mockReturnValue("tok");
    mock.getMe.mockResolvedValue(fakeUser);
    mock.getRefreshToken.mockReturnValue("refresh-tok");
    mock.logout.mockRejectedValue(new Error("network error"));

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.logout();
    });
    expect(result.current.user).toBeNull();
  });

  it("throws when useAuth is called outside AuthProvider", () => {
    expect(() => renderHook(() => useAuth())).toThrow("useAuth must be used inside AuthProvider");
  });
});
