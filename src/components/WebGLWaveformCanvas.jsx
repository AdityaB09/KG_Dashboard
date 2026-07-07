import { useEffect, useMemo, useRef, useState } from "react";

const COLOR_MAP = {
  red: [187 / 255, 30 / 255, 34 / 255, 1],
  blue: [77 / 255, 146 / 255, 168 / 255, 1],
  yellow: [245 / 255, 158 / 255, 11 / 255, 1],
  green: [52 / 255, 211 / 255, 153 / 255, 1],
  cyan: [125 / 255, 211 / 255, 252 / 255, 1],
  white: [226 / 255, 232 / 255, 240 / 255, 1],
};

function getColor(colorName = "cyan") {
  return COLOR_MAP[colorName] || COLOR_MAP.cyan;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function mapToClipY({
  value,
  mode,
  min,
  max,
  cssHeight,
  pxPerMm,
  voltageScaleMmPerMv,
  centerMv,
}) {
  if (!Number.isFinite(value)) return 0;

 if (mode === "millivolts") {
  const halfHeightPx = Math.max(1, cssHeight / 2);
  const deltaMv = value - centerMv;
  const deltaPx = deltaMv * voltageScaleMmPerMv * pxPerMm;

  return clamp(deltaPx / halfHeightPx, -0.985, 0.985);
}

  if (mode === "unit") {
    return Math.max(-1, Math.min(1, value * 2 - 1));
  }

  if (mode === "auto") {
    const range = max - min || 1;
    return Math.max(-1, Math.min(1, ((value - min) / range) * 2 - 1));
  }

  return Math.max(-1, Math.min(1, value));
}

function interpolateArrayValue(values, outputIndex, outputLength) {
  if (!values?.length) return 0;
  if (values.length === 1 || outputLength <= 1) return values[0];

  const sourcePosition = (outputIndex / (outputLength - 1)) * (values.length - 1);
  const leftIndex = Math.floor(sourcePosition);
  const rightIndex = Math.min(values.length - 1, leftIndex + 1);
  const t = sourcePosition - leftIndex;

  const left = Number(values[leftIndex]) || 0;
  const right = Number(values[rightIndex]) || left;

  return left + (right - left) * t;
}

function createShader(gl, type, source) {
  const shader = gl.createShader(type);
  if (!shader) throw new Error("Unable to create WebGL shader.");

  gl.shaderSource(shader, source);
  gl.compileShader(shader);

  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const message = gl.getShaderInfoLog(shader);
    gl.deleteShader(shader);
    throw new Error(message || "WebGL shader compile failed.");
  }

  return shader;
}

function createProgram(gl) {
  const vertexShader = createShader(
    gl,
    gl.VERTEX_SHADER,
    `
      attribute vec2 a_position;

      void main() {
        gl_Position = vec4(a_position, 0.0, 1.0);
      }
    `
  );

  const fragmentShader = createShader(
    gl,
    gl.FRAGMENT_SHADER,
    `
      precision mediump float;
      uniform vec4 u_color;

      void main() {
        gl_FragColor = u_color;
      }
    `
  );

  const program = gl.createProgram();
  if (!program) throw new Error("Unable to create WebGL program.");

  gl.attachShader(program, vertexShader);
  gl.attachShader(program, fragmentShader);
  gl.linkProgram(program);

  gl.deleteShader(vertexShader);
  gl.deleteShader(fragmentShader);

  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const message = gl.getProgramInfoLog(program);
    gl.deleteProgram(program);
    throw new Error(message || "WebGL program link failed.");
  }

  return program;
}

