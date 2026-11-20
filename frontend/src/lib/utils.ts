import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatQty(value: number | string): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  return Number.isInteger(num) ? String(num) : num.toFixed(3).replace(/\.?0+$/, "");
}

export function formatRole(role: string): string {
  switch (role) {
    case "ADMIN":
      return "System Admin";
    case "STORE_MANAGER":
      return "Store Manager";
    case "REQUESTOR":
      return "Requestor";
    default:
      return role;
  }
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
