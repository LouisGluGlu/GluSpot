# Experimental Windows 7 owned-playback runtime

This separate build retains the current pinned NetEase, QQ and Kugou adapters
and the existing stdio bridge/provider boundary. It uses the unmodified official
Node 13.14.0 Windows x64 release, the last Node line whose documented platform
list includes Windows 7. It does not patch Windows or relax TLS verification.
Node 13 is end of life; this is an explicitly selected legacy package, not the
modern runtime or a claim of equal upstream support.

Run on the modern Windows build host after preparing `build/online-music`:

```powershell
packaging/windows/legacy-online/prepare-runtime.ps1
```

The separate output is `build/online-music-win7`; an existing nonempty output is
preserved and must be moved aside before another build. `-NodeArchive` accepts
an already downloaded archive only when its pinned official SHA-256 matches.
`-SourceRuntime`, `-OutputDirectory`, `-BuildNode` and `-NpmPath` select build
locations/tools without changing the normal runtime.

The build copies the pinned modules and lowers CommonJS JavaScript syntax with
esbuild 0.24.2. Upstream ESM entrypoints are retained as source evidence outside
the verified stdio path; their execution on Node 13 is not supported by this build.
Standards-based core-js 3.45.1 and abort-controller 3.0.0 provide the
needed language/abort features; @peculiar/webcrypto 1.5.0 uses Node's native
cryptography. A narrowly scoped bootstrap supplies Node builtin `node:` aliases,
UUIDv4, RFC 5869 HKDF and the file-removal API used by the pinned QQ modules.
The original adapter `package.json` support declarations remain intact. Their
Node support floors are not overridden or represented as upstream support.

`SOURCE.json` records the source lock, bridge hash, transform/tool versions,
per-file before/after hashes and remaining limits. Original package licenses
remain in `node_modules`, adapter notices remain in `licenses`, and the official
legacy Node license replaces the modern Node license. Build tools and modern
esbuild executables are excluded from the result.

`verify-runtime.cjs` runs the actual legacy interpreter against real pinned
adapter modules. It verifies RFC 5869 output, QQ AES/session encryption and
integrity, QR generation, injected stream-request contracts, local HTTP
redirects/cookies/cancellation, and all three real stdio preload paths. Tests
use isolated temporary state and loopback HTTP only. No account or receiver is
contacted. These fixtures do not establish real network login, music playback
or execution on a Windows 7 device; a legacy build remains experimental until
those tests are completed there.
