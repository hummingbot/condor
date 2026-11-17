"""ST-04: a handler hot reload must not orphan running continuous routines.

``main.py`` reloads every module under ``handlers/`` with ``importlib.reload``
when a file changes. The Telegram task registry was a module-level ``{}``, so
the reload rebound it to an empty dict: ``_stop_instance`` then found no task,
removed the instance and reported success while the routine kept running.
"""

import asyncio
import importlib

import handlers.routines as tg_routines


class _JobQueue:
    def get_jobs_by_name(self, name):
        return []


class _Context:
    def __init__(self, instances):
        self.user_data = {"routine_instances": instances}
        self.job_queue = _JobQueue()
        self.application = None


def test_reload_keeps_the_task_registry_and_stop_still_cancels(monkeypatch):
    async def scenario():
        task = asyncio.create_task(asyncio.sleep(3600))
        tg_routines._continuous_tasks["keepme"] = task
        try:
            reloaded = importlib.reload(tg_routines)
            assert reloaded._continuous_tasks.get("keepme") is task

            monkeypatch.setattr(reloaded, "get_routine", lambda name: None)
            context = _Context({"keepme": {"routine_name": "watcher"}})
            assert reloaded._stop_instance(context, 1, "keepme") is True
            await asyncio.sleep(0)
            assert task.cancelled()
        finally:
            tg_routines._continuous_tasks.pop("keepme", None)
            task.cancel()

    asyncio.run(scenario())
