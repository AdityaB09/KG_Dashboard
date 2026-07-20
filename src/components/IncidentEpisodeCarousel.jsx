import { useRef } from "react";
import IncidentEpisodeCarouselControls
  from "./IncidentEpisodeCarouselControls";
import "./SLMWidgetAdditions.css";

const SWIPE_THRESHOLD_PX = 45;

export default function IncidentEpisodeCarousel({
  incidents = [],
  activeIncidentIndex = 0,
  onIncidentChange,
  episodes = [],
  activeEpisodeIndex = 0,
  onEpisodeChange,
  children,
}) {
  const touchStartXRef = useRef(null);

  const moveEpisode = (direction) => {
    if (episodes.length <= 1) return;

    const next = (
      activeEpisodeIndex +
      direction +
      episodes.length
    ) % episodes.length;

    onEpisodeChange?.(next);
  };

  return (
    <div
      className="kgen-episode-carousel"
      onTouchStart={(event) => {
        touchStartXRef.current =
          event.changedTouches?.[0]?.clientX ??
          null;
      }}
      onTouchEnd={(event) => {
        const startX =
          touchStartXRef.current;

        const endX =
          event.changedTouches?.[0]?.clientX;

        touchStartXRef.current = null;

        if (
          startX === null ||
          endX === undefined
        ) {
          return;
        }

        const distance =
          endX - startX;

        if (
          Math.abs(distance) <
          SWIPE_THRESHOLD_PX
        ) {
          return;
        }

        moveEpisode(
          distance < 0 ? 1 : -1
        );
      }}
    >
      <IncidentEpisodeCarouselControls
        incidents={incidents}
        activeIncidentIndex={
          activeIncidentIndex
        }
        onIncidentChange={
          onIncidentChange
        }
        episodes={episodes}
        activeEpisodeIndex={
          activeEpisodeIndex
        }
        onEpisodeChange={
          onEpisodeChange
        }
      />

      {children}
    </div>
  );
}
