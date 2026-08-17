import { useCallback, useEffect, useMemo, useState } from "react";
import {
  acknowledgeEscalation,
  escalateEscalation,
  getEscalation,
  resolveEscalation,
} from "../services/escalationService";
import "./EscalationPage.css";

const TERMINAL = new Set(["RESOLVED", "CANCELLED"]);
const ESCALATION_POLL_MS = Math.max(1000, Number(import.meta.env.VITE_ESCALATION_POLL_MS || 2500));
const COUNTDOWN_TICK_MS = Math.max(250, Number(import.meta.env.VITE_ESCALATION_COUNTDOWN_TICK_MS || 1000));

function valueOrDash(value) {
  const text = String(value ?? "").trim();
  return text || "—";
}

function titleCaseStatus(value) {
  const upper = String(value || "").toUpperCase();
  const special = {
    ACK_PENDING: "ACK Pending",
    ACK_TIMEOUT: "ACK Timeout",
    EPIC_CDS_HOOK_INVOKED: "Epic CDS Hook Invoked",
    EPIC_CDS_CARD_RETURNED: "Epic CDS Card Returned",
    EPIC_CDS_FEEDBACK_RECEIVED: "Epic CDS Feedback Received",
    EPIC_ROUTING_ACTIVE: "Epic Routing Active",
    ORACLE_FHIR_COMMUNICATION_ATTEMPTED: "Oracle FHIR Communication Attempted",
    ORACLE_FHIR_COMMUNICATION_CREATED: "Oracle FHIR Communication Created",
    ORACLE_FHIR_COMMUNICATION_VERIFIED: "Oracle FHIR Communication Verified",
  };
  if (special[upper]) return special[upper];
  return String(value || "")
    .toLowerCase()
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatDate(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) ? parsed.toLocaleString() : String(value);
}

