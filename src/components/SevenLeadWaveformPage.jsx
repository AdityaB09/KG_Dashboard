import { useEffect, useMemo, useState } from "react";
import WebGLWaveformCanvas from "./WebGLWaveformCanvas";
import { connectWaveformStream } from "../services/waveformStream";
import { connectFhirStream } from "../services/fhirStream";
import "./SevenLeadWaveformPage.css";

const LEADS = [
  { id: "lead1", label: "Lead 1", color: "cyan" },
  { id: "lead2", label: "Lead 2", color: "cyan" },
  { id: "lead3", label: "Lead 3", color: "cyan" },
  { id: "avr", label: "aVR", color: "cyan" },
  { id: "avl", label: "aVL", color: "cyan" },
  { id: "avf", label: "aVF", color: "cyan" },
  { id: "pleth", label: "SpO₂ Pleth", color: "blue" },
];

const EMPTY_FRAME = {
  sampleRate: 200,
  batchSize: 0,
  receivedAt: "",
  leads: Object.fromEntries(LEADS.map((lead) => [lead.id, []])),
  vitals: {
    heartRate: "--",
    spo2: "--",
    systolic: "--",
    diastolic: "--",
    respiratoryRate: "--",
    temperature: "--",
  },
};

function valueOrDash(value) {
  return value === null || value === undefined || value === "" ? "--" : value;
}

export default function SevenLeadWaveformPage({ patient, onOpenAnalytics }) {
  const [waveFrame, setWaveFrame] = useState(EMPTY_FRAME);
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
  const interpretation = fhirFrame?.interpretation;
  const dataQuality = fhirFrame?.dataQuality;

  return (
    <section className="wave7-page">
      <header className="wave7-header">
        <div>
          <p className="wave7-eyebrow">Real-time telemetry</p>
          <h1>{patient?.name || "Selected Patient"}</h1>
          <span>
            MRN {patient?.mrn || "--"} • {patient?.location || "Bedside"} •{" "}
            {fhirFrame?.source || "oracle-smart"}
          </span>
        </div>

        <div className="wave7-header-actions">
          <span className={`wave7-live-pill ${streamStatus}`}>
            ● {streamStatus === "live" ? "Live WebGL" : "Connecting"}
          </span>

          <button type="button" className="wave7-action-btn" onClick={onOpenAnalytics}>
            Open Analytics
          </button>
        </div>
      </header>

      <main className="wave7-body">
        <section className="wave7-monitor-card">
          <div className="wave7-monitor-title">
            <div>
              <p className="wave7-eyebrow">High precision</p>
              <h2>7 waveform monitor</h2>
            </div>

            <span>{waveFrame.sampleRate || 200} samples/sec</span>
          </div>

          <div className="wave7-stack">
            {LEADS.map((lead) => (
              <article className="wave7-lead-row" key={lead.id}>
                <span>{lead.label}</span>

                <WebGLWaveformCanvas
                  samples={waveFrame.leads?.[lead.id] || []}
                  points={760}
                  color={lead.color}
                  mode="bipolar"
                />
              </article>
            ))}
          </div>
        </section>

        <aside className="wave7-side-rail">
          <Metric label="HR" value={valueOrDash(vitals.heartRate)} unit="BPM" icon="♥" />
          <Metric label="SpO₂" value={valueOrDash(vitals.spo2)} unit="%" icon="●" />
          <Metric
            label="BP"
            value={`${valueOrDash(vitals.systolic)}/${valueOrDash(vitals.diastolic)}`}
            unit="mmHg"
            icon="⌁"
          />
          <Metric label="RR" value={valueOrDash(vitals.respiratoryRate)} unit="/min" icon="↕" />
          <Metric label="Temp" value={valueOrDash(vitals.temperature)} unit="°C" icon="♨" />

          <section className="wave7-insight-card">
            <strong>Insight</strong>
            <p>
              {interpretation?.rhythm ||
                "Oracle/FHIR clinical insight will appear when the backend stream is connected."}
            </p>
          </section>

          <section className="wave7-lab-card">
            <strong>Labs</strong>
            <span>K {valueOrDash(labs.potassium)}</span>
            <span>Cr {valueOrDash(labs.creatinine)}</span>
            <span>Glu {valueOrDash(labs.glucose)}</span>
            <small>
              FHIR {dataQuality?.fhirFieldCount ?? 0} • Fallback{" "}
              {dataQuality?.fallbackFieldCount ?? 0}
            </small>
          </section>
        </aside>
      </main>
    </section>
  );
}

function Metric({ label, value, unit, icon }) {
  return (
    <section className="wave7-metric">
      <span>{icon}</span>
      <small>{label}</small>
      <strong>{value}</strong>
      <em>{unit}</em>
    </section>
  );
}