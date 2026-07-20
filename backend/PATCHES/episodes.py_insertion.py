# EXACT LOCATION:
# backend/app/episodes.py
#
# Inside async def finalize_capture(...), place this block
# immediately AFTER the existing self.publish({... "type":
# "episode.captured" ...}) call.
#
# Do not place it before metadata.json, waveforms.npz,
# clinical_context.json, and analysis.json are written.

try:
    from app.phase7.orchestrator import (
        phase7_orchestrator,
    )

    phase7_orchestrator.schedule_captured_episode(
        episode_id=capture.episode_id,
        incident_id=metadata.get(
            "incidentId"
        ),
    )

except Exception as phase7_error:
    # Scheduling failure must never break
    # waveform capture or persistence.
    print(
        "[KGEN PHASE7 SCHEDULE ERROR]",
        type(phase7_error).__name__,
        str(phase7_error),
    )
