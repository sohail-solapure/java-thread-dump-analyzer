from typing import List, Dict, Optional
from pydantic import BaseModel, Field


class StackFrame(BaseModel):
    text: str
    method: Optional[str] = None  # extracted method signature if possible


class LockRef(BaseModel):
    id_hex: str  # e.g., 0x00000000
    type: Optional[str] = None  # e.g., java.lang.Object
    action: str  # "waiting on", "waiting to lock", "locked"


class ThreadInfo(BaseModel):
    name: str
    tid: Optional[str] = None
    nid: Optional[str] = None
    daemon: Optional[bool] = None
    priority: Optional[str] = None
    os_prio: Optional[str] = None
    state: Optional[str] = None  # e.g., RUNNABLE, BLOCKED, WAITING, TIMED_WAITING
    stack: List[StackFrame] = Field(default_factory=list)
    locks: List[LockRef] = Field(default_factory=list)
    ownable_syncs: List[LockRef] = Field(default_factory=list)  # from "Locked ownable synchronizers"


class ThreadDumpMeta(BaseModel):
    deadlock_section_present: bool = False
    raw_deadlock_block: Optional[str] = None


class HotMethod(BaseModel):
    method: str
    count: int


class ContentionMonitor(BaseModel):
    monitor_id: str
    waiters: int
    owner_thread: Optional[str] = None
    owner_tid: Optional[str] = None


class Aggregates(BaseModel):
    by_state: Dict[str, int]
    hot_methods: List[HotMethod]
    contention_monitors: List[ContentionMonitor]


class RootCauseSummary(BaseModel):
    title: str
    confidence: str  # Low, Medium, High
    details: List[str] = Field(default_factory=list)
    suggested_actions: List[str] = Field(default_factory=list)
    statement: Optional[str] = None


class AnalysisResult(BaseModel):
    total_threads: int
    aggregates: Aggregates
    deadlocks_detected: bool
    deadlock_note: Optional[str] = None
    per_thread_summaries: List[Dict]
    root_cause: Optional[RootCauseSummary] = None
