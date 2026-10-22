# Prototype Instructions

## Approved product direction

- Selected concept: the second dark immersive landing page, centered Chinese headline, glass player and four-layout selector.
- The brand mark begins centered, then shrinks into the page header. Keep the product window demonstration at 700 × 380.
- Traffic-light controls appear only in settings, never in the player or island.
- Showcase Chace — The Rain Song, Alicks — Ibuprofen, and goodboy noah — Airport Bar (original upload, not the later remix). All covers remain upright, square, uncropped, without rotation, skew or perspective. Only Chace and Alicks have lyric demonstrations; Airport Bar has no invented lyrics.
- This is a silent interaction demo, not music streaming. Retain truthful device/platform testing limits from the project README.
- Use a solid dark page background; only the music demonstration uses GluSpot's production Qt album-flow background, sampled at 60 fps into a silent 12-second H.264 loop. This is source sampling, not a measured browser or screen frame rate. Export native island appearance from the production Qt painter with the `solid_album` palette role; do not use `sea_level` or invent an island layout.
- Lyric animations use the production Qt lyric painter at 30 fps with alpha WebM and PNG/WebP stills. Their estimated demo timing comes from the user-provided screenshots and is not aligned to actual audio.
- Include island/window/fullscreen morphing and a timed, pausable chapter progress control. Pause automatic progression in the background, outside the viewport, and with reduced motion.
- Public copy says online music, with no platform names or logos. Downloads go directly to the GitHub Release.
- Show the requested round/cylindrical Wi-Fi speakers in the listening scene, while marketing copy describes compatible Wi-Fi audio devices.
- Preserve the simulated desktop as a blurred, dimmed website illustration. The listening room uses the all-off scene as its base plus a generated neutral-white playback LED and low wave layer. Preserve the four generated rainbow illumination plates as unused source assets; playback light and waves respond to the device demo.
- The silent Personal DJ preview lets playback/pause trigger a next-song speaking preview that automatically finishes after 6.5 seconds. Render five clean rings through the production `paint_personal_dj_icon` helper using the original cover palette; ready is `#16c777`. Preserve the animation and still exports with their provenance.
- Application and releases remain 6.417. The user explicitly authorized syncing website/ to main and deploying/binding gluspot.top on Cloudflare after verification.

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.
