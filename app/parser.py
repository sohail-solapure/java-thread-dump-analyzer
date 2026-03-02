import re
from typing import List, Tuple, Dict, Optional
from .models import ThreadInfo, StackFrame, LockRef, ThreadDumpMeta

# Patterns for common HotSpot style dumps
THREAD_HEADER_RE = re.compile(
    r'^\"(?P<name>.+?)\".*$',
)
# Additional patterns for thread state detection in header
STATE_IN_HEADER_RE = re.compile(r'\b(?P<state>RUNNABLE|BLOCKED|WAITING|TIMED_WAITING|TERMINATED|NEW)\b')
WAITING_ON_CONDITION_RE = re.compile(r'waiting on condition\b')
WAITING_ON_MONITOR_RE = re.compile(r'waiting for monitor entry\b')
WAITING_ON_LATCH_RE = re.compile(r'waiting on\s+\[\w+\]')
PARKING_RE = re.compile(r'parking to wait for\s+<0x[0-9a-f]+>')
IN_OBJECT_WAIT_RE = re.compile(r'in Object\.wait\(\)')
SLEEPING_RE = re.compile(r'sleeping on')
SUSPENDED_RE = re.compile(r'suspended')
NATIVE_METHOD_RE = re.compile(r'Native Method')
COMPILING_RE = re.compile(r'Compilation')
GC_TASK_RE = re.compile(r'GC task')
VM_OPERATION_RE = re.compile(r'VM Operation')
VM_THREAD_RE = re.compile(r'VM Thread')
JNI_GLOBAL_REFS_RE = re.compile(r'JNI global references')
HEADER_KV_RE = re.compile(r'(\bprio=(?P<prio>\d+))|(\bos_prio=(?P<os_prio>\d+))|(\btid=(?P<tid>0x[0-9a-fA-F]+))|(\bnid=(?P<nid>0x[0-9a-fA-F]+))')
STATE_RE = re.compile(r'^\s*java\.lang\.Thread\.State:\s*(?P<state>\S+)')
FRAME_RE = re.compile(r'^\s*at\s+(?P<method>[^\(]+)\(.*\)')
LOCK_WAIT_RE = re.compile(r'^\s*-\s*waiting on\s*<(?P<id>[0-9a-fx]+)>\s*\(a\s+(?P<type>[^\)]+)\)')
LOCK_WAIT_TO_LOCK_RE = re.compile(r'^\s*-\s*waiting to lock\s*<(?P<id>[0-9a-fx]+)>\s*\(a\s+(?P<type>[^\)]+)\)')
LOCK_HELD_RE = re.compile(r'^\s*-\s*locked\s*<(?P<id>[0-9a-fx]+)>\s*\(a\s+(?P<type>[^\)]+)\)')
DEADLOCK_START_RE = re.compile(r'^Found one Java-level deadlock:')
OWNABLE_HEADER_RE = re.compile(r'^\s*Locked ownable synchronizers:')
OWNABLE_NONE_RE = re.compile(r'^\s*-\s*None')
OWNABLE_ENTRY_RE = re.compile(r'^\s*-\s*<(?P<id>[0-9a-fx]+)>\s*\(a\s+(?P<type>[^\)]+)\)')


