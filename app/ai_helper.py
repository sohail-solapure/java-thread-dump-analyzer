import os
import json
import requests
import base64
from dotenv import load_dotenv, find_dotenv
from typing import Optional

# Ensure environment variables are loaded even if importer didn't
load_dotenv(find_dotenv())


def _build_prompt(raw_dump: str, summary: dict) -> str:
    """Build a detailed prompt for root cause analysis using thread dump data."""
    thread_count = summary.get('total_threads', 0)
    deadlocks = summary.get('deadlocks_detected', False)
    states = {}
    agg = summary.get('aggregates', {})
    if isinstance(agg, dict):
        states = agg.get('by_state', {})
    elif hasattr(agg, 'by_state'):
        states = agg.by_state if isinstance(agg.by_state, dict) else {}

    hot_methods = []
    if isinstance(agg, dict):
        hot_methods = agg.get('hot_methods', [])[:5]
    elif hasattr(agg, 'hot_methods'):
        hot_methods = [{'method': h.method, 'count': h.count} for h in (agg.hot_methods or [])[:5]]

    contention = []
    if isinstance(agg, dict):
        contention = agg.get('contention_monitors', [])[:3]
    elif hasattr(agg, 'contention_monitors'):
        contention = [
            {'monitor_id': c.monitor_id, 'waiters': c.waiters, 'owner_thread': c.owner_thread}
            for c in (agg.contention_monitors or [])[:3]
        ]

    # Build per-thread detail for deeper analysis
    per_threads = summary.get('per_thread_summaries', [])
    thread_details = []
    for t in (per_threads or [])[:30]:
        if isinstance(t, dict):
            td = t
        else:
            td = t.__dict__ if hasattr(t, '__dict__') else {}
        name = td.get('name', '?')
        state = td.get('state', '?')
        top_method = td.get('top_method', '?')
        waiting_on = td.get('waiting_on', [])
        held_locks = td.get('held_locks', [])
        snippet = td.get('stack_snippet', [])[:5]
        thread_details.append(
            f"  - {name} [{state}] top={top_method} waiting={waiting_on} held={held_locks}\n"
            + ("    stack: " + " -> ".join(snippet[:3]) if snippet else "")
        )

    parts = [
        "You are an expert JVM performance analyst and Java thread dump specialist.",
        "Analyze this Java thread dump thoroughly and provide a DETAILED root cause analysis.",
        "\n## Context:",
        f"- Total Threads: {thread_count}",
        f"- Deadlocks Detected: {'Yes' if deadlocks else 'No'}",
        "\n## Thread States:",
        *[f"- {k}: {v}" for k, v in (states.items() if isinstance(states, dict) else [])],
        "\n## Top Methods (hotspots):",
        *[f"- {m.get('method', '') if isinstance(m, dict) else getattr(m, 'method', '')} "
          f"(count: {m.get('count', 0) if isinstance(m, dict) else getattr(m, 'count', 0)})"
          for m in hot_methods],
    ]

    if contention:
        parts.extend([
            "\n## Contention Points:",
            *[f"- Monitor: {c.get('monitor_id', '') if isinstance(c, dict) else getattr(c, 'monitor_id', '')}, "
              f"Waiters: {c.get('waiters', 0) if isinstance(c, dict) else getattr(c, 'waiters', 0)}, "
              f"Owner: {c.get('owner_thread', 'Unknown') if isinstance(c, dict) else getattr(c, 'owner_thread', 'Unknown')}"
              for c in contention]
        ])

    if thread_details:
        parts.extend([
            "\n## Thread Details (sample of first 30):",
            *thread_details
        ])

    # Include a portion of the raw dump for additional context
    # Keeping this very small (5k chars instead of 50k) to avoid Gemini Free Tier rate limits
    raw_sample = raw_dump[:5000] if len(raw_dump) > 5000 else raw_dump

    parts.extend([
        "\n## Raw Thread Dump (truncated):\n",
        raw_sample,
        "\n## Analysis Instructions:",
        "1. Identify the PRIMARY performance bottleneck or issue in this thread dump",
        "2. List the top 3 most likely root causes, ordered by probability, with detailed explanation",
        "3. For each root cause, cite SPECIFIC thread names, stack frames, and lock IDs as evidence",
        "4. Identify any thread pools that are exhausted or stuck",
        "5. Check for connection pool exhaustion, database lock contention, or I/O blocking patterns",
        "6. If deadlocks are detected, explain the full deadlock cycle with thread names",
        "7. Provide specific, actionable remediation steps for each root cause",
        "8. Rate the severity: CRITICAL / HIGH / MEDIUM / LOW",
        "9. Format the response in clear markdown with headings and bullet points"
    ])

    return "\n".join(parts)


