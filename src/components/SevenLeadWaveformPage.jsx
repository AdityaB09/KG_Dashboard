import { useEffect, useMemo, useRef, useState } from "react";
import WebGLWaveformCanvas from "./WebGLWaveformCanvas";
import { connectWaveformStream } from "../services/waveformStream";
import "./SevenLeadWaveformPage.css";

const ECG_PAPER_SPEED_MM_PER_SEC = 25;
const DEFAULT_VISIBLE_SECONDS = 3;
const DEFAULT_GAIN_MM_PER_MV = 10;
const DEFAULT_GAIN_MODE = "auto";
const ECG_GAIN_OPTIONS = [5, 10, 20];

const ECG_ZERO_MV = 0;

const AUTO_HEADROOM = 0.88;
const AUTO_LOW_FILL_AT_10 = 0.16;
const AUTO_RETURN_FROM_5_FILL = 0.72;
const AUTO_RETURN_FROM_20_FILL = 0.28;

const MIN_PX_PER_MM = 3.2;
const MAX_PX_PER_MM = 9.0;
const GRID_GAP_PX = 6;

const WAVEFORM_COLUMN_COUNT = 3;
const WAVEFORM_ROW_COUNT = 3;
const VITAL_RAIL_WIDTH_PX = 160;

const LEADS = [
  { id: "lead1", label: "Lead I", color: "cyan", area: "lead1" },
  { id: "lead2", label: "Lead II", color: "cyan", area: "lead2" },
  { id: "lead3", label: "Lead III", color: "cyan", area: "lead3" },
  { id: "avr", label: "aVR", color: "cyan", area: "avr" },
  { id: "avl", label: "aVL", color: "cyan", area: "avl" },
  { id: "avf", label: "aVF", color: "cyan", area: "avf" },
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
  vitals: {},
};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

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

function median(values) {
  if (!values.length) return 0;

  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);

  if (sorted.length % 2 === 0) {
    return (sorted[middle - 1] + sorted[middle]) / 2;
  }

  return sorted[middle];
}

function formatMvValue(value) {
  const numericValue = Number(value);

  if (!Number.isFinite(numericValue)) return "--";
  if (Math.abs(numericValue) >= 10) return numericValue.toFixed(1);
  if (Math.abs(numericValue) >= 1) return numericValue.toFixed(2);

  return numericValue.toFixed(3);
}

function getLeadStats(samples = [], latestFallback) {
  const values = samples.map(Number).filter(Number.isFinite);

  if (!values.length) {
    return {
      latest: formatMvValue(latestFallback),
      min: "--",
      max: "--",
      p2p: "--",
    };
  }

  const latest = values[values.length - 1];
  const min = Math.min(...values);
  const max = Math.max(...values);

  return {
    latest: formatMvValue(latest),
    min: formatMvValue(min),
    max: formatMvValue(max),
    p2p: formatMvValue(max - min),
  };
}


function appendLeadWindows(prev, frame) {
  const sampleRate = frame.sampleRate || 220;
  const maxPoints = Math.round(sampleRate * DEFAULT_VISIBLE_SECONDS);

  const next = {};

  for (const lead of LEADS) {
    const previousSamples = prev[lead.id] || [];
    const incomingSamples = frame.leadsMv?.[lead.id] || [];

    next[lead.id] = [...previousSamples, ...incomingSamples].slice(-maxPoints);
  }

  return next;
}



function getLeadAmplitude(samples = []) {
  const values = samples.map(Number).filter(Number.isFinite);

  if (!values.length) {
    return {
      minMv: 0,
      maxMv: 0,
      p2pMv: 0,
      robustAbsMv: 0,
      hardAbsMv: 0,
    };
  }

  const minMv = Math.min(...values);
  const maxMv = Math.max(...values);
  const p2pMv = maxMv - minMv;

  const absFromZero = values.map((value) => Math.abs(value - ECG_ZERO_MV));

  return {
    minMv,
    maxMv,
    p2pMv,

    // Stable readability value.
    // This prevents one noisy sample from constantly flipping gain.
    robustAbsMv: percentile(absFromZero, 95),

    // Hard safety value.
    // This protects the actual spike from going outside the tile.
    hardAbsMv: Math.max(...absFromZero),
  };
}

