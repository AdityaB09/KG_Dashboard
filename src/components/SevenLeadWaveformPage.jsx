import { useEffect, useMemo, useRef, useState } from "react";
import WebGLWaveformCanvas from "./WebGLWaveformCanvas";
import { connectWaveformStream } from "../services/waveformStream";
import "./SevenLeadWaveformPage.css";

const ECG_PAPER_SPEED_MM_PER_SEC = 25;
const DEFAULT_VISIBLE_SECONDS = 3;
const DEFAULT_GAIN_MM_PER_MV = 10;
const DEFAULT_GAIN_MODE = "auto";

/*
  Calibrated display gain ladder.

  This does not alter ECG data or morphology.
  It only changes how many millimeters represent 1 mV.
  The active gain is always shown as Auto 2.5 / 5 / 10 / 20 / 40.
*/
const ECG_GAIN_OPTIONS = [2.5, 5, 10, 20, 40];

const ECG_ZERO_MV = 0;

const AUTO_HEADROOM = 0.9;
const AUTO_TARGET_P2P_FILL = 0.46;
const AUTO_KEEP_MIN_P2P_FILL = 0.34;
const AUTO_KEEP_MAX_P2P_FILL = 0.64;

const MIN_PX_PER_MM = 3.2;
const MAX_PX_PER_MM = 9.0;
const GRID_GAP_PX = 6;

const WAVEFORM_COLUMN_COUNT = 2;
const WAVEFORM_ROW_COUNT = 3;
const VITAL_RAIL_WIDTH_PX = 160;

