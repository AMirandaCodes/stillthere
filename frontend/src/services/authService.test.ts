import { describe, it, expect, beforeEach, vi } from "vitest";

// Mock the api module so authService can be imported without axios setup
vi.mock("./api", () => ({ default: { post: vi.fn(), get: vi.fn() } }));

import { authService } from "./authService";

const ACCESS_KEY = "stillthere_access_token";
const REFRESH_KEY = "stillthere_refresh_token";

const tokens = {
  access_token: "test-access-token",
  refresh_token: "test-refresh-token",
  token_type: "bearer" as const,
  expires_in: 1800,
};

describe("authService — token storage", () => {
  beforeEach(() => localStorage.clear());

  describe("getToken", () => {
    it("returns null when nothing is stored", () => {
      expect(authService.getToken()).toBeNull();
    });

    it("returns the access token after setTokens", () => {
      authService.setTokens(tokens);
      expect(authService.getToken()).toBe("test-access-token");
    });
  });

  describe("setTokens", () => {
    it("writes both tokens to localStorage under the correct keys", () => {
      authService.setTokens(tokens);
      expect(localStorage.getItem(ACCESS_KEY)).toBe("test-access-token");
      expect(localStorage.getItem(REFRESH_KEY)).toBe("test-refresh-token");
    });

    it("overwrites previously stored tokens", () => {
      authService.setTokens(tokens);
      authService.setTokens({ ...tokens, access_token: "new-token" });
      expect(localStorage.getItem(ACCESS_KEY)).toBe("new-token");
    });
  });

  describe("clearTokens", () => {
    it("removes both localStorage keys", () => {
      authService.setTokens(tokens);
      authService.clearTokens();
      expect(localStorage.getItem(ACCESS_KEY)).toBeNull();
      expect(localStorage.getItem(REFRESH_KEY)).toBeNull();
    });

    it("is a no-op when called with nothing stored", () => {
      expect(() => authService.clearTokens()).not.toThrow();
    });
  });
});