function getHalfRowMm(rowHeightPx, pxPerMm) {
  if (!rowHeightPx || !pxPerMm) return 0;
  return rowHeightPx / pxPerMm / 2;
}

function getPeakFillRatio({ absMv, gainMmPerMv, rowHeightPx, pxPerMm }) {
  const halfRowMm = getHalfRowMm(rowHeightPx, pxPerMm);

  if (!halfRowMm || !gainMmPerMv) return 0;

  return (absMv * gainMmPerMv) / halfRowMm;
}

function peakFitsAtGain({ hardAbsMv, gainMmPerMv, rowHeightPx, pxPerMm }) {
  const fill = getPeakFillRatio({
    absMv: hardAbsMv,
    gainMmPerMv,
    rowHeightPx,
    pxPerMm,
  });

  return fill <= AUTO_HEADROOM;
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

  const fits20 = peakFitsAtGain({
    hardAbsMv: amplitude.hardAbsMv,
    gainMmPerMv: 20,
    rowHeightPx,
    pxPerMm,
  });

  const fits10 = peakFitsAtGain({
    hardAbsMv: amplitude.hardAbsMv,
    gainMmPerMv: 10,
    rowHeightPx,
    pxPerMm,
  });

  const fits5 = peakFitsAtGain({
    hardAbsMv: amplitude.hardAbsMv,
    gainMmPerMv: 5,
    rowHeightPx,
    pxPerMm,
  });

  const fillAt10 = getPeakFillRatio({
    absMv: amplitude.robustAbsMv,
    gainMmPerMv: 10,
    rowHeightPx,
    pxPerMm,
  });

  const current = currentGain || DEFAULT_GAIN_MM_PER_MV;

  // Hard safety first. If the true peak cannot fit at 10, use 5.
  if (!fits10 && fits5) return 5;

  // If even 5 cannot fit, 5 is still the safest standard ECG gain.
  if (!fits5) return 5;

  // Avoid flickering after switching to 5.
  if (current === 5) {
    return fits10 && fillAt10 <= AUTO_RETURN_FROM_5_FILL ? 10 : 5;
  }

  // Avoid flickering after switching to 20.
  if (current === 20) {
    if (fillAt10 >= AUTO_RETURN_FROM_20_FILL) return 10;
    return fits20 ? 20 : 10;
  }

  // Use 20 only when the signal is genuinely small and full peaks still fit.
  if (fillAt10 < AUTO_LOW_FILL_AT_10 && fits20) {
    return 20;
  }

  return 10;
}

function getDisplayScale({ samples, gainMmPerMv, rowHeightPx, pxPerMm }) {
  const amplitude = getLeadAmplitude(samples);
  const safeGain = gainMmPerMv || DEFAULT_GAIN_MM_PER_MV;
  const halfRowMm = getHalfRowMm(rowHeightPx, pxPerMm);
  const halfRangeMv = halfRowMm > 0 ? halfRowMm / safeGain : 1;

  const peakFill = getPeakFillRatio({
    absMv: amplitude.hardAbsMv,
    gainMmPerMv: safeGain,
    rowHeightPx,
    pxPerMm,
  });

  return {
    centerMv: 0,
    halfRangeMv,
    topMv: halfRangeMv,
    bottomMv: -halfRangeMv,
    mvPerLargeBox: 5 / safeGain,
    peakFillPercent: Math.round(Math.min(1.5, peakFill) * 100),
    clipRisk: peakFill > AUTO_HEADROOM,
  };
}

function isZeroVisible() {
  return true;
}

function getZeroLineTopPercent() {
  return 50;
}



export default function SevenLeadWaveformPage({ patient, onOpenAnalytics }) {
  const [waveFrame, setWaveFrame] = useState(EMPTY_FRAME);
  const [leadWindows, setLeadWindows] = useState(EMPTY_LEADS);
  const [streamStatus, setStreamStatus] = useState("connecting");

 

  const [leadAutoGains, setLeadAutoGains] = useState(() =>
    Object.fromEntries(LEADS.map((lead) => [lead.id, DEFAULT_GAIN_MM_PER_MV]))
  );

  const [gridSize, setGridSize] = useState({
    width: 0,
    height: 0,
  });

  const gridRef = useRef(null);

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
    const gridElement = gridRef.current;

    if (!gridElement) return undefined;

    function updateMeasurements() {
      const rect = gridElement.getBoundingClientRect();

      setGridSize({
        width: Math.max(1, rect.width),
        height: Math.max(1, rect.height),
      });
    }

    updateMeasurements();

    const observer = new ResizeObserver(updateMeasurements);
    observer.observe(gridElement);

    return () => observer.disconnect();
  }, []);

