import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { authService } from "@/services/authService";
import type { UserResponse } from "@/types/auth";

interface AuthContextValue {
  user: UserResponse | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, fullName: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const token = authService.getToken();
    if (!token) {
      setIsLoading(false);
      return;
    }
    authService
      .getMe()
      .then(setUser)
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message.toLowerCase() : "";
        if (msg.includes("401") || msg.includes("unauthorized") || msg.includes("not authenticated")) {
          authService.clearTokens();
        }
      })
      .finally(() => setIsLoading(false));
  }, []);

  async function login(email: string, password: string) {
    await authService.login({ email, password });
    const me = await authService.getMe();
    setUser(me);
  }

  async function register(email: string, fullName: string, password: string) {
    await authService.register({ email, full_name: fullName, password });
    await login(email, password);
  }

  async function logout() {
    const refreshToken = authService.getRefreshToken() ?? "";
    try {
      await authService.logout(refreshToken);
    } catch {
      // Server-side revocation failed; clear local state anyway.
      // The refresh token will expire naturally on the backend.
    }
    authService.clearTokens();
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ user, isLoading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
