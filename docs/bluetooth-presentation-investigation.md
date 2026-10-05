# Bluetooth early-playback investigation

The user reported that Bluetooth plays significantly early while the wired
output and HomePod remain aligned in 6.411. Work is isolated on
`codex/bluetooth-presentation-fix`, starting from `0742070`. The audible
problem is not yet reproduced or fixed by this investigation.

## Evidence

The running app is the delivered 6.411 executable. Its native helper matches
the verified 6.411 build hash
`bbd8419e7dd2c0cb9466a2446b9c542a6bd3687c14c099be79607a51facc0fac`.
M6 is correctly identified as Bluetooth. During read-only inspection there
was no active cast helper and the music application's sessions were inactive.
That snapshot cannot establish whether direct music leaked during the reported
failure. Existing controller migration requires the verified music sessions to
reach the spare output, and excludes every selected playback endpoint from
source candidates.

Two separate seven-second silence-only probes exercised the existing
production renderer against M6 and Realtek. They connected no AirPlay receiver,
captured no input, saved no PCM, and performed no route, default, volume or mute
operations. Both reported valid endpoint clocks and zero meaningful samples.
M6's final write queue was about 39.8 ms with 20 ms padding; Realtek's was about
40.1 ms with the same padding. Their reported maximum stream latency was zero.
These measurements describe silence at the Windows endpoint, not Bluetooth
acoustic alignment during real music.

The preceding 6.409 music run reported about 239.5 ms M6 queue with 20 ms
padding and zero maximum stream latency. The user confirmed basic alignment
in that historical run. Its difference from today's silent probe does not
identify an acoustic correction and is not reused as a fixed offset.

## Clock contract and next verification

The scheduler derives the first newly written frame's presentation time from
the stream-relative position divided by its paired frequency, the pre-write
frame count, and the correlated 100 ns QPC timestamp. The queue delay is reduced
by the timestamp age. Adding maximum stream latency to a valid device-clock
estimate would count a pipeline again. Invalid or initial observations currently
fall back to padding plus reported maximum latency; the transition between
those estimates also needs live evidence.

The existing calculation is exposed to offline tests without changing its
production policy. Tests can now cover frequency units, QPC age, driver queue
versus padding, initial zero position, invalid observations and fallback
transitions instead of testing only a precomputed desired cursor.

A diagnostic exported while the problem is audible is still required. In the
6.411 onedir app it is written beneath `_internal/logs/`. Relevant evidence is
the selected members, source process/output, complete named `local_renders`
and current timing. A playback listening check remains necessary after a
specific correction; neither a renderer phase near zero nor the silent probe
proves speakers are acoustically synchronized.

The final native rebuild passed all 11 CTest suites in 47.57 seconds, including
the new direct estimator cases in the existing music-audio suite. Whitespace
checking passed. No Python code, scheduling policy or packaged executable was
changed, and no acoustic synchronization pass is claimed.

Reference interfaces:

- [IAudioClock::GetPosition](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/nf-audioclient-iaudioclock-getposition)
- [IAudioClient::GetStreamLatency](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/nf-audioclient-iaudioclient-getstreamlatency)
- [IAudioClock2::GetDevicePosition](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/nf-audioclient-iaudioclock2-getdeviceposition)

Raw Clock2 hardware frames can use a different sampling rate from the client.
They cannot be subtracted directly from 44.1 kHz submitted frames and do not
establish the remote Bluetooth speaker's acoustic position.
