import { asRecord } from "../format";
import type { CleanerBucketItem, CleanerPreview } from "../types";

export function CleanerSummary({
  preview,
  teachResult,
  policy,
  audit,
}: {
  preview: CleanerPreview | null;
  teachResult: Record<string, unknown> | null;
  policy: Record<string, unknown> | null;
  audit: Record<string, unknown> | null;
}) {
  return (
    <div className="cleaner-summary">
      <div className="metric-row">
        <Metric label="Auto" value={String(preview?.auto_eligible_count ?? 0)} />
        <Metric label="Protected" value={String(preview?.protected_count ?? 0)} />
        <Metric label="Candidate" value={String(preview?.candidate_count ?? 0)} />
        <Metric label="Audit" value={String(asRecord(audit).count ?? 0)} />
      </div>
      <Bucket title="Auto eligible" items={preview?.auto_eligible ?? []} />
      <Bucket title="Protected" items={preview?.protected ?? []} />
      <Bucket title="Candidates" items={preview?.candidates ?? []} />
      <Bucket title="No action" items={preview?.no_action ?? []} />
      <details>
        <summary>State</summary>
        <pre className="json-view">
          {JSON.stringify(
            {
              teach: teachResult,
              policy,
              artifact_path: preview?.artifact_path,
              mailbox_mutation: preview?.mailbox_mutation,
              llm_authorization: preview?.llm_authorization,
            },
            null,
            2,
          )}
        </pre>
      </details>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}

function Bucket({ title, items }: { title: string; items: CleanerBucketItem[] }) {
  return (
    <div className="bucket">
      <h3>{title}</h3>
      {items.length === 0 ? <p className="empty">No items.</p> : null}
      {items.slice(0, 8).map((item) => (
        <article key={`${title}-${item.email_id}`} className="bucket-item">
          <b>{item.subject || item.email_id}</b>
          <span>{item.from_email || item.from_name || "unknown sender"}</span>
          <small>{item.policy_reason || item.reason || item.automation_authority || item.category}</small>
        </article>
      ))}
    </div>
  );
}