def _build_root_cause_prompt(raw_dump: str, summary: dict) -> str:
    """Build a focused prompt specifically for root cause statement generation."""
    thread_count = summary.get('total_threads', 0)
    deadlocks = summary.get('deadlocks_detected', False)
    agg = summary.get('aggregates', {})

    states = {}
    if isinstance(agg, dict):
        states = agg.get('by_state', {})
    elif hasattr(agg, 'by_state'):
        states = agg.by_state if isinstance(agg.by_state, dict) else {}

    hot_methods = []
    if isinstance(agg, dict):
        hot_methods = agg.get('hot_methods', [])[:5]
    elif hasattr(agg, 'hot_methods'):
        hot_methods = [{'method': h.method, 'count': h.count} for h in (agg.hot_methods or [])[:5]]

    # Per-thread details
    per_threads = summary.get('per_thread_summaries', [])
    blocked_threads = []
    waiting_threads = []
    for t in (per_threads or []):
        td = t if isinstance(t, dict) else (t.__dict__ if hasattr(t, '__dict__') else {})
        state = td.get('state', '')
        if state == 'BLOCKED':
            blocked_threads.append(td)
        elif state in ('WAITING', 'TIMED_WAITING'):
            waiting_threads.append(td)

    parts = [
        "You are a senior JVM performance engineer performing root cause analysis on a production Java thread dump.",
        "\nProvide a PRECISE, ACTIONABLE root cause statement. Be specific about:",
        "- What exact resource/lock/method is causing the issue",
        "- Which thread pool or component is affected",
        "- The chain of events leading to the problem",
        "- Quantitative evidence (how many threads, what percentage)",
        f"\n## Thread Dump Summary:",
        f"- Total Threads: {thread_count}",
        f"- Deadlocks: {'YES - CRITICAL' if deadlocks else 'No'}",
        f"- States: {json.dumps(states)}",
        f"- Hot methods: {json.dumps([{'m': m.get('method', '') if isinstance(m, dict) else getattr(m, 'method', ''), 'c': m.get('count', 0) if isinstance(m, dict) else getattr(m, 'count', 0)} for m in hot_methods])}",
    ]

    if blocked_threads:
        parts.append(f"\n## BLOCKED Threads ({len(blocked_threads)}):")
        for t in blocked_threads[:10]:
            parts.append(f"  - {t.get('name', '?')}: top={t.get('top_method', '?')} waiting_on={t.get('waiting_on', [])}")

    if waiting_threads:
        parts.append(f"\n## WAITING/TIMED_WAITING Threads ({len(waiting_threads)}):")
        for t in waiting_threads[:15]:
            parts.append(f"  - {t.get('name', '?')}: top={t.get('top_method', '?')}")

    # Truncate raw dump to keep within context limits (Google Free tier has 15k RPM token limit)
    raw_sample = raw_dump[:3000]

    parts.extend([
        "\n## Raw Dump (sample):\n",
        raw_sample,
        "\n## Output Format:",
        "1. Start with a 2-3 sentence SIMPLE SUMMARY in plain terminology that a non-technical person can easily understand.",
        "2. Follow it with the deeper technical explanation of the primary bottleneck, evidence from the dump, and affected components.",
        "3. End with an actionable recommendation.",
        "DO NOT use tags like [MEDIUM] or [CRITICAL] in your output."
    ])

    return "\n".join(parts)


