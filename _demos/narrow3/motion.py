"""Retargetable opacity transitions for the narrow study controls."""


class Fade:
    def __init__(self, value=0.0):
        self.start = self.target = value
        self.started = 0.0
        self.duration = 0.0
        # Bumped on every real retarget. The narrow surface pins its fade
        # background and prepared veil to one generation, so a reversal
        # starts a new frozen basis instead of reusing the previous one.
        self.generation = 0

    def value(self, now):
        if not self.duration:
            return self.target
        t = max(0.0, min(1.0, (now - self.started) / self.duration))
        return self.start + (self.target - self.start) * t

    def retarget(self, target, now, duration):
        if target != self.target:
            self.start = self.value(now)
            self.target = target
            self.started = now
            self.duration = duration
            self.generation += 1

    def settled(self, now):
        return now >= self.started + self.duration


class EaseOutFade(Fade):
    """Retargetable cubic ease-out for opacity and utility-row position."""
    def value(self, now):
        if not self.duration:
            return self.target
        t = max(0.0, min(1.0, (now - self.started) / self.duration))
        eased = 1.0 - (1.0 - t) ** 3
        return self.start + (self.target - self.start) * eased


class Visibility:
    DURATION = .320
    IDLE_DELAY = 1.200

    def __init__(self, enabled):
        self.enabled = enabled
        # All chrome and the veil sample this same curve, including reversals.
        self.opacity = EaseOutFade()
        self.footer = self.opacity
        self.top = [self.opacity] * 4
        self.top_enter = None
        self.idle_since = None
        self.progress = 0.0

    def advance(self, now, top_hover, bottom_hover, playlist):
        previous_target = self.opacity.target
        active = self.enabled and (top_hover or bottom_hover)
        if active:
            if self.top_enter is None:
                self.top_enter = now
            self.idle_since = None
            self.opacity.retarget(1.0, now, self.DURATION)
        elif not self.enabled:
            self.top_enter = None
            self.idle_since = None
            self.opacity.retarget(0.0, now, self.DURATION)
        else:
            self.top_enter = None
            if self.idle_since is None:
                self.idle_since = now
            if now - self.idle_since >= self.IDLE_DELAY - 1e-9:
                self.opacity.retarget(0.0, now, self.DURATION)
        self.progress = (self.opacity.start if previous_target != self.opacity.target
                         else self.opacity.value(now))

    def settled(self, now):
        return self.opacity.settled(now)
