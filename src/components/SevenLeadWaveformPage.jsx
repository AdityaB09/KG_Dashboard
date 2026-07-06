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

const ECG_GAIN_OPTIONS = [5, 10, 20];
const DEFAULT_GAIN_MODE = "auto";
const AUTOSCALE_TARGET_FILL = 0.72;
const AUTOSCALE_LOW_FILL = 0.20;
const AUTOSCALE_HIGH_FILL = 0.86;

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

function median(values) {
  if (!values.length) return 0;

  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);

  if (sorted.length % 2 === 0) {
    return (sorted[middle - 1] + sorted[middle]) / 2;
  }

  return sorted[middle];
}

const AUTO_GAIN_HEADROOM = 0.88;
const AUTO_GAIN_COMFORT_LOW = 0.18;
const AUTO_GAIN_COMFORT_HIGH = 0.72;

function percentile(values, p) {
  if (!values.length) return 0;

  const sorted = [...values].sort((a, b) => a - b);
  const index = (p / 100) * (sorted.length - 1);
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  const weight = index - lower;

  if (lower === upper) return sorted[lower];

  return sorted[lower] * (1 - weight) + sorted[upper] * weight;
}

function getLeadAmplitude(samples = []) {
  const values = samples.map(Number).filter(Number.isFinite);

  if (!values.length) {
    return {
      centerMv: 0,
      robustAbsMv: 0,
      hardAbsMv: 0,
      minMv: 0,
      maxMv: 0,
      p2pMv: 0,
    };
  }

  const centerMv = median(values);
  const deviations = values.map((value) => Math.abs(value - centerMv));

  const minMv = Math.min(...values);
  const maxMv = Math.max(...values);

  return {
    centerMv,

    // This controls stable visual sizing.
    // It prevents one noisy sample from causing constant gain flicker.
    robustAbsMv: percentile(deviations, 95),

    // This protects the true spike.
    // This is the hard containment value.
    hardAbsMv: Math.max(...deviations),

    minMv,
    maxMv,
    p2pMv: maxMv - minMv,
  };
}

function getFillRatio({ absMv, gainMmPerMv, rowHeightPx, pxPerMm }) {
  if (!rowHeightPx || !pxPerMm || !gainMmPerMv) return 0;

  const halfRowMm = rowHeightPx / pxPerMm / 2;

  if (halfRowMm <= 0) return 0;

  return (absMv * gainMmPerMv) / halfRowMm;
}

function gainKeepsSpikeInside({ hardAbsMv, gainMmPerMv, rowHeightPx, pxPerMm }) {
  const hardFill = getFillRatio({
    absMv: hardAbsMv,
    gainMmPerMv,
    rowHeightPx,
    pxPerMm,
  });

  return hardFill <= AUTO_GAIN_HEADROOM;
}

function chooseAutoGain({ samples, currentGain, rowHeightPx, pxPerMm }) {
  const values = samples.map(Number).filter(Number.isFinite);

  if (!values.length || !rowHeightPx || !pxPerMm) {
    return currentGain || DEFAULT_GAIN_MM_PER_MV;
  }

  const amplitude = getLeadAmplitude(values);

  if (amplitude.hardAbsMv < 0.005) {
    return 20;
  }

  const safeAt20 = gainKeepsSpikeInside({
    hardAbsMv: amplitude.hardAbsMv,
    gainMmPerMv: 20,
    rowHeightPx,
    pxPerMm,
  });

  const safeAt10 = gainKeepsSpikeInside({
    hardAbsMv: amplitude.hardAbsMv,
    gainMmPerMv: 10,
    rowHeightPx,
    pxPerMm,
  });

  const safeAt5 = gainKeepsSpikeInside({
    hardAbsMv: amplitude.hardAbsMv,
    gainMmPerMv: 5,
    rowHeightPx,
    pxPerMm,
  });

  const robustFillAt10 = getFillRatio({
    absMv: amplitude.robustAbsMv,
    gainMmPerMv: 10,
    rowHeightPx,
    pxPerMm,
  });

  const current = currentGain || DEFAULT_GAIN_MM_PER_MV;

  // Hard safety first:
  // if 10 cannot keep the real spike visible, move to 5.
  if (!safeAt10 && safeAt5) {
    return 5;
  }

  // If even 5 cannot fully contain it, 5 is still the safest standard ECG gain.
  if (!safeAt5) {
    return 5;
  }

  // Prefer 10 mm/mV whenever it is safe and clinically readable.
  if (safeAt10 && robustFillAt10 >= AUTO_GAIN_COMFORT_LOW && robustFillAt10 <= AUTO_GAIN_COMFORT_HIGH) {
    return 10;
  }

  // If the signal is too small at 10, use 20 only if the full spike still fits.
  if (robustFillAt10 < AUTO_GAIN_COMFORT_LOW && safeAt20) {
    return 20;
  }

  // If the signal is too large at 10, use 5.
  if (robustFillAt10 > AUTO_GAIN_COMFORT_HIGH) {
    return 5;
  }

  // Hysteresis: keep current gain if it is safe.
  const currentSafe = gainKeepsSpikeInside({
    hardAbsMv: amplitude.hardAbsMv,
    gainMmPerMv: current,
    rowHeightPx,
    pxPerMm,
  });

  if (currentSafe) {
    return current;
  }

  return safeAt10 ? 10 : 5;
}

