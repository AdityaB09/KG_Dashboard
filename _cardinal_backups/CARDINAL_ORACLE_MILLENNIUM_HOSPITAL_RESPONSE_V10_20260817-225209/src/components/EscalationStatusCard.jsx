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

function formatStatus(value) {
  const upper = String(value || "").toUpperCase();
  if (upper === "ACK_PENDING") return "ACK Pending";
  if (upper === "ACKNOWLEDGED") return "Acknowledged";
  if (upper === "ACK_TIMEOUT") return "ACK Due";
  return String(value || "")
    .toLowerCase()
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function levelShort(level) {
  const match = String(level || "").match(/^L\d/);
  return match ? match[0] : "";
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

export default function EscalationStatusCard({
  episodeId,
  incidentId,
  compact = false,
}) {
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

  const responseDueAt = escalation?.ackDueAt || escalation?.nextEscalationAt || null;
  const countdown = useMemo(
    () => remainingText(responseDueAt, now),
    [responseDueAt, now]
  );
  const showCountdown = Boolean(responseDueAt);

  if (!escalation || escalation.effectiveLevel === "L0_MONITOR") return null;

  const severity = levelShort(escalation.effectiveLevel).toLowerCase();
  const level = levelShort(escalation.effectiveLevel);
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
      className={`cardinal-response-strip severity-${severity}${compact ? " is-compact" : ""}`}
      role="link"
      tabIndex={0}
      aria-label={`Open ${levelLabel} escalation details`}
      title={`Open ${levelLabel} escalation`}
      onClick={openEscalation}
      onKeyDown={onStripKeyDown}
    >
      <span className="response-strip-label">RESPONSE</span>
      <span className="response-strip-divider" />
      <strong className="response-strip-level" title={levelLabel}>
        {level}
      </strong>
      <span className="response-strip-divider" />
      <strong className="response-strip-status">
        {formatStatus(escalation.status)}
      </strong>
      {showCountdown && (
        <>
          <span className="response-strip-divider response-strip-deadline-divider" />
          <span
            className={`response-strip-countdown ${
              countdown === "DUE" ? "is-due" : ""
            }`}
          >
            {countdown}
          </span>
        </>
      )}
      <span className="response-strip-details" aria-hidden="true">
        {compact ? "›" : "Details"}
      </span>
    </section>
  );
}
