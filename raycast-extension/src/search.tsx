import { Action, ActionPanel, Clipboard, Grid, Icon, showHUD, showToast, Toast, open } from "@raycast/api";
import { useFetch } from "@raycast/utils";
import { useState } from "react";
import { ensureServer, fmtSize, KIND_ICON, LupaFile, searchUrl, thumbUrl } from "./api";

const KINDS = [
  ["", "Wszystko"], ["image", "Obrazy"], ["design", "Projekty (PSD, AI, Affinity)"], ["pdf", "PDF"],
  ["video", "Wideo"], ["audio", "Audio"], ["doc", "Dokumenty"], ["sheet", "Arkusze"], ["raw", "Zdjęcia RAW"],
];

function Results(props: { query?: string; similar?: LupaFile }) {
  const [q, setQ] = useState(props.query ?? "");
  const [kind, setKind] = useState("");
  const params: Record<string, string> = props.similar ? { similar: String(props.similar.id) } : { q };
  if (kind) params.kinds = kind;
  const active = Boolean(props.similar || q.trim());
  const { data, isLoading, error } = useFetch<{ items: LupaFile[] }>(searchUrl(params), {
    execute: active,
    keepPreviousData: true,
    onError: async () => {
      await showToast({ style: Toast.Style.Failure, title: "Lupa nie odpowiada", message: "Uruchom aplikację Lupa" });
    },
  });
  const items = active ? data?.items ?? [] : [];

  return (
    <Grid
      columns={5}
      fit={Grid.Fit.Fill}
      aspectRatio="1"
      isLoading={isLoading && active}
      throttle
      searchText={props.similar ? undefined : q}
      onSearchTextChange={props.similar ? undefined : setQ}
      navigationTitle={props.similar ? `Podobne do: ${props.similar.name}` : "Lupa"}
      searchBarPlaceholder={props.similar ? "Podobne pliki" : "Opisz, czego szukasz… np. „logo zina na czarnym tle”"}
      searchBarAccessory={
        <Grid.Dropdown tooltip="Typ pliku" storeValue onChange={setKind}>
          {KINDS.map(([v, t]) => <Grid.Dropdown.Item key={v} value={v} title={t} />)}
        </Grid.Dropdown>
      }
    >
      {error ? (
        <Grid.EmptyView icon="🔌" title="Lupa nie działa" description="Naciśnij ↵, aby ją uruchomić"
          actions={<ActionPanel><Action title="Uruchom Lupę" onAction={ensureServer} /></ActionPanel>} />
      ) : !active ? (
        <Grid.EmptyView icon="🔎" title="Szukaj po znaczeniu" description="Na Macu i na NAS-ie — obrazy, PDF-y, projekty, wideo…" />
      ) : (
        items.map((f) => (
          <Grid.Item
            key={f.id}
            content={f.thumb ? { source: thumbUrl(f), fallback: Icon.Document } : { fileIcon: f.path }}
            title={f.name}
            subtitle={[KIND_ICON[f.kind], fmtSize(f.size), f.offline ? "offline" : ""].filter(Boolean).join(" ")}
            quickLook={f.offline ? undefined : { path: f.path, name: f.name }}
            actions={
              <ActionPanel>
                <ActionPanel.Section>
                  <Action.Open title="Otwórz" target={f.path} />
                  <Action.ShowInFinder path={f.path} />
                  {!f.offline && <Action.ToggleQuickLook shortcut={{ modifiers: ["cmd"], key: "y" }} />}
                </ActionPanel.Section>
                <ActionPanel.Section>
                  <Action.Push
                    title="Znajdź podobne"
                    icon={Icon.MagnifyingGlass}
                    shortcut={{ modifiers: ["cmd"], key: "s" }}
                    target={<Results similar={f} />}
                  />
                  <Action
                    title="Kopiuj plik"
                    icon={Icon.CopyClipboard}
                    shortcut={{ modifiers: ["cmd", "shift"], key: "c" }}
                    onAction={async () => {
                      await Clipboard.copy({ file: f.path });
                      await showHUD("Skopiowano plik — wklej go w Finderze lub mailu");
                    }}
                  />
                  <Action.CopyToClipboard title="Kopiuj ścieżkę" content={f.path} shortcut={{ modifiers: ["cmd"], key: "." }} />
                  <Action.OpenWith path={f.path} shortcut={{ modifiers: ["cmd"], key: "o" }} />
                  <Action title="Otwórz w Lupie" icon={Icon.AppWindow} onAction={() => open("/Applications/Lupa.app")} />
                </ActionPanel.Section>
              </ActionPanel>
            }
          />
        ))
      )}
    </Grid>
  );
}

export default function Command() {
  return <Results />;
}
