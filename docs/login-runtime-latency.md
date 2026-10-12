# Login runtime latency

This October 12, 2026 change keeps the existing provider, protected credentials,
QR key/create contracts and asynchronous Qt task boundary. It addresses the
reported delay between choosing a platform and seeing its QR code.

## Local causes and changes

The old Python request lock and Node sequence serialized every platform's
account, QR, lyric, playlist and DJ request through one child. Waiting for the
Python lock was outside the request timeout. A slow catalog operation therefore
delayed QR acquisition even when the login service itself was responsive.

The catalog transport remains sequential. Authentication has at most three
separate, lazy children, one for each supported platform. Begin, poll, cancel,
account validation and logout for one platform use its same child, preserving
the existing isolated-attempt state and timeout exit behavior. A stalled
authentication child does not stop a healthy catalog child. Closing the backend
wakes pending requests and reaps every child off-thread.

Only a genuinely visible login source page requests background module
preparation. Its internal `prepare_login` operation loads exactly the pinned
modules used by that platform's QR path. It does not call a network or QR
function, create an account service, request a QR key, read a protected record,
or save credentials. General service construction and source availability
checks do not start prepared children. Deliberate selection still requests the
actual QR, so background preparation does not consume QR validity time.

An account already verified by the current service can return its existing card
without another live login-status request. Unverified startup records still
require live validation. Canceled queued source, account, begin, poll and
preparation actions check the existing generation before running, while late
results retain their established generation fences.

## Measured evidence

These are single observations on the development machine, not service-latency
guarantees. Real anonymous QR probes used a separate backend, did not scan or
confirm a QR, and never read or save the user's protected account files. Logs
contain only fixed source names, elapsed times, success flags and memory counts;
no keys, QR content, credentials or signed URLs are included.

| Scenario | Before | After |
| --- | ---: | ---: |
| Login requested 100 ms into a synthetic 900 ms lyric request, using actual Node child transport | 797 ms; catalog had to finish first | 62 ms; catalog remained active and completed normally |

| Platform | Real cold QR | Real next QR in the same child | First real QR after offline module preparation |
| --- | ---: | ---: | ---: |
| NetEase | 1235 ms | 171 ms | 141 ms |
| Kugou | 532 ms | 62 ms | 281 ms |
| QQ | 813 ms | 468 ms | 484 ms |

Separate account-free module import probes measured approximately 902 ms for
NetEase's verified QR imports, 241 ms for Kugou and 108 ms for QQ. Parallel
offline preparation of all three reached ready in 1281 ms. Clicking before
preparation completes can still wait for imports; remote DNS, TLS and API delays
remain outside the application's control.

Prepared-child working sets were approximately 124.1 MiB for NetEase, 64.5 MiB
for Kugou and 56.8 MiB for QQ, about 245.3 MiB total on this machine. Preparation
is confined to the login page rather than ordinary playback/startup. This is a
real memory tradeoff for avoiding first-click module loading.
An unused prepared child expires after 60 seconds on a daemon timer and is
closed off the GUI thread. Any real QR, account or logout request claims its
lane under the existing registration lock before waiting for network I/O, so
the timer cannot retire a live session or an already queued real request.
Retirement checks the exact registered lane identity and cannot close a later
replacement; application close also cancels its remaining preparation timers.

Actual-child regressions cover catalog/login independence, independent platform
requests, same-lane QR continuity, timeout isolation, concurrent shutdown,
sanitized errors, inherited-secret filtering, closed-backend fences and
credential-free/network-free preparation. Pure service tests cover optional
backend support, unchanged account generations and absent storage activity.
The final 100 focused Python backend/service/credential/DJ tests passed in
7.822 seconds, including real prepared-child reaping, used-lane preservation
and stale-retirement/close races. Native controller and full-suite results are
reported with the integrated branch verification in README.

## Limits

An already running request for the same authentication platform retains its
original deadline; queued UI requests can be discarded, but this change does
not claim to interrupt every upstream operation safely. Network outages,
authentication expiry, QR confirmation and sustained real-account behavior are
separate from these anonymous QR timing observations. Version 6.412 and the
existing packaged executable are unchanged.