function countdownText(value, now) {
  if (!value) return "—";
  const due = new Date(value).getTime();
  if (!Number.isFinite(due)) return "—";
  const seconds = Math.ceil((due - now) / 1000);
  if (seconds <= 0) return "Due";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function Field({ label, children }) {
  return (
    <div className="cardinal-escalation-field">
      <span>{label}</span>
      <strong>{children}</strong>
    </div>
  );
}

function ListBlock({ title, values }) {
  if (!Array.isArray(values) || values.length === 0) return null;
  return (
    <section className="cardinal-escalation-copy-block">
      <h3>{title}</h3>
      <ul>{values.map((value, index) => <li key={`${title}-${index}`}>{String(value)}</li>)}</ul>
    </section>
  );
}

function latestEvent(timeline, type) {
  return [...(timeline || [])].reverse().find((item) => item?.type === type) || null;
}

function deliveryState(value) {
  const raw = String(value || "").toLowerCase();
  if (["sent", "created", "verified", "active", "accepted", "ready"].includes(raw)) return raw === "verified" ? "VERIFIED" : "DELIVERED";
  if (["failed", "error"].includes(raw)) return "FAILED";
  if (["skipped", "not_configured"].includes(raw)) return "SKIPPED";
  if (["sandbox_unavailable", "unavailable"].includes(raw)) return "SANDBOX UNAVAILABLE";
  if (["attempted", "submitted"].includes(raw)) return "DELIVERY ATTEMPTED";
  return raw ? raw.toUpperCase().replaceAll("_", " ") : "READY";
}

function Channel({ title, status, children }) {
  const normalized = String(status || "READY").toLowerCase().replaceAll(" ", "-");
  return (
    <div className="cardinal-delivery-channel">
      <div className="cardinal-delivery-channel-head">
        <strong>{title}</strong>
        <span className={`channel-status channel-${normalized}`}>{status}</span>
      </div>
      {children ? <div className="cardinal-delivery-meta">{children}</div> : null}
    </div>
  );
}

function DeliveryChannels({ escalation }) {
  const provider = String(escalation?.provider || "cardinal").toLowerCase();
  const delivery = escalation?.delivery || {};
  const vendor = delivery.vendor || {};
  const email = delivery.email || {};
  const timeline = Array.isArray(escalation?.timeline) ? escalation.timeline : [];

  const emailEvent = latestEvent(timeline, "EMAIL_SENT");
  const emailStatus = deliveryState(email.status);

  if (provider === "oracle") {
    const comm = vendor.fhirCommunication || {};
    const verification = comm.verification || {};
    const validation = vendor.recipientValidation || {};
    const message = vendor.groupMessaging || {};
    const discovery = vendor.groupInboxDiscovery || {};
    const resolvedTarget = vendor.resolvedTarget || {};

    const verificationStatus =
      comm.verificationStatus ||
      verification.status ||
      (comm.status === "verified" ? "verified" : "");

    const verificationHttp =
      comm.verificationHttpStatus ||
      verification.httpStatus;

    return (
      <section className="cardinal-escalation-card">
        <h2>Delivery Channels · Oracle Health</h2>
        <div className="cardinal-delivery-grid">
          <Channel title="Email" status={emailStatus}>
            <span>Recipient: {valueOrDash(email.recipient || email.to)}</span>
            <span>
              Timestamp: {formatDate(
                email.sentAt ||
                email.at ||
                emailEvent?.at ||
                delivery.completedAt
              )}
            </span>
          </Channel>

          <Channel
            title="Oracle FHIR Communication"
            status={deliveryState(
              verificationStatus ||
              comm.status
            )}
          >
            <span>
              Communication ID: {valueOrDash(
                comm.communicationId ||
                comm.externalId
              )}
            </span>
            <span>Sender: {valueOrDash(comm.sender)}</span>
            <span>Recipient: {valueOrDash(comm.recipient)}</span>
            <span>Create HTTP: {valueOrDash(comm.httpStatus)}</span>
            <span>
              Verification: {
                verificationStatus
                  ? `${String(verificationStatus).toUpperCase()}${
                      verificationHttp ? ` · HTTP ${verificationHttp}` : ""
                    }`
                  : "—"
              }
            </span>
            <span>
              Verified At: {formatDate(
                comm.verifiedAt ||
                verification.verifiedAt
              )}
            </span>
          </Channel>

          <Channel
            title="Oracle Group Inbox / Message Center"
            status={deliveryState(
              message.status ||
              validation.status ||
              discovery.status
            )}
          >
            <span>
              Inbox: {valueOrDash(
                message.recipientName ||
                resolvedTarget.name ||
                discovery.recipientName ||
                discovery.name
              )}
            </span>
            <span>
              Recipient ID: {valueOrDash(
                message.recipientId ||
                validation.recipientId ||
                resolvedTarget.id ||
                discovery.recipientId
              )}
            </span>
            <span>
              Validated: {valueOrDash(
                validation.isValid ??
                validation.valid
              )}
            </span>
            <span>
              Message ID: {valueOrDash(
                message.messageId ||
                message.externalId
              )}
            </span>
            <span>
              HTTP: {valueOrDash(
                message.httpStatus ||
                validation.httpStatus ||
                discovery.httpStatus
              )}
            </span>
          </Channel>
        </div>
      </section>
    );
  }

  if (provider === "epic") {
    const routing = latestEvent(timeline, "EPIC_ROUTING_ACTIVE");
    const hook = latestEvent(timeline, "EPIC_CDS_HOOK_INVOKED");
    const card = latestEvent(timeline, "EPIC_CDS_CARD_RETURNED");
    const feedback = latestEvent(timeline, "EPIC_CDS_FEEDBACK_RECEIVED");
    return (
      <section className="cardinal-escalation-card">
        <h2>Delivery Channels · Epic</h2>
        <div className="cardinal-delivery-grid">
          <Channel title="Email" status={emailStatus}>
            <span>Recipient: {valueOrDash(email.recipient || email.to)}</span>
            <span>Timestamp: {formatDate(email.sentAt || email.at || emailEvent?.at)}</span>
          </Channel>
          <Channel title="Epic Active Escalation" status={routing ? "READY" : deliveryState(vendor.status)}>
            <span>Effective level: {valueOrDash(escalation.effectiveLevel)}</span>
            <span>Activated: {formatDate(routing?.at || delivery.startedAt)}</span>
          </Channel>
          <Channel title="Epic CDS Hook" status={card ? "DELIVERED" : hook ? "DELIVERY ATTEMPTED" : "READY"}>
            <span>Request received: {formatDate(hook?.at)}</span>
            <span>Card UUID: {valueOrDash(card?.data?.cardUuid)}</span>
            <span>Response: {card?.data?.responseMs != null ? `${card.data.responseMs} ms` : "—"}</span>
          </Channel>
          <Channel title="Epic CDS Feedback" status={feedback ? "DELIVERED" : "READY"}>
            <span>Received: {formatDate(feedback?.at)}</span>
            <span>{feedback ? "Feedback is present in the audit timeline." : "Awaiting optional CDS feedback."}</span>
          </Channel>
        </div>
      </section>
    );
  }

  return null;
}

export default function EscalationPage({ eventId }) {
  const [escalation, setEscalation] = useState(null);
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [action, setAction] = useState("");
  const [now, setNow] = useState(Date.now());

  const load = useCallback(async () => {
    try {
      const result = await getEscalation(eventId);
      setEscalation(result);
      setStatus("ready");
      setError("");
    } catch (err) {
      setStatus("error");
      setError(err?.message || "Unable to load escalation.");
    }
  }, [eventId]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, ESCALATION_POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), COUNTDOWN_TICK_MS);
    return () => window.clearInterval(timer);
  }, []);

  const model = escalation?.modelResponse || {};
  const isTerminal = TERMINAL.has(String(escalation?.status || "").toUpperCase());
  const canAcknowledge = escalation && !isTerminal && String(escalation.status || "").toUpperCase() !== "ACKNOWLEDGED";
  const canEscalate = escalation && !isTerminal && escalation.effectiveLevel !== "L4_EMERGENCY_RESPONSE";
  const timeline = useMemo(() => [...(Array.isArray(escalation?.timeline) ? escalation.timeline : [])].reverse(), [escalation]);
  const ackCountdown = countdownText(escalation?.ackDueAt || escalation?.nextEscalationAt, now);

  async function runAction(name, request) {
    setAction(name);
    setError("");
    try {
      const result = await request();
      setEscalation(result);
      setStatus("ready");
    } catch (err) {
      setError(err?.message || `Unable to ${name.toLowerCase()} escalation.`);
    } finally {
      setAction("");
    }
  }

  function openFullEpisode() {
    const query = new URLSearchParams({ page: "physiology" });
    if (escalation?.episodeId) query.set("episodeId", escalation.episodeId);
    if (escalation?.incidentId) query.set("incidentId", escalation.incidentId);
    window.location.assign(`/?${query.toString()}`);
  }

  if (status === "loading") return <main className="cardinal-escalation-page cardinal-escalation-centered"><div className="cardinal-escalation-loading">Loading clinical escalation…</div></main>;
  if (!escalation) return <main className="cardinal-escalation-page cardinal-escalation-centered"><section className="cardinal-escalation-error"><h1>Escalation unavailable</h1><p>{error || "The requested escalation case could not be loaded."}</p></section></main>;

  return (
    <main className="cardinal-escalation-page">
      <header className="cardinal-escalation-header">
        <div><span className="cardinal-escalation-kicker">CARDINAL · Clinical Escalation</span><h1>{valueOrDash(escalation.effectiveLevelLabel)}</h1><p>{valueOrDash(escalation.effectiveLevel)} · {valueOrDash(escalation.assignedRole)}</p></div>
        <div className={`cardinal-escalation-status status-${String(escalation.status || "").toLowerCase()}`}>{titleCaseStatus(escalation.status)}{ackCountdown !== "—" ? ` · ${ackCountdown}` : ""}</div>
      </header>

      {error ? <div className="cardinal-escalation-inline-error">{error}</div> : null}

      <section className="cardinal-escalation-grid">
        <article className="cardinal-escalation-card">
          <h2>Escalation Decision</h2>
          <div className="cardinal-escalation-fields">
            <Field label="Model Recommendation">{valueOrDash(escalation.modelSuggestedLevel)}</Field>
            <Field label="Policy Minimum">{valueOrDash(escalation.policyMinimumLevel)}</Field>
            <Field label="Effective Level">{valueOrDash(escalation.effectiveLevel)}</Field>
            <Field label="Assigned Role">{valueOrDash(escalation.assignedRole)}</Field>
            <Field label="Policy ID">{valueOrDash(escalation.policyId)}</Field>
            <Field label="Policy Version">{valueOrDash(escalation.policyVersion)}</Field>
            <Field label="Confidence">{valueOrDash(escalation.modelConfidence)}</Field>
          </div>
          {escalation.modelRationale ? <section className="cardinal-escalation-copy-block"><h3>Escalation Rationale</h3><p>{escalation.modelRationale}</p></section> : null}
          {Array.isArray(escalation?.policyDecision?.rulesFired) && escalation.policyDecision.rulesFired.length ? <section className="cardinal-escalation-copy-block"><h3>Policy Rules Fired</h3><ul>{escalation.policyDecision.rulesFired.map((rule) => <li key={rule.ruleId}>{`${rule.ruleId}: ${rule.reason || rule.minimumLevel}`}</li>)}</ul></section> : null}
        </article>

        <article className="cardinal-escalation-card cardinal-escalation-clinical">
          <h2>Clinical Interpretation</h2>
          {model.episodeSummary ? <section className="cardinal-escalation-copy-block no-border"><h3>Episode Summary</h3><p>{model.episodeSummary}</p></section> : null}
          {model.rhythm ? <section className="cardinal-escalation-copy-block"><h3>Rhythm</h3><p>{model.rhythm}</p></section> : null}
          {model.primaryEtiology ? <section className="cardinal-escalation-copy-block"><h3>Primary Etiology</h3><p>{model.primaryEtiology}</p></section> : null}
          {model.mechanism ? <section className="cardinal-escalation-copy-block"><h3>Mechanism</h3><p>{model.mechanism}</p></section> : null}
          <ListBlock title="Key ECG Evidence" values={model.keyECGEvidence} />
          <ListBlock title="Contributing Factors" values={model.contributingFactors} />
          <ListBlock title="Recommended Actions" values={model.recommendedActions} />
        </article>
      </section>

      <section className="cardinal-escalation-grid">
        <article className="cardinal-escalation-card">
          <h2>Response State</h2>
          <div className="cardinal-escalation-fields">
            <Field label="Current Status">{titleCaseStatus(escalation.status)}</Field>
            <Field label="Assigned / Routed Role">{valueOrDash(escalation.assignedRole)}</Field>
            <Field label="ACK Due">{formatDate(escalation.ackDueAt || escalation.nextEscalationAt)}</Field>
            <Field label="Countdown">{ackCountdown}</Field>
            <Field label="Acknowledged By">{valueOrDash(escalation.acknowledgedBy)}</Field>
            <Field label="Acknowledged Role">{valueOrDash(escalation.acknowledgedRole)}</Field>
            <Field label="Acknowledged At">{formatDate(escalation.acknowledgedAt)}</Field>
            <Field label="Time to ACK">{escalation.timeToAckSeconds != null ? `${escalation.timeToAckSeconds}s` : "—"}</Field>
            <Field label="Resolution">{escalation.resolvedAt ? `Resolved ${formatDate(escalation.resolvedAt)}` : "Open"}</Field>
          </div>
        </article>
        <article className="cardinal-escalation-card">
          <h2>Identifiers</h2>
          <div className="cardinal-escalation-fields">
            <Field label="Event ID">{valueOrDash(escalation.eventId)}</Field>
            <Field label="Correlation ID">{valueOrDash(escalation.correlationId)}</Field>
            <Field label="Episode ID">{valueOrDash(escalation.episodeId)}</Field>
            <Field label="Incident ID">{valueOrDash(escalation.incidentId)}</Field>
            <Field label="Provider">{valueOrDash(escalation.provider)}</Field>
            <Field label="Patient ID">{valueOrDash(escalation.patientId)}</Field>
            <Field label="Encounter ID">{valueOrDash(escalation.encounterId)}</Field>
          </div>
        </article>
      </section>

      <DeliveryChannels escalation={escalation} />

      <section className="cardinal-escalation-card cardinal-escalation-actions-card">
        <div><h2>Response Actions</h2><p>Every action is appended to the CARDINAL audit timeline.</p></div>
        <div className="cardinal-escalation-actions">
          <button type="button" disabled={!canAcknowledge || Boolean(action)} onClick={() => runAction("Acknowledge", () => acknowledgeEscalation(eventId))}>{action === "Acknowledge" ? "Acknowledging…" : "Acknowledge"}</button>
          <button type="button" className="secondary" onClick={openFullEpisode}>Open Full Episode</button>
          <button type="button" className="secondary" disabled={!canEscalate || Boolean(action)} onClick={() => runAction("Escalate", () => escalateEscalation(eventId))}>{action === "Escalate" ? "Escalating…" : "Escalate"}</button>
          <button type="button" className="secondary" disabled={isTerminal || Boolean(action)} onClick={() => runAction("Resolve", () => resolveEscalation(eventId))}>{action === "Resolve" ? "Resolving…" : "Resolve"}</button>
        </div>
      </section>

      <section className="cardinal-escalation-card">
        <h2>Complete Escalation Timeline</h2>
        <div className="cardinal-escalation-timeline">
          {timeline.length ? timeline.map((event, index) => (
            <div className="cardinal-escalation-timeline-row" key={`${event.at || "event"}-${index}`}>
              <time>{formatDate(event.at)}</time>
              <div><strong>{titleCaseStatus(event.type)}</strong>{event.detail ? <p>{event.detail}</p> : null}<div className="timeline-meta">{[event.deliveryResult, event.httpStatus ? `HTTP ${event.httpStatus}` : null, event.externalVendorId ? `ID ${event.externalVendorId}` : null].filter(Boolean).join(" · ")}</div></div>
            </div>
          )) : <p>No timeline events have been recorded.</p>}
        </div>
      </section>
    </main>
  );
}