def _safe_to_dict(result_obj) -> dict:
    """Safely convert an AnalysisResult Pydantic model to dict."""
    try:
        if hasattr(result_obj, "model_dump"):
            return result_obj.model_dump()
        elif hasattr(result_obj, "dict"):
            return result_obj.dict()
        else:
            from pydantic.json import pydantic_encoder
            return json.loads(json.dumps(result_obj, default=pydantic_encoder))
    except Exception as e:
        print(f"[ai_helper] Warning: could not convert result_obj to dict: {e}")
        # Manual fallback for common fields
        d = {}
        for attr in ['total_threads', 'deadlocks_detected', 'deadlock_note', 'per_thread_summaries']:
            if hasattr(result_obj, attr):
                d[attr] = getattr(result_obj, attr)
        if hasattr(result_obj, 'aggregates'):
            agg = result_obj.aggregates
            d['aggregates'] = {
                'by_state': agg.by_state if hasattr(agg, 'by_state') else {},
                'hot_methods': [{'method': h.method, 'count': h.count} for h in (agg.hot_methods or [])] if hasattr(agg, 'hot_methods') else [],
                'contention_monitors': [{'monitor_id': c.monitor_id, 'waiters': c.waiters, 'owner_thread': c.owner_thread} for c in (agg.contention_monitors or [])] if hasattr(agg, 'contention_monitors') else [],
            }
        if hasattr(result_obj, 'root_cause') and result_obj.root_cause:
            rc = result_obj.root_cause
            d['root_cause'] = {
                'title': getattr(rc, 'title', ''),
                'confidence': getattr(rc, 'confidence', ''),
                'details': getattr(rc, 'details', []),
                'suggested_actions': getattr(rc, 'suggested_actions', []),
                'statement': getattr(rc, 'statement', ''),
            }
        return d


