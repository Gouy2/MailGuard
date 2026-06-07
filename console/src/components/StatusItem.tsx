import type { ReactNode } from "react";

export function StatusItem({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="status-item">
      {icon}
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}
