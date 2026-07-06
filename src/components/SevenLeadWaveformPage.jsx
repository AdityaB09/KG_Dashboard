import { useEffect, useMemo, useRef, useState } from "react";
import WebGLWaveformCanvas from "./WebGLWaveformCanvas";
import { connectWaveformStream } from "../services/waveformStream";
import "./SevenLeadWaveformPage.css";

const ECG_PAPER_SPEED_MM_PER_SEC = 25;
const DEFAULT_GAIN_MM_PER_MV = 10;
const DEFAULT_VISIBLE_SECONDS = 6;

const MIN_PX_PER_MM = 3.5;
const MAX_PX_PER_MM = 8.5;
const ECG_ROW_HEIGHT_MM = 28;

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
  source: "physionet-ptb-xl",
  sampleRate: 220,
  batchSize: 0,
  receivedAt: "",
  leadsMv: Object.fromEntries(LEADS.map((lead) => [lead.id, []])),
  latestMv: {},
  xAxis: {
    secondsVisible: DEFAULT_VISIBLE_SECONDS,
  },
  vitals: {
    heartRate: "--",
    spo2: "--",
    systolic: "--",
    diastolic: "--",
    respiratoryRate: "--",
    temperature: "--",
  },
};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function valueOrDash(value) {
  return value === null || value === undefined || value === "" ? "--" : value;
}

function formatMv(value) {
  if (value === null || value === undefined || value === "") return "--";

  const numericValue = Number(value);

  if (!Number.isFinite(numericValue)) return "--";

  return numericValue.toFixed(3);
}

export default function SevenLeadWaveformPage({ patient, onOpenAnalytics }) {
  const [waveFrame, setWaveFrame] = useState(EMPTY_FRAME);
  const [streamStatus, setStreamStatus] = useState("connecting");
  const [gainMmPerMv, setGainMmPerMv] = useState(DEFAULT_GAIN_MM_PER_MV);
  const [plotWidthPx, setPlotWidthPx] = useState(0);

  const monitorRef = useRef(null);

  useEffect(() => {
    const disconnectWaveforms = connectWaveformStream({
      onFrame: (frame) => {
        setWaveFrame(frame);
        setStreamStatus(frame.status === "connected" ? "live" : "warning");
      },
      onError: () => setStreamStatus("warning"),
    });

    return () => {
      disconnectWaveforms?.();
    };
  }, [patient?.id]);

  useEffect(() => {
    const element = monitorRef.current;
    if (!element) return undefined;

    function updateWidth() {
      const rect = element.getBoundingClientRect();
      setPlotWidthPx(Math.max(1, rect.width));
    }

    updateWidth();

    const observer = new ResizeObserver(updateWidth);
    observer.observe(element);

    return () => observer.disconnect();
  }, []);

  const vitals = waveFrame.vitals || EMPTY_FRAME.vitals;
  const visibleSeconds = waveFrame.xAxis?.secondsVisible || DEFAULT_VISIBLE_SECONDS;
  const sampleRate = waveFrame.sampleRate || 220;
  const visiblePoints = Math.round(sampleRate * visibleSeconds);

  const paperWidthMm = visibleSeconds * ECG_PAPER_SPEED_MM_PER_SEC;

  const pxPerMm = clamp(
    plotWidthPx > 0 ? plotWidthPx / paperWidthMm : 5,
    MIN_PX_PER_MM,
    MAX_PX_PER_MM
  );

  const rowHeightPx = Math.round(
    clamp(pxPerMm * ECG_ROW_HEIGHT_MM, 106, 170)
  );

  const metricBoxes = useMemo(() => {
    return [
      {
        label: "Lead I",
        title: "HR",
        value: valueOrDash(vitals.heartRate),
        unit: "BPM",
        icon: "♥",
      },
      {
        label: "Lead II",
        title: "Lead II",
        value: formatMv(waveFrame.latestMv?.lead2),
        unit: "mV",
        icon: "⌁",
      },
      {
        label: "Lead III",
        title: "Lead III",
        value: formatMv(waveFrame.latestMv?.lead3),
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
            ●{" "}
            {streamStatus === "live"
              ? "Live WebGL"
              : streamStatus === "warning"
              ? "Waveform Warning"
              : "Connecting"}
          </span>

          <span className="wave7-speed-pill">
            {sampleRate} Hz • {visibleSeconds}s • {gainMmPerMv} mm/mV
          </span>

          <div className="wave7-gain-toggle" aria-label="ECG gain selector">
            {[5, 10, 20].map((gain) => (
              <button
                key={gain}
                type="button"
                className={gainMmPerMv === gain ? "active" : ""}
                onClick={() => setGainMmPerMv(gain)}
              >
                {gain}
              </button>
            ))}
          </div>

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
              {sampleRate} samples/sec • 25 mm/sec • {gainMmPerMv} mm/mV
            </span>
          </div>

          <div
            ref={monitorRef}
            className="wave7-stack"
            style={{
              "--ecg-mm": `${pxPerMm}px`,
              "--wave7-row-height": `${rowHeightPx}px`,
            }}
          >
            {LEADS.map((lead) => (
              <article className="wave7-lead-row" key={lead.id}>
                <span>{lead.label}</span>

                <div className="wave7-calibrated-strip">
                  <WebGLWaveformCanvas
                    samples={waveFrame.leadsMv?.[lead.id] || []}
                    points={visiblePoints}
                    color={lead.color}
                    mode="millivolts"
                    pxPerMm={pxPerMm}
                    voltageScaleMmPerMv={gainMmPerMv}
                    centerMv={0}
                  />
                </div>
              </article>
            ))}
          </div>
        </section>

        <aside
          className="wave7-side-rail"
          style={{
            "--wave7-row-height": `${rowHeightPx}px`,
          }}
        >
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