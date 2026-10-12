import { getPreferenceValues, open, showToast, Toast } from "@raycast/api";

export type LupaFile = {
  id: number;
  path: string;
  name: string;
  ext: string;
  kind: string;
  size: number;
  mtime: number;
  folder: string;
  offline: boolean;
  thumb: number;
  why?: string;
};

export const server = () => (getPreferenceValues<{ server?: string }>().server || "http://127.0.0.1:7766").replace(/\/$/, "");
export const thumbUrl = (f: LupaFile) => `${server()}/api/thumb/${f.id}`;

export function searchUrl(params: Record<string, string>) {
  const qs = new URLSearchParams({ limit: "60", ...params });
  return `${server()}/api/search?${qs}`;
}

export async function ensureServer() {
  // Lupa.app starts the server; open it in the background if it is not running yet
  await showToast({ style: Toast.Style.Animated, title: "Uruchamiam Lupę…" });
  await open("/Applications/Lupa.app");
}

export function fmtSize(b: number) {
  if (!b) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) {
    b /= 1024;
    i++;
  }
  return `${b.toFixed(i > 1 ? 1 : 0)} ${u[i]}`;
}

export const KIND_ICON: Record<string, string> = {
  image: "🖼", video: "🎬", audio: "🎵", pdf: "📕", doc: "📄", text: "📝", sheet: "📊", slides: "📽",
  design: "🎨", raw: "📷", code: "💻", archive: "🗜", font: "🔤", web: "🌐",
};
