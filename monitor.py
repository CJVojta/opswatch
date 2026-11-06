"""HTTP probe and bounded scheduler."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(url, timeout):
    start = time.monotonic()
    try:
        request = Request(url, headers={"User-Agent": "OpsWatch/1.0"})
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            code = response.status
            ok, detail = 200 <= code < 300, f"HTTP {code}"
    except HTTPError as error:
        code = error.code
        ok, detail = False, f"HTTP {code}"
        error.close()
    except (URLError, OSError, ValueError) as error:
        code = None
        ok, detail = False, str(getattr(error, "reason", error))[:180]
    return ok, code, round((time.monotonic() - start) * 1000), detail


class Scheduler:
    def __init__(self, store, workers=4, check=probe):
        self.store = store
        self.workers = workers
        self.check = check
        self.stop = Event()
        self.thread = Thread(target=self.run, name="monitor-scheduler")

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()

    def run(self):
        pending = {}
        prune_at = 0
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            while not self.stop.is_set():
                try:
                    for monitor_id, future in list(pending.items()):
                        if future.done():
                            del pending[monitor_id]
                            try:
                                future.result()
                            except Exception:
                                logging.exception("Monitor %s could not save its check", monitor_id)
                    now = int(time.time())
                    if now >= prune_at:
                        self.store.prune(now)
                        prune_at = now + 3600
                    for monitor in self.store.due(now):
                        if len(pending) >= self.workers:
                            break
                        if monitor["id"] not in pending:
                            pending[monitor["id"]] = executor.submit(self.run_check, monitor)
                except Exception:
                    logging.exception("Scheduler tick failed")
                self.stop.wait(1)

    def run_check(self, monitor):
        result = self.check(monitor["url"], monitor["timeout"])
        self.store.record(monitor["id"], int(time.time()), *result)
