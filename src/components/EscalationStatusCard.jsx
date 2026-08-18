import { useEffect, useMemo, useState } from "react";
import {
  getEscalationForEpisode,
  getEscalationForIncident,
} from "../services/escalationService";
import "./EscalationStatusCard.css";

const ESCALATION_POLL_MS = Math.max(
  1000,
  Number(import.meta.env.VITE_ESCALATION_POLL_MS || 2500)
);
const COUNTDOWN_TICK_MS = Math.max(
  250,
  Number(import.meta.env.VITE_ESCALATION_COUNTDOWN_TICK_MS || 1000)
);

const PATHWAY_DISPLAY = {
  MONITOR_ONLY: { short: "T0", severity: "monitor" },
  CARE_TEAM_REVIEW: { short: "T1", severity: "care" },
  URGENT_PROVIDER_REVIEW: { short: "T2", severity: "provider" },
  RAPID_RESPONSE_ACTIVATION: { short: "T3", severity: "rrt" },
  CODE_RESPONSE_ACTIVATION: { short: "E", severity: "code" },
  // Legacy stored cases before backend canonicalization.
  L0_MONITOR: { short: "T0", severity: "monitor" },
  L1_NURSING_REVIEW: { short: "T1", severity: "care" },
  L2_URGENT_PROVIDER_REVIEW: { short: "T2", severity: "provider" },
  L3_RAPID_RESPONSE_REVIEW: { short: "T3", severity: "rrt" },
  L4_EMERGENCY_RESPONSE: { short: "E", severity: "code" },
};

function formatStatus(value, autoEnabled) {
  const upper = String(value || "").toUpperCase();
  if (upper === "ROUTED_AUTO_ADVANCE") return "Routed · Auto On";
  if (upper === "ROUTED_TERMINAL") return "Response Routed";
  if (upper === "ROUTED") return autoEnabled ? "Routed · Auto On" : "Routed";
  if (upper === "ACK_PENDING") return "Legacy Routed";
  return String(value || "")
    .toLowerCase()
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function remainingText(dueAt, now) {
  if (!dueAt) return "—";
  const due = new Date(dueAt).getTime();
  if (!Number.isFinite(due)) return "—";
  const remaining = Math.ceil((due - now) / 1000);
  if (remaining <= 0) return "DUE";
  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export default function EscalationStatusCard({ episodeId, incidentId, compact = false }) {
  const [escalation, setEscalation] = useState(null);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), COUNTDOWN_TICK_MS);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const load = async () => {
      try {
        const result = episodeId
          ? await getEscalationForEpisode(episodeId)
          : incidentId
          ? await getEscalationForIncident(incidentId)
          : null;
        if (!cancelled) setEscalation(result?.escalation || null);
      } catch {
        if (!cancelled) setEscalation(null);
      }
    };
    if (episodeId || incidentId) {
      load();
      timer = window.setInterval(load, ESCALATION_POLL_MS);
    }
    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
    };
  }, [episodeId, incidentId]);

  const responseDueAt = escalation?.autoEscalationEnabled
    ? escalation?.nextEscalationAt || null
    : null;
  const countdown = useMemo(
    () => remainingText(responseDueAt, now),
    [responseDueAt, now]
  );

  if (!escalation) return null;
  const pathway = PATHWAY_DISPLAY[String(escalation.effectiveLevel || "").toUpperCase()] || {
    short: "RESPONSE",
    severity: "provider",
  };
  if (pathway.severity === "monitor") return null;

  const levelLabel = escalation.effectiveLevelLabel || escalation.effectiveLevel;
  const openEscalation = () =>
    window.location.assign(`/escalation/${encodeURIComponent(escalation.eventId)}`);
  const onStripKeyDown = (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openEscalation();
    }
  };

  return (
    <section
      className={`cardinal-response-strip severity-${pathway.severity}${compact ? " is-compact" : ""}`}
      role="link"
      tabIndex={0}
      aria-label={`Open ${levelLabel} clinical response details`}
      title={`Open ${levelLabel} clinical response`}
      onClick={openEscalation}
      onKeyDown={onStripKeyDown}
    >
      <span className="response-strip-label">RESPONSE</span>
      <span className="response-strip-divider" />
      <strong className="response-strip-level" title={levelLabel}>{pathway.short}</strong>
      <span className="response-strip-divider" />
      <strong className="response-strip-status">
        {formatStatus(escalation.status, escalation.autoEscalationEnabled)}
      </strong>
      {responseDueAt && (
        <>
          <span className="response-strip-divider response-strip-deadline-divider" />
          <span className={`response-strip-countdown ${countdown === "DUE" ? "is-due" : ""}`}>
            {countdown}
          </span>
        </>
      )}
      <span className="response-strip-details" aria-hidden="true">{compact ? "›" : "Details"}</span>
    </section>
  );
}
