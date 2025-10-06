# views_admin.py
from __future__ import annotations

import json
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.contrib.admin.views.decorators import staff_member_required
from django.utils.html import escape
from django.db.models import Count, Q
from django.utils.timezone import localtime

from .models import ChatSession, Message, Event, Agent, Tag


# -------------------------
# Helpers / constants
# -------------------------

def _current_agent(request) -> Agent | None:
    """Return the logged-in staff user's Agent profile, or None."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return None
    try:
        return request.user.agent_profile
    except Agent.DoesNotExist:
        return None


STATUSES = ["open", "waiting_agent", "agent_joined", "resolved"]


def _session_row_payload(s: ChatSession) -> dict:
    return {
        "id": s.id,
        "topic": s.topic or "",
        "status": s.status,
        "priority": s.priority,
        "created_at": localtime(s.created_at).isoformat(),
        "updated_at": localtime(s.updated_at).isoformat(),
        "agent": s.agent.display_nickname if s.agent else None,
    }


@csrf_exempt
@staff_member_required
@require_http_methods(["POST"])
def delete_message(request):
    """Hard-delete a single message in a session."""
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("invalid JSON")

    session_id = data.get("session")
    message_id = data.get("message_id")
    if not session_id or not message_id:
        return HttpResponseBadRequest("missing fields")

    try:
        sess = ChatSession.objects.get(id=session_id)
    except ChatSession.DoesNotExist:
        return HttpResponseBadRequest("invalid session")

    try:
        msg = Message.objects.get(id=message_id, session=sess)
    except Message.DoesNotExist:
        return HttpResponseBadRequest("invalid message")

    msg.delete()
    Event.objects.create(session=sess, kind="resolved", payload={"deleted_message_id": message_id})
    return JsonResponse({"ok": True})


@csrf_exempt
@staff_member_required
@require_http_methods(["POST"])
def delete_chat(request):
    """Hard-delete an entire chat session (and cascade messages/events)."""
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("invalid JSON")

    session_id = data.get("session")
    if not session_id:
        return HttpResponseBadRequest("missing session")

    try:
        sess = ChatSession.objects.get(id=session_id)
    except ChatSession.DoesNotExist:
        return HttpResponseBadRequest("invalid session")

    # If you prefer soft delete, add a 'deleted' flag to ChatSession instead.
    sess.delete()
    return JsonResponse({"ok": True})


# -------------------------
# JSON endpoints (unchanged behavior)
# -------------------------

@staff_member_required
@require_http_methods(["GET"])
def queue(request):
    """Legacy: return waiting sessions (JSON)."""
    sessions = ChatSession.objects.filter(status="waiting_agent").order_by("created_at")[:100]
    data = [{"id": s.id, "topic": s.topic, "created_at": s.created_at.isoformat()} for s in sessions]
    return JsonResponse({"waiting": data})


@csrf_exempt
@staff_member_required
@require_http_methods(["POST"])
def join(request):
    """Agent joins a session (marks agent_joined, posts system line)."""
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("invalid JSON")

    session_id = data.get("session")
    if not session_id:
        return HttpResponseBadRequest("missing session")

    try:
        sess = ChatSession.objects.get(id=session_id)
    except ChatSession.DoesNotExist:
        return HttpResponseBadRequest("invalid session")

    agent = _current_agent(request)
    if not agent:
        return HttpResponseBadRequest("agent profile missing (is this user staff?)")

    sess.agent = agent
    sess.status = "agent_joined"
    sess.save(update_fields=["agent", "status", "updated_at"])

    Event.objects.create(session=sess, kind="agent_joined", payload={"nickname": agent.display_nickname})
    Message.objects.create(
        session=sess, sender_type="system", author="System",
        body=f"Agent {agent.display_nickname} has joined the chat."
    )
    Event.objects.create(session=sess, kind="bot_suppressed", payload={})

    return JsonResponse({"ok": True, "agent": agent.display_nickname})


@csrf_exempt
@staff_member_required
@require_http_methods(["POST"])
def send(request):
    """Send an agent message into a session (idempotent via client_nonce)."""
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("invalid JSON")

    session_id   = data.get("session")
    body         = (data.get("body") or "").strip()
    client_nonce = (str(data.get("client_nonce") or "")).strip()[:64]  # NEW

    if not session_id or not body:
        return HttpResponseBadRequest("missing fields")

    try:
        sess = ChatSession.objects.get(id=session_id)
    except ChatSession.DoesNotExist:
        return HttpResponseBadRequest("invalid session")

    agent = _current_agent(request)
    if not agent:
        return HttpResponseBadRequest("agent profile missing (is this user staff?)")

    # Dedupe: if same nonce already stored for this session, don't create another row
    if client_nonce:
        exists = Message.objects.filter(session=sess, client_nonce=client_nonce).exists()
        if exists:
            return JsonResponse({"ok": True, "dedup": True})

    msg = Message.objects.create(
        session=sess,
        sender_type="agent",
        author=f"Agent • {agent.display_nickname}",
        body=body,
        visibility="public",
        client_nonce=client_nonce or None,  # NEW
    )
    return JsonResponse({"ok": True, "id": msg.id})


@csrf_exempt
@staff_member_required
@require_http_methods(["POST"])
def resolve(request):
    """Mark a session as resolved and (optionally) tag it."""
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("invalid JSON")

    session_id = data.get("session")
    tags = data.get("tags") or []
    if not session_id:
        return HttpResponseBadRequest("missing session")

    try:
        sess = ChatSession.objects.get(id=session_id)
    except ChatSession.DoesNotExist:
        return HttpResponseBadRequest("invalid session")

    sess.status = "resolved"
    sess.save(update_fields=["status", "updated_at"])

    tag_objs = []
    for t in tags:
        tag, _ = Tag.objects.get_or_create(name=t[:40])
        tag_objs.append(tag)
    if tag_objs:
        sess.tags.add(*tag_objs)

    Event.objects.create(session=sess, kind="resolved", payload={"tags": tags})
    Message.objects.create(session=sess, sender_type="system", author="System", body="Chat resolved by agent.")
    return JsonResponse({"ok": True})


# -------------------------
# Admin HTML pages (now point to /support/... by default)
# -------------------------

@staff_member_required
@require_http_methods(["GET"])
def queue_page(request):
    """Simple admin queue page: lists waiting sessions and auto-refreshes."""
    BASE = "/support" if request.path.startswith("/support/") else "/admin/support"
    html = f"""