def parse_thread_dump(text: str) -> Tuple[List[ThreadInfo], ThreadDumpMeta]:
    lines = text.splitlines()
    threads: List[ThreadInfo] = []
    meta = ThreadDumpMeta()

    i = 0
    current: Optional[ThreadInfo] = None
    deadlock_lines: List[str] = []
    in_deadlock = False

    while i < len(lines):
        line = lines[i]
        # Detect deadlock section
        if DEADLOCK_START_RE.match(line):
            in_deadlock = True
            meta.deadlock_section_present = True
        if in_deadlock:
            deadlock_lines.append(line)
            i += 1
            continue

        # Thread header
        m = THREAD_HEADER_RE.match(line)
        if m:
            if current:
                threads.append(current)
            name = m.group('name').strip()
            # Extract key/values from header line
            prio = None
            os_prio = None
            tid = None
            nid = None
            for km in HEADER_KV_RE.finditer(line):
                if km.group('prio'):
                    prio = km.group('prio')
                if km.group('os_prio'):
                    os_prio = km.group('os_prio')
                if km.group('tid'):
                    tid = km.group('tid')
                if km.group('nid'):
                    nid = km.group('nid')
            is_daemon = (' daemon ' in f' {line} ')
            # Try to infer state from header if possible
            inferred_state = None
            
            # Check for explicit state in header first
            state_match = STATE_IN_HEADER_RE.search(line)
            if state_match:
                inferred_state = state_match.group('state')
            # Check for common state indicators in header
            elif IN_OBJECT_WAIT_RE.search(line):
                inferred_state = 'WAITING'
            elif WAITING_ON_CONDITION_RE.search(line) or WAITING_ON_MONITOR_RE.search(line):
                inferred_state = 'WAITING'
            elif WAITING_ON_LATCH_RE.search(line):
                inferred_state = 'WAITING'
            elif PARKING_RE.search(line):
                inferred_state = 'WAITING'
            elif SLEEPING_RE.search(line):
                inferred_state = 'TIMED_WAITING'
            elif SUSPENDED_RE.search(line):
                inferred_state = 'RUNNABLE'  # Or 'SUSPENDED' if you want to track separately
            elif COMPILING_RE.search(line) or GC_TASK_RE.search(line) or VM_OPERATION_RE.search(line):
                inferred_state = 'RUNNABLE'  # VM internal threads
            elif NATIVE_METHOD_RE.search(line) and not inferred_state:
                # If we're in a native method and no other state was detected
                inferred_state = 'RUNNABLE'
                
            current = ThreadInfo(
                name=name,
                tid=tid,
                nid=nid,
                priority=prio,
                os_prio=os_prio,
                daemon=is_daemon,
                state=inferred_state  # Set initial state from header if possible
            )
            i += 1
            continue

        if current is not None:
            # State - handle both standard and non-standard state formats
            sm = STATE_RE.match(line)
            if sm:
                state = sm.group('state').strip()
                # Special case: 'in Object.wait()' is actually WAITING, not RUNNABLE
                if 'Object.wait' in str(current.stack) and state == 'RUNNABLE':
                    current.state = 'WAITING'
                # Only update if we don't have a state yet or if the new state is more specific
                elif current.state is None or current.state in ('RUNNABLE', 'UNKNOWN'):
                    current.state = state
                i += 1
                continue

            # Stack frame
            fm = FRAME_RE.match(line)
            if fm:
                method = fm.group('method').strip()
                current.stack.append(StackFrame(text=line.strip(), method=method))
                i += 1
                continue

            # Locks
            for regex, action in (
                (LOCK_WAIT_RE, 'waiting on'),
                (LOCK_WAIT_TO_LOCK_RE, 'waiting to lock'),
                (LOCK_HELD_RE, 'locked'),
            ):
                lm = regex.match(line)
                if lm:
                    current.locks.append(
                        LockRef(id_hex=f"0x{lm.group('id').lower().lstrip('0x')}", type=lm.group('type').strip(), action=action)
                    )
                    i += 1
                    break
            else:
                # Check for ownable synchronizers section
                if OWNABLE_HEADER_RE.match(line):
                    i += 1
                    # Consume following lines until blank line or next header indicator
                    while i < len(lines):
                        ln = lines[i]
                        if ln.strip() == '':
                            break
                        # Another thread header starts with a quote
                        if THREAD_HEADER_RE.match(ln):
                            # Do not consume; outer loop will handle as new thread
                            break
                        if OWNABLE_NONE_RE.match(ln):
                            i += 1
                            continue
                        em = OWNABLE_ENTRY_RE.match(ln)
                        if em:
                            current.ownable_syncs.append(
                                LockRef(id_hex=f"0x{em.group('id').lower().lstrip('0x')}", type=em.group('type').strip(), action='locked')
                            )
                            i += 1
                            continue
                        # unknown line in ownable section; skip
                        i += 1
                    # do not advance here; allow outer loop to process possible next header/blank
                    continue
                else:
                    i += 1
            continue

        i += 1

    # Append last thread
    if current:
        threads.append(current)

    # Capture deadlock block if present (until blank line or EOF)
    if meta.deadlock_section_present:
        meta.raw_deadlock_block = "\n".join(deadlock_lines).strip()

    return threads, meta