const ECG_MINOR_BOXES_PER_MAJOR = 5;
const PPG_MINI_GRAPH_POINTS = 64;

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
  const visibleSeconds =
    Number(frame.xAxis?.secondsVisible) || DEFAULT_VISIBLE_SECONDS;

  const maxPoints = Math.round(sampleRate * visibleSeconds);
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
      robustP2pMv: 0,
      displayP2pMv: 0,
      robustAbsMv: 0,
      hardAbsMv: 0,
    };
  }

  const minMv = Math.min(...values);
  const maxMv = Math.max(...values);
  const p2pMv = maxMv - minMv;

  const p05 = percentile(values, 5);
  const p95 = percentile(values, 95);
  const robustP2pMv = Math.max(0, p95 - p05);

  const absFromZero = values.map((value) => Math.abs(value - ECG_ZERO_MV));

  /*
    displayP2pMv is used only for gain choice.
    It avoids one noisy sample dominating the visual gain decision,
    but it still respects the real peak-to-peak signal.
  */
  const displayP2pMv = Math.max(robustP2pMv, p2pMv * 0.65);

  return {
    minMv,
    maxMv,
    p2pMv,
    robustP2pMv,
    displayP2pMv,
    robustAbsMv: percentile(absFromZero, 95),
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

function getP2pFillRatio({ p2pMv, gainMmPerMv, rowHeightPx, pxPerMm }) {
  if (!rowHeightPx || !pxPerMm || !gainMmPerMv) return 0;

  const rowHeightMm = rowHeightPx / pxPerMm;

  if (!rowHeightMm) return 0;

  return (p2pMv * gainMmPerMv) / rowHeightMm;
}

function getClosestGain(allowedGains, idealGain) {
  if (!allowedGains.length) return DEFAULT_GAIN_MM_PER_MV;

  return allowedGains.reduce((bestGain, gain) => {
    const bestDistance = Math.abs(bestGain - idealGain);
    const nextDistance = Math.abs(gain - idealGain);

    return nextDistance < bestDistance ? gain : bestGain;
  }, allowedGains[0]);
}

function chooseAutoGain({ samples, currentGain, rowHeightPx, pxPerMm }) {
  const values = samples.map(Number).filter(Number.isFinite);

  if (!values.length || !rowHeightPx || !pxPerMm) {
    return currentGain || DEFAULT_GAIN_MM_PER_MV;
  }

  const amplitude = getLeadAmplitude(values);
  const rowHeightMm = rowHeightPx / pxPerMm;
  const current = currentGain || DEFAULT_GAIN_MM_PER_MV;

  if (!rowHeightMm || amplitude.hardAbsMv < 0.005) {
    return ECG_GAIN_OPTIONS[ECG_GAIN_OPTIONS.length - 1];
  }

  const viableGains = ECG_GAIN_OPTIONS.filter((gain) =>
    peakFitsAtGain({
      hardAbsMv: amplitude.hardAbsMv,
      gainMmPerMv: gain,
      rowHeightPx,
      pxPerMm,
    })
  );

  if (!viableGains.length) {
    return ECG_GAIN_OPTIONS[0];
  }

  const currentStillFits = viableGains.includes(current);
  const currentP2pFill = getP2pFillRatio({
    p2pMv: amplitude.displayP2pMv,
    gainMmPerMv: current,
    rowHeightPx,
    pxPerMm,
  });

  /*
    Hysteresis:
    Keep the current gain if it is safe and already visually acceptable.
    This prevents rapid gain flicker.
  */
  if (
    currentStillFits &&
    currentP2pFill >= AUTO_KEEP_MIN_P2P_FILL &&
    currentP2pFill <= AUTO_KEEP_MAX_P2P_FILL
  ) {
    return current;
  }

  const idealGain =
    (AUTO_TARGET_P2P_FILL * rowHeightMm) /
    Math.max(amplitude.displayP2pMv, 0.01);

  return getClosestGain(viableGains, idealGain);
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
    ? Math.max(1, gridSize.width - VITAL_RAIL_WIDTH_PX - GRID_GAP_PX * 2)
    : 720;

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
            {/* <p className="wave7-eyebrow">High precision</p> */}
            <h2>6 waveform ECG matrix</h2>
          </div>

          {/* <span>Calibrated auto gain</span> */}
        </div>

<section
  ref={gridRef}
  className="wave7-grid"
 style={{
  "--ecg-mm": `${pxPerMm}px`,
  "--ecg-major-box": `${pxPerMm * ECG_MINOR_BOXES_PER_MAJOR}px`,
  "--ecg-half-mm": `${pxPerMm / 2}px`,
  "--ecg-half-major-box": `${(pxPerMm * ECG_MINOR_BOXES_PER_MAJOR) / 2}px`,
  "--ecg-zero-y": "50%",
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

function downsampleSeries(values, targetPoints = PPG_MINI_GRAPH_POINTS) {
  if (values.length <= targetPoints) return values;

  return Array.from({ length: targetPoints }, (_, index) => {
    const sourceIndex = Math.round(
      (index / (targetPoints - 1)) * (values.length - 1)
    );

    return values[sourceIndex];
  });
}

function getOnePpgBeat(values, sampleRate = 220) {
  const cleanValues = values
    .map((value) => Number(value))
    .filter(Number.isFinite);

  if (cleanValues.length < 12) {
    return cleanValues.length ? cleanValues : [0.5];
  }

  const minValue = Math.min(...cleanValues);
  const maxValue = Math.max(...cleanValues);
  const range = maxValue - minValue || 1;

  const threshold = minValue + range * 0.58;
  const minPeakDistance = Math.max(12, Math.round(sampleRate * 0.32));

  const peaks = [];
  let lastPeakIndex = -minPeakDistance;

  for (let index = 1; index < cleanValues.length - 1; index += 1) {
    const value = cleanValues[index];

    if (index - lastPeakIndex < minPeakDistance) continue;

    const isLocalPeak =
      value >= threshold &&
      value >= cleanValues[index - 1] &&
      value >= cleanValues[index + 1];

    if (isLocalPeak) {
      peaks.push(index);
      lastPeakIndex = index;
    }
  }

  let selectedPeakIndex = peaks[peaks.length - 1];

  if (!Number.isFinite(selectedPeakIndex)) {
    const recentStart = Math.floor(cleanValues.length * 0.55);
    let bestIndex = recentStart;

    for (let index = recentStart; index < cleanValues.length; index += 1) {
      if (cleanValues[index] > cleanValues[bestIndex]) {
        bestIndex = index;
      }
    }

    selectedPeakIndex = bestIndex;
  }

  const beforePeak = Math.round(sampleRate * 0.18);
  const afterPeak = Math.round(sampleRate * 0.52);
  const desiredLength = beforePeak + afterPeak;

  let start = selectedPeakIndex - beforePeak;
  let end = selectedPeakIndex + afterPeak;

  if (start < 0) {
    end += Math.abs(start);
    start = 0;
  }

  if (end > cleanValues.length) {
    start = Math.max(0, start - (end - cleanValues.length));
    end = cleanValues.length;
  }

  const beat = cleanValues.slice(start, end);

  if (beat.length < 8) {
    return downsampleSeries(cleanValues.slice(-desiredLength), PPG_MINI_GRAPH_POINTS);
  }

  return downsampleSeries(beat, PPG_MINI_GRAPH_POINTS);
}

function MiniPpgWaveform({ series, sampleRate = 220 }) {
  const width = 86;
  const height = 42;

  const oneBeat = getOnePpgBeat(series || [], sampleRate);

  const min = Math.min(...oneBeat);
  const max = Math.max(...oneBeat);
  const range = max - min || 1;

  const points = oneBeat
    .map((value, index) => {
      const x =
        oneBeat.length <= 1
          ? width
          : (index / (oneBeat.length - 1)) * width;

      const y = height - ((value - min) / range) * height;

      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const lastValue = oneBeat[oneBeat.length - 1];
  const lastX = width;
  const lastY = height - ((lastValue - min) / range) * height;

  return (
    <svg
      className="wave7-spo2-reference-graph"
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden="true"
    >
      <polyline points={points} />
      <circle cx={lastX} cy={lastY} r="2.6" />
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

const ppgSeries =
  Array.isArray(vitals.ppgTrace) && vitals.ppgTrace.length
    ? vitals.ppgTrace
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
         graph={
  <MiniPpgWaveform
    series={ppgSeries}
    sampleRate={waveFrame.sampleRate || 220}
  />
}
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
