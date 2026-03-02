from fastapi import FastAPI, File, UploadFile, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
import uuid
from datetime import datetime
import io
import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

from .parser import parse_thread_dump
from .analyzer import analyze_threads
from .models import AnalysisResult
from .ai_helper import generate_ai_insights, generate_ai_pdf_summary, generate_ai_root_cause_statement
from .ai_helper import generate_ai_qa_answer

app = FastAPI(title="Java Thread Dump Analyzer")

# Static files and templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

load_dotenv(find_dotenv())

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    ai_enabled = bool(os.getenv("GOOGLE_API_KEY"))
    ai_model = os.getenv("GOOGLE_MODEL", "gemini-1.5-pro")
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "result": None, "error": None, "ai_enabled": ai_enabled, "ai_model": ai_model},
    )


@app.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, file: UploadFile = File(...)):
    try:
        if not file.filename.lower().endswith(".txt"):
            raise ValueError("Please upload a .txt thread dump file")
        content_bytes = await file.read()
        text = content_bytes.decode(errors="replace")
        # Validate it's a Java thread dump before proceeding
        tl = text.lower()
        has_openjdk = "full thread dump openjdk" in tl
        has_smr = "threads class smr info:" in tl
        if not (has_openjdk or has_smr):
            raise ValueError("The uploaded file doesn't look like a Java thread dump")
        threads, meta = parse_thread_dump(text)
        result: AnalysisResult = analyze_threads(threads, meta)
        # Optional AI insights (requires GOOGLE_API_KEY)
        ai_insights = generate_ai_insights(text, result)
        ai_pdf_summary = generate_ai_pdf_summary(text, result)
        # Focused AI root cause statement to make the issue explicit
        ai_root_cause_stmt = generate_ai_root_cause_statement(text, result)
        try:
            if ai_root_cause_stmt and getattr(result, "root_cause", None):
                # Override or set the statement to the AI-generated concise statement
                result.root_cause.statement = ai_root_cause_stmt
        except Exception:
            pass
        ai_enabled = bool(os.getenv("GOOGLE_API_KEY"))
        ai_model = os.getenv("GOOGLE_MODEL", "gemini-1.5-pro")
        analyzed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Cache latest analysis in app state for PDF export and Q&A history
        app.state.last_analysis = {
            "result": result,
            "filename": file.filename,
            "ai_insights": ai_insights,
            "ai_pdf_summary": ai_pdf_summary,
            "ai_root_cause_stmt": ai_root_cause_stmt,
            "analyzed_at": analyzed_at,
            "raw_text": text,
            "qa_history": [],
        }
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "result": result,
                "error": None,
                "filename": file.filename,
                "analyzed_at": analyzed_at,
                "ai_insights": ai_insights,
                "ai_enabled": ai_enabled,
                "ai_model": ai_model,
                # Optionally, could pass history to template later
            },
        )
    except Exception as e:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "result": None, "error": str(e)},
        )


@app.get("/download")
async def download_pdf(request: Request):
    # ensure we have a cached analysis
    cache = getattr(app.state, "last_analysis", None)
    if not cache:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "result": None, "error": "No analysis available. Upload and analyze a file first."},
        )

    # Lazy import so server can start without PDF deps
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter, A4
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet
    except Exception:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "result": None, "error": "PDF generation dependency missing. Please install reportlab (pip install reportlab)."},
        )

    ctx = {
        "request": request,
        "result": cache["result"],
        "filename": cache["filename"],
        "ai_insights": cache.get("ai_insights"),
        "ai_pdf_summary": cache.get("ai_pdf_summary"),
        "analyzed_at": cache.get("analyzed_at"),
        "ai_enabled": bool(os.getenv("GOOGLE_API_KEY")),
        "ai_model": os.getenv("GOOGLE_MODEL", "gemini-1.5-pro"),
    }
    # Generate PDF using reportlab
    from io import BytesIO
    pdf_buffer = BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    
    # Title
    story.append(Paragraph("Java Thread Dump Analysis Report", styles['Title']))
    story.append(Spacer(1, 12))
    
    # File info
    story.append(Paragraph(f"File: {cache['filename']}", styles['Normal']))
    story.append(Paragraph(f"Generated: {cache.get('analyzed_at', '-')}", styles['Normal']))
    story.append(Spacer(1, 12))
    
    # Overview
    story.append(Paragraph("Overview", styles['Heading2']))
    result = cache["result"]
    overview_data = [
        ['Total Threads', str(result.total_threads)],
        ['Deadlocks Detected', 'Yes' if result.deadlocks_detected else 'No']
    ]
    overview_table = Table(overview_data)
    story.append(overview_table)
    story.append(Spacer(1, 12))
    
    # AI Insights
    if cache.get("ai_insights"):
        story.append(Paragraph("AI Insights", styles['Heading2']))
        story.append(Paragraph(cache["ai_insights"], styles['Normal']))
        story.append(Spacer(1, 12))
    
    # Build PDF
    doc.build(story)
    pdf_buffer.seek(0)

    original = cache["filename"] or "analysis.txt"
    base = os.path.splitext(os.path.basename(original))[0]
    out_name = f"{base}_Analyzed.pdf"
    headers = {"Content-Disposition": f"attachment; filename=\"{out_name}\""}
    return StreamingResponse(pdf_buffer, media_type="application/pdf", headers=headers)


