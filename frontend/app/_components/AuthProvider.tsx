"use client";

/**
 * The signed-in account, shared across the app (Milestone 5, Requirement 5.1).
 *
 * Hydrates once on mount via `/me` — but only when a token is actually stored,
 * so a logged-out visitor costs no round trip and Milestone 1–4's anonymous
 * flow starts exactly as fast as before. A token the backend rejects is already
 * cleared by `lib/api`; here it simply resolves to a logged-out state.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import {
  login as apiLogin,
  logout as apiLogout,
  me,
  signup as apiSignup,
  type ApiResult,
} from "@/lib/api";
import { getSessionToken } from "@/lib/auth";
import type { AuthResponse, User } from "@/types/api";

type Credentials = (email: string, password: string) => Promise<ApiResult<AuthResponse>>;

interface AuthValue {
  user: User | null;
  /** True until the initial `/me` hydration settles. */
  loading: boolean;
  signup: Credentials;
  login: Credentials;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!getSessionToken()) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    void me().then((result) => {
      if (cancelled) return;
      setUser(result.ok ? result.data : null);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Each returns the ApiResult unchanged so the form can render the backend's
  // plain-language message without this provider inventing an error shape.
  const establish = useCallback(
    (call: Credentials): Credentials =>
      async (email, password) => {
        const result = await call(email, password);
        if (result.ok) setUser(result.data.user);
        return result;
      },
    [],
  );

  const logout = useCallback(async () => {
    await apiLogout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        signup: establish(apiSignup),
        login: establish(apiLogin),
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error("useAuth must be used inside an AuthProvider");
  }
  return value;
}
