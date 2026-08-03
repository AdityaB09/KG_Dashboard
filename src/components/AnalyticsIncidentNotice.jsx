import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  createPortal,
} from "react-dom";
import "./SLMWidgetAdditions.css";
import "./SLMWidgetSnackbar.css";


function isPersistentNotice(
  notice
) {
  return String(
    notice?.mode ||
    ""
  ).startsWith(
    "evaluation"
  );
}


export default function AnalyticsIncidentNotice({
  notice,
  onOpen,
  onDismiss,
  durationMs = 6500,
}) {
  const [
    visible,
    setVisible,
  ] = useState(false);

  const dismissRef =
    useRef(onDismiss);

  useEffect(() => {
    dismissRef.current =
      onDismiss;
  }, [onDismiss]);

  const persistent =
    isPersistentNotice(
      notice
    );

  const noticeKey =
    useMemo(
      () =>
        [
          notice?.mode,
          notice?.status,
          notice?.incidentId,
          notice?.episodeId,
          notice?.runId,
          notice?.title,
        ].join("|"),
      [
        notice?.mode,
        notice?.status,
        notice?.incidentId,
        notice?.episodeId,
        notice?.runId,
        notice?.title,
      ]
    );

  useEffect(() => {
    if (!notice) {
      setVisible(false);
      return undefined;
    }

    setVisible(false);

    const showTimer =
      window.setTimeout(
        () =>
          setVisible(true),
        20
      );

    if (persistent) {
      console.info(
        "[KGEN NOTICE] persistent evaluation notice shown",
        {
          mode:
            notice.mode,
          episodeId:
            notice.episodeId,
          incidentId:
            notice.incidentId,
        }
      );

      return () => {
        window.clearTimeout(
          showTimer
        );
      };
    }

    const hideTimer =
      window.setTimeout(
        () =>
          setVisible(false),
        Math.max(
          500,
          durationMs - 240
        )
      );

    const dismissTimer =
      window.setTimeout(
        () =>
          dismissRef
            .current?.(),
        durationMs
      );

    return () => {
      window.clearTimeout(
        showTimer
      );
      window.clearTimeout(
        hideTimer
      );
      window.clearTimeout(
        dismissTimer
      );
    };
  }, [
    noticeKey,
    durationMs,
    notice,
    persistent,
  ]);

  if (
    !notice ||
    typeof document ===
      "undefined"
  ) {
    return null;
  }

  const closeSnackbar = () => {
    setVisible(false);

    window.setTimeout(
      () =>
        dismissRef
          .current?.(),
      220
    );
  };

  const openAnalytics = () => {
    setVisible(false);
    onOpen?.();
  };

  const isReady =
    notice.status ===
    "ready";

  return createPortal(
    <div
      className="kgen-snackbar-layer"
      aria-live="polite"
      aria-atomic="true"
    >
      <div
        className={[
          "kgen-analytics-snackbar",
          notice.status ||
            "captured",
          persistent
            ? "persistent"
            : "",
          visible
            ? "show"
            : "",
        ].join(" ")}
        role="status"
      >
        <div
          className="kgen-snackbar-icon"
          aria-hidden="true"
        >
          {isReady
            ? "✓"
            : "●"}
        </div>

        <div
          className="kgen-snackbar-copy"
        >
          <strong>
            {notice.title ||
              "New ECG incident available"}
          </strong>

          <span>
            {notice.message ||
              "Open Analytics to review the captured incident."}
          </span>
        </div>

        <div
          className="kgen-snackbar-actions"
        >
          <button
            type="button"
            className="kgen-snackbar-open"
            onClick={
              openAnalytics
            }
          >
            Open Analytics
          </button>

          <button
            type="button"
            className="kgen-snackbar-close"
            onClick={
              closeSnackbar
            }
            aria-label="Dismiss notification"
          >
            ×
          </button>
        </div>

        {!persistent && (
          <span
            className="kgen-snackbar-progress"
            style={{
              animationDuration:
                `${durationMs}ms`,
            }}
            aria-hidden="true"
          />
        )}
      </div>
    </div>,
    document.body
  );
}