const visibleSeconds =
  Number(waveFrame.xAxis?.secondsVisible) || DEFAULT_VISIBLE_SECONDS;

const sampleRate = waveFrame.sampleRate || 220;
const visiblePoints = Math.round(sampleRate * visibleSeconds);
const paperWidthMm = visibleSeconds * ECG_PAPER_SPEED_MM_PER_SEC;

const waveformGridWidth =
  gridSize.width > 0
    ? Math.max(1, gridSize.width - VITAL_RAIL_WIDTH_PX - GRID_GAP_PX * 3)
    : 1080;

const tileWidthPx =
  gridSize.width > 0
    ? waveformGridWidth / WAVEFORM_COLUMN_COUNT
    : 360;

const tileHeightPx =
  gridSize.height > 0
    ? (gridSize.height - GRID_GAP_PX * 2) / WAVEFORM_ROW_COUNT
    : 160;

const pxPerMm = clamp(tileWidthPx / paperWidthMm, MIN_PX_PER_MM, MAX_PX_PER_MM);
const rowHeightPx = Math.max(1, tileHeightPx);

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

  const leadTiles = useMemo(() => {
    return LEADS.map((lead) => {
      const samples = leadWindows[lead.id] || [];
      const stats = getLeadStats(samples, waveFrame.latestMv?.[lead.id]);

      // const leadGainMode = leadGainModes[lead.id] || DEFAULT_GAIN_MODE;
      // const autoGain = leadAutoGains[lead.id] || DEFAULT_GAIN_MM_PER_MV;

      // const effectiveGain =
      //   leadGainMode === "auto"
      //     ? autoGain
      //     : Number(leadGainMode) || DEFAULT_GAIN_MM_PER_MV;

      const autoGain = leadAutoGains[lead.id] || DEFAULT_GAIN_MM_PER_MV;
const effectiveGain = autoGain;

      const scale = getDisplayScale({
        samples,
        gainMmPerMv: effectiveGain,
        rowHeightPx,
        pxPerMm,
      });

      return {
        ...lead,
        latest: stats.latest,
        p2p: stats.p2p,
        // gainMode: leadGainMode,
        // gainMmPerMv: effectiveGain,
        gainMode: DEFAULT_GAIN_MODE,
gainMmPerMv: effectiveGain,
        scale,
      };
    });
  }, [
    leadWindows,
    waveFrame.latestMv,
    // leadGainModes,
    leadAutoGains,
    rowHeightPx,
    pxPerMm,
  ]);

// const gainSummary = useMemo(() => {
//   return ECG_GAIN_OPTIONS.map((gain) => ({
//     gain,
//     count: leadTiles.filter((lead) => lead.gainMmPerMv === gain).length,
//   }));
// }, [leadTiles]);

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

          {/* <span className="wave7-speed-pill">
            {sampleRate} Hz • {visibleSeconds}s • 25 mm/sec • per-lead gain
          </span> */}

          <button
            type="button"
            className="wave7-action-btn"
            onClick={onOpenAnalytics}
          >
            Open Analytics
          </button>
        </div>
      </header>

      <main className="wave7-monitor-card">
        <div className="wave7-monitor-title">
          <div>
            <p className="wave7-eyebrow">High precision</p>
            <h2>6 waveform ECG matrix</h2>
          </div>

          <span>5/10/20 mm/mV</span>
        </div>

<section
  ref={gridRef}
  className="wave7-grid"
  style={{
    "--ecg-mm": `${pxPerMm}px`,
    "--wave7-grid-gap": `${GRID_GAP_PX}px`,
    "--wave7-vitals-width": `${VITAL_RAIL_WIDTH_PX}px`,
  }}
