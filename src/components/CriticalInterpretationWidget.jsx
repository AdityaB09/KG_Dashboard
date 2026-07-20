import {
  useEffect,
  useMemo,
  useRef,
} from "react";
import "./SLMWidgetAdditions.css";

const PRIMARY_METRIC_KEYS = [
  "episodeCount",
  "durationSeconds",
  "referenceVCount",
  "medianHeartRateBpm",
];

function MetricValue({ metric }) {
  return (
    <>
      {metric.value}
      {metric.unit
        ? ` ${metric.unit}`
        : ""}
    </>
  );
}

function MetricCard({ metric }) {
  return (
    <div className="kgen-slm-metric-card">
      <span>{metric.label}</span>

      <strong>
        <MetricValue metric={metric} />
      </strong>
    </div>
  );
}

export default function CriticalInterpretationWidget({
  result,
  status = "loading",
  fallback,
}) {
  const scrollRef = useRef(null);

  const widget =
    result?.widgetInterpretation || null;

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [
    result?.incidentId,
    result?.status,
    result?.modelState?.modelAlias,
  ]);

  const {
    primaryMetrics,
    additionalMetrics,
  } = useMemo(() => {
    const metrics =
      widget?.keyMetrics || [];

    const primary = [];
    const additional = [];

    metrics.forEach((metric) => {
      if (
        PRIMARY_METRIC_KEYS.includes(
          metric.key
        )
      ) {
        primary.push(metric);
      } else {
        additional.push(metric);
      }
    });

    return {
      primaryMetrics:
        primary.slice(0, 4),
      additionalMetrics:
        additional,
    };
  }, [widget?.keyMetrics]);

  if (!widget) {
    return (
      <div className="kgen-slm-widget pending">
        <h3>
          {status === "error"
            ? "Interpretation unavailable"
            : fallback?.title ||
              "Preparing interpretation"}
        </h3>

        <p>
          {status === "error"
            ? "The deterministic episode remains available. The narrative service could not be loaded."
            : fallback?.rhythm ||
              "Deterministic analysis and the validated narrative are being prepared."}
        </p>
      </div>
    );
  }

  const current =
    widget.currentSituation || {};

  return (
    <div
      className={`kgen-slm-widget ${
        widget.severity || "warning"
      }`}
    >
      <div className="kgen-slm-widget-heading">
        <div>
          <span>
            {widget.statusLabel ||
              "Clinical review required"}
          </span>

          <h3>{widget.headline}</h3>
        </div>

        <small>
          {result?.modelState?.available
            ? `Validated narrative • ${
                result.modelState
                  .modelAlias ||
                "configured model"
              }`
            : "Deterministic summary"}
        </small>
      </div>

      <div
        ref={scrollRef}
        className="kgen-slm-widget-scroll"
      >
        <div className="kgen-slm-summary">
          <p>
            <b>Episode:</b>{" "}
            {widget.episodeNarrative}
          </p>

          <p>
            <b>ECG evidence:</b>{" "}
            {widget.arrhythmiaNarrative}
          </p>

          <p>
            <b>Morphology:</b>{" "}
            {widget.morphologyNarrative}
          </p>

          <p>
            <b>Current situation:</b>{" "}
            {current.narrative}
          </p>

          <p>
            <b>Root-cause assessment:</b>{" "}
            {widget.rootCauseNarrative}
          </p>
        </div>

        {!!primaryMetrics.length && (
          <div className="kgen-slm-metrics">
            {primaryMetrics.map(
              (metric) => (
                <MetricCard
                  key={metric.key}
                  metric={metric}
                />
              )
            )}
          </div>
        )}

        {!!additionalMetrics.length && (
          <details className="kgen-slm-details">
            <summary>
              Additional ECG measurements
            </summary>

            <div className="kgen-slm-metrics compact">
              {additionalMetrics.map(
                (metric) => (
                  <MetricCard
                    key={metric.key}
                    metric={metric}
                  />
                )
              )}
            </div>
          </details>
        )}

        {!!widget.possibleContributors?.length && (
          <details className="kgen-slm-details">
            <summary>
              Possible contributors for review
            </summary>

            {widget.possibleContributors.map(
              (item) => (
                <article key={item.title}>
                  <strong>{item.title}</strong>

                  <span>
                    {item.confidenceLabel}
                    {" confidence • "}
                    {item.temporalFit}
                  </span>

                  {item.evidenceAgainst?.map(
                    (text) => (
                      <p key={text}>
                        Weakening evidence:{" "}
                        {text}
                      </p>
                    )
                  )}
                </article>
              )
            )}
          </details>
        )}

        {!!widget.importantLimitations?.length && (
          <details className="kgen-slm-details">
            <summary>
              Important limitations
            </summary>

            <ul>
              {widget.importantLimitations.map(
                (item) => (
                  <li key={item}>
                    {item}
                  </li>
                )
              )}
            </ul>
          </details>
        )}

        <p className="kgen-slm-disclaimer">
          {widget.displayDisclaimer}
        </p>
      </div>
    </div>
  );
}