export default function WebGLWaveformCanvas({
  values,
  samples,
  points = 720,
  color = "cyan",
  mode = "bipolar",
  className = "",
  pxPerMm = 4,
  voltageScaleMmPerMv = 10,
  centerMv = 0,
}) {
  const canvasRef = useRef(null);
  const glRef = useRef(null);
  const programRef = useRef(null);
  const positionBufferRef = useRef(null);
  const animationRef = useRef(null);

  const colorLocationRef = useRef(null);
  const cssHeightRef = useRef(1);

  const bufferRef = useRef(new Float32Array(points));
  const verticesRef = useRef(new Float32Array(points * 2));
  const writeIndexRef = useRef(0);

  const propsRef = useRef({
    mode,
    color,
    pxPerMm,
    voltageScaleMmPerMv,
    centerMv,
    min: -1,
    max: 1,
  });

  const [webglSupported, setWebglSupported] = useState(true);

  const valueRange = useMemo(() => {
    if (!values?.length) return { min: -1, max: 1 };

    return {
      min: Math.min(...values),
      max: Math.max(...values),
    };
  }, [values]);

  useEffect(() => {
    propsRef.current = {
      mode,
      color,
      pxPerMm,
      voltageScaleMmPerMv,
      centerMv,
      min: valueRange.min,
      max: valueRange.max,
    };
  }, [mode, color, pxPerMm, voltageScaleMmPerMv, centerMv, valueRange]);

  useEffect(() => {
    bufferRef.current = new Float32Array(points);
    verticesRef.current = new Float32Array(points * 2);
    writeIndexRef.current = 0;
  }, [points]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;

    try {
      const gl = canvas.getContext("webgl", {
        alpha: true,
        antialias: false,
        depth: false,
        stencil: false,
        preserveDrawingBuffer: false,
      });

      if (!gl) {
        setWebglSupported(false);
        return undefined;
      }

      const program = createProgram(gl);
      const positionLocation = gl.getAttribLocation(program, "a_position");
      const colorLocation = gl.getUniformLocation(program, "u_color");
      const positionBuffer = gl.createBuffer();

      if (!positionBuffer || positionLocation < 0 || !colorLocation) {
        throw new Error("Unable to initialize WebGL waveform buffer.");
      }

      glRef.current = gl;
      programRef.current = program;
      positionBufferRef.current = positionBuffer;
      colorLocationRef.current = colorLocation;

      gl.useProgram(program);
      gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, verticesRef.current.byteLength, gl.DYNAMIC_DRAW);
      gl.enableVertexAttribArray(positionLocation);
      gl.vertexAttribPointer(positionLocation, 2, gl.FLOAT, false, 0, 0);

      function resizeCanvas() {
        const dpr = window.devicePixelRatio || 1;
        const rect = canvas.getBoundingClientRect();

        cssHeightRef.current = Math.max(1, rect.height);

        canvas.width = Math.max(1, Math.floor(rect.width * dpr));
        canvas.height = Math.max(1, Math.floor(rect.height * dpr));

        gl.viewport(0, 0, canvas.width, canvas.height);
      }

      resizeCanvas();

      const resizeObserver = new ResizeObserver(resizeCanvas);
      resizeObserver.observe(canvas);

      function draw() {
        const activeGl = glRef.current;
        const activeProgram = programRef.current;
        const activeBuffer = positionBufferRef.current;
        const activeColorLocation = colorLocationRef.current;

        if (!activeGl || !activeProgram || !activeBuffer || !activeColorLocation) {
          return;
        }

        const signalBuffer = bufferRef.current;
        const vertices = verticesRef.current;
        const writeIndex = writeIndexRef.current;
        const currentProps = propsRef.current;

        for (let i = 0; i < points; i += 1) {
          const x = points <= 1 ? 0 : -1 + (2 * i) / (points - 1);
          const sourceIndex = (writeIndex + i) % points;
          const rawValue = signalBuffer[sourceIndex];

          const y = mapToClipY({
            value: rawValue,
            mode: currentProps.mode,
            min: currentProps.min,
            max: currentProps.max,
            cssHeight: cssHeightRef.current,
            pxPerMm: currentProps.pxPerMm,
            voltageScaleMmPerMv: currentProps.voltageScaleMmPerMv,
            centerMv: currentProps.centerMv,
          });

          vertices[i * 2] = x;
          vertices[i * 2 + 1] = y;
        }

        activeGl.useProgram(activeProgram);
        activeGl.bindBuffer(activeGl.ARRAY_BUFFER, activeBuffer);
        activeGl.bufferSubData(activeGl.ARRAY_BUFFER, 0, vertices);

        activeGl.clearColor(0, 0, 0, 0);
        activeGl.clear(activeGl.COLOR_BUFFER_BIT);

        activeGl.uniform4fv(activeColorLocation, getColor(currentProps.color));
        activeGl.drawArrays(activeGl.LINE_STRIP, 0, points);

        animationRef.current = requestAnimationFrame(draw);
      }

      animationRef.current = requestAnimationFrame(draw);

      return () => {
        if (animationRef.current) cancelAnimationFrame(animationRef.current);
        resizeObserver.disconnect();

        gl.deleteBuffer(positionBuffer);
        gl.deleteProgram(program);

        glRef.current = null;
        programRef.current = null;
        positionBufferRef.current = null;
      };
    } catch (error) {
      console.error("[KGEN WEBGL INIT ERROR]", error);
      setWebglSupported(false);
      return undefined;
    }
  }, [points]);

  useEffect(() => {
    if (!values?.length) return;

    const signalBuffer = bufferRef.current;

    for (let i = 0; i < signalBuffer.length; i += 1) {
      signalBuffer[i] = interpolateArrayValue(values, i, signalBuffer.length);
    }

    writeIndexRef.current = 0;
  }, [values]);

  useEffect(() => {
    if (!samples?.length) return;

    const signalBuffer = bufferRef.current;

    for (const sample of samples) {
  signalBuffer[writeIndexRef.current] = Number(sample) || 0;
  writeIndexRef.current = (writeIndexRef.current + 1) % signalBuffer.length;
}
  }, [samples]);

  if (!webglSupported) {
    return <div className={`webgl-waveform-fallback ${className}`}>WebGL unavailable</div>;
  }

  return <canvas ref={canvasRef} className={`webgl-waveform-canvas ${className}`} />;
}