class ChatMessage(BaseModel):
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class ChatSession:
    def __init__(self):
        self.messages: List[ChatMessage] = []
        self.max_history = 50  # Maximum number of messages to keep in history
    
    def add_message(self, role: str, content: str):
        self.messages.append(ChatMessage(role=role, content=content))
        # Trim history if needed
        if len(self.messages) > self.max_history:
            self.messages = self.messages[-self.max_history:]
    
    def get_messages(self, max_messages: int = 20) -> List[dict]:
        """Get recent messages as a list of dicts"""
        recent = self.messages[-max_messages:]
        return [{"role": msg.role, "content": msg.content, "timestamp": msg.timestamp} 
                for msg in recent]

# Initialize chat session storage
app.state.chat_sessions = {}

def get_chat_session(session_id: str) -> ChatSession:
    """Get or create a chat session for the given ID"""
    if session_id not in app.state.chat_sessions:
        app.state.chat_sessions[session_id] = ChatSession()
    return app.state.chat_sessions[session_id]

class ChatResponse(BaseModel):
    response: str
    refused: bool

@app.post("/chat/message", response_model=ChatResponse)
async def chat_message(request: Request, message: str):
    """Handle chat messages and return AI responses."""
    try:
        # Get or create chat session
        session_id = request.session.get("session_id", str(uuid.uuid4()))
        request.session["session_id"] = session_id
        
        # Get chat history from session
        chat_history = request.session.get("chat_history", [])
        
        # Add user message to history
        chat_history.append({"role": "user", "content": message})
        
        try:
            # Get AI response
            response = await generate_ai_response(message, chat_history, request)
            chat_history.append({"role": "assistant", "content": response})
            return {"response": response, "refused": False}
            
        except Exception as ai_error:
            error_msg = str(ai_error)
            print(f"AI processing error: {error_msg}")
            return {
                "session_id": session_id,
                "response": "I'm sorry, I encountered an error. Please try again.",
                "refused": False,
                "history": chat_history[-20:]
            }
        
    except Exception as e:
        print(f"Error in chat_message: {e}")
        error_msg = "I'm sorry, I encountered an error processing your message. Please try again."
        session_id = data.get("session_id", str(uuid.uuid4())) if 'data' in locals() else str(uuid.uuid4())
        return {
            "session_id": session_id,
            "response": error_msg,
            "refused": False,
            "history": []
        }


@app.post("/ask")
async def ask_question(request: Request):
    """Answer a question strictly from the currently analyzed thread dump.
    Returns JSON: {answer: str|None, refused: bool}
    """
    cache = getattr(app.state, "last_analysis", None)
    if not cache:
        return {"answer": None, "refused": True}
    try:
        data = await request.json()
        question = (data.get("question") or "").strip()
    except Exception:
        question = ""
    if not question:
        return {"answer": None, "refused": True}

    result = cache.get("result")
    raw_text = cache.get("raw_text") or ""

    def build_parsed_summary_answer(r: AnalysisResult) -> str:
        try:
            lines = []
            lines.append(f"Total threads: {getattr(r, 'total_threads', '-')}")
            dead = getattr(r, 'deadlocks_detected', False)
            lines.append(f"Deadlocks detected: {'Yes' if dead else 'No'}")
            by_state = getattr(getattr(r, 'aggregates', None), 'by_state', {}) or {}
            if by_state:
                parts = []
                for k, v in by_state.items():
                    parts.append(f"{k}={v}")
                lines.append("By state: " + ", ".join(parts))
            hot = getattr(getattr(r, 'aggregates', None), 'hot_methods', []) or []
            if hot:
                top3 = ", ".join([f"{h.method} ({h.count})" for h in hot[:3]])
                lines.append("Top methods: " + top3)
            cont = getattr(getattr(r, 'aggregates', None), 'contention_monitors', []) or []
            if cont:
                c0 = cont[0]
                owner = getattr(c0, 'owner_thread', None) or '-'
                lines.append(f"Top contention monitor: {c0.monitor_id} waiters={c0.waiters} owner={owner}")
            return "\n".join(lines)
        except Exception:
            return "Summary not available."

    ql = question.lower()
    is_summary_query = any(s in ql for s in ["summary", "overview", "by state", "hot method", "deadlock", "contention"]) 

    history = cache.get("qa_history")
    if history is None:
        history = []
        cache["qa_history"] = history

    qa = generate_ai_qa_answer(question, result, raw_text, history=history)
    answer = qa.get("answer") if isinstance(qa, dict) else None

    # Build entry and persist in history
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {"timestamp": ts, "question": question, "answer": answer or ""}
    history.append({"q": question, "a": answer or ""})
    # cap history to last 100
    if len(history) > 100:
        del history[:-100]

    # If AI produced no text and it's a summary-like query, provide parsed fallback, but still record answer
    if (not answer) and is_summary_query and result is not None:
        fallback = build_parsed_summary_answer(result)
        entry["answer"] = fallback
        history[-1] = {"q": question, "a": fallback}

    return entry


@app.get("/qa_history")
async def get_qa_history():
    cache = getattr(app.state, "last_analysis", None)
    if not cache:
        return {"items": []}
    hist = cache.get("qa_history") or []
    # Return structured items with no timestamps except current won't have. We'll synthesize none.
    items = []
    for item in hist:
        items.append({"question": item.get("q", ""), "answer": item.get("a", "")})
    return {"items": items}
