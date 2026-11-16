# Apple Music companion

Windows 11 host for the Apple Music page. It is not built by `build-exe.bat`.

Build on a Windows machine that has the .NET Framework 4.8 targeting pack and the WebView2 Evergreen runtime:

```
dotnet build native/apple_music_companion/GluSpotAppleMusicCompanion.csproj -c Release
```

The program reads one JSON line from stdin and writes one JSON line to stdout. Login stays inside the WebView profile under `%LOCALAPPDATA%\GluSpot\apple-music-profile`. That folder is not part of the git tree.

The development path is fixed at `bin/Release/net48/GluSpotAppleMusicCompanion.exe` under this directory. A packaged build looks only beside `GluSpot.exe`. See `docs/apple-music-companion.md`.

The project targets x64 and sets Prefer32Bit to false so the process matches WebView2Loader. Startup keeps the window on a monitor until WebView2 is ready. If CreateAsync or EnsureCoreWebView2Async fails, the process writes the exception type and message to `%LOCALAPPDATA%\GluSpot\apple-music-companion.log` and exits. Playback may move that window off-screen only after startup succeeded.

On Windows, `accept.ps1` is UTF-8 with a BOM and prints ASCII only. It checks for the WebView2 runtime, builds this project, and starts the executable. A failure prints `FAIL step=` and `reason=`:

```
powershell -NoProfile -ExecutionPolicy Bypass -File native/apple_music_companion/accept.ps1
```
