import { useEffect, useMemo, useRef, useState } from "react";
import WebGLWaveformCanvas from "./WebGLWaveformCanvas";
import { connectWaveformStream } from "../services/waveformStream";
import "./SevenLeadWaveformPage.css";

const ECG_PAPER_SPEED_MM_PER_SEC = 25;
const DEFAULT_GAIN_MM_PER_MV = 10;
const DEFAULT_VISIBLE_SECONDS = 6;

const MIN_PX_PER_MM = 3.5;
const MAX_PX_PER_MM = 8.5;
const ECG_ROW_HEIGHT_MM = 24;

const LEADS = [
  { id: "lead1", label: "Lead I", color: "cyan" },
  { id: "lead2", label: "Lead II", color: "cyan" },
  { id: "lead3", label: "Lead III", color: "cyan" },
  { id: "avr", label: "aVR", color: "cyan" },
  { id: "avl", label: "aVL", color: "cyan" },
  { id: "avf", label: "aVF", color: "cyan" },
  { id: "v1", label: "V1", color: "cyan" },
];

const EMPTY_LEADS = Object.fromEntries(LEADS.map((lead) => [lead.id, []]));

const EMPTY_FRAME = {
  source: "physionet-ptb-xl",
  sampleRate: 220,
  batchSize: 0,
  receivedAt: "",
  leadsMv: EMPTY_LEADS,
  latestMv: {},
  xAxis: {
    secondsVisible: DEFAULT_VISIBLE_SECONDS,
  },
  vitals: {
    heartRate: "--",
  },
};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function valueOrDash(value) {
  return value === null || value === undefined || value === "" ? "--" : value;
}

function formatMv(value) {
  const numericValue = Number(value);

  if (!Number.isFinite(numericValue)) return "--";

  return numericValue.toFixed(3);
}

function getLeadStats(samples = [], latestFallback) {
  const values = samples.map(Number).filter(Number.isFinite);

  if (!values.length) {
    return {
      latest: formatMv(latestFallback),
      p2p: "--",
      min: "--",
      max: "--",
    };
  }

  const latest = values[values.length - 1];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const p2p = max - min;

  return {
    latest: formatMv(latest),
    p2p: formatMv(p2p),
    min: formatMv(min),
    max: formatMv(max),
  };
}

function appendLeadWindows(prev, frame) {
  const sampleRate = frame.sampleRate || 220;
  const visibleSeconds = frame.xAxis?.secondsVisible || DEFAULT_VISIBLE_SECONDS;
  const maxPoints = Math.round(sampleRate * visibleSeconds);

  const next = {};

  for (const lead of LEADS) {
    const previousSamples = prev[lead.id] || [];
    const incomingSamples = frame.leadsMv?.[lead.id] || [];

    next[lead.id] = [...previousSamples, ...incomingSamples].slice(-maxPoints);
  }

  return next;
}

export default function SevenLeadWaveformPage({ patient, onOpenAnalytics }) {
  const [waveFrame, setWaveFrame] = useState(EMPTY_FRAME);
  const [leadWindows, setLeadWindows] = useState(EMPTY_LEADS);
  const [streamStatus, setStreamStatus] = useState("connecting");
  const [gainMmPerMv, setGainMmPerMv] = useState(DEFAULT_GAIN_MM_PER_MV);
  const [plotWidthPx, setPlotWidthPx] = useState(0);
  const [titleHeightPx, setTitleHeightPx] = useState(0);

  const monitorRef = useRef(null);
  const titleRef = useRef(null);

  useEffect(() => {
    setLeadWindows(EMPTY_LEADS);

    const disconnectWaveforms = connectWaveformStream({
      onFrame: (frame) => {
        setWaveFrame(frame);
        setLeadWindows((prev) => appendLeadWindows(prev, frame));
        setStreamStatus(frame.status === "connected" ? "live" : "warning");
      },
      onError: () => setStreamStatus("warning"),
    });

    return () => {
      disconnectWaveforms?.();
    };
  }, [patient?.id]);

  useEffect(() => {
    const monitorElement = monitorRef.current;
    const titleElement = titleRef.current;

    if (!monitorElement || !titleElement) return undefined;

    function updateMeasurements() {
      const monitorRect = monitorElement.getBoundingClientRect();
      const titleRect = titleElement.getBoundingClientRect();

      setPlotWidthPx(Math.max(1, monitorRect.width));
      setTitleHeightPx(Math.max(1, titleRect.height));
    }

    updateMeasurements();

    const observer = new ResizeObserver(updateMeasurements);
    observer.observe(monitorElement);
    observer.observe(titleElement);

    return () => observer.disconnect();
  }, []);

  const visibleSeconds = waveFrame.xAxis?.secondsVisible || DEFAULT_VISIBLE_SECONDS;
  const sampleRate = waveFrame.sampleRate || 220;
  const visiblePoints = Math.round(sampleRate * visibleSeconds);

  const paperWidthMm = visibleSeconds * ECG_PAPER_SPEED_MM_PER_SEC;

  const pxPerMm = clamp(
    plotWidthPx > 0 ? plotWidthPx / paperWidthMm : 5,
    MIN_PX_PER_MM,
    MAX_PX_PER_MM
  );

  const rowHeightPx = Math.round(clamp(pxPerMm * ECG_ROW_HEIGHT_MM, 112, 150));
  const titleOffsetPx = Math.round(titleHeightPx + 10);

  const metricBoxes = useMemo(() => {
    return LEADS.map((lead) => {
      const stats = getLeadStats(leadWindows[lead.id], waveFrame.latestMv?.[lead.id]);

      return {
        id: lead.id,
        label: lead.label,
        latest: stats.latest,
        p2p: stats.p2p,
        min: stats.min,
        max: stats.max,
      };
    });
  }, [leadWindows, waveFrame.latestMv]);

  return (
    <section className="wave7-page">
      <header className="wave7-header">
        <div className="wave7-patient-copy">
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
          <div ref={titleRef} className="wave7-monitor-title">
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
            "--wave7-title-offset": `${titleOffsetPx}px`,
          }}
        >
          {metricBoxes.map((metric) => (
            <MetricBox key={metric.id} {...metric} />
          ))}
        </aside>
      </main>
    </section>
  );
}

function MetricBox({ label, latest, p2p, min, max }) {
  return (
    <section className="wave7-metric">
      <small>{label}</small>

      <div className="wave7-metric-main">
        <strong>{latest}</strong>
        <em>mV latest</em>
      </div>

      <div className="wave7-metric-stats">
        <span>
          P-P <b>{p2p}</b>
        </span>
        <span>
          Min <b>{min}</b>
        </span>
        <span>
          Max <b>{max}</b>
        </span>
      </div>
    </section>
  );
}