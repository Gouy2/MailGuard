export type AgentMode = "agent" | "agent_readonly" | "simple";

export type SseMessage = {
  event: string;
  data: Record<string, unknown>;
};

export type TraceRecord = {
  trace_id: string;
  event: string;
  timestamp?: string;
  payload: Record<string, unknown>;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  status?: string;
  traceId?: string;
};

export type PendingTool = {
  id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  session_id: string;
  trace_id?: string;
  reason?: string;
  created_at?: string;
};

export type CleanRule = {
  rule_id: string;
  action: string;
  scope: string;
  value: string;
  status: string;
  source?: string;
  reason?: string;
  created_at?: string;
  updated_at?: string;
  approved_at?: string;
  disabled_at?: string;
  metadata?: Record<string, unknown>;
};

export type CleanerBucketItem = {
  email_id: string;
  subject?: string;
  from_email?: string;
  from_name?: string;
  category?: string;
  importance?: string;
  reason?: string;
  policy_reason?: string;
  automation_authority?: string;
  memory_match?: string;
  suggested_action?: string;
  clean_rule_match?: CleanRule | Record<string, unknown>;
};

export type CleanerPreview = {
  status: string;
  run_id?: string;
  artifact_path?: string;
  fetched?: number;
  auto_eligible_count?: number;
  protected_count?: number;
  candidate_count?: number;
  no_action_count?: number;
  auto_eligible?: CleanerBucketItem[];
  protected?: CleanerBucketItem[];
  candidates?: CleanerBucketItem[];
  no_action?: CleanerBucketItem[];
  mailbox_mutation?: boolean;
  proposal_mutation?: boolean;
  llm_authorization?: boolean;
};

export type Health = {
  service?: string;
  status?: string;
  sessions?: Record<string, unknown>;
  tools?: string[];
};
