import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Brain,
  Check,
  Inbox,
  Play,
  RefreshCw,
  Send,
  Settings,
  ShieldCheck,
  Wrench,
  X,
} from "lucide-react";
import {
  approvePending,
  approveRule,
  disableRule,
  getCleanerAudit,
  getCleanerPolicy,
  getHealth,
  getPending,
  getTrace,
  listRules,
  rejectPending,
  runCleanerPreview,
  streamChat,
  teachCleaner,
} from "./api";
import { CleanerSummary } from "./components/CleanerSummary";
import { MarkdownText } from "./components/MarkdownText";
import { StatusItem } from "./components/StatusItem";
import { TraceIcon } from "./components/TraceIcon";
import { asString, compactJson, errorMessage, traceKey, traceSummary } from "./format";
import type {
  AgentMode,
  ChatMessage,
  CleanerPreview,
  CleanRule,
  Health,
  PendingTool,
  SseMessage,
  TraceRecord,
} from "./types";

const API_BASE_KEY = "mailguard.console.apiBase";
const TOKEN_KEY = "mailguard.console.token";
const SESSION_KEY = "mailguard.console.session";
const SYSTEM_PROMPT_KEY = "mailguard.console.systemPrompt";

export default function App() {
  const [apiBase, setApiBase] = useState(() => localStorage.getItem(API_BASE_KEY) || "/api");
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || "");
  const [sessionId, setSessionId] = useState(() => localStorage.getItem(SESSION_KEY) || "console");
  const [systemPrompt, setSystemPrompt] = useState(() => localStorage.getItem(SYSTEM_PROMPT_KEY) || "");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [mode, setMode] = useState<AgentMode>("agent");
  const [health, setHealth] = useState<Health>({});
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: crypto.randomUUID(),
      role: "system",
      text: "MailGuard Console is ready.",
      status: "local",
    },
  ]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [traceEvents, setTraceEvents] = useState<TraceRecord[]>([]);
  const [selectedTraceId, setSelectedTraceId] = useState("");
  const [selectedTraceKey, setSelectedTraceKey] = useState("");
  const [pending, setPending] = useState<PendingTool[]>([]);
  const [confirmPending, setConfirmPending] = useState<PendingTool | null>(null);
  const [rules, setRules] = useState<CleanRule[]>([]);
  const [confirmRule, setConfirmRule] = useState<CleanRule | null>(null);
  const [teachInput, setTeachInput] = useState("以后 Facebook 通知都归档，但安全邮件不要动");
  const [teachResult, setTeachResult] = useState<Record<string, unknown> | null>(null);
  const [preview, setPreview] = useState<CleanerPreview | null>(null);
  const [policy, setPolicy] = useState<Record<string, unknown> | null>(null);
  const [audit, setAudit] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("Ready");

  const config = useMemo(() => ({ apiBase, token, sessionId, systemPrompt }), [apiBase, token, sessionId, systemPrompt]);
  const visibleTraceEvents = useMemo(
    () => (selectedTraceId ? traceEvents.filter((record) => record.trace_id === selectedTraceId) : traceEvents),
    [selectedTraceId, traceEvents],
  );
  const selectedTrace = useMemo(
    () => traceEvents.find((record) => traceKey(record) === selectedTraceKey) ?? null,
    [selectedTraceKey, traceEvents],
  );

  useEffect(() => {
    localStorage.setItem(API_BASE_KEY, apiBase);
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(SESSION_KEY, sessionId);
    localStorage.setItem(SYSTEM_PROMPT_KEY, systemPrompt);
  }, [apiBase, token, sessionId, systemPrompt]);

  useEffect(() => {
    void refreshAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refreshAll() {
    await Promise.allSettled([refreshHealth(), refreshPending(), refreshRules(), refreshPolicy(), refreshAudit()]);
  }

  async function refreshHealth() {
    try {
      setHealth(await getHealth(config));
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  async function refreshPending() {
    try {
      setPending(await getPending(config));
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  async function refreshRules() {
    try {
      setRules(await listRules(config));
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  async function refreshPolicy() {
    try {
      setPolicy(await getCleanerPolicy(config));
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  async function refreshAudit() {
    try {
      setAudit(await getCleanerAudit(config));
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  async function sendMessage() {
    const text = input.trim();
    if (!text || sending) return;
    const userMessage: ChatMessage = { id: crypto.randomUUID(), role: "user", text };
    const assistantId = crypto.randomUUID();
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      text: "",
      status: "streaming",
    };
    setInput("");
    setSending(true);
    setNotice("Streaming agent turn");
    setMessages((items) => [...items, userMessage, assistantMessage]);

    try {
      await streamChat(config, mode, text, (event) => handleSseEvent(event, assistantId));
    } catch (error) {
      setMessages((items) =>
        items.map((item) =>
          item.id === assistantId ? { ...item, text: errorMessage(error), status: "error" } : item,
        ),
      );
      setNotice(errorMessage(error));
    } finally {
      setSending(false);
      void refreshPending();
    }
  }

  function handleSseEvent(event: SseMessage, assistantId: string) {
    const data = event.data;
    if (event.event === "status") {
      const traceId = asString(data.trace_id);
      setSelectedTraceId(traceId);
      setMessages((items) => items.map((item) => (item.id === assistantId ? { ...item, traceId } : item)));
    }
    if (event.event === "trace") {
      const record = data as TraceRecord;
      setTraceEvents((items) => mergeTraceEvents(items, [record]));
      setSelectedTraceId(record.trace_id);
      setSelectedTraceKey(traceKey(record));
      if (record.event === "tool_pending") void refreshPending();
    }
    if (event.event === "token") {
      setMessages((items) =>
        items.map((item) =>
          item.id === assistantId
            ? { ...item, text: asString(data.text) || item.text + asString(data.delta), traceId: asString(data.trace_id) }
            : item,
        ),
      );
    }
    if (event.event === "done") {
      const traceId = asString(data.trace_id);
      setMessages((items) =>
        items.map((item) =>
          item.id === assistantId
            ? { ...item, text: asString(data.text), status: asString(data.status) || "ok", traceId }
            : item,
        ),
      );
      setNotice(`Done: ${asString(data.status) || "ok"}`);
      if (traceId) void reconcileTrace(traceId, { selectLatest: true });
    }
    if (event.event === "error") {
      setMessages((items) =>
        items.map((item) =>
          item.id === assistantId ? { ...item, text: asString(data.message), status: "error" } : item,
        ),
      );
      setNotice(asString(data.message));
    }
  }

  async function reconcileTrace(traceId: string, options: { selectLatest?: boolean } = {}) {
    try {
      const records = await getTrace(config, traceId);
      setTraceEvents((items) => mergeTraceEvents(items, records));
      if (options.selectLatest && records.length > 0) {
        setSelectedTraceId(traceId);
        setSelectedTraceKey(traceKey(records[records.length - 1]));
      }
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  async function selectTrace(traceId: string) {
    if (!traceId) return;
    setSelectedTraceId(traceId);
    const localRecords = traceEvents.filter((record) => record.trace_id === traceId);
    if (localRecords.length > 0) {
      setSelectedTraceKey(traceKey(localRecords[localRecords.length - 1]));
    }
    await reconcileTrace(traceId, { selectLatest: true });
  }

  async function approveSelectedPending() {
    if (!confirmPending) return;
    setBusy(confirmPending.id);
    try {
      await approvePending(config, confirmPending.id);
      if (confirmPending.trace_id) await reconcileTrace(confirmPending.trace_id, { selectLatest: true });
      setConfirmPending(null);
      await refreshPending();
      setNotice("Pending approved");
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy("");
    }
  }

  async function rejectSelectedPending(item: PendingTool) {
    setBusy(item.id);
    try {
      await rejectPending(config, item.id);
      if (item.trace_id) await reconcileTrace(item.trace_id, { selectLatest: true });
      await refreshPending();
      setNotice("Pending rejected");
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy("");
    }
  }

  async function submitTeach() {
    if (!teachInput.trim()) return;
    setBusy("teach");
    try {
      const result = await teachCleaner(config, teachInput.trim());
      setTeachResult(result);
      await refreshRules();
      setNotice("Cleaner teach completed");
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy("");
    }
  }

  async function approveCleanRule(ruleId: string) {
    setBusy(ruleId);
    try {
      await approveRule(config, ruleId);
      setConfirmRule(null);
      await refreshRules();
      setNotice("Rule approved");
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy("");
    }
  }

  async function disableCleanRule(ruleId: string) {
    setBusy(ruleId);
    try {
      await disableRule(config, ruleId);
      await refreshRules();
      setNotice("Rule disabled");
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy("");
    }
  }

  async function previewCleaner() {
    setBusy("preview");
    try {
      setPreview(await runCleanerPreview(config));
      setNotice("Cleaner preview ready");
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy("");
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>MailGuard Console</h1>
          <p>{notice}</p>
        </div>
        <div className="topbar-actions">
          <button className="ghost" onClick={() => setSettingsOpen((value) => !value)} title="Settings">
            <Settings size={16} />
            Settings
          </button>
          <button className="icon-button" onClick={() => void refreshAll()} title="Refresh">
            <RefreshCw size={18} />
          </button>
        </div>
      </header>

      {settingsOpen ? (
        <section className="settings-panel panel">
          <label>
            API
            <input value={apiBase} onChange={(event) => setApiBase(event.target.value)} />
          </label>
          <label>
            Session
            <input value={sessionId} onChange={(event) => setSessionId(event.target.value)} />
          </label>
          <label>
            Token
            <input value={token} onChange={(event) => setToken(event.target.value)} type="password" />
          </label>
          <label className="wide-field">
            System Prompt
            <textarea value={systemPrompt} onChange={(event) => setSystemPrompt(event.target.value)} />
          </label>
        </section>
      ) : null}

      <section className="status-strip">
        <StatusItem icon={<ShieldCheck size={16} />} label="Server" value={health.status || "unknown"} />
        <StatusItem icon={<Wrench size={16} />} label="Tools" value={String(health.tools?.length ?? 0)} />
        <StatusItem icon={<AlertTriangle size={16} />} label="Pending" value={String(pending.length)} />
        <StatusItem icon={<Inbox size={16} />} label="Mode" value={modeLabel(mode)} />
      </section>

      <section className="workspace">
        <section className="chat-pane panel">
          <div className="panel-header">
            <div>
              <h2>Chat</h2>
              <span>multi-turn agent loop</span>
            </div>
            <div className="segmented">
              {(["agent", "agent_readonly", "simple"] as AgentMode[]).map((item) => (
                <button
                  key={item}
                  className={mode === item ? "active" : ""}
                  onClick={() => setMode(item)}
                  title={modeLabel(item)}
                >
                  {shortMode(item)}
                </button>
              ))}
            </div>
          </div>
          <div className="messages">
            {messages.map((message) => (
              <article key={message.id} className={`message ${message.role}`}>
                <div className="message-meta">
                  <span>{message.role}</span>
                  {message.status ? <b>{message.status}</b> : null}
                  {message.traceId ? <button onClick={() => void selectTrace(message.traceId || "")}>{message.traceId.slice(0, 8)}</button> : null}
                </div>
                <MarkdownText text={message.text || "..."} />
              </article>
            ))}
          </div>
          <div className="composer">
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  void sendMessage();
                }
              }}
              placeholder="让 agent 检查最近邮件、生成 clean rule，或请求一个需要审批的动作。"
            />
            <button className="primary" onClick={() => void sendMessage()} disabled={sending || !input.trim()}>
              <Send size={18} />
              Send
            </button>
          </div>
        </section>

        <section className="trace-pane panel">
          <div className="panel-header">
            <div>
              <h2>Trace</h2>
              <span>{selectedTraceId ? selectedTraceId : "live observable chain"}</span>
            </div>
            <div className="button-row">
              <button className="ghost" onClick={() => selectedTraceId && void reconcileTrace(selectedTraceId, { selectLatest: true })}>
                <RefreshCw size={16} />
                Sync
              </button>
              <button
                className="ghost"
                onClick={() => {
                  setSelectedTraceId("");
                  setSelectedTraceKey("");
                }}
              >
                All
              </button>
            </div>
          </div>
          <div className="trace-list">
            {visibleTraceEvents.map((record) => (
              <button
                key={traceKey(record)}
                className={`trace-row ${selectedTraceKey === traceKey(record) ? "selected" : ""}`}
                onClick={() => {
                  setSelectedTraceId(record.trace_id);
                  setSelectedTraceKey(traceKey(record));
                }}
              >
                <TraceIcon event={record.event} />
                <span>{record.event}</span>
                <b>{traceSummary(record)}</b>
              </button>
            ))}
          </div>
          <pre className="json-view">{selectedTrace ? JSON.stringify(selectedTrace, null, 2) : "No trace selected."}</pre>
        </section>
      </section>

      <section className="lower-grid">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Pending</h2>
              <span>dangerous tool approval</span>
            </div>
            <button className="ghost" onClick={() => void refreshPending()}>
              <RefreshCw size={16} />
              Refresh
            </button>
          </div>
          <div className="pending-list">
            {pending.length === 0 ? <p className="empty">No pending tool calls.</p> : null}
            {pending.map((item) => (
              <article key={item.id} className="pending-item">
                <div>
                  <b>{item.tool_name}</b>
                  <span>{item.reason || item.id}</span>
                  <code>{compactJson(item.arguments)}</code>
                </div>
                <div className="button-row">
                  <button className="danger" onClick={() => setConfirmPending(item)} disabled={busy === item.id}>
                    <Check size={16} />
                    Approve
                  </button>
                  <button className="ghost" onClick={() => void rejectSelectedPending(item)} disabled={busy === item.id}>
                    <X size={16} />
                    Reject
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="panel cleaner-panel">
          <div className="panel-header">
            <div>
              <h2>Cleaner</h2>
              <span>teach, rules, dry-run preview</span>
            </div>
            <button className="ghost" onClick={() => void previewCleaner()} disabled={busy === "preview"}>
              <Play size={16} />
              Preview
            </button>
          </div>

          <div className="teach-row">
            <input value={teachInput} onChange={(event) => setTeachInput(event.target.value)} />
            <button className="primary" onClick={() => void submitTeach()} disabled={busy === "teach" || !teachInput.trim()}>
              <Brain size={16} />
              Teach
            </button>
          </div>

          <div className="rules-list">
            {rules.map((rule) => (
              <article key={rule.rule_id} className={`rule ${rule.status}`}>
                <div>
                  <b>
                    {rule.action}:{rule.scope}
                  </b>
                  <span>{rule.value}</span>
                  <small>{rule.reason}</small>
                </div>
                <div className="button-row">
                  {rule.status !== "enabled" ? (
                    <button className="ghost" onClick={() => setConfirmRule(rule)} disabled={busy === rule.rule_id}>
                      <Check size={15} />
                      Enable
                    </button>
                  ) : null}
                  {rule.status !== "disabled" ? (
                    <button className="ghost" onClick={() => void disableCleanRule(rule.rule_id)} disabled={busy === rule.rule_id}>
                      <X size={15} />
                      Disable
                    </button>
                  ) : null}
                </div>
              </article>
            ))}
          </div>

          <CleanerSummary preview={preview} teachResult={teachResult} policy={policy} audit={audit} />
        </section>
      </section>

      {confirmPending ? (
        <div className="modal-backdrop">
          <section className="modal">
            <div className="panel-header">
              <div>
                <h2>Approve Tool Call</h2>
                <span>{confirmPending.tool_name}</span>
              </div>
              <button className="icon-button" onClick={() => setConfirmPending(null)}>
                <X size={18} />
              </button>
            </div>
            <p>This will execute the pending tool call against the active provider.</p>
            <pre className="json-view">{JSON.stringify(confirmPending, null, 2)}</pre>
            <div className="button-row end">
              <button className="ghost" onClick={() => setConfirmPending(null)}>
                Cancel
              </button>
              <button className="danger" onClick={() => void approveSelectedPending()} disabled={busy === confirmPending.id}>
                <Check size={16} />
                Approve
              </button>
            </div>
          </section>
        </div>
      ) : null}

      {confirmRule ? (
        <div className="modal-backdrop">
          <section className="modal">
            <div className="panel-header">
              <div>
                <h2>Enable Rule</h2>
                <span>
                  {confirmRule.action}:{confirmRule.scope}
                </span>
              </div>
              <button className="icon-button" onClick={() => setConfirmRule(null)}>
                <X size={18} />
              </button>
            </div>
            <p>Enabled archive rules can authorize future cleaner runs. Protect rules will block matching archive actions.</p>
            <pre className="json-view">{JSON.stringify(confirmRule, null, 2)}</pre>
            <div className="button-row end">
              <button className="ghost" onClick={() => setConfirmRule(null)}>
                Cancel
              </button>
              <button className="danger" onClick={() => void approveCleanRule(confirmRule.rule_id)} disabled={busy === confirmRule.rule_id}>
                <Check size={16} />
                Enable
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}

function mergeTraceEvents(current: TraceRecord[], incoming: TraceRecord[]): TraceRecord[] {
  const seen = new Set(current.map((item) => traceKey(item)));
  const merged = [...current];
  for (const item of incoming) {
    const key = traceKey(item);
    if (!seen.has(key)) {
      seen.add(key);
      merged.push(item);
    }
  }
  return merged;
}

function modeLabel(mode: AgentMode): string {
  if (mode === "agent_readonly") return "readonly";
  if (mode === "simple") return "simple";
  return "agent";
}

function shortMode(mode: AgentMode): string {
  if (mode === "agent_readonly") return "RO";
  if (mode === "simple") return "S";
  return "A";
}
