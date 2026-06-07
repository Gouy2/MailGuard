import { Brain, Check, Clock, FileText, Wrench } from "lucide-react";

export function TraceIcon({ event }: { event: string }) {
  if (event.startsWith("llm")) return <Brain size={16} />;
  if (event.startsWith("tool")) return <Wrench size={16} />;
  if (event === "turn_end") return <Check size={16} />;
  if (event === "turn_start") return <Clock size={16} />;
  return <FileText size={16} />;
}