>
  {leadTiles.map((lead) => (
    <WaveformTile
      key={lead.id}
      lead={lead}
      samples={waveFrame.leadsMv?.[lead.id] || []}
      visiblePoints={visiblePoints}
      pxPerMm={pxPerMm}
      // onGainModeChange={updateLeadGainMode}
    />
  ))}

  <BedsideVitalsPanel waveFrame={waveFrame} />
</section>
      </main>
    </section>
  );
}

// function WaveformTile({
//   lead,
//   samples,
//   visiblePoints,
//   pxPerMm,
//   onGainModeChange,
// }) {
//   const { scale } = lead;

//   return (
//     <article
//   className={`wave7-wave-tile ${scale.clipRisk ? "clip-risk" : ""}`}
//   style={{ gridArea: lead.area }}
// >
//       <div className="wave7-calibrated-strip">
//         <YAxisScale scale={scale} />

//         <div className="wave7-zero-line" />

//         <WebGLWaveformCanvas
//           samples={samples}
//           points={visiblePoints}
//           color={lead.color}
//           mode="millivolts"
//           pxPerMm={pxPerMm}
//           voltageScaleMmPerMv={lead.gainMmPerMv}
//           centerMv={0}
//         />
//       </div>

//       <div className="wave7-tile-top">
//         <strong>{lead.label}</strong>
//         <span>
//           {lead.gainMode === "auto" ? "Auto" : "Manual"} {lead.gainMmPerMv}
//         </span>
//       </div>

//       <div className="wave7-lead-gain-toggle" aria-label={`${lead.label} gain selector`}>
//         <button
//           type="button"
//           className={lead.gainMode === "auto" ? "active" : ""}
//           onClick={() => onGainModeChange(lead.id, "auto")}
//         >
//           A
//         </button>

//         {ECG_GAIN_OPTIONS.map((gain) => (
//           <button
//             key={gain}
//             type="button"
//             className={lead.gainMode === gain ? "active" : ""}
//             onClick={() => onGainModeChange(lead.id, gain)}
//           >
//             {gain}
//           </button>
//         ))}
//       </div>

//       <div className="wave7-tile-bottom">
//         <strong>
//           {lead.latest}
//           <em>mV</em>
//         </strong>

//         <span>P-P {lead.p2p}</span>
//       </div>
//     </article>
//   );
// }

function WaveformTile({
  lead,
  samples,
  visiblePoints,
  pxPerMm,
}) {
  const { scale } = lead;

  return (
    <article
      className={`wave7-wave-tile ${scale.clipRisk ? "clip-risk" : ""}`}
      style={{ gridArea: lead.area }}
    >
      <div className="wave7-calibrated-strip">
        <YAxisScale scale={scale} />

        <div className="wave7-zero-line" />

        <WebGLWaveformCanvas
          samples={samples}
          points={visiblePoints}
          color={lead.color}
          mode="millivolts"
          pxPerMm={pxPerMm}
          voltageScaleMmPerMv={lead.gainMmPerMv}
          centerMv={0}
        />
      </div>

      <div className="wave7-tile-top">
        <strong>{lead.label}</strong>
        <span>Auto {lead.gainMmPerMv}</span>
      </div>

      <div className="wave7-tile-bottom">
        <strong>
          {lead.latest}
          <em>mV</em>
        </strong>

        <span>P-P {lead.p2p}</span>
      </div>
    </article>
  );
}

function YAxisScale({ scale }) {
  return (
    <div className={`wave7-y-scale ${scale.clipRisk ? "clip-risk" : ""}`}>
      <span>+{formatMvValue(scale.halfRangeMv)}</span>
      <span>0 mV</span>
      <span>-{formatMvValue(scale.halfRangeMv)}</span>
    </div>
  );
}

function formatVitalValue(value, decimals = 0) {
  const numericValue = Number(value);

  if (!Number.isFinite(numericValue)) return "--";

  return numericValue.toFixed(decimals);
}



function useRollingSeries(value, maxPoints = 28) {
  const [series, setSeries] = useState(() =>
    Array.from({ length: maxPoints }, () => Number(value) || 0)
  );

  useEffect(() => {
    const numericValue = Number(value);

    if (!Number.isFinite(numericValue)) return;

    setSeries((previous) => [
      ...previous.slice(-(maxPoints - 1)),
      numericValue,
    ]);
  }, [value, maxPoints]);

  return series;
}

