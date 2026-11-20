import type { TokenResponse, UserRead } from "./api/types";

const TOKEN_STORAGE_KEY = "ims_v2_access_token";
const USER_STORAGE_KEY = "ims_v2_cached_user";

export function getStoredToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string | null): void {
  try {
    if (token) {
      window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
    } else {
      window.localStorage.removeItem(TOKEN_STORAGE_KEY);
      window.localStorage.removeItem(USER_STORAGE_KEY);
    }
  } catch {
    // Ignore restricted storage environments.
  }
}

export function getCachedUser(): UserRead | null {
  try {
    const raw = window.localStorage.getItem(USER_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as UserRead) : null;
  } catch {
    return null;
  }
}

export function setCachedUser(user: UserRead | null): void {
  try {
    if (user) {
      window.localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(user));
    } else {
      window.localStorage.removeItem(USER_STORAGE_KEY);
    }
  } catch {
    // Ignore restricted storage environments.
  }
}

export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = getStoredToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

/**
 * Normalize FastAPI error payloads (plain `detail` strings or Pydantic validation
 * error arrays) into a single user-friendly message.
 */
export async function parseErrorDetail(response: Response): Promise<string> {
  if (response.status === 429) {
    return "Too many login attempts. Please wait a minute and try again.";
  }
  if (response.status >= 500) {
    return "The server encountered an unexpected error. Please try again.";
  }
  try {
    const data = (await response.json()) as { detail?: unknown };
    if (typeof data.detail === "string" && data.detail.trim()) {
      return data.detail;
    }
    if (Array.isArray(data.detail)) {
      const messages = data.detail
        .map((entry) =>
          typeof entry === "object" && entry !== null && "msg" in entry
            ? String((entry as { msg: unknown }).msg)
            : ""
        )
        .filter(Boolean);
      if (messages.length > 0) return messages.join(". ");
    }
  } catch {
    // Fallback below.
  }
  if (response.status === 401) return "Invalid credentials or expired session.";
  if (response.status === 403) return "You do not have permission to perform this action.";
  if (response.status === 404) return "Requested resource was not found.";
  return `Request failed (HTTP ${response.status}).`;
}

export function formatNetworkError(error: unknown, fallback: string): string {
  if (error instanceof TypeError) {
    return "Unable to reach the server. Changes will stay queued offline.";
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

export async function loginRequest(email: string, password: string): Promise<TokenResponse> {
  const response = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    throw new Error(await parseErrorDetail(response));
  }
  const data = (await response.json()) as TokenResponse;
  setStoredToken(data.access_token);
  setCachedUser(data.user);
  return data;
}

/**
 * Validate the stored Bearer token against `/api/v1/auth/me` and refresh the cached profile.
 *
 * - HTTP 401 clears local credentials immediately (e.g., expired token or admin deactivation).
 * - Network failures (`TypeError`) fall back to the cached profile so the PWA can boot offline.
 */
export async function fetchCurrentUser(): Promise<UserRead> {
  try {
    const response = await fetch("/api/v1/auth/me", { headers: authHeaders() });
    if (response.status === 401) {
      setStoredToken(null);
      throw new Error("Session expired. Please sign in again.");
    }
    if (!response.ok) {
      throw new Error(await parseErrorDetail(response));
    }
    const user = (await response.json()) as UserRead;
    setCachedUser(user);
    return user;
  } catch (error) {
    // Allow offline boot using cached profile when token is still stored.
    const cached = getCachedUser();
    if (error instanceof TypeError && cached && getStoredToken()) {
      return cached;
    }
    throw error;
  }
}
