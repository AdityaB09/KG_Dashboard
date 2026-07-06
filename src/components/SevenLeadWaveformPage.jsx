import { useEffect, useMemo, useState } from "react";
import WebGLWaveformCanvas from "./WebGLWaveformCanvas";
import { connectWaveformStream } from "../services/waveformStream";
import "./SevenLeadWaveformPage.css";

const LEADS = [
  { id: "lead1", label: "Lead I", color: "cyan" },
  { id: "lead2", label: "Lead II", color: "cyan" },
  { id: "lead3", label: "Lead III", color: "cyan" },
  { id: "avr", label: "aVR", color: "cyan" },
  { id: "avl", label: "aVL", color: "cyan" },
  { id: "avf", label: "aVF", color: "cyan" },
  { id: "v1", label: "V1", color: "cyan" },
];
const EMPTY_FRAME = {
  sampleRate: 220,
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

function latestAmplitude(samples = []) {
  if (!samples.length) return "--";
  const latest = samples[samples.length - 1];

  if (!Number.isFinite(latest)) return "--";

  return latest.toFixed(2);
}

export default function SevenLeadWaveformPage({ patient, onOpenAnalytics }) {
  const [waveFrame, setWaveFrame] = useState(EMPTY_FRAME);
  const [streamStatus, setStreamStatus] = useState("connecting");

  useEffect(() => {
    const disconnectWaveforms = connectWaveformStream({
      onFrame: (frame) => {
        setWaveFrame(frame);
        setStreamStatus(frame.status === "error" ? "warning" : "live");
      },
      onError: () => setStreamStatus("warning"),
    });

    return () => {
      disconnectWaveforms?.();
    };
  }, [patient?.id]);

  const vitals = waveFrame.vitals || EMPTY_FRAME.vitals;
  const visibleSeconds = waveFrame.xAxis?.secondsVisible || 6;
  const visiblePoints = Math.round((waveFrame.sampleRate || 220) * visibleSeconds);

  const metricBoxes = useMemo(() => {
    return [
      {
        label: "Lead 1",
        title: "HR",
        value: valueOrDash(vitals.heartRate),
        unit: "BPM",
        icon: "♥",
      },
      {
        label: "Lead 2",
        title: "Lead II",
        value: valueOrDash(waveFrame.latestMv?.lead2),
        unit: "mV",
        icon: "⌁",
      },
      {
        label: "Lead 3",
        title: "Lead III",
        value: valueOrDash(waveFrame.latestMv?.lead3),
        unit: "mV",
        icon: "⌁",
      },
      {
        label: "aVR",
        title: "BP",
        value: `${valueOrDash(vitals.systolic)}/${valueOrDash(vitals.diastolic)}`,
        unit: "mmHg",
        icon: "↯",
      },
      {
        label: "aVL",
        title: "RR",
        value: valueOrDash(vitals.respiratoryRate),
        unit: "/min",
        icon: "↕",
      },
      {
        label: "aVF",
        title: "Temp",
        value: valueOrDash(vitals.temperature),
        unit: "°C",
        icon: "♨",
      },
      {
        label: "V1",
        title: "SpO₂",
        value: valueOrDash(vitals.spo2),
        unit: "%",
        icon: "●",
      },
    ];
  }, [vitals, waveFrame.latestMv]);

  return (
    <section className="wave7-page">
      <header className="wave7-header">
        <div>
          <p className="wave7-eyebrow">Real-time telemetry</p>
          <h1>{patient?.name || "Selected Patient"}</h1>
          <span>
            MRN {patient?.mrn || "--"} • {patient?.location || "Bedside"} •{" "}
            {waveFrame.source || "physionet-ptb-xl"}
          </span>
        </div>

        <div className="wave7-header-actions">
          <span className={`wave7-live-pill ${streamStatus}`}>
            ● {streamStatus === "live" ? "Live WebGL" : "Connecting"}
          </span>

          <span className="wave7-speed-pill">
            {waveFrame.sampleRate || 220} Hz • {visibleSeconds}s window
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

            <span>
              {waveFrame.sampleRate || 220} samples/sec • 25 mm/sec display model
            </span>
          </div>

          <div className="wave7-stack">
            {LEADS.map((lead) => (
              <article className="wave7-lead-row" key={lead.id}>
                <span>{lead.label}</span>

                <WebGLWaveformCanvas
                  samples={waveFrame.leads?.[lead.id] || []}
                  points={visiblePoints}
                  color={lead.color}
                  mode="bipolar"
                />
              </article>
            ))}
          </div>
        </section>

        <aside className="wave7-side-rail">
          {metricBoxes.map((metric) => (
            <MetricBox key={metric.label} {...metric} />
          ))}
        </aside>
      </main>
    </section>
  );
}
function MetricBox({ label, title, value, unit, icon }) {
  return (
    <section className="wave7-metric">
      <small>{label}</small>
      <span>{icon}</span>
      <b>{title}</b>
      <strong>{value}</strong>
      <em>{unit}</em>
    </section>
  );
}