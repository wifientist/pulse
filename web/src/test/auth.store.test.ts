import { describe, expect, it, beforeEach } from "vitest";

import { useAuthStore } from "../store/auth";

describe("auth store", () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({ token: null });
  });

  it("persists token to localStorage on setToken", () => {
    useAuthStore.getState().setToken("abc123");
    expect(localStorage.getItem("pulse_admin_token")).toBe("abc123");
    expect(useAuthStore.getState().token).toBe("abc123");
  });

  it("removes token from localStorage on clearToken", () => {
    useAuthStore.getState().setToken("abc");
    useAuthStore.getState().clearToken();
    expect(localStorage.getItem("pulse_admin_token")).toBeNull();
    expect(useAuthStore.getState().token).toBeNull();
  });
});
