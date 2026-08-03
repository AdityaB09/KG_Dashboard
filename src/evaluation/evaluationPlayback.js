export function createEvaluationPlayback({
  episode,
  onFrame,
  onStateChange,
  onComplete,
  intervalMs = 40,
  visibleSeconds = 6,
}) {
  if (!episode?.ecg) {
    throw new Error(
      "An adapted evaluation episode is required."
    );
  }

  const ecgRate =
    Number(episode.ecg.sampleRate) || 250;

  const ppgRate =
    Number(episode.ppg?.sampleRate) || 125;

  const ecgStep = Math.max(
    1,
    Math.round(
      (ecgRate * intervalMs) / 1000
    )
  );

  const ppgStep = Math.max(
    1,
    Math.round(
      (ppgRate * intervalMs) / 1000
    )
  );

  const totalEcgSamples = Math.round(
    ecgRate *
      Number(
        episode.ecg.durationSeconds || 8
      )
  );

  const maxVisibleEcgSamples = Math.round(
    ecgRate * visibleSeconds
  );

  const maxVisiblePpgSamples = Math.round(
    ppgRate * visibleSeconds
  );

  const leadIds = Array.isArray(
    episode.ecg.leadIds
  )
    ? episode.ecg.leadIds
    : Object.keys(
        episode.ecg.waveforms || {}
      );

  const ppgWaveform = Array.isArray(
    episode.ppg?.waveform
  )
    ? episode.ppg.waveform
    : [];

  function createInitialEcgWindows() {
    return Object.fromEntries(
      leadIds.map((leadId) => [
        leadId,
        Array(maxVisibleEcgSamples).fill(0),
      ])
    );
  }

  function createInitialPpgWindow() {
    return Array(
      maxVisiblePpgSamples
    ).fill(0);
  }

  let ecgPosition = 0;
  let ppgPosition = 0;
  let timer = null;
  let playbackSpeed = 1;

  let leadWindows =
    createInitialEcgWindows();

  let ppgWindow =
    createInitialPpgWindow();

  function notifyState(state) {
    onStateChange?.({
      state,
      playing: state === "playing",

      elapsedSeconds:
        ecgPosition / ecgRate,

      durationSeconds:
        totalEcgSamples / ecgRate,

      speed: playbackSpeed,
    });
  }

  function buildFrame() {
    const latestMv = Object.fromEntries(
      Object.entries(leadWindows).map(
        ([leadId, values]) => [
          leadId,
          values.length
            ? values[values.length - 1]
            : null,
        ]
      )
    );

    return {
      source: "cardinal-evaluation",
      status: "connected",
      sampleRate: ecgRate,
      batchSize: ecgStep,
      receivedAt: new Date().toISOString(),

      leadsMv: leadWindows,
      latestMv,

      xAxis: {
        secondsVisible: visibleSeconds,

        elapsedSeconds:
          ecgPosition / ecgRate,
      },

      vitals: {
        ...episode.vitals,
        ppgTrace: ppgWindow,
      },

      evaluation: {
        synthetic: true,
        episodeId: episode.episodeId,
      },
    };
  }

  function emitCurrentFrame() {
    onFrame?.(buildFrame());
  }

  function emitFrame() {
    const nextLeads = {};

    for (const leadId of leadIds) {
      const completeLead =
        episode.ecg.waveforms?.[leadId] ||
        [];

      const incoming = completeLead.slice(
        ecgPosition,
        ecgPosition + ecgStep
      );

      nextLeads[leadId] = [
        ...(leadWindows[leadId] || []),
        ...incoming,
      ].slice(-maxVisibleEcgSamples);
    }

    const incomingPpg = ppgWaveform.slice(
      ppgPosition,
      ppgPosition + ppgStep
    );

    ppgWindow = [
      ...ppgWindow,
      ...incomingPpg,
    ].slice(-maxVisiblePpgSamples);

    leadWindows = nextLeads;

    emitCurrentFrame();

    ecgPosition += ecgStep;
    ppgPosition += ppgStep;

    if (
      ecgPosition >= totalEcgSamples
    ) {
      pause();
      notifyState("complete");
      onComplete?.();
    }
  }

  function play() {
    if (timer) {
      return;
    }

    if (
      ecgPosition >= totalEcgSamples
    ) {
      restart({
        autoPlay: false,
      });
    }

    const delay = Math.max(
      10,
      intervalMs / playbackSpeed
    );

    timer = window.setInterval(
      emitFrame,
      delay
    );

    notifyState("playing");
  }

  function pause() {
    if (timer) {
      window.clearInterval(timer);
      timer = null;
    }

    notifyState("paused");
  }

  function restart({
    autoPlay = true,
  } = {}) {
    if (timer) {
      window.clearInterval(timer);
      timer = null;
    }

    leadWindows =
      createInitialEcgWindows();

    ppgWindow =
      createInitialPpgWindow();

    ecgPosition = 0;
    ppgPosition = 0;

    emitCurrentFrame();
    notifyState("ready");

    if (autoPlay) {
      play();
    }
  }

  function setSpeed(nextSpeed) {
    const numeric = Number(nextSpeed);

    if (
      !Number.isFinite(numeric) ||
      numeric <= 0
    ) {
      return;
    }

    const wasPlaying = Boolean(timer);

    if (timer) {
      window.clearInterval(timer);
      timer = null;
    }

    playbackSpeed = numeric;

    if (wasPlaying) {
      play();
    } else {
      notifyState("paused");
    }
  }

  function stop() {
    if (timer) {
      window.clearInterval(timer);
      timer = null;
    }

    ecgPosition = 0;
    ppgPosition = 0;

    notifyState("stopped");
  }

  return {
    play,
    pause,
    restart,
    stop,
    setSpeed,
  };
}