<!doctype html>
<meta charset="utf-8">
<title>Support Queue</title>
<style>
  body{{font:14px system-ui,-apple-system,Segoe UI,Roboto,Arial;margin:0;background:#f8fafc}}
  header{{display:flex;align-items:center;justify-content:space-between;background:#0b67ff;color:#fff;padding:12px 16px}}
  header h1{{font-size:16px;margin:0}}
  main{{max-width:1000px;margin:20px auto;background:#fff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden}}
  table{{width:100%;border-collapse:collapse}}
  th,td{{padding:10px 12px;border-bottom:1px solid #eef2f7;text-align:left}}
  tr:hover{{background:#f8fafc}}
  .pill{{font-size:12px;background:#fff7ed;color:#9a3412;border:1px solid #fed7aa;padding:2px 8px;border-radius:999px}}
  .sub{{color:#64748b;font-size:12px}}
  .topbar{{display:flex;gap:10px;align-items:center}}
  .refresh{{border:0;background:#fff;color:#0b67ff;border:1px solid #dbeafe;padding:8px 10px;border-radius:8px;cursor:pointer}}
</style>
<header>
  <h1>Support Queue</h1>
  <div class="topbar">
    <button class="refresh" id="refresh">Refresh</button>
  </div>
</header>
<main>
  <table id="tbl">
    <thead>
      <tr><th>ID</th><th>Topic</th><th>Created</th><th>Status</th><th></th></tr>
    </thead>
    <tbody id="tbody">
      <tr><td colspan="5" class="sub">Loading…</td></tr>
    </tbody>
  </table>
</main>
<script>
const BASE = "{BASE}";
const T = document.getElementById('tbody');
const btn = document.getElementById('refresh');

async function load(){{
  try{{
    const r = await fetch(`${{BASE}}/queue.json`);
    if(!r.ok) return;
    const data = await r.json();
    const rows = (data.waiting || []).map(s => {{
      const created = new Date(s.created_at).toLocaleString();
      return `<tr>
        <td>#${{s.id}}</td>
        <td>${{(s.topic||'-')}}</td>
        <td><span class="sub">${{created}}</span></td>
        <td><span class="pill">Waiting agent</span></td>
        <td><a href="${{BASE}}/sessions/${{s.id}}/">Open</a></td>
      </tr>`;
    }});
    T.innerHTML = rows.length ? rows.join('') : '<tr><td colspan="5" class="sub">No sessions waiting.</td></tr>';
  }}catch(e){{}}
}}

btn.addEventListener('click', load);
load();
setInterval(load, 5000);
</script>
"""
    return HttpResponse(html)



@staff_member_required
@require_http_methods(["GET"])
def session_page(request, session_id: int):
    """Admin console for a specific session. Color-coded roles + mobile scroll + safe templating."""
    try:
        sess = ChatSession.objects.select_related("user", "agent").get(id=session_id)
    except ChatSession.DoesNotExist:
        return HttpResponseBadRequest("invalid session")

    u = getattr(sess, "user", None)
    user_id    = escape(str(getattr(u, "id", "-"))) if u else "-"
    user_phone = escape(getattr(u, "phone", "") or "-")
    user_name  = escape(getattr(u, "display_name", "") or (getattr(u, "phone", "") or "-"))
    user_ref   = escape(sess.user_ref or (sess.metadata or {}).get("user_ref", "") or "-")
    visitor_id = escape(sess.visitor_id or "-")
    guest_user = "false" if u else "true"  # injected to JS directly

    sid_safe = escape(str(session_id))
    BASE = "/support" if request.path.startswith("/support/") else "/admin/support"

    HTML = r"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Session #__SID__ • Support</title>

<div id="sp-root" class="sp-root">
<style>
  /* ================== SCOPED STYLES ================== */
  #sp-root{ --sp-muted:#64748b; --sp-blue:#dbeafe; --sp-green:#d1fae5; --sp-blue700:#1d4ed8; --sp-border:#e5e7eb; --sp-bg:#f8fafc;
            --sp-agent:#f1f5f9; --sp-user:#e6f0ff; --sp-sys:#eef2f7; --sp-danger:#fee2e2;
            --sp-header-h:60px; --sp-footer-h:64px; }

  #sp-root *{ box-sizing:border-box }
  #sp-root{ font:14px system-ui,-apple-system,Segoe UI,Roboto,Arial; background:var(--sp-bg); color:#0f172a }

  /* Header */
  #sp-root .sp-header{ position:sticky; top:0; z-index:1000; display:flex; align-items:center; justify-content:space-between;
                        background:#0b67ff; color:#fff; padding:12px 16px; height:var(--sp-header-h) }
  #sp-root .sp-title{ display:flex; flex-direction:column; gap:4px }
  #sp-root .sp-title h1{ font-size:16px; margin:0 }
  #sp-root .sp-title .sp-sub{ opacity:.95; font-size:12px }
  #sp-root .sp-info{ margin-top:4px; display:flex; gap:10px; flex-wrap:wrap; align-items:center }
  #sp-root .sp-pipe{ opacity:.7 }
  #sp-root .sp-actions{ display:flex; gap:8px; align-items:center }

  /* Buttons */
  #sp-root .sp-btn{ cursor:pointer; border:1px solid #dbeafe; background:#fff; color:#0b67ff;
                     border-radius:10px; padding:8px 12px }
  #sp-root .sp-btn:hover{ filter:brightness(.98) }
  #sp-root .sp-btn.success{ background:#ecfdf5; border-color:#bbf7d0; color:#065f46 }
  #sp-root .sp-btn.danger{ background:#fee2e2; border-color:#fecaca; color:#991b1b }

  /* Kebab (mobile) */
  #sp-root .sp-kebab{ line-height:0; background:transparent; color:#fff; border:0; cursor:pointer;
                        width:36px; height:36px; border-radius:8px; display:none; align-items:center; justify-content:center }
  #sp-root .sp-kebab:hover{ background:rgba(255,255,255,.1) }
  #sp-root .sp-kebab svg{ width:20px; height:20px }

  /* Card */
  #sp-root .sp-card{ max-width:980px; margin:16px auto; display:flex; flex-direction:column; background:#fff;
                      border:1px solid var(--sp-border); border-radius:14px; overflow:hidden }
  @media (max-width: 1020px){ #sp-root .sp-card{ margin:8px } }

  /* Log area: independently scrollable (mobile + desktop) */
  #sp-root .sp-log{
      /* Use dynamic viewport to avoid iOS 100vh bugs */
      height:calc(100dvh - var(--sp-header-h) - var(--sp-footer-h) - 32px);
      min-height:360px;
      overflow:auto !important;
      -webkit-overflow-scrolling:touch;
      overscroll-behavior:contain;
      touch-action: pan-y;
      padding:16px;
      background:#fcfcfd
  }
  @supports not (height: 100dvh){
    #sp-root .sp-log{ height:calc(100vh - var(--sp-header-h) - var(--sp-footer-h) - 32px); }
  }

  /* Message rows */
  #sp-root .sp-line{ position:relative; display:flex; margin:10px 0; gap:8px; align-items:flex-start; width:100% }

  /* Bubbles roomy so long sentences don't wrap early */
  #sp-root .sp-bubble{
      max-width:98%;
      padding:10px 14px;
      border-radius:14px;
      white-space:pre-wrap;
      word-break:normal;
      overflow-wrap:break-word;
      hyphens:auto;
      line-height:1.45;
      box-shadow:0 1px 0 rgba(0,0,0,.03);
  }
  @media (max-width:720px){
    #sp-root .sp-bubble{ max-width:99% }
  }

  /* ===== COLOR DIFFERENTIATION (no alignment dependency) =====
     - User messages: green
     - Others (agent/system/AI): blue                                      */
  #sp-root .sp-line.sp-user  .sp-bubble{ background: var(--sp-green) !important; }
  #sp-root .sp-line.sp-other .sp-bubble{ background: var(--sp-blue)  !important; }

  /* Swipe-to-delete (message) */
  #sp-root .sp-line.swiping{ cursor:grabbing }
  #sp-root .sp-holder{ display:inline-block; transform:translateX(0); transition:transform .15s ease }
  #sp-root .sp-line.delete-hint .sp-holder{ background:linear-gradient(90deg, var(--sp-danger) 0, transparent 160px); border-radius:14px }
  #sp-root .sp-trash{ position:absolute; left:8px; top:50%; transform:translateY(-50%); opacity:0;
                       transition:opacity .15s ease; font-size:12px; color:#991b1b }
  #sp-root .sp-line.delete-ready .sp-trash{ opacity:1 }

  /* Composer */
  #sp-root .sp-footer{ display:flex; gap:8px; padding:12px; border-top:1px solid var(--sp-border); background:#fff;
                        height:var(--sp-footer-h); align-items:center }
  #sp-root .sp-footer input, #sp-root .sp-footer button{ padding:12px; border-radius:10px; border:1px solid var(--sp-border) }
  #sp-root .sp-footer input{ flex:1 }
  #sp-root .sp-btn-send{ background:#0b67ff; color:#fff; border-color:#dbeafe }
  #sp-root .sp-btn-send:hover{ filter:brightness(1.03) }

  /* Bottom Sheet (mobile) */
  #sp-root .sp-sheet-bg{ position:fixed; inset:0; background:rgba(0,0,0,.35); backdrop-filter:saturate(120%) blur(1px);
                          display:none; z-index:50 }
  #sp-root .sp-sheet{ position:fixed; left:0; right:0; bottom:-100%; z-index:51; background:#fff;
                       border-top-left-radius:16px; border-top-right-radius:16px; box-shadow:0 -10px 30px rgba(0,0,0,.25);
                       transition:bottom .18s ease; max-height:70vh; overflow:auto }
  #sp-root .sp-sheet.open{ bottom:0 }
  #sp-root .sp-sheet-hd{ padding:12px 16px; border-bottom:1px solid var(--sp-border); display:flex; align-items:center; justify-content:space-between }
  #sp-root .sp-sheet-ttl{ font-weight:700 }
  #sp-root .sp-sheet-x{ border:0; background:transparent; font-size:22px; line-height:1.2; cursor:pointer }
  #sp-root .sp-sheet-bd{ padding:12px 16px; display:grid; gap:10px }
  #sp-root .sp-kv{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; font-size:13px }
  #sp-root .sp-kv b{ min-width:84px; color:#111 }
  #sp-root .sp-div{ height:1px; background:var(--sp-border); margin:4px 0 8px }

  /* Desktop vs Mobile helpers */
  #sp-root .sp-desktop{ display:block }
  #sp-root .sp-mobile{ display:none }

  @media (max-width: 720px){
    #sp-root .sp-desktop{ display:none }
    #sp-root .sp-mobile{ display:flex }
    #sp-root .sp-header{ padding:10px 12px }
    #sp-root .sp-log{ height:calc(100dvh - var(--sp-header-h) - var(--sp-footer-h) - 12px); padding:12px }
    #sp-root .sp-kebab{ display:inline-flex }
    #sp-root .sp-actions{ gap:6px }
    #sp-root .sp-btn{ padding:7px 10px }
  }

  /* Safety: if some global CSS set overflow hidden on the page */
  html, body { height:auto; overflow:auto !important; }
  /* Ensure our container never traps scroll */
  #sp-root, #sp-root .sp-card{ overflow:visible; }
</style>

  <header class="sp-header">
    <div class="sp-title">
      <h1>Session #__SID__</h1>
      <div class="sp-sub sp-desktop">You’ll be auto-joined.</div>
      <div class="sp-sub sp-info sp-desktop">
        <span><b>User:</b> __USER_NAME__</span>
        <span class="sp-pipe">|</span>
        <span><b>ID:</b> __USER_ID__</span>
        <span class="sp-pipe">|</span>
        <span><b>Phone:</b> __USER_PHONE__</span>
        <span class="sp-pipe">|</span>
        <span><b>User Ref:</b> __USER_REF__</span>
        <span class="sp-pipe">|</span>
        <span><b>Visitor ID:</b> __VISITOR_ID__</span>
      </div>
    </div>

    <div class="sp-actions">
      <!-- Mobile menu trigger -->
      <button id="sp-kebab" class="sp-kebab sp-mobile" aria-label="More">
        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <circle cx="12" cy="5" r="2"></circle>
          <circle cx="12" cy="12" r="2"></circle>
          <circle cx="12" cy="19" r="2"></circle>
        </svg>
      </button>

      <!-- Desktop-only Delete Chat -->
      <button id="sp-delete-chat-desktop" class="sp-btn danger sp-desktop">Delete Chat</button>

      <!-- Leave + Resolve -->
      <button id="sp-leave" class="sp-btn">Leave</button>
      <button id="sp-resolve" class="sp-btn success">Resolve</button>
    </div>
  </header>

  <!-- Mobile bottom sheet -->
  <div id="sp-sheet-bg" class="sp-sheet-bg"></div>
  <section id="sp-sheet" class="sp-sheet" role="dialog" aria-modal="true" aria-labelledby="sp-sheet-title" aria-hidden="true">
    <div class="sp-sheet-hd">
      <div id="sp-sheet-title" class="sp-sheet-ttl">Session options</div>
      <button id="sp-sheet-x" class="sp-sheet-x" aria-label="Close">×</button>
    </div>
    <div class="sp-sheet-bd">
      <div class="sp-kv"><b>User:</b> <span>__USER_NAME__</span></div>
      <div class="sp-kv"><b>ID:</b> <span>__USER_ID__</span></div>
      <div class="sp-kv"><b>Phone:</b> <span>__USER_PHONE__</span></div>
      <div class="sp-kv"><b>User Ref:</b> <span>__USER_REF__</span></div>
      <div class="sp-kv"><b>Visitor ID:</b> <span>__VISITOR_ID__</span></div>
      <div class="sp-div"></div>
      <button id="sp-delete-chat" class="sp-btn danger" style="width:100%">Delete Chat</button>
    </div>
  </section>

  <main class="sp-card">
    <div id="sp-log" class="sp-log" aria-live="polite"></div>
    <footer class="sp-footer">
      <input id="sp-msg" placeholder="Type reply…">
      <button id="sp-send" class="sp-btn sp-btn-send">Send</button>
    </footer>
  </main>
</div> <!-- /#sp-root -->

<script>
/* ================== SCOPED JS ================== */
const BASE = "__BASE__";
const SID  = "__SID__";
const GUEST = __GUEST_BOOL__; // true if user is not registered
const API  = {
  messages: "/api/chat/messages",
  join:     `${BASE}/join`,
  send:     `${BASE}/send`,
  resolve:  `${BASE}/resolve`,
  leave:    `${BASE}/leave`,           // ✅ new
  delmsg:   `${BASE}/delete-message`,
  delchat:  `${BASE}/delete-chat`
};

const $ = (id) => document.getElementById(id);

const logEl    = $("sp-log");
const msgI     = $("sp-msg");
const sendB    = $("sp-send");
const resolveB = $("sp-resolve");
const leaveB   = $("sp-leave");         // ✅ new
const delChatB = $("sp-delete-chat");   // mobile sheet delete
const delChatDesktopB = $("sp-delete-chat-desktop");  // desktop header delete

// Sheet elements
const kebabB = $("sp-kebab");
const sheet  = $("sp-sheet");
const sheetBg= $("sp-sheet-bg");
const sheetX = $("sp-sheet-x");

let lastId = 0, timer = null;

// ---- one-time guard + nonce + optimistic map + send lock ----
if (!window.__SP_BOUND__) window.__SP_BOUND__ = true;
function makeNonce(){
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return String(Date.now()) + "-" + Math.random().toString(16).slice(2);
}
const optimistic = new Map(); // nonce -> DOM row
let sendLock = false;

function safe(s){ return (s||'').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

/* Swipe-to-delete (message) — updated to NOT block vertical scrolling */
function attachSwipe(rowEl, msgId){
  const bubble = rowEl.querySelector('.sp-bubble');
  if (!bubble) return;

  const holder = document.createElement('span');
  holder.className = 'sp-holder';
  bubble.parentNode.insertBefore(holder, bubble);
  holder.appendChild(bubble);

  const ghost = document.createElement('div');
  ghost.className = 'sp-trash';
  ghost.textContent = '↤ delete';
  rowEl.appendChild(ghost);

  let sx=0, sy=0, dx=0, dy=0, dragging=false, threshold = 120, maxPull = 160, horizontal=false;

  function onDown(e){
    const pt=('touches' in e)? e.touches[0] : e;
    sx = pt.clientX; sy = pt.clientY; dx = 0; dy = 0; dragging = true; horizontal = false;
    rowEl.classList.add('swiping','delete-hint');
  }
  function onMove(e){
    if(!dragging) return;
    const pt=('touches' in e)? e.touches[0] : e;
    dx = pt.clientX - sx; dy = pt.clientY - sy;

    if (!horizontal && Math.abs(dx) > Math.abs(dy) + 6) horizontal = true;

    if (!horizontal){
      return; // vertical scroll wins
    }

    if (dx > 0) dx = 0;          // only left
    if (dx < -maxPull) dx = -maxPull;
    holder.style.transform = `translateX(${dx}px)`;
    if (dx <= -threshold) rowEl.classList.add('delete-ready'); else rowEl.classList.remove('delete-ready');

    if (e.cancelable) e.preventDefault();
  }
  async function onUp(){
    if(!dragging) return;
    dragging=false;
    const shouldDelete = horizontal && dx <= -threshold;

    holder.style.transform = 'translateX(0px)';
    rowEl.classList.remove('swiping','delete-ready','delete-hint');

    if (shouldDelete && msgId != null) {
      const ok = confirm('Delete this message?');
      if (ok) await deleteMsg(msgId);
    }
  }

  rowEl.addEventListener('mousedown', onDown, {passive:true});
  rowEl.addEventListener('mousemove', onMove,  {passive:false});
  rowEl.addEventListener('mouseup',   onUp,    {passive:true});
  rowEl.addEventListener('touchstart',onDown,  {passive:true});
  rowEl.addEventListener('touchmove', onMove,  {passive:false});
  rowEl.addEventListener('touchend',  onUp,    {passive:true});
}

/* ====== RECONCILE-AWARE RENDER ======
   - If m.client_nonce matches an optimistic row, upgrade it instead of appending. */
function renderLine(m){
  if (m.client_nonce) {
    const opt = optimistic.get(m.client_nonce);
    if (opt && opt.isConnected) {
      optimistic.delete(m.client_nonce);
      opt.dataset.nonce = ''; // consumed

      const t = (m.sender_type||'system').toLowerCase();
      const role = (t === 'user') ? 'sp-user' : 'sp-other';
      opt.className = 'sp-line ' + role;

      let prefix = '';
      if (t === 'system' || t === 'bot') {
        prefix = '<b>System</b>: ';
      } else if (t === 'user' && GUEST) {
        const name = safe(m.author||'Guest');
        prefix = `<b>${name}</b>: `;
      }

      const bubble = opt.querySelector('.sp-bubble');
      if (bubble) bubble.innerHTML = `${prefix}${safe(m.body)}`;

      logEl.scrollTop = logEl.scrollHeight;
      attachSwipe(opt, m.id);
      return;
    }
  }

  // Normal render path
  const t = (m.sender_type||'system').toLowerCase();
  const role = (t === 'user') ? 'sp-user' : 'sp-other';

  let prefix = '';
  if (t === 'system' || t === 'bot') {
    prefix = '<b>System</b>: ';
  } else if (t === 'user' && GUEST) {
    const name = safe(m.author||'Guest');
    prefix = `<b>${name}</b>: `;
  }

  const row = document.createElement('div');
  row.className = 'sp-line ' + role;

  const bubble = document.createElement('div');
  bubble.className = 'sp-bubble';
  bubble.innerHTML = `${prefix}${safe(m.body)}`;
  row.appendChild(bubble);

  logEl.appendChild(row);
  logEl.scrollTop = logEl.scrollHeight;

  attachSwipe(row, m.id);
}

async function deleteMsg(id){
  try {
    const r = await fetch(API.delmsg, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ session: SID, message_id: id })
    });
    if (!r.ok) return;
    lastId = 0;
    logEl.innerHTML = '';
    await poll(true);
  } catch(e) {}
}

async function deleteChat(){
  if (!confirm('Delete ENTIRE chat? This cannot be undone.')) return;
  try {
    const r = await fetch(API.delchat, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ session: SID })
    });
    if (!r.ok) return;
    window.location.href = "__BASE__/dashboard/";
  } catch(e) {}
}

// ---- optimistic with nonce ----
function lineOptimistic(text, nonce){
  const row = document.createElement('div');
  row.className = 'sp-line sp-other';  // staff is "other" => blue
  if (nonce) row.dataset.nonce = nonce;
  const bubble = document.createElement('div');
  bubble.className = 'sp-bubble';
  bubble.innerHTML = safe(text);
  row.appendChild(bubble);
  logEl.appendChild(row);
  logEl.scrollTop = logEl.scrollHeight;
  attachSwipe(row, null); // msg id will arrive on next poll
  if (nonce) optimistic.set(nonce, row);
}

async function joinSession(){
  await fetch(API.join, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ session: SID })
  });
}

async function poll(initial=false){
  try {
    const r = await fetch(`${API.messages}?session=${encodeURIComponent(SID)}&after_id=${initial?0:lastId}`);
    if(!r.ok) return;
    const data = await r.json();
    (data.messages||[]).forEach(m=>{ renderLine(m); lastId = m.id || lastId; });
  } catch(e) {}
}

// ---- send with nonce + lock; polling will reconcile ----
async function sendMsg(){
  if (sendLock) return;
  const body = (msgI.value||'').trim();
  if(!body) return;

  sendLock = true;
  const nonce = makeNonce();
  msgI.value = '';
  lineOptimistic(body, nonce);

  try {
    await fetch(API.send, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ session: SID, body, client_nonce: nonce })
    });
  } catch(e) {
    // rollback optimistic on hard failure
    const row = optimistic.get(nonce);
    if (row && row.isConnected) row.remove();
    optimistic.delete(nonce);
  } finally {
    sendLock = false;
  }
}

async function resolveChat(){
  try {
    await fetch(API.resolve, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ session: SID, tags: [] })
    });
    const row = document.createElement('div');
    row.className = 'sp-line sp-other';
    const bubble = document.createElement('div');
    bubble.className = 'sp-bubble';
    bubble.textContent = 'Chat marked as resolved.';
    row.appendChild(bubble);
    logEl.appendChild(row);
  } catch(e) {}
}

// ✅ Leave chat (explicit)
async function leaveChat(){
  try {
    await fetch(API.leave, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ session: SID })
    });
    const row = document.createElement('div');
    row.className = 'sp-line sp-other';
    const bubble = document.createElement('div');
    bubble.className = 'sp-bubble';
    bubble.textContent = 'You left this chat.';
    row.appendChild(bubble);
    logEl.appendChild(row);
    setTimeout(()=>{ window.location.href = `${BASE}/dashboard/`; }, 500);
  } catch(e) {}
}

/* Wire up */
sendB.onclick = sendMsg;
resolveB.onclick  = resolveChat;
if (leaveB) leaveB.onclick = leaveChat;                 // ✅ new
if (delChatB) delChatB.onclick = deleteChat;            // mobile sheet delete
if (delChatDesktopB) delChatDesktopB.onclick = deleteChat; // desktop header delete
msgI.addEventListener('keydown', e => { if(e.key==='Enter') sendMsg(); });

// Single timer instance
(async function(){
  await joinSession();
  await poll(true);
  if (timer) clearInterval(timer);
  timer = setInterval(poll, 1200);
})();

/* Auto-leave on tab close (best-effort) */
window.addEventListener('beforeunload', function(){
  try{
    const blob = new Blob([JSON.stringify({ session: SID })], { type: 'application/json' });
    if (navigator.sendBeacon) navigator.sendBeacon(API.leave, blob);
  }catch(_){}
});

/* Bottom Sheet (mobile) */
(function(){
  function openSheet(){
    if (!sheet) return;
    sheet.setAttribute('aria-hidden','false');
    sheet.classList.add('open');
    if (sheetBg){ sheetBg.style.display = 'block'; }
  }
  function closeSheet(){
    if (!sheet) return;
    sheet.setAttribute('aria-hidden','true');
    sheet.classList.remove('open');
    if (sheetBg){ sheetBg.style.display = 'none'; }
  }
  if (kebabB) kebabB.addEventListener('click', (e)=>{ e.stopPropagation(); openSheet(); });
  if (sheetX)  sheetX.addEventListener('click', (e)=>{ e.stopPropagation(); closeSheet(); });
  if (sheetBg) sheetBg.addEventListener('click', closeSheet);
  document.addEventListener('keydown', (e)=>{ if (e.key === 'Escape') closeSheet(); });
})();

/* Mobile scroll guard: if <html> or <body> got locked, unlock it softly */
(function(){
  try{
    const d = document;
    const hs = getComputedStyle(d.documentElement).overflowY;
    const bs = getComputedStyle(d.body).overflowY;
    if ((hs === 'hidden' || hs === 'clip') || (bs === 'hidden' || bs === 'clip')) {
      d.documentElement.style.overflowY = 'auto';
      d.body.style.overflowY = 'auto';
    }
  }catch(_){}
})();
</script>
</html>"""

    html = (HTML
            .replace("__SID__", sid_safe)
            .replace("__BASE__", BASE)
            .replace("__GUEST_BOOL__", guest_user)
            .replace("__USER_NAME__", user_name)
            .replace("__USER_ID__", user_id)
            .replace("__USER_PHONE__", user_phone)
            .replace("__USER_REF__", user_ref)
            .replace("__VISITOR_ID__", visitor_id)
            )

    return HttpResponse(html)

# -------------------------
# Unified JSON + Dashboard with tabs
# -------------------------

@staff_member_required
@require_http_methods(["GET"])
def sessions_json(request):
    """
    Unified JSON for dashboard tabs, with optional ?status=<...>&q=<search> and counts.
    """
    status = (request.GET.get("status") or "waiting_agent").strip()
    if status not in STATUSES:
        status = "waiting_agent"

    q = (request.GET.get("q") or "").strip()

    qs = ChatSession.objects.filter(status=status)

    if q:
        # Search by numeric id OR text fields (topic, agent name parts)
        id_filter = Q()
        if q.isdigit():
            id_filter = Q(id=int(q))
        qs = qs.filter(
            id_filter |
            Q(topic__icontains=q) |
            Q(agent__user__username__icontains=q) |
            Q(agent__user__first_name__icontains=q) |
            Q(agent__user__last_name__icontains=q)
        )

    qs = qs.order_by("-updated_at")[:300]
    rows = [_session_row_payload(s) for s in qs]

    # counts per status for tab badges
    counts = dict(ChatSession.objects.values("status").annotate(n=Count("id")).values_list("status", "n"))
    for st in STATUSES:
        counts.setdefault(st, 0)

    return JsonResponse({"status": status, "rows": rows, "counts": counts})


@staff_member_required
@require_http_methods(["GET"])
def dashboard_page(request):
    """Tabbed dashboard: Open / Waiting / Agent Joined / Resolved."""
    BASE = "/support" if request.path.startswith("/support/") else "/admin/support"
    html = f"""
<!doctype html>
<meta charset="utf-8">
<title>Support Dashboard</title>
<style>
  :root{{--blue:#0b67ff;--muted:#64748b;--bg:#f8fafc;--border:#e5e7eb}}
  *{{box-sizing:border-box}}
  body{{font:14px system-ui,-apple-system,Segoe UI,Roboto,Arial;margin:0;background:var(--bg)}}
  header{{display:flex;align-items:center;gap:12px;justify-content:space-between;background:var(--blue);color:#fff;padding:12px 16px}}
  header h1{{font-size:16px;margin:0}}
  .bar{{display:flex;gap:8px;align-items:center}}
  .bar input{{border:0;border-radius:8px;padding:8px 10px;min-width:260px}}
  .bar button{{border:1px solid #dbeafe;background:#fff;color:var(--blue);border-radius:8px;padding:8px 10px;cursor:pointer}}
  main{{max-width:1100px;margin:18px auto;background:#fff;border:1px solid var(--border);border-radius:12px;overflow:hidden}}
  nav.tabs{{display:flex;gap:4px;border-bottom:1px solid var(--border);background:#fcfcfd}}
  .tab{{padding:10px 12px;cursor:pointer}}
  .tab.active{{border-bottom:2px solid var(--blue);font-weight:600}}
  .count{{font-size:12px;background:#eef2ff;color:#1d4ed8;border:1px solid #dbeafe;padding:2px 6px;border-radius:999px;margin-left:6px}}
  table{{width:100%;border-collapse:collapse}}
  th,td{{padding:10px 12px;border-bottom:1px solid #eef2f7;text-align:left}}
  tr:hover{{background:#f8fafc}}
  .sub{{color:var(--muted);font-size:12px}}
  .pill{{font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid}}
  .pill.open{{background:#eef2ff;color:#1d4ed8;border-color:#c7d2fe}}
  .pill.waiting_agent{{background:#fff7ed;color:#9a3412;border-color:#fed7aa}}
  .pill.agent_joined{{background:#ecfdf5;color:#065f46;border-color:#bbf7d0}}
  .pill.resolved{{background:#f1f5f9;color:#334155;border-color:#e2e8f0}}
  .right{{text-align:right}}
  .link{{color:#0b67ff;text-decoration:none}}
</style>
<header>
  <h1>Support Dashboard</h1>
  <div class="bar">
    <input id="search" placeholder="Search by ID, topic, or agent…">
    <button id="refresh">Refresh</button>
  </div>
</header>
<main>
  <nav class="tabs" id="tabs">
    <div class="tab" data-status="open">Open <span class="count" id="c-open">0</span></div>
    <div class="tab active" data-status="waiting_agent">Waiting <span class="count" id="c-waiting_agent">0</span></div>
    <div class="tab" data-status="agent_joined">Agent Joined <span class="count" id="c-agent_joined">0</span></div>
    <div class="tab" data-status="resolved">Resolved <span class="count" id="c-resolved">0</span></div>
  </nav>
  <table>
    <thead>
      <tr>
        <th>ID</th><th>Topic</th><th>Status</th><th>Agent</th><th>Priority</th>
        <th>Updated</th><th class="right"></th>
      </tr>
    </thead>
    <tbody id="tbody">
      <tr><td colspan="7" class="sub">Loading…</td></tr>
    </tbody>
  </table>
</main>

<script>
const BASE   = "{BASE}";
const T      = document.getElementById('tbody');
const tabs   = document.getElementById('tabs');
const btn    = document.getElementById('refresh');
const qInput = document.getElementById('search');

let CURRENT = 'waiting_agent';
let timer   = null;

function fmt(t){{ try{{ return new Date(t).toLocaleString(); }}catch(e){{ return t; }} }}
// Escape JS template braces for Python f-strings:
function pill(st){{ return `<span class="pill ${{st}}">${{st.replace('_',' ')}}</span>`; }}
function esc(s){{ return (s||'').replace(/[&<>]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c])); }}

function paintCounts(counts){{
  document.getElementById('c-open').textContent           = counts.open || 0;
  document.getElementById('c-waiting_agent').textContent  = counts.waiting_agent || 0;
  document.getElementById('c-agent_joined').textContent   = counts.agent_joined || 0;
  document.getElementById('c-resolved').textContent       = counts.resolved || 0;
}}

async function load(){{
  const p = new URLSearchParams({{status: CURRENT}});
  const q = qInput.value.trim(); if (q) p.set('q', q);
  try{{
    const r = await fetch(`${{BASE}}/sessions.json?` + p.toString());
    if(!r.ok) return;
    const data = await r.json();
    paintCounts(data.counts || {{}});
    const rows = (data.rows||[]).map(s => `
      <tr>
        <td>#${{s.id}}</td>
        <td>${{esc(s.topic) || '-'}}</td>
        <td>${{pill(s.status)}}</td>
        <td>${{esc(s.agent) || '<span class="sub">—</span>'}}</td>
        <td>${{esc(s.priority)}}</td>
        <td><span class="sub">${{fmt(s.updated_at)}}</span></td>
        <td class="right"><a class="link" href="${{BASE}}/sessions/${{s.id}}/">Open</a></td>
      </tr>
    `);
    T.innerHTML = rows.length ? rows.join('') : '<tr><td colspan="7" class="sub">No sessions.</td></tr>';
  }}catch(e){{}}
}}

tabs.addEventListener('click', (e)=>{{
  const t = e.target.closest('.tab');
  if(!t) return;
  document.querySelectorAll('.tab').forEach(el=>el.classList.remove('active'));
  t.classList.add('active');
  CURRENT = t.dataset.status;
  load();
}});

btn.addEventListener('click', load);
qInput.addEventListener('keydown', (e)=>{{ if(e.key==='Enter') load(); }});

load();
timer && clearInterval(timer);
timer = setInterval(load, 5000);
</script>
"""
    return HttpResponse(html)


# views_admin.py
@csrf_exempt
@staff_member_required
@require_http_methods(["POST"])
def leave(request):
    """
    Agent leaves a session: unassign agent and set status.
    If the user had requested an agent, return to 'waiting_agent', otherwise 'open'.
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("invalid JSON")

    session_id = data.get("session")
    if not session_id:
        return HttpResponseBadRequest("missing session")

    try:
        sess = ChatSession.objects.get(id=session_id)
    except ChatSession.DoesNotExist:
        return HttpResponseBadRequest("invalid session")

    agent = _current_agent(request)
    if not agent:
        return HttpResponseBadRequest("agent profile missing (is this user staff?)")

    # Only the assigned agent (or staff) can leave; proceed anyway to unlock
    sess.agent = None
    # If a human was requested earlier, keep waiting; else mark open
    sess.status = "waiting_agent" if Event.objects.filter(session=sess, kind="agent_requested").exists() else "open"
    sess.save(update_fields=["agent", "status", "updated_at"])

    Event.objects.create(session=sess, kind="agent_left", payload={"nickname": agent.display_nickname})
    Message.objects.create(
        session=sess,
        sender_type="system",
        author="System",
        body=f"Agent {agent.display_nickname} left the chat.",
    )
    return JsonResponse({"ok": True, "status": sess.status})

