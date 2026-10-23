export const REPO = "https://github.com/LouisGluGlu/GluSpot";
export const RELEASES_API = "https://api.github.com/repos/LouisGluGlu/GluSpot/releases?per_page=100";
// If GitHub is unavailable, show the release list instead of pinning an old build.
export const RELEASE_FALLBACK = { version: null, url: `${REPO}/releases` };

function compareVersions(left, right) {
  const a = left.split(".").map(Number), b = right.split(".").map(Number);
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    const difference = (a[i] ?? 0) - (b[i] ?? 0);
    if (difference) return difference;
  }
  return 0;
}

export function selectLatestRelease(releases) {
  let latest = RELEASE_FALLBACK;
  if (!Array.isArray(releases)) return latest;
  for (const release of releases) {
    const version = /^v(\d+(?:\.\d+){1,3})$/.exec(release?.tag_name)?.[1];
    if (!version || release.draft || !release.published_at || !Array.isArray(release.assets)) continue;
    const uploaded = release.assets.filter(asset => asset?.state === "uploaded" && asset.size > 0);
    // Published prereleases are intentional downloads too. Skip releases whose
    // Windows installer/portable uploads are still incomplete (or source only).
    if (!uploaded.some(asset => /^GluSpot-.*-Setup-x64\.exe$/i.test(asset.name)) ||
        !uploaded.some(asset => /^GluSpot-.*-windows-x64\.zip$/i.test(asset.name))) continue;
    if (!latest.version || compareVersions(version, latest.version) > 0) {
      latest = { version, url: `${REPO}/releases/tag/${release.tag_name}` };
    }
  }
  return latest;
}

export async function loadLatestRelease({ signal, fetchImpl = fetch } = {}) {
  try {
    const response = await fetchImpl(RELEASES_API, {
      headers: { Accept: "application/vnd.github+json" },
      cache: "no-cache",
      signal,
    });
    if (!response.ok) return RELEASE_FALLBACK;
    return selectLatestRelease(await response.json());
  } catch {
    return RELEASE_FALLBACK;
  }
}
