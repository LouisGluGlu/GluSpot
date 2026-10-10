# Output panel and surface motion

This scoped change follows the user's four UI references on the existing V2 music surfaces. It does not change the test-build version or rebuild the packaged executable.

## Device chooser

The chooser grows from the circular output control and returns to it on dismissal. Its bounded spring retains the displayed position and velocity when opening/closing is interrupted. Parent hide or a presentation-mode change dismisses immediately so a detached chooser cannot remain visible.

Confirmed devices sort first. A selected device with a known volume uses the entire capsule as a volume bar: a white filled part and a gray remainder, with contrasting glyph/text on each part. A selected device without volume evidence retains a gray capsule. An unselected device has no capsule or visible checkbox. Selection still follows provider confirmation; a click never fabricates a connected state.

Wheel input anywhere inside the chooser is consumed. It never reaches the lyric page, including at either list boundary. Boundary feedback is bounded by the list's available scroll range. Volume wheel input requires continuous pointer residence for one second inside the selected capsule's white filled part. The gray remainder, unknown/zero-volume rows and all other areas scroll the list. Leaving the white hit area, moving to another device or scrolling the list resets the dwell.

The redundant header and scope sentence are removed from the visible panel. Capture scope remains in device tooltips and accessibility descriptions. F5 refreshes devices, Ctrl+N opens the existing manual AirPlay-IP dialog, and Esc dismisses. A group remains explicitly stoppable; connection failures remain visible.

## Music surfaces

Windowed and fullscreen companion/translation text uses half its former font size. Island lyric typography is unchanged. Long title/artist fields wait 30 seconds before the existing smooth round trip. Hover starts scrolling immediately; leaving returns continuously to the starting edge and begins a fresh 30-second wait. Changing songs resets the metadata identity independently for both fields.

The native window mask and painted frame use the album artwork's logical corner radius. Fullscreen has no rounded outer mask. Transition snapshots and live mode glyphs preserve continuous shape changes when the target mode is changed during an animation. The shared glyphs identify fullscreen, window and island. Hovering the island album artwork springs it larger, darkens its face and reveals the destination glyph while preserving the original hit target and lyric layout.

The island accessory notice uses a centered bold name, a larger device glyph and a thicker battery ring. Reliable battery retains the live lyrics; missing battery shows the name. Five-second restoration and ink selected from the rendered background are preserved.

## Multiple Bluetooth outputs

The eight-member music group already supports multiple active Windows/Bluetooth endpoints without AirPlay. It still requires a separate available, unoccupied capture endpoint and verified migration of the music application to that endpoint. Windows policy readback alone is not evidence that a live stream moved. Microsoft's [stream-routing documentation](https://learn.microsoft.com/en-us/windows/win32/coreaudio/stream-routing-implementation-considerations) describes the application's participation in switching its active streams.

Migration timeout now retains bounded scalar diagnostics: the attempted endpoint, observed endpoints/PIDs and whether playback stayed on the original output, disappeared or changed process/output. No PCM or recording is retained. A source that stayed on its original endpoint can try another safe spare, up to three candidates, only after both owned policies and actual playback have been verified restored. Cancellation, changed identity, occupation and restoration failure stop retrying. The sender starts only after every source session is verified at the chosen endpoint. Failure gives a Chinese recovery hint instead of the generic English exception.

This improves setup/recovery but cannot make a third-party player that pins an output rebuild its stream. Such a player must use its system-default output and resume/recreate playback. Endpoint mute also mutes our renderer; session mute was previously measured to suppress process-loopback capture. Neither is a safe substitute for the separate source endpoint.

## Evidence

Tests cover native wheel delivery through rows/sliders/viewport, dwell/reset and list-edge feedback; interrupted chooser motion; native corner masks and dirty rendering; presentation retargeting; cover hover; metadata continuity and translation scale; island contrast/content restoration; strict migration diagnostics and candidate restoration. Generated native previews are kept in ignored `logs/`. Physical reproduction of the reported Bluetooth migration failure and acoustic alignment require the failed device/player combination to be connected again; they are not inferred from unit tests.

Final verification: all **3276** Python tests passed in the isolated branch and all **3247** passed after the scoped patch was integrated into `E:/gpt-island`. Native sender code and the existing packaged executable were not changed. The isolated branch is `codex/output-panel-refinement`; all generated GIF/PNG previews remain in ignored `logs/`.
