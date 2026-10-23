import logo from "../assets/logo-flowbridge.png";
import { cn } from "@/lib/utils";

export function BrandMark({ className }: { className?: string }) {
  return (
    <img
      src={logo}
      className={cn("size-8 shrink-0 object-contain", className)}
      alt=""
      aria-hidden="true"
    />
  );
}
export function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <BrandMark />
      <span className="text-base font-semibold tracking-tight">FlowBridge</span>
    </div>
  );
}