def _call_gemini_api(prompt: str, timeout: int = 60) -> Optional[str]:
    """Make a call to the Gemini/Gemma API and return the text response."""
    enc_key = os.getenv("GOOGLE_API_KEY")
    if not enc_key:
        print("[ai_helper] No GOOGLE_API_KEY set")
        return None

    # Decrypt obfuscated API key (Base64 reversed)
    try:
        api_key = base64.b64decode(enc_key[::-1]).decode('utf-8')
    except Exception as e:
        print(f"[ai_helper] Failed to decrypt GOOGLE_API_KEY: {e}")
        return None

    model_name = os.getenv("GOOGLE_MODEL", "gemma-3-27b-it")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 4096,
            "temperature": 0.3
        }
    }

    headers = {'Content-Type': 'application/json'}

    try:
        print(f"[ai_helper] Calling API: model={model_name}, prompt_len={len(prompt)}, timeout={timeout}s")
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        print(f"[ai_helper] API response status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            if 'candidates' in result and len(result['candidates']) > 0:
                candidate = result['candidates'][0]
                if 'content' in candidate and 'parts' in candidate['content']:
                    parts = candidate['content']['parts']
                    if len(parts) > 0 and 'text' in parts[0]:
                        text = parts[0]['text']
                        print(f"[ai_helper] Got response: {len(text)} chars")
                        return text
            # Log what we got if no candidates
            print(f"[ai_helper] No candidates in response: {json.dumps(result)[:500]}")
        else:
            print(f"[ai_helper] API error {response.status_code}: {response.text[:500]}")

        return None
    except requests.exceptions.Timeout:
        print(f"[ai_helper] API call timed out after {timeout}s")
        return None
    except Exception as e:
        print(f"[ai_helper] API call failed: {e}")
        return None


def generate_ai_insights(raw_dump: str, result_obj) -> Optional[str]:
    """
    Generate AI insights using Gemma model via direct HTTP API call.
    Returns a markdown string or None on failure/unavailable.
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None

    try:
        summary = _safe_to_dict(result_obj)
        prompt = _build_prompt(raw_dump, summary)
        return _call_gemini_api(prompt, timeout=90)
    except Exception as e:
        print(f"[ai_helper] generate_ai_insights error: {e}")
        return None


def generate_ai_root_cause_statement(raw_dump: str, result_obj) -> Optional[str]:
    """
    Generate a detailed root cause analysis using Gemma model via HTTP API.
    Returns a structured markdown response with analysis and recommendations.
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None

    try:
        summary = _safe_to_dict(result_obj)
        prompt = _build_root_cause_prompt(raw_dump, summary)
        result = _call_gemini_api(prompt, timeout=90)
        return result
    except Exception as e:
        print(f"[ai_helper] generate_ai_root_cause_statement error: {e}")
        return None


def generate_ai_pdf_summary(raw_dump: str, result_obj) -> Optional[str]:
    """
    Generate a concise, PDF-friendly executive summary.
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None

    try:
        summary = _safe_to_dict(result_obj)
        thread_count = summary.get('total_threads', 0)
        deadlocks = summary.get('deadlocks_detected', False)
        agg = summary.get('aggregates', {})
        states = agg.get('by_state', {}) if isinstance(agg, dict) else {}
        hot = agg.get('hot_methods', [])[:5] if isinstance(agg, dict) else []

        prompt = (
            "You are an expert JVM performance analyst. Create a concise executive summary suitable for a PDF report.\n"
            "Use short bullet points (no markdown symbols, just hyphens) and avoid excessive verbosity.\n"
            f"Total Threads: {thread_count}\n"
            f"Deadlocks: {'Yes' if deadlocks else 'No'}\n"
            f"Thread States: {json.dumps(states)}\n"
            f"Top Methods: {json.dumps([m.get('method', '') if isinstance(m, dict) else '' for m in hot])}\n"
            "\nProvide a 3-5 bullet point summary of the key findings and recommendations."
        )

        return _call_gemini_api(prompt, timeout=60)
    except Exception as e:
        print(f"[ai_helper] generate_ai_pdf_summary error: {e}")
        return None


def generate_ai_qa_answer(question: str, result_obj, raw_dump: str, history: Optional[list] = None) -> dict:
    """
    Answer a question using the provided thread dump analysis.
    Returns: dict with 'answer' (str) and 'refused' (bool) keys
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return {
            "answer": "AI features are currently unavailable. Please check if the Google API key is configured.",
            "refused": True
        }

    try:
        summary = _safe_to_dict(result_obj)

        # Build context from summary
        thread_count = summary.get('total_threads', 0)
        deadlocks = summary.get('deadlocks_detected', False)
        agg = summary.get('aggregates', {})
        states = agg.get('by_state', {}) if isinstance(agg, dict) else {}
        hot = agg.get('hot_methods', [])[:5] if isinstance(agg, dict) else []

        prompt_parts = [
            "You are an expert Java performance analyst. Answer the user's question about this thread dump.",
            "Be specific, cite thread names and stack frames when possible.",
            "\n## Thread Dump Summary:",
            f"- Total threads: {thread_count}",
            f"- Deadlocks detected: {deadlocks}",
            "\n## Thread States:",
            *[f"- {state}: {count}" for state, count in (states.items() if isinstance(states, dict) else [])],
        ]

        if hot:
            prompt_parts.append("\n## Hot Methods:")
            for m in hot:
                method = m.get('method', '') if isinstance(m, dict) else getattr(m, 'method', '')
                count = m.get('count', 0) if isinstance(m, dict) else getattr(m, 'count', 0)
                prompt_parts.append(f"- `{method}` ({count} times)")

        # Add conversation history
        if history:
            prompt_parts.append("\n## Recent Conversation:")
            for msg in (history or [])[-3:]:
                q = msg.get('q', '') if isinstance(msg, dict) else ''
                a = msg.get('a', '') if isinstance(msg, dict) else ''
                if q:
                    prompt_parts.append(f"User: {q}")
                if a:
                    prompt_parts.append(f"Assistant: {a[:200]}...")

        prompt_parts.extend([
            f"\n## Current Question:\n{question}",
            "\n## Instructions:",
            "- Answer concisely but thoroughly",
            "- Use markdown formatting",
            "- Cite specific evidence from the thread dump",
            "- If you're not sure, say so",
        ])

        prompt = "\n".join(prompt_parts)
        answer = _call_gemini_api(prompt, timeout=60)

        if answer:
            return {"answer": answer, "refused": False}
        else:
            return {"answer": "I couldn't process that request. Please try again.", "refused": False}

    except Exception as e:
        print(f"[ai_helper] generate_ai_qa_answer error: {e}")
        return {"answer": "I encountered an error processing your question. Please try again.", "refused": False}
