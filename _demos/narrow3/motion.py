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


class Visibility:
    def __init__(self, footer_always):
        self.top = [Fade() for _ in range(4)]
        self.footer = Fade(float(footer_always))
        self.top_enter = None
        self.top_leave = None
        self.bottom_leave = None
        self.footer_always = footer_always

    def advance(self, now, top_hover, bottom_hover, playlist):
        if top_hover:
            if self.top_enter is None:
                self.top_enter = now
            self.top_leave = None
        else:
            if self.top_enter is not None:
                self.top_leave = now
            self.top_enter = None
        if bottom_hover:
            self.bottom_leave = None
        elif self.bottom_leave is None:
            self.bottom_leave = now
        top_requested = top_hover or (self.top_leave is not None and now - self.top_leave < .9)
        footer_requested = self.footer_always or bottom_hover or (self.bottom_leave is not None and now - self.bottom_leave < .9 and self.footer.target > 0)
        # In list mode the footer must be gone before any secondary button appears.
        if playlist and (top_requested or any(fade.value(now) > .001 for fade in self.top)):
            footer_requested = False
        self.footer.retarget(float(footer_requested), now, .16 if footer_requested else .18)
        footer_clear = self.footer.value(now) <= .001
        for index, fade in enumerate(self.top):
            ready = self.top_enter is not None and now - self.top_enter >= .14 + index * .02
            show = top_requested and (ready or fade.target > 0) and (not playlist or footer_clear)
            fade.retarget(float(show), now, .16 if show else .18)

    def settled(self, now):
        return self.footer.settled(now) and all(fade.settled(now) for fade in self.top)
