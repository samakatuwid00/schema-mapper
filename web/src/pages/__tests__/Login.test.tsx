import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../../auth";
import Login from "../Login";

vi.mock("../../api/client", () => ({
  me: vi.fn(() =>
    Promise.reject(Object.assign(new Error("Not authenticated"), { status: 401, detail: "Not authenticated" })),
  ),
  login: vi.fn(),
  logout: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}));

import * as api from "../../api/client";

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<div>OVERVIEW_PAGE</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("Login", () => {
  beforeEach(() => {
    vi.mocked(api.login).mockReset();
  });

  it("renders username/password fields and disables submit until filled", async () => {
    const user = userEvent.setup();
    renderLogin();

    const submit = screen.getByRole("button", { name: /sign in/i });
    expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText(/username/i), "ops");
    await user.type(screen.getByLabelText(/password/i), "secret");
    expect(submit).toBeEnabled();
  });

  it("shows the API error detail when login fails", async () => {
    const user = userEvent.setup();
    vi.mocked(api.login).mockRejectedValue(
      Object.assign(new Error("Invalid credentials"), { status: 401, detail: "Invalid credentials" }),
    );
    renderLogin();

    await user.type(screen.getByLabelText(/username/i), "ops");
    await user.type(screen.getByLabelText(/password/i), "wrong");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Invalid credentials");
  });

  it("logs in and navigates to the overview on success", async () => {
    const user = userEvent.setup();
    vi.mocked(api.login).mockResolvedValue({ username: "ops", role: "admin" });
    renderLogin();

    await user.type(screen.getByLabelText(/username/i), "ops");
    await user.type(screen.getByLabelText(/password/i), "secret");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("OVERVIEW_PAGE")).toBeInTheDocument();
    expect(api.login).toHaveBeenCalledWith("ops", "secret");
  });
});
