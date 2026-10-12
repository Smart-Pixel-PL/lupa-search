import { open, closeMainWindow } from "@raycast/api";

export default async function Command() {
  await closeMainWindow();
  await open("/Applications/Lupa.app");
}
