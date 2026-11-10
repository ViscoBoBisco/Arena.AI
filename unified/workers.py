"""Supervised isolated execution for capability tools.

Capability handlers execute in POSIX fork workers with OS CPU/address-space
limits. The parent retains ledger ownership, measures each worker's peak RSS,
and can terminate it on cancellation or wall-clock deadline. A killed mutating
or billable operation remains outcome-unknown and is sent through recovery.
"""
from __future__ import annotations

import math
import multiprocessing
import os
import resource
import sys
import time
from dataclasses import dataclass

from .contracts import ToolReceipt


@dataclass
class WorkerDispatch:
    receipt: ToolReceipt | None
    cpu_seconds: float
    ambiguous: bool = False
    peak_rss_bytes: int = 0
    memory_limit_bytes: int = 0

    def __post_init__(self):
        if self.receipt is not None:
            self.receipt.usage["cpu_seconds"] = max(0.0, float(self.cpu_seconds))
            self.receipt.usage["peak_rss_bytes"] = max(0, int(self.peak_rss_bytes))
            self.receipt.usage["memory_limit_bytes"] = max(0, int(self.memory_limit_bytes))


def _cpu_seconds_for_pid(pid: int) -> float:
    """Read a worker's CPU time from Linux procfs."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as stream:
            stat = stream.read()
        fields = stat[stat.rfind(")") + 2:].split()
        ticks = int(fields[11]) + int(fields[12])
        return ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return 0.0


def _peak_rss_bytes() -> int:
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak * 1024 if sys.platform.startswith("linux") else peak


def _peak_rss_for_pid(pid: int) -> int:
    """Read VmHWM (peak resident set) for a live Linux worker."""
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as stream:
            for line in stream:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _limit_receipt(args, budget: str, detail: str,
                   attempt_id: str | None = None) -> ToolReceipt:
    task_id, step_id, tool = args[:3]
    return ToolReceipt(task_id, step_id, attempt_id or "", "failed", "error.budget.v1", detail,
                       errors=[{"type": "budget_exceeded", "budget": budget,
                                "tool": tool}])


def _dispatch(receipt, cpu_seconds, memory_limit_bytes, *,
              ambiguous=False, peak_rss_bytes=0) -> WorkerDispatch:
    return WorkerDispatch(receipt, cpu_seconds, ambiguous,
                          peak_rss_bytes, memory_limit_bytes)


def _worker_entry(send_conn, broker, call_args, call_kwargs,
                  cpu_seconds: float, memory_mb: int) -> None:
    """Child entrypoint; the parent owns all durable task/attempt state."""
    try:
        memory_bytes = max(1, int(memory_mb)) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        cpu_limit = max(1, int(math.ceil(cpu_seconds)))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
        started = time.process_time()
        receipt = broker.call(*call_args, **call_kwargs)
        used_cpu = max(0.0, time.process_time() - started)
        peak_rss = _peak_rss_bytes()
        send_conn.send(("receipt", receipt, used_cpu, peak_rss))
    except MemoryError:
        used_cpu = max(0.0, time.process_time() - locals().get("started", time.process_time()))
        send_conn.send(("limit", "memory_mb", used_cpu, _peak_rss_bytes()))
    except BaseException as exc:
        used_cpu = max(0.0, time.process_time() - locals().get("started", time.process_time()))
        try:
            send_conn.send(("exception", type(exc).__name__, str(exc), used_cpu,
                            _peak_rss_bytes()))
        except Exception:
            pass
    finally:
        send_conn.close()


def dispatch_isolated(broker, call_args: tuple, call_kwargs: dict, *,
                      deadline_at: float, cancel_check, cpu_seconds: float,
                      memory_mb: int, mutating: bool,
                      on_dispatch=None) -> WorkerDispatch:
    """Run a broker call with killable deadline, CPU, and address-space limits.

    ``memory_limit_bytes`` reports the enforced RLIMIT_AS ceiling, while
    ``peak_rss_bytes`` is a measured resident-set high-water mark. They are
    intentionally separate: virtual address space is not RSS.
    """
    memory_limit_bytes = max(1, int(memory_mb)) * 1024 * 1024
    if cpu_seconds <= 0:
        receipt = _limit_receipt(call_args, "cpu_seconds", "task CPU budget exceeded",
                                 call_kwargs.get("attempt_id"))
        return _dispatch(receipt, 0, memory_limit_bytes)
    if memory_mb <= 0:
        receipt = _limit_receipt(call_args, "memory_mb", "task memory budget exceeded",
                                 call_kwargs.get("attempt_id"))
        return _dispatch(receipt, 0, 0)
    try:
        ctx = multiprocessing.get_context("fork")
    except ValueError as exc:  # pragma: no cover - non-POSIX qualification guard
        raise RuntimeError("isolated tool workers require a fork-capable platform") from exc

    receive, send = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_worker_entry,
                          args=(send, broker, call_args, call_kwargs,
                                cpu_seconds, memory_mb), daemon=True)
    if on_dispatch:
        on_dispatch()
    process.start()
    send.close()
    message = None
    cpu_seen = 0.0
    rss_seen = 0
    stop_reason = None
    try:
        while process.is_alive():
            cpu_seen = max(cpu_seen, _cpu_seconds_for_pid(process.pid))
            rss_seen = max(rss_seen, _peak_rss_for_pid(process.pid))
            if cancel_check():
                stop_reason = "cancelled"
                break
            if time.time() >= deadline_at:
                stop_reason = "time_seconds"
                break
            if cpu_seen >= cpu_seconds:
                stop_reason = "cpu_seconds"
                break
            if receive.poll(0.02):
                try:
                    message = receive.recv()
                except EOFError:
                    message = None
                break
        if stop_reason:
            process.terminate()
            process.join(timeout=0.25)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
        else:
            process.join(timeout=0.5)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            if message is None and receive.poll():
                try:
                    message = receive.recv()
                except EOFError:
                    pass
    finally:
        receive.close()
        if process.is_alive():
            process.kill()
            process.join(timeout=1)

    if stop_reason:
        if mutating:
            return _dispatch(None, cpu_seen, memory_limit_bytes,
                             ambiguous=True, peak_rss_bytes=rss_seen)
        if stop_reason == "cancelled":
            task_id, step_id, _tool = call_args[:3]
            receipt = ToolReceipt(task_id, step_id, call_kwargs.get("attempt_id", ""),
                                  "cancelled", "cancelled.v1", "worker cancelled")
        else:
            receipt = _limit_receipt(call_args, stop_reason,
                                     f"{stop_reason} budget exceeded",
                                     call_kwargs.get("attempt_id"))
        return _dispatch(receipt, cpu_seen, memory_limit_bytes,
                         peak_rss_bytes=rss_seen)

    if message and message[0] == "receipt":
        receipt, used_cpu, used_rss = message[1], float(message[2]), int(message[3])
        measured_cpu = max(cpu_seen, used_cpu)
        measured_rss = max(rss_seen, used_rss)
        if measured_cpu >= cpu_seconds:
            if mutating:
                return _dispatch(None, measured_cpu, memory_limit_bytes,
                                 ambiguous=True, peak_rss_bytes=measured_rss)
            receipt = _limit_receipt(call_args, "cpu_seconds", "task CPU budget exceeded",
                                     call_kwargs.get("attempt_id"))
            return _dispatch(receipt, measured_cpu, memory_limit_bytes,
                             peak_rss_bytes=measured_rss)
        if mutating and any(
                error.get("type") == "budget_exceeded"
                and error.get("budget") != "model_tokens"
                for error in receipt.errors if isinstance(error, dict)):
            return _dispatch(None, measured_cpu, memory_limit_bytes,
                             ambiguous=True, peak_rss_bytes=measured_rss)
        return _dispatch(receipt, measured_cpu, memory_limit_bytes,
                         peak_rss_bytes=measured_rss)

    if message and message[0] == "limit":
        budget, used_cpu, used_rss = message[1], float(message[2]), int(message[3])
        measured_cpu, measured_rss = max(cpu_seen, used_cpu), max(rss_seen, used_rss)
        if mutating:
            return _dispatch(None, measured_cpu, memory_limit_bytes,
                             ambiguous=True, peak_rss_bytes=measured_rss)
        receipt = _limit_receipt(call_args, budget, f"{budget} budget exceeded",
                                 call_kwargs.get("attempt_id"))
        return _dispatch(receipt, measured_cpu, memory_limit_bytes,
                         peak_rss_bytes=measured_rss)

    if message and message[0] == "exception":
        _kind, _, used_cpu, used_rss = (
            message[1], message[2], float(message[3]), int(message[4]))
        measured_cpu, measured_rss = max(cpu_seen, used_cpu), max(rss_seen, used_rss)
        if mutating:
            return _dispatch(None, measured_cpu, memory_limit_bytes,
                             ambiguous=True, peak_rss_bytes=measured_rss)
        receipt = _limit_receipt(call_args, "worker", f"isolated worker failed: {_kind}",
                                 call_kwargs.get("attempt_id"))
        return _dispatch(receipt, measured_cpu, memory_limit_bytes,
                         peak_rss_bytes=measured_rss)

    if mutating:
        return _dispatch(None, cpu_seen, memory_limit_bytes,
                         ambiguous=True, peak_rss_bytes=rss_seen)
    task_id, step_id, tool = call_args[:3]
    receipt = ToolReceipt(
        task_id, step_id, call_kwargs.get("attempt_id", ""), "failed",
        "error.worker.v1", f"worker exited without a receipt for {tool}",
        errors=[{"type": "worker_terminated", "exitcode": process.exitcode}])
    return _dispatch(receipt, cpu_seen, memory_limit_bytes, peak_rss_bytes=rss_seen)