function getGainDecisionInfo({ samples, gainMmPerMv, rowHeightPx, pxPerMm }) {
  const amplitude = getLeadAmplitude(samples);

  const hardFill = getFillRatio({
    absMv: amplitude.hardAbsMv,
    gainMmPerMv,
    rowHeightPx,
    pxPerMm,
  });

  const robustFill = getFillRatio({
    absMv: amplitude.robustAbsMv,
    gainMmPerMv,
    rowHeightPx,
    pxPerMm,
  });

  let reason = "standard";

  if (gainMmPerMv === 5) {
    reason = "contains peak";
  } else if (gainMmPerMv === 20) {
    reason = "low amplitude";
  }

  const clipRisk = hardFill > AUTO_GAIN_HEADROOM;

  return {
    fillPercent: Math.round(Math.max(0, Math.min(1.5, robustFill)) * 100),
    peakFillPercent: Math.round(Math.max(0, Math.min(1.5, hardFill)) * 100),
    reason: clipRisk ? "peak risk" : reason,
    clipRisk,
    centerMv: amplitude.centerMv,
  };
}

function getScaleInfo({ gainMmPerMv, rowHeightPx, pxPerMm, centerMv = 0 }) {
  const safeGain = gainMmPerMv || DEFAULT_GAIN_MM_PER_MV;
  const safePxPerMm = pxPerMm || 5;
  const safeRowHeightPx = rowHeightPx || 120;

  const rowHeightMm = safeRowHeightPx / safePxPerMm;
  const halfRangeMv = rowHeightMm / 2 / safeGain;

  return {
    gainMmPerMv: safeGain,
    mvPerSmallBox: 1 / safeGain,
    mvPerLargeBox: 5 / safeGain,
    halfRangeMv,
    centerMv,
    topMv: centerMv + halfRangeMv,
    bottomMv: centerMv - halfRangeMv,
  };
}


function formatScale(value) {
  const numericValue = Number(value);

  if (!Number.isFinite(numericValue)) return "--";

  if (Math.abs(numericValue) >= 10) return numericValue.toFixed(1);
  if (Math.abs(numericValue) >= 1) return numericValue.toFixed(2);
  return numericValue.toFixed(3);
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
  const [leadGainModes, setLeadGainModes] = useState(() =>
  Object.fromEntries(LEADS.map((lead) => [lead.id, DEFAULT_GAIN_MODE]))
);

const [leadAutoGains, setLeadAutoGains] = useState(() =>
  Object.fromEntries(LEADS.map((lead) => [lead.id, DEFAULT_GAIN_MM_PER_MV]))
);
  const [plotWidthPx, setPlotWidthPx] = useState(0);
  const [titleHeightPx, setTitleHeightPx] = useState(0);

  const monitorRef = useRef(null);
  const titleRef = useRef(null);

  function updateLeadGainMode(leadId, nextMode) {
  setLeadGainModes((previousModes) => ({
    ...previousModes,
    [leadId]: nextMode,
  }));
}

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


useEffect(() => {
  if (!rowHeightPx || !pxPerMm) return;

  setLeadAutoGains((previousGains) => {
    const nextGains = { ...previousGains };
    let changed = false;

    for (const lead of LEADS) {
      const nextGain = chooseAutoGain({
        samples: leadWindows[lead.id] || [],
        currentGain: previousGains[lead.id] || DEFAULT_GAIN_MM_PER_MV,
        rowHeightPx,
        pxPerMm,
      });

      if (nextGains[lead.id] !== nextGain) {
        nextGains[lead.id] = nextGain;
        changed = true;
      }
    }

    return changed ? nextGains : previousGains;
  });
}, [leadWindows, rowHeightPx, pxPerMm]);

const metricBoxes = useMemo(() => {
  return LEADS.map((lead) => {
    const samples = leadWindows[lead.id] || [];
    const stats = getLeadStats(samples, waveFrame.latestMv?.[lead.id]);

    const leadGainMode = leadGainModes[lead.id] || DEFAULT_GAIN_MODE;
    const autoGain = leadAutoGains[lead.id] || DEFAULT_GAIN_MM_PER_MV;

    const effectiveGain =
      leadGainMode === "auto"
        ? autoGain
        : Number(leadGainMode) || DEFAULT_GAIN_MM_PER_MV;

    const decision = getGainDecisionInfo({
      samples,
      gainMmPerMv: effectiveGain,
      rowHeightPx,
      pxPerMm,
    });

    const scale = getScaleInfo({
      gainMmPerMv: effectiveGain,
      rowHeightPx,
      pxPerMm,
      centerMv: decision.centerMv,
    });

    return {
      id: lead.id,
      label: lead.label,
      latest: stats.latest,
      p2p: stats.p2p,
      min: stats.min,
      max: stats.max,
      gainMode: leadGainMode,
      gainMmPerMv: effectiveGain,
      mvPerLargeBox: scale.mvPerLargeBox,
      halfRangeMv: scale.halfRangeMv,
      fillPercent: decision.fillPercent,
      peakFillPercent: decision.peakFillPercent,
      reason: decision.reason,
      clipRisk: decision.clipRisk,
      centerMv: decision.centerMv,
    };
  });
}, [
  leadWindows,
  waveFrame.latestMv,
  leadGainModes,
  leadAutoGains,
  rowHeightPx,
  pxPerMm,
]);

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
    {sampleRate} Hz • {visibleSeconds}s • per-lead Auto/5/10/20
  </span>

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
             {sampleRate} samples/sec • 25 mm/sec • each lead Auto/5/10/20 mm/mV
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
{LEADS.map((lead) => {
  const leadSamples = leadWindows[lead.id] || [];

  const leadGainMode = leadGainModes[lead.id] || DEFAULT_GAIN_MODE;
  const autoGain = leadAutoGains[lead.id] || DEFAULT_GAIN_MM_PER_MV;

  const effectiveGain =
    leadGainMode === "auto"
      ? autoGain
      : Number(leadGainMode) || DEFAULT_GAIN_MM_PER_MV;

  const decision = getGainDecisionInfo({
    samples: leadSamples,
    gainMmPerMv: effectiveGain,
    rowHeightPx,
    pxPerMm,
  });

  const scale = getScaleInfo({
    gainMmPerMv: effectiveGain,
    rowHeightPx,
    pxPerMm,
    centerMv: decision.centerMv,
  });

  return (
    <article className="wave7-lead-row" key={lead.id}>
      <span>{lead.label}</span>

      <div className="wave7-calibrated-strip">
        <YAxisScale scale={scale} clipRisk={decision.clipRisk} />

        <WebGLWaveformCanvas
          samples={waveFrame.leadsMv?.[lead.id] || []}
          points={visiblePoints}
          color={lead.color}
          mode="millivolts"
          pxPerMm={pxPerMm}
          voltageScaleMmPerMv={effectiveGain}
          centerMv={decision.centerMv}
        />
      </div>
    </article>
  );
})}
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
  <MetricBox
    key={metric.id}
    {...metric}
    onGainModeChange={updateLeadGainMode}
  />
))}
        </aside>
      </main>
    </section>
  );
}

