# Java Thread Dump Analyzer (Web)

A minimal FastAPI-based web tool to analyze Java thread dump `.txt` files. Upload a dump and get:

- Summary by thread state
- Hot methods (most frequent top stack frames)
- Contention suspects (locks with many waiters)
- Deadlock detection (based on dump markers)
- Per-thread brief summaries

## Quick Start

1) Create a virtual environment (recommended)

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2) Install dependencies

```bash
pip install -r requirements.txt
```

3) Run the server

```bash
uvicorn app.main:app --reload
```

4) Open the UI

- Navigate to http://127.0.0.1:8000
- Upload your thread dump `.txt`

## Project Structure

```
app/
  main.py             # FastAPI app & routes
  parser.py           # Thread dump parser
  analyzer.py         # Analyzer logic
  models.py           # Pydantic models for IO
  templates/
    index.html        # Upload UI and results view
  static/
    style.css         # Minimal styles
requirements.txt
README.md
```

## Notes

- Parser targets common HotSpot/Oracle/OpenJDK text thread dumps. Dumps vary across vendors/flags; we can extend heuristics with real samples.
- No data is stored; analysis is in-memory per upload.
- Large dumps are supported up to a configurable size limit; increase if needed.

## Next Steps

- Improve deadlock inference (monitor wait-for graph)
- Tag threads (GC, JIT, RMI, HTTP pool) by name patterns
- Detect stuck/hung candidates (BLOCKED on same monitor, RUNNABLE CPU hogs by hot methods)
- Export JSON/CSV
- Add dark mode & richer UI
