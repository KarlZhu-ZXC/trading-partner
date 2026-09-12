import { notFound } from "next/navigation";
import { ComponentGallery } from "./component-gallery";
export default function DesignSystemPage() {
  if (process.env.CONSOLE_DESIGN_PREVIEW !== "1" && process.env.NODE_ENV !== "development") notFound();
  return <ComponentGallery />;
}
