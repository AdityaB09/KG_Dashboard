import "./SLMWidgetAdditions.css";

function wrappedIndex(
  currentIndex,
  direction,
  count
) {
  if (count <= 0) return 0;

  return (
    currentIndex +
    direction +
    count
  ) % count;
}

export default function IncidentEpisodeCarouselControls({
  incidents = [],
  activeIncidentIndex = 0,
  onIncidentChange,
  episodes = [],
  activeEpisodeIndex = 0,
  onEpisodeChange,
}) {
  const activeIncident =
    incidents[activeIncidentIndex] || null;

  const incidentLabel =
    activeIncident?.display ||
    activeIncident?.title ||
    activeIncident?.category ||
    "Captured incident";

  return (
    <div
      className="kgen-incident-episode-controls"
      aria-label="Incident and episode navigation"
    >
      <div className="kgen-carousel-control-group">
        <button
          type="button"
          disabled={incidents.length <= 1}
          onClick={() =>
            onIncidentChange?.(
              wrappedIndex(
                activeIncidentIndex,
                -1,
                incidents.length
              )
            )
          }
          aria-label="Previous incident"
        >
          ‹
        </button>

        <div className="kgen-carousel-control-copy">
          <strong>
            Incident{" "}
            {incidents.length
              ? activeIncidentIndex + 1
              : 0}
            {" of "}
            {incidents.length}
          </strong>

          <small title={incidentLabel}>
            {incidentLabel}
          </small>
        </div>

        <button
          type="button"
          disabled={incidents.length <= 1}
          onClick={() =>
            onIncidentChange?.(
              wrappedIndex(
                activeIncidentIndex,
                1,
                incidents.length
              )
            )
          }
          aria-label="Next incident"
        >
          ›
        </button>
      </div>

      <span
        className="kgen-carousel-divider"
        aria-hidden="true"
      />

      <div className="kgen-carousel-control-group">
        <button
          type="button"
          disabled={episodes.length <= 1}
          onClick={() =>
            onEpisodeChange?.(
              wrappedIndex(
                activeEpisodeIndex,
                -1,
                episodes.length
              )
            )
          }
          aria-label="Previous episode"
        >
          ‹
        </button>

        <div className="kgen-carousel-control-copy">
          <strong>
            Episode{" "}
            {episodes.length
              ? activeEpisodeIndex + 1
              : 0}
            {" of "}
            {episodes.length}
          </strong>

          <div className="kgen-carousel-dots">
            {episodes
              .slice(0, 8)
              .map((episode, index) => (
                <button
                  key={
                    episode.id ||
                    episode.episodeId ||
                    index
                  }
                  type="button"
                  className={
                    index ===
                    activeEpisodeIndex
                      ? "active"
                      : ""
                  }
                  onClick={() =>
                    onEpisodeChange?.(index)
                  }
                  aria-label={`Open episode ${
                    index + 1
                  }`}
                />
              ))}

            {episodes.length > 8 && (
              <span>
                +{episodes.length - 8}
              </span>
            )}
          </div>
        </div>

        <button
          type="button"
          disabled={episodes.length <= 1}
          onClick={() =>
            onEpisodeChange?.(
              wrappedIndex(
                activeEpisodeIndex,
                1,
                episodes.length
              )
            )
          }
          aria-label="Next episode"
        >
          ›
        </button>
      </div>
    </div>
  );
}
