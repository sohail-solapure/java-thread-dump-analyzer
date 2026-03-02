from collections import Counter, defaultdict
from typing import List, Dict, Tuple
from .models import (
    ThreadInfo,
    ThreadDumpMeta,
    AnalysisResult,
    Aggregates,
    HotMethod,
    ContentionMonitor,
    RootCauseSummary,
)


def _top_method(thread: ThreadInfo) -> str:
    if not thread.stack:
        return "<no stack>"
    # Use the first 'at ...' frame captured
    return thread.stack[0].method or thread.stack[0].text


def analyze_threads(threads: List[ThreadInfo], meta: ThreadDumpMeta) -> AnalysisResult:
    # First pass: Clean up thread states
    for thread in threads:
        if not thread.state:
            # Try to infer state from stack frames if not set
            if any('Object.wait' in str(frame) for frame in thread.stack):
                thread.state = 'WAITING'
            elif any('Thread.sleep' in str(frame) for frame in thread.stack):
                thread.state = 'TIMED_WAITING'
            elif any('LockSupport.park' in str(frame) for frame in thread.stack):
                thread.state = 'WAITING'
            elif any('synchronized' in str(frame) for frame in thread.stack):
                thread.state = 'BLOCKED'
            else:
                thread.state = 'RUNNABLE'  # Default to RUNNABLE if we can't determine
    
    # Aggregation: states
    by_state = Counter(t.state or "RUNNABLE" for t in threads)  # Default to RUNNABLE instead of UNKNOWN

    # Hot methods: count top-of-stack method frequency
    top_methods = Counter(_top_method(t) for t in threads if t.stack)

    # Contention: monitors with many waiters (waiting on / waiting to lock)
    waiter_counts: Dict[str, int] = defaultdict(int)
    for t in threads:
        for lk in t.locks:
            if lk.action in ("waiting on", "waiting to lock"):
                waiter_counts[lk.id_hex] += 1

    # Owners from "Locked ownable synchronizers" section
    owner_by_monitor: Dict[str, ThreadInfo] = {}
    for t in threads:
        for own in t.ownable_syncs:
            owner_by_monitor[own.id_hex] = t

    # Build contention list including owner (if known)
    contention_list = []
    for mid, cnt in sorted(waiter_counts.items(), key=lambda kv: kv[1], reverse=True)[:15]:
        owner = owner_by_monitor.get(mid)
        contention_list.append(
            ContentionMonitor(
                monitor_id=mid,
                waiters=cnt,
                owner_thread=(owner.name if owner else None),
                owner_tid=(owner.tid if owner else None),
            )
        )

    # If no explicit waiters were parsed but we have owners and many threads are waiting/blocked,
    # surface owners as suspects with waiters=0 (UI will explain).
    if not contention_list and owner_by_monitor:
        for mid, owner in list(owner_by_monitor.items())[:10]:
            contention_list.append(
                ContentionMonitor(
                    monitor_id=mid,
                    waiters=0,
                    owner_thread=owner.name,
                    owner_tid=owner.tid,
                )
            )

    aggregates = Aggregates(
        by_state=dict(sorted(by_state.items(), key=lambda kv: kv[0])),
        hot_methods=[HotMethod(method=m, count=c) for m, c in top_methods.most_common(15)],
        contention_monitors=contention_list,
    )

    per_thread = []
    for t in threads:
        top = _top_method(t)
        waiting_on = [lk.id_hex for lk in t.locks if lk.action in ("waiting on", "waiting to lock")]
        held = [lk.id_hex for lk in t.locks if lk.action == "locked"]
        own_syncs = [lk.id_hex for lk in t.ownable_syncs]
        # Prepare a concise stack snippet (first 25 frames)
        stack_snippet = [sf.text for sf in t.stack[:25]]
        per_thread.append(
            {
                "name": t.name,
                "tid": t.tid,
                "nid": t.nid,
                "state": t.state,
                "top_method": top,
                "waiting_on": waiting_on,
                "held_locks": held,
                "ownable_syncs": own_syncs,
                "daemon": t.daemon,
                "priority": t.priority,
                "os_prio": t.os_prio,
                "stack_size": len(t.stack),
                "stack_snippet": stack_snippet,
            }
        )

    deadlocks = bool(meta.deadlock_section_present)

    # Infer root cause with simple heuristics
    root_cause: RootCauseSummary | None = None

    total = max(1, len(threads))
    blocked = (by_state.get("BLOCKED", 0))
    waiting = (by_state.get("WAITING", 0) + by_state.get("TIMED_WAITING", 0))
    runnable = by_state.get("RUNNABLE", 0)

    def hot_contains(substrs: List[str]) -> bool:
        s = {hm.method for hm in aggregates.hot_methods}
        for m in s:
            lower = m.lower()
            if any(sub in lower for sub in substrs):
                return True
        return False

    def synthesize_statement() -> str:
        parts = []
        parts.append(f"Threads: total={len(threads)}, RUNNABLE={by_state.get('RUNNABLE',0)}, BLOCKED={by_state.get('BLOCKED',0)}, WAITING={by_state.get('WAITING',0)+by_state.get('TIMED_WAITING',0)}")
        if aggregates.hot_methods:
            hm = aggregates.hot_methods[0]
            parts.append(f"Hot method: '{hm.method}' ({hm.count})")
        if aggregates.contention_monitors:
            topm = aggregates.contention_monitors[0]
            owner = f", owner={topm.owner_thread}" if topm.owner_thread else ""
            parts.append(f"Top monitor: {topm.monitor_id} waiters={topm.waiters}{owner}")
        if deadlocks:
            parts.append("Deadlock detected")
        return " | ".join(parts)

    if deadlocks:
        root_cause = RootCauseSummary(
            title="Java-level deadlock detected",
            confidence="High",
            details=[
                f"Deadlock section present in dump.",
                "Threads are mutually waiting on monitors; progress is impossible without intervention.",
            ],
            suggested_actions=[
                "Identify the involved threads and locks from the deadlock section.",
                "Review code paths for cyclical lock acquisition; enforce a consistent lock ordering.",
                "Consider deploying a fix and restarting the process to recover service.",
            ],
            statement=synthesize_statement(),
        )
    elif blocked / total >= 0.30 and aggregates.contention_monitors:
        top_monitor = aggregates.contention_monitors[0]
        root_cause = RootCauseSummary(
            title="Lock contention bottleneck",
            confidence="Medium-High" if top_monitor.waiters >= 5 else "Medium",
            details=[
                f"{blocked} of {total} threads are BLOCKED ({blocked/total:.0%}).",
                f"Monitor {top_monitor.monitor_id} has {top_monitor.waiters} waiter(s).",
                (f"Owned by thread '{top_monitor.owner_thread}' (tid={top_monitor.owner_tid})." if top_monitor.owner_thread else ""),
            ],
            suggested_actions=[
                "Investigate synchronized blocks or ReentrantLocks around the hotspot method(s).",
                "Reduce critical section length or increase concurrency (sharding, finer-grained locks).",
                "Capture additional dumps over time to confirm persistent contention.",
            ],
            statement=synthesize_statement(),
        )
    elif hot_contains(["unsafe.park", "object.wait", "condition", "locksupport.park"]) and waiting / total >= 0.5:
        root_cause = RootCauseSummary(
            title="Many threads parked/waiting",
            confidence="Medium",
            details=[
                f"{waiting} of {total} threads are WAITING/TIMED_WAITING.",
                "Top frames dominated by park/wait indicate threads are idle or awaiting work/locks.",
            ],
            suggested_actions=[
                "Check thread pool queue sizes and producers; ensure tasks are being submitted.",
                "If expected (idle system), no action. If unexpected, investigate upstream backpressure or starvation.",
            ],
            statement=synthesize_statement(),
        )
    elif hot_contains(["epollwait", "socketdispatcher.read0", "poll", "kqueue"]):
        root_cause = RootCauseSummary(
            title="I/O wait dominating",
            confidence="Medium",
            details=[
                "Hot methods indicate threads are waiting on network I/O (epoll/poll).",
                f"WAITING/TIMED_WAITING threads: {waiting}; RUNNABLE: {runnable}.",
            ],
            suggested_actions=[
                "Check downstream service latencies and network health.",
                "Review connection pools, timeouts, and backpressure configuration.",
            ],
            statement=synthesize_statement(),
        )
    else:
        root_cause = RootCauseSummary(
            title="No clear single root cause",
            confidence="Low",
            details=[
                "No deadlocks detected. State distribution and hotspots do not indicate a single dominant issue.",
            ],
            suggested_actions=[
                "Capture multiple dumps 10–20s apart to analyze thread state stability.",
                "Correlate with CPU, GC, and APM traces to pinpoint resource pressure.",
            ],
            statement=synthesize_statement(),
        )

    return AnalysisResult(
        total_threads=len(threads),
        aggregates=aggregates,
        deadlocks_detected=deadlocks,
        deadlock_note=(meta.raw_deadlock_block or None),
        per_thread_summaries=per_thread,
        root_cause=root_cause,
    )