function YAxisScale({ scale, clipRisk }) {
  return (
    <div className={`wave7-y-scale ${clipRisk ? "clip-risk" : ""}`} aria-hidden="true">
      <span>+{formatScale(scale.halfRangeMv)} mV</span>
      <span>{clipRisk ? "peak risk" : "0"}</span>
      <span>-{formatScale(scale.halfRangeMv)} mV</span>
    </div>
  );
}


function MetricBox({
  id,
  label,
  latest,
  p2p,
  min,
  max,
  gainMode,
  gainMmPerMv,
  mvPerLargeBox,
  halfRangeMv,
  fillPercent,
  peakFillPercent,
  reason,
  clipRisk,
  onGainModeChange,
}) {
  return (
    <section className={`wave7-metric ${clipRisk ? "clip-risk" : ""}`}>
      <div className="wave7-metric-head">
        <small>{label}</small>

        <span className="wave7-auto-gain-badge">
          {gainMode === "auto" ? "Auto" : "Manual"} {gainMmPerMv}
        </span>
      </div>

      <div className="wave7-lead-gain-toggle" aria-label={`${label} gain selector`}>
        <button
          type="button"
          className={gainMode === "auto" ? "active" : ""}
          onClick={() => onGainModeChange(id, "auto")}
        >
          A
        </button>

        {ECG_GAIN_OPTIONS.map((gain) => (
          <button
            key={gain}
            type="button"
            className={gainMode === gain ? "active" : ""}
            onClick={() => onGainModeChange(id, gain)}
          >
            {gain}
          </button>
        ))}
      </div>

      <div className="wave7-metric-main">
        <strong>{latest}</strong>
        <em>mV latest</em>
      </div>

      <div className="wave7-metric-stats compact">
        <span>
          P-P <b>{p2p}</b>
        </span>
        <span>
          Min <b>{min}</b>
        </span>
        <span>
          Max <b>{max}</b>
        </span>
        <span>
          Big <b>{formatScale(mvPerLargeBox)}</b>
        </span>
        <span>
          Range <b>±{formatScale(halfRangeMv)}</b>
        </span>
        <span>
          Peak <b>{peakFillPercent}%</b>
        </span>
      </div>

      <div className={`wave7-gain-reason ${clipRisk ? "clip-risk" : ""}`}>
        {reason}
      </div>
    </section>
  );
}