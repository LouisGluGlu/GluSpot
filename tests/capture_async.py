"""Deadlines for asynchronous capture tests and their lifecycle hooks."""
import asyncio
import inspect
import unittest


async def bounded(coro, timeout=10):
    return await asyncio.wait_for(coro, timeout)


def run_async(coro, timeout=10):
    return asyncio.run(bounded(coro, timeout))


class CaptureAsyncTestCase(unittest.IsolatedAsyncioTestCase):
    def _callAsync(self, func, /, *args, **kwargs):
        return self._asyncioRunner.run(bounded(func(*args, **kwargs)),
                                       context=self._asyncioTestContext)

    def _callMaybeAsync(self, func, /, *args, **kwargs):
        if inspect.iscoroutinefunction(func):
            return self._callAsync(func, *args, **kwargs)
        return super()._callMaybeAsync(func, *args, **kwargs)
