/**
 * Whether a browser needs the packed-alpha MP4 instead of the alpha WebM.
 *
 * Chromium renders WebM alpha. Safari plays VP9 but drops its alpha, and on
 * iOS every browser is WebKit whatever its name, so everything that is not
 * desktop or Android Chromium gets the packed MP4. `?alpha=packed` or
 * `?alpha=webm` forces a path for testing.
 */
export function packedAlphaFor(userAgent = "", search = "") {
  const forced = new URLSearchParams(search).get("alpha");
  if (forced === "packed" || forced === "webm") return forced === "packed";
  const ios = /\b(iPhone|iPad|iPod)\b/.test(userAgent) || /\b(CriOS|EdgiOS|FxiOS|OPiOS)\//.test(userAgent);
  const chromium = /\b(Chrome|Chromium|Edg|OPR)\//.test(userAgent);
  return ios || !chromium;
}
