import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import ProtectedRoute from "./ProtectedRoute";

vi.mock("@/context/AuthContext", () => ({
  useAuth: vi.fn(),
}));

import { useAuth } from "@/context/AuthContext";

const mockUseAuth = vi.mocked(useAuth);

const noUser = {
  user: null,
  isLoading: false,
  login: vi.fn(),
  logout: vi.fn(),
  register: vi.fn(),
};

const withUser = {
  user: { id: "1", email: "a@b.com", full_name: "A", is_active: true, is_admin: false, created_at: "2026-01-01T00:00:00Z" },
  isLoading: false,
  login: vi.fn(),
  logout: vi.fn(),
  register: vi.fn(),
};

function renderWithRouter(authState: typeof noUser, initialPath = "/protected") {
  mockUseAuth.mockReturnValue(authState);
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route element={<ProtectedRoute />}>
          <Route path="/protected" element={<div>Protected Content</div>} />
        </Route>
        <Route path="/login" element={<div>Login Page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProtectedRoute", () => {
  it("redirects unauthenticated user to /login", () => {
    renderWithRouter(noUser);
    expect(screen.getByText("Login Page")).toBeInTheDocument();
    expect(screen.queryByText("Protected Content")).not.toBeInTheDocument();
  });

  it("renders child routes for authenticated user", () => {
    renderWithRouter(withUser);
    expect(screen.getByText("Protected Content")).toBeInTheDocument();
    expect(screen.queryByText("Login Page")).not.toBeInTheDocument();
  });

  it("shows a spinner while isLoading is true", () => {
    mockUseAuth.mockReturnValue({ ...noUser, isLoading: true });
    render(
      <MemoryRouter initialEntries={["/protected"]}>
        <Routes>
          <Route element={<ProtectedRoute />}>
            <Route path="/protected" element={<div>Protected Content</div>} />
          </Route>
          <Route path="/login" element={<div>Login Page</div>} />
        </Routes>
      </MemoryRouter>,
    );
    // Neither login nor content should be visible while loading
    expect(screen.queryByText("Login Page")).not.toBeInTheDocument();
    expect(screen.queryByText("Protected Content")).not.toBeInTheDocument();
  });
});
