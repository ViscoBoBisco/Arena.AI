"""Supervised isolated execution for capability tools.

Tool adapters run in forked workers with OS address-space/CPU limits. The
parent retains ledger ownership and can terminate a worker on cancellation or
wall-clock deadline. A killed mutating tool has an unknown outcome and is left
for the normal ledger reconciliation path; it is never converted to a retryable
failure receipt.
"""
from __future__ import annotations

import math
import multiprocessing
import os
import resource
import time
from dataclasses import dataclass

from .contracts import ToolReceipt


@dataclass
class WorkerDispatch:
    receipt: ToolReceipt | None
    cpu_seconds: float
    ambiguous: bool = False

    def __post_init__(self):
        if self.receipt is not None:
            self.receipt.usage["cpu_seconds"] = max(
                0.0, float(self.cpu_seconds))


def _cpu_seconds_for_pid(pid: int) -> float:
    """Read this process' CPU time from procfs, or zero when unavailable."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as stream:
            stat = stream.read()
        fields = stat[stat.rfind(")") + 2:].split()
        ticks = int(fields[11]) + int(fields[12])  # utime + stime, fields 14/15
        return ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return 0.0


def _limit_receipt(args, budget: str, detail: str,
                   attempt_id: str | None = None) -> ToolReceipt:
    task_id, step_id, tool = args[:3]
    return ToolReceipt(task_id, step_id, attempt_id or "", "failed", "error.budget.v1", detail,
                       errors=[{"type": "budget_exceeded", "budget": budget,
                                "tool": tool}])


def _worker_entry(send_conn, broker, call_args, call_kwargs,
                  cpu_seconds: int, memory_mb: int) -> None:
    """Child entrypoint; the parent owns all durable task/attempt state."""
    try:
        memory_bytes = max(1, int(memory_mb)) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        cpu_limit = max(1, int(math.ceil(cpu_seconds)))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
        started = time.process_time()
        receipt = broker.call(*call_args, **call_kwargs)
        used_cpu = max(0.0, time.process_time() - started)
        receipt.usage["cpu_seconds"] = used_cpu
        send_conn.send(("receipt", receipt, used_cpu))
    except MemoryError:
        used_cpu = max(0.0, time.process_time() - locals().get("started", time.process_time()))
        send_conn.send(("limit", "memory_mb", used_cpu))
    except BaseException as exc:
        # A Python-level exception is known to have crossed the tool boundary;
        # mutating tools are still treated as ambiguous by the parent if no
        # normalized receipt can be returned.
        used_cpu = max(0.0, time.process_time() - locals().get("started", time.process_time()))
        try:
            send_conn.send(("exception", type(exc).__name__, str(exc), used_cpu))
        except Exception:
            pass
    finally:
        send_conn.close()


def dispatch_isolated(broker, call_args: tuple, call_kwargs: dict, *,
                      deadline_at: float, cancel_check, cpu_seconds: float,
                      memory_mb: int, mutating: bool) -> WorkerDispatch:
    """Run a broker call under killable time, CPU, and address-space limits."""
    if cpu_seconds <= 0:
        return WorkerDispatch(_limit_receipt(call_args, "cpu_seconds",
                                             "task CPU budget exceeded",
                                             call_kwargs.get("attempt_id")), 0.0)
    if memory_mb <= 0:
        return WorkerDispatch(_limit_receipt(call_args, "memory_mb",
                                             "task memory budget exceeded",
                                             call_kwargs.get("attempt_id")), 0.0)
    try:
        ctx = multiprocessing.get_context("fork")
    except ValueError as exc:  # pragma: no cover - non-POSIX qualification guard
        raise RuntimeError("isolated tool workers require a fork-capable platform") from exc

    receive, send = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_worker_entry,
                          args=(send, broker, call_args, call_kwargs,
                                cpu_seconds, memory_mb), daemon=True)
    process.start()
    send.close()
    message = None
    cpu_seen = 0.0
    stop_reason = None
    try:
        while process.is_alive():
            cpu_seen = max(cpu_seen, _cpu_seconds_for_pid(process.pid))
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
            cpu_seen = max(cpu_seen, _cpu_seconds_for_pid(process.pid))
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
            return WorkerDispatch(None, cpu_seen, ambiguous=True)
        if stop_reason == "cancelled":
            task_id, step_id, tool = call_args[:3]
            return WorkerDispatch(ToolReceipt(
                task_id, step_id, call_kwargs.get("attempt_id", ""),
                "cancelled", "cancelled.v1", "worker cancelled"), cpu_seen)
        return WorkerDispatch(_limit_receipt(call_args, stop_reason,
                                             f"{stop_reason} budget exceeded",
                                             call_kwargs.get("attempt_id")), cpu_seen)

    if message and message[0] == "receipt":
        receipt, used_cpu = message[1], float(message[2])
        measured_cpu = max(cpu_seen, used_cpu)
        if measured_cpu >= cpu_seconds:
            if mutating:
                return WorkerDispatch(None, measured_cpu, ambiguous=True)
            return WorkerDispatch(_limit_receipt(
                call_args, "cpu_seconds", "task CPU budget exceeded",
                call_kwargs.get("attempt_id")), measured_cpu)
        if mutating and any(
                error.get("type") == "budget_exceeded"
                and error.get("budget") != "model_tokens"
                for error in receipt.errors if isinstance(error, dict)):
            return WorkerDispatch(None, measured_cpu, ambiguous=True)
        return WorkerDispatch(receipt, measured_cpu)
    if message and message[0] == "limit":
        budget, used_cpu = message[1], float(message[2])
        if mutating:
            return WorkerDispatch(None, max(cpu_seen, used_cpu), ambiguous=True)
        return WorkerDispatch(_limit_receipt(call_args, budget,
                                             f"{budget} budget exceeded",
                                             call_kwargs.get("attempt_id")),
                              max(cpu_seen, used_cpu))
    if message and message[0] == "exception":
        _kind, _detail, used_cpu = message[1], message[2], float(message[3])
        if mutating:
            return WorkerDispatch(None, max(cpu_seen, used_cpu), ambiguous=True)
        return WorkerDispatch(_limit_receipt(call_args, "worker",
                                             f"isolated worker failed: {_kind}",
                                             call_kwargs.get("attempt_id")),
                              max(cpu_seen, used_cpu))

    if mutating:
        return WorkerDispatch(None, cpu_seen, ambiguous=True)
    task_id, step_id, tool = call_args[:3]
    return WorkerDispatch(ToolReceipt(
        task_id, step_id, call_kwargs.get("attempt_id", ""), "failed",
        "error.worker.v1", f"worker exited without a receipt for {tool}",
        errors=[{"type": "worker_terminated", "exitcode": process.exitcode}]), cpu_seen)
