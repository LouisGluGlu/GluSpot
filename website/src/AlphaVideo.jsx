import { useEffect, useRef } from "react";
import { packedAlphaFor } from "./alphaSupport.js";

/**
 * A transparent video loop that also works in Safari.
 *
 * The exports are VP9 WebM with an alpha channel. Safari (and every iOS
 * browser, all WebKit) plays VP9 but drops WebM alpha, so the transparent
 * area turns black and the blurred lines show their raw colour as solid
 * blocks. `canPlayType` cannot tell: it answers "probably" for VP9 there too.
 *
 * Off Chromium we play `<name>-packed.mp4` instead: H.264 with the
 * premultiplied colour in the top half and the alpha as grey in the bottom
 * half, recombined in WebGL. The source video stays in the page, invisible,
 * so the caller's play/pause and visibility logic is unchanged; `video.ready`
 * turns on only after the first frame has been composited, so the still
 * image stays until then (and for good if WebGL is unavailable).
 *
 * `?alpha=packed` or `?alpha=webm` in the page URL forces a path for testing.
 */
export function AlphaVideo({ base, video, on }) {
  if (!usesPackedAlpha()) {
    return <video key={base} ref={video.ref} className={on ? "alpha on" : "alpha"} src={`${base}.webm`} muted loop playsInline preload="none" aria-hidden="true"
      onLoadedData={() => video.setReady(true)} onError={() => video.setReady(false)} />;
  }
  return <PackedAlpha key={base} base={base} video={video} on={on} />;
}

let packedChoice;
export function usesPackedAlpha() {
  if (packedChoice === undefined) packedChoice = typeof window !== "undefined" && packedAlphaFor(navigator.userAgent, window.location.search);
  return packedChoice;
}

const VERTEX = `
attribute vec2 p;
varying vec2 v;
void main() { v = vec2(p.x * 0.5 + 0.5, 0.5 - p.y * 0.5); gl_Position = vec4(p, 0.0, 1.0); }`;
// Colour comes premultiplied from the top half, alpha from the bottom half.
// The half-texel inset keeps bilinear sampling from crossing the seam.
const FRAGMENT = `
precision mediump float;
varying vec2 v;
uniform sampler2D t;
uniform float inset;
void main() {
  float y = clamp(v.y * 0.5, 0.0, 0.5 - inset);
  vec3 c = texture2D(t, vec2(v.x, y)).rgb;
  float a = texture2D(t, vec2(v.x, clamp(0.5 + v.y * 0.5, 0.5 + inset, 1.0))).r;
  gl_FragColor = vec4(min(c, vec3(a)), a);
}`;

function PackedAlpha({ base, video, on }) {
  const canvas = useRef(null);
  const setReady = video.setReady;
  useEffect(() => {
    const node = video.ref.current, surface = canvas.current;
    if (!node || !surface) return undefined;
    const gl = surface.getContext("webgl", { alpha: true, premultipliedAlpha: true, antialias: false, preserveDrawingBuffer: false });
    if (!gl) return undefined;
    const program = link(gl);
    if (!program) return undefined;
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    gl.useProgram(program);
    const where = gl.getAttribLocation(program, "p");
    gl.enableVertexAttribArray(where);
    gl.vertexAttribPointer(where, 2, gl.FLOAT, false, 0, 0);
    const inset = gl.getUniformLocation(program, "inset");
    const texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    for (const [key, value] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR], [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, key, value);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);

    let shown = false, pending = 0, alive = true;
    const draw = () => {
      if (!alive || node.readyState < 2 || !node.videoWidth) return;
      const width = node.videoWidth, height = Math.floor(node.videoHeight / 2);
      if (surface.width !== width || surface.height !== height) { surface.width = width; surface.height = height; }
      gl.viewport(0, 0, width, height);
      try { gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, node); }
      catch { return; }
      gl.uniform1f(inset, 0.5 / node.videoHeight);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      if (!shown) { shown = true; setReady(true); }
    };
    const frameCallback = typeof node.requestVideoFrameCallback === "function";
    const loop = () => {
      draw();
      if (!alive || node.paused || node.ended) { pending = 0; return; }
      pending = frameCallback ? node.requestVideoFrameCallback(loop) : requestAnimationFrame(loop);
    };
    const start = () => { if (!pending) loop(); };
    const stop = () => {
      if (!pending) return;
      if (frameCallback) node.cancelVideoFrameCallback(pending); else cancelAnimationFrame(pending);
      pending = 0;
    };
    const lost = event => { event.preventDefault(); shown = false; setReady(false); };
    node.addEventListener("play", start);
    node.addEventListener("pause", stop);
    node.addEventListener("loadeddata", draw);
    node.addEventListener("seeked", draw);
    surface.addEventListener("webglcontextlost", lost);
    if (!node.paused) start(); else draw();
    return () => {
      alive = false;
      stop();
      node.removeEventListener("play", start);
      node.removeEventListener("pause", stop);
      node.removeEventListener("loadeddata", draw);
      node.removeEventListener("seeked", draw);
      surface.removeEventListener("webglcontextlost", lost);
      gl.deleteTexture(texture);
      gl.deleteBuffer(buffer);
      gl.deleteProgram(program);
    };
  }, [base, video.ref, setReady]);
  return <>
    <video ref={video.ref} className="alpha-source" src={`${base}-packed.mp4`} muted loop playsInline preload="none" aria-hidden="true"
      onError={() => setReady(false)} />
    <canvas ref={canvas} className={on ? "alpha on" : "alpha"} aria-hidden="true" />
  </>;
}

function link(gl) {
  const shader = (type, source) => {
    const item = gl.createShader(type);
    gl.shaderSource(item, source);
    gl.compileShader(item);
    return gl.getShaderParameter(item, gl.COMPILE_STATUS) ? item : null;
  };
  const vertex = shader(gl.VERTEX_SHADER, VERTEX), fragment = shader(gl.FRAGMENT_SHADER, FRAGMENT);
  if (!vertex || !fragment) return null;
  const program = gl.createProgram();
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  return gl.getProgramParameter(program, gl.LINK_STATUS) ? program : null;
}
