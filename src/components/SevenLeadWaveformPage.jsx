import { useEffect, useMemo, useState } from "react";
import WebGLWaveformCanvas from "./WebGLWaveformCanvas";
import { connectWaveformStream } from "../services/waveformStream";
import { connectFhirStream } from "../services/fhirStream";

const LEADS = [
  { id: "lead1", label: "Lead 1", color: "cyan" },
  { id: "lead2", label: "Lead 2", color: "cyan" },
  { id: "lead3", label: "Lead 3", color: "cyan" },
  { id: "avr", label: "aVR", color: "cyan" },
  { id: "avl", label: "aVL", color: "cyan" },
  { id: "avf", label: "aVF", color: "cyan" },
  { id: "pleth", label: "SpO₂ Pleth", color: "blue" },
];

const EMPTY_WAVEFORM_FRAME = {
  receivedAt: "",
  sampleRate: 200,
  leads: Object.fromEntries(LEADS.map((lead) => [lead.id, []])),
  vitals: {
    heartRate: "--",
    spo2: "--",
    systolic: "--",
    diastolic: "--",
    temperature: "--",
    respiratoryRate: "--",
  },
};

export default function SevenLeadWaveformPage({ patient, onOpenAnalytics }) {
  const [waveFrame, setWaveFrame] = useState(EMPTY_WAVEFORM_FRAME);
  const [fhirFrame, setFhirFrame] = useState(null);
  const [streamStatus, setStreamStatus] = useState("connecting");

  useEffect(() => {
    const disconnectWaveforms = connectWaveformStream({
      onFrame: (frame) => {
        setWaveFrame(frame);
        setStreamStatus("live");
      },
      onError: () => setStreamStatus("warning"),
    });

    const disconnectFhir = connectFhirStream({
      provider: "oracle",
      patientId: "",
      onFrame: (frame) => setFhirFrame(frame),
      onHeartbeat: () => {},
      onError: () => {},
    });

    return () => {
      disconnectWaveforms?.();
      disconnectFhir?.();
    };
  }, [patient?.id, patient?.fhirId]);

  const vitals = useMemo(() => {
    return {
      ...waveFrame.vitals,
      ...(fhirFrame?.vitals || {}),
    };
  }, [waveFrame.vitals, fhirFrame]);

  const labs = fhirFrame?.labs || {};
  const source = fhirFrame?.source || "oracle-smart";
  const dataQuality = fhirFrame?.dataQuality;

  return (
    <section className="waveform-main-page">
      <header className="waveform-main-header">
        <div>
          <p className="eyebrow">Real-time telemetry</p>
          <h1>{patient?.name || "Selected Patient"}</h1>
          <span>
            MRN {patient?.mrn || "--"} • {patient?.location || "Bedside Monitor"} • {source}
          </span>
        </div>

        <div className="waveform-header-actions">
          <span className={`waveform-live-pill ${streamStatus}`}>
            ● {streamStatus === "live" ? "Live WebGL" : "Connecting"}
          </span>

          <button type="button" className="ghost-btn" onClick={onOpenAnalytics}>
            Open Analytics
          </button>
        </div>
      </header>

      <main className="waveform-main-grid">
        <section className="waveform-monitor-card">
          <div className="waveform-monitor-title">
            <div>
              <p className="eyebrow">High precision</p>
              <h2>7 waveform monitor</h2>
            </div>

            <span>{waveFrame.sampleRate || 200} samples/sec</span>
          </div>

          <div className="waveform-stack">
            {LEADS.map((lead) => (
              <article className="webgl-lead-row" key={lead.id}>
                <span>{lead.label}</span>

                <WebGLWaveformCanvas
                  samples={waveFrame.leads?.[lead.id] || []}
                  points={900}
                  color={lead.color}
                  mode="bipolar"
                />
              </article>
            ))}
          </div>
        </section>

        <aside className="waveform-vitals-rail">
          <Metric label="HR" value={vitals.heartRate} unit="BPM" icon="♥" />
          <Metric label="SpO₂" value={vitals.spo2} unit="%" icon="●" />
          <Metric
            label="BP"
            value={`${vitals.systolic}/${vitals.diastolic}`}
            unit="mmHg"
            icon="⌁"
          />
          <Metric label="RR" value={vitals.respiratoryRate} unit="/min" icon="↕" />
          <Metric label="Temp" value={vitals.temperature} unit="°C" icon="♨" />
        </aside>
      </main>

      <section className="waveform-lower-grid">
        <InfoPanel title="Lab results">
          <Row label="Glucose" value={labs.glucose ?? "--"} unit="mg/dL" />
          <Row label="Potassium" value={labs.potassium ?? "--"} unit="mmol/L" />
          <Row label="Creatinine" value={labs.creatinine ?? "--"} unit="mg/dL" />
          <Row label="WBC" value={labs.wbc ?? "--"} unit="10³/uL" />
        </InfoPanel>

        <InfoPanel title="FHIR data quality">
          <Row label="FHIR fields" value={dataQuality?.fhirFieldCount ?? 0} unit="" />
          <Row label="Fallback fields" value={dataQuality?.fallbackFieldCount ?? 0} unit="" />
          <Row label="Observations" value={dataQuality?.observationCount ?? 0} unit="" />
          <Row label="Matched" value={dataQuality?.matchedObservationCount ?? 0} unit="" />
        </InfoPanel>

        <InfoPanel title="Interpretation">
          <p className="waveform-interpretation">
            {fhirFrame?.interpretation?.rhythm ||
              "FHIR clinical interpretation will appear after Oracle SMART data is received."}
          </p>
        </InfoPanel>
      </section>
    </section>
  );
}

function Metric({ label, value, unit, icon }) {
  return (
    <div className="waveform-metric">
      <span>{icon}</span>
      <small>{label}</small>
      <strong>{value}</strong>
      <em>{unit}</em>
    </div>
  );
}

function InfoPanel({ title, children }) {
  return (
    <section className="waveform-info-panel">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function Row({ label, value, unit }) {
  return (
    <div className="waveform-info-row">
      <span>{label}</span>
      <strong>{value}</strong>
      <em>{unit}</em>
    </div>
  );
}