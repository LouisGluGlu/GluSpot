# Liquid Glass source evidence

Public implementation and license review on October 11, 2026. All links below
pin the inspected revision. Reference code was read, not executed or copied;
demo images, music, fonts and video were not downloaded. These findings establish
specific techniques and limitations, not a production-maturity guarantee.

| Reference | Evidence and relevance | Limits and license |
| --- | --- | --- |
| [rdev/liquid-glass-react](https://github.com/rdev/liquid-glass-react/tree/ac48eab18d1f7f444ae30002d240cae29c863a21) | [Filter and component](https://github.com/rdev/liquid-glass-react/blob/ac48eab18d1f7f444ae30002d240cae29c863a21/src/index.tsx#L51) use SVG channel displacement over a blurred backdrop and keep the content above the optical layer. [CPU map generator](https://github.com/rdev/liquid-glass-react/blob/ac48eab18d1f7f444ae30002d240cae29c863a21/src/shader-utils.ts#L75) computes and encodes a geometry-derived displacement map. | Version 1.1.1. No test directory in the inspected complete tree; repository metadata's latest push was June 13, 2025. README documents absent displacement in Safari/Firefox and stability limits for shader mode. Its saturation boosts are unsuitable for GluSpot's high-chroma correction. [MIT](https://github.com/rdev/liquid-glass-react/blob/ac48eab18d1f7f444ae30002d240cae29c863a21/LICENSE). |
| [VII-Cae/hyalite--liquid-glass](https://github.com/VII-Cae/hyalite--liquid-glass/tree/b9f27192a7256bf5295bf027b582b86864f9b59d) | [Implementation](https://github.com/VII-Cae/hyalite--liquid-glass/blob/b9f27192a7256bf5295bf027b582b86864f9b59d/hyalite.js#L168) bounds map size/rebuild frequency, buckets geometry, separates map/filter reuse and limits retained idle maps. [Filter](https://github.com/VII-Cae/hyalite--liquid-glass/blob/b9f27192a7256bf5295bf027b582b86864f9b59d/hyalite.js#L394) samples a real displaced backdrop. [Self-check runner](https://github.com/VII-Cae/hyalite--liquid-glass/blob/b9f27192a7256bf5295bf027b582b86864f9b59d/demo/run-cases.mjs) drives real Chromium; [cases](https://github.com/VII-Cae/hyalite--liquid-glass/blob/b9f27192a7256bf5295bf027b582b86864f9b59d/demo/cases.html) inspect map/filter/resize results. This is useful evidence for bounded native material and verification. | Version 0.5.0. Runner inspected but not run. Real browser displacement is Chromium-specific. Its strong folded-lens defaults should not be copied into small music controls. [MIT](https://github.com/VII-Cae/hyalite--liquid-glass/blob/b9f27192a7256bf5295bf027b582b86864f9b59d/LICENSE). |
| [iyinchao/liquid-glass-studio](https://github.com/iyinchao/liquid-glass-studio/tree/f7b28c36305a862f5cffed3ddd51511cf1204f56) | [Shader sampling](https://github.com/iyinchao/liquid-glass-studio/blob/f7b28c36305a862f5cffed3ddd51511cf1204f56/src/shaders/fragment-main.glsl#L136) offsets RGB texture channels; [final composition](https://github.com/iyinchao/liquid-glass-studio/blob/f7b28c36305a862f5cffed3ddd51511cf1204f56/src/shaders/fragment-main.glsl#L467) combines distance-field geometry, refraction, tint, Fresnel/glare and antialiasing. | Shader laboratory/editor; private package 0.0.0, build/lint scripts without a test script or test directory. README still lists UI content inside a shape as pending. [MIT](https://github.com/iyinchao/liquid-glass-studio/blob/f7b28c36305a862f5cffed3ddd51511cf1204f56/LICENSE). |
| [naughtyduk/liquidGL](https://github.com/naughtyduk/liquidGL/tree/88f681ab7035fd55b04f63edff1841e32c4199e9) | [WebGL implementation](https://github.com/naughtyduk/liquidGL/blob/88f681ab7035fd55b04f63edff1841e32c4199e9/scripts/liquidGL.js#L2930) and [WGSL sampling](https://github.com/naughtyduk/liquidGL/blob/88f681ab7035fd55b04f63edff1841e32c4199e9/scripts/liquidGL.js#L3730) refract scene textures. A page rasterizer, backend fallback and explicit resource release support its rendering path. | The README excludes real-time CSS-animation refraction and requires maintained snapshots/registered dynamic elements. No conventional automated-test suite in the inspected tree. [Actual license](https://github.com/naughtyduk/liquidGL/blob/88f681ab7035fd55b04f63edff1841e32c4199e9/LICENSE) makes code in `scripts/` and `package/` MIT and expressly excludes demo assets; GitHub's `NOASSERTION` metadata alone would not establish this. |

`Kehua00/GlassUI` returned 404 for repository metadata, tree and README during this
review. No implementation claim is made for that name.

## Application to GluSpot

The original Qt material samples only an already prepared application image,
softens the interior and displaces the rounded rim with bounded raster/cache
cost. It does not capture the desktop, call `QWidget.grab`, recursively render
a widget or introduce a browser dependency. Icons, labels, control semantics
and hit geometry remain with their existing owners.

Where the native island paints its background directly without a reusable
raster, its music buttons use the transparent tint, directional highlight and
rim fallback. That path approximates the optical appearance; it does not claim
actual displaced background sampling. Retired notification, weather, time and
plugin painters are outside this change.

Device connection material follows the existing capsule geometry. The pale
circle blends continuously into a darker name capsule; caller opacity, device
glyph transforms, queue/clock state and clipping remain independent. The caller
may supply the capsule's corresponding source-pixel rectangle for real sampling.

Album-color darkening/softening is separate from glass rendering. Geometry or
material defaults that boost saturation or create strong field folding are not
used as evidence for an appropriate palette policy.

## Verification of the music integrations

The native Windows Qt command below passed all **885 tests** at
`QT_SCALE_FACTOR=1` (4.274 seconds) and again at `QT_SCALE_FACTOR=2`
(4.856 seconds), without skips:

```powershell
.venv\Scripts\python.exe -X utf8 -X faulthandler -m unittest tests.test_liquid_glass_music_surfaces tests.test_audio_output_motion tests.test_island_stack_visual_messages tests.test_island_background_system tests.test_island_presentation
```

The new integration checks cover theme-appropriate sharp glyphs, hover/press
material, retained hit rectangles/media clock, exclusion of obsolete notice
backdrops, enclosing opacity, live sampled connection backgrounds and unchanged
pose/clipping. Existing music-control/ripple, OS-theme, presentation and device
connection regressions remain in the command. Compilation and scoped whitespace
checks passed.

Two synthetic native previews at DPR 1/2 were generated and visually inspected
with dark, light and album-tinted backgrounds, three island interaction states
and three circle-to-caption positions. These checks used artificial images and
device models; they neither access an account nor play audio or exercise a
physical device. Full-application and shared-helper validation is recorded in
the parent change's readiness document.
