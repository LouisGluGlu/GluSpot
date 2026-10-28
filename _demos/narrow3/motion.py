"""Retargetable opacity transitions for the narrow study controls."""


class Fade:
    def __init__(self, value=0.0):
        self.start = self.target = value
        self.started = 0.0
        self.duration = 0.0

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

    def settled(self, now):
        return now >= self.started + self.duration


class EaseOutFade(Fade):
    """Retargetable ease-out interpolation for the utility row's position."""
    def value(self, now):
        if not self.duration:
            return self.target
        t = max(0.0, min(1.0, (now - self.started) / self.duration))
        eased = 1.0 - (1.0 - t) ** 3
        return self.start + (self.target - self.start) * eased


class Visibility:
    def __init__(self, enabled):
        self.enabled = enabled
        self.top = [Fade() for _ in range(4)]
        self.footer = Fade()
        self.top_enter = None

    def advance(self, now, top_hover, bottom_hover, playlist):
        active = self.enabled and (top_hover or bottom_hover)
        if active and self.top_enter is None:
            self.top_enter = now
        if not active:
            self.top_enter = None
        self.footer.retarget(float(active), now, .16 if active else .18)
        for index, fade in enumerate(self.top):
            show = active and now - self.top_enter >= .14 + index * .02
            fade.retarget(float(show), now, .16 if show else .18)

    def settled(self, now):
        return self.footer.settled(now) and all(fade.settled(now) for fade in self.top)