function MiniSpo2Trend({ series }) {
  const width = 86;
  const height = 42;

  const values = series
    .map((value) => Number(value))
    .filter(Number.isFinite);

  const safeValues = values.length ? values : [97];

  const min = Math.min(92, ...safeValues);
  const max = Math.max(100, ...safeValues);
  const range = max - min || 1;

  const points = safeValues
    .map((value, index) => {
      const x =
        safeValues.length <= 1
          ? width
          : (index / (safeValues.length - 1)) * width;

      const y = height - ((value - min) / range) * height;

      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const lastValue = safeValues[safeValues.length - 1];
  const lastX = width;
  const lastY = height - ((lastValue - min) / range) * height;

  return (
    <svg
      className="wave7-spo2-reference-graph"
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden="true"
    >
      <polyline points={points} />
      <circle cx={lastX} cy={lastY} r="2.8" />
    </svg>
  );
}

function BedsideVitalsPanel({ waveFrame }) {
  const vitals = waveFrame.vitals || {};

  const heartRate =
    vitals.heartRate ??
    vitals.hr ??
    vitals.heart_rate;

  const spo2 =
    vitals.spo2 ??
    vitals.SpO2 ??
    vitals.oxygenSaturation ??
    vitals.oxygen_saturation;

  const systolic =
    vitals.systolic ??
    vitals.sbp ??
    vitals.systolicBloodPressure;

  const diastolic =
    vitals.diastolic ??
    vitals.dbp ??
    vitals.diastolicBloodPressure;

  const respiratoryRate =
    vitals.respiratoryRate ??
    vitals.rr ??
    vitals.respiratory_rate;

  const temperature =
    vitals.temperature ??
    vitals.temp ??
    vitals.bodyTemperature ??
    vitals.body_temperature;

  // const spo2Series = useRollingSeries(spo2, 28);
  const rollingSpo2Series = useRollingSeries(spo2, 28);

const spo2Series =
  Array.isArray(vitals.spo2Trace) && vitals.spo2Trace.length
    ? vitals.spo2Trace
    : rollingSpo2Series;


  return (
    <aside className="wave7-vitals-panel wave7-reference-vitals">
      <div className="wave7-vitals-header">
        <p className="wave7-eyebrow">Bedside widgets</p>
        <span>{waveFrame.status === "connected" ? "Live signal" : "Waiting"}</span>
      </div>

      <ReferenceVitalCard
        className="hr"
        label="HR"
        value={formatVitalValue(heartRate)}
        unit=""
      />

      <ReferenceVitalCard
        className="spo2"
        label="SpO₂"
        value={formatVitalValue(spo2)}
        unit=""
        graph={<MiniSpo2Trend series={spo2Series} />}
      />

      <ReferenceVitalCard
        className="bp"
        label="NIBP"
        value={`${formatVitalValue(systolic)}/${formatVitalValue(diastolic)}`}
        unit=""
      />

      <ReferenceVitalCard
        className="rr"
        label="RR"
        value={formatVitalValue(respiratoryRate)}
        unit=""
      />

      <ReferenceVitalCard
        className="temp"
        label="Temp"
        value={formatVitalValue(temperature, 1)}
        unit=""
      />
    </aside>
  );
}

function ReferenceVitalCard({ label, value, unit, graph, className = "" }) {
  return (
    <article className={`wave7-reference-vital-card ${className}`}>
      <div className="wave7-reference-vital-title">
        <span>{label}</span>
      </div>

      <div className="wave7-reference-vital-main">
        <div className="wave7-reference-vital-value">
          <strong>{value}</strong>
          <em>{unit}</em>
        </div>

        {graph && (
          <div className="wave7-reference-graph-box">
            {graph}
          </div>
        )}
      </div>
    </article>
  );
}

function CompactVitalCard({ label, value, unit, graph, className = "" }) {
  return (
    <div className={`wave7-vital-card wave7-vital-compact ${className}`}>
      <div className="wave7-compact-vital-top">
        <span>{label}</span>
      </div>

      <div className="wave7-compact-vital-body">
        <div className="wave7-compact-vital-value">
          <strong>{value}</strong>
          <em>{unit}</em>
        </div>

        {graph && (
          <div className="wave7-compact-vital-graph">
            {graph}
          </div>
        )}
      </div>
    </div>
  );
}
