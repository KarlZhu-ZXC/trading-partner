import { redirect } from "next/navigation";

/** Compatibility route for bookmarks created before Journal absorbed period reviews. */
export default function TradeRetroRedirect() {
  redirect("/decision-workbench#reviews");
}
