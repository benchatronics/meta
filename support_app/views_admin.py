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
    """Send an agent message into a session."""
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("invalid JSON")

    session_id = data.get("session")
    body = (data.get("body") or "").strip()
    if not session_id or not body:
        return HttpResponseBadRequest("missing fields")

    try:
        sess = ChatSession.objects.get(id=session_id)
    except ChatSession.DoesNotExist:
        return HttpResponseBadRequest("invalid session")

    agent = _current_agent(request)
    if not agent:
        return HttpResponseBadRequest("agent profile missing (is this user staff?)")

    Message.objects.create(
        session=sess,
        sender_type="agent",
        author=f"Agent • {agent.display_nickname}",
        body=body
    )
    return JsonResponse({"ok": True})


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
    """Admin console for a specific session. Auto-joins and shows live chat."""
    sid_safe = escape(str(session_id))
    BASE = "/support" if request.path.startswith("/support/") else "/admin/support"
    html = f"""
<!doctype html>
<meta charset="utf-8">
<title>Session #{sid_safe} • Support</title>
<style>
  body{{font:14px system-ui,-apple-system,Segoe UI,Roboto,Arial;margin:0;background:#f8fafc}}
  header{{display:flex;align-items:center;justify-content:space-between;background:#0b67ff;color:#fff;padding:12px 16px}}
  header h1{{font-size:16px;margin:0}}
  header .sub{{opacity:.9;font-size:12px}}
  main{{max-width:980px;margin:20px auto;background:#fff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden}}
  #log{{height:450px;overflow:auto;padding:12px;background:#fcfcfd}}
  .line{{margin:0 0 8px}}
  .sys{{color:#64748b}}
  .user{{color:#0f172a}}
  .agent{{color:#065f46}}
  footer{{display:flex;gap:8px;padding:12px;border-top:1px solid #e5e7eb;background:#fff}}
  footer input, footer button{{padding:10px;border-radius:8px;border:1px solid #e5e7eb}}
  footer input{{flex:1}}
  .actions{{display:flex;gap:8px}}
  .btn{{cursor:pointer}}
  .success{{background:#ecfdf5;border-color:#bbf7d0;color:#065f46}}
</style>
<header>
  <div>
    <h1>Session #{sid_safe}</h1>
    <div class="sub">You’ll be auto-joined.</div>
  </div>
  <div class="actions">
    <button id="resolve" class="btn success">Resolve</button>
  </div>
</header>

<main>
  <div id="log" aria-live="polite"></div>
  <footer>
    <input id="agent" placeholder="Agent name (optional, your nick is used automatically)">
    <input id="msg" placeholder="Type reply…">
    <button id="send" class="btn">Send</button>
  </footer>
</main>

<script>
const BASE = "{BASE}";
const SID  = "{sid_safe}";
const API  = {{
  messages: "/api/chat/messages",
  join:     `${{BASE}}/join`,
  send:     `${{BASE}}/send`,
  resolve:  `${{BASE}}/resolve`
}};
const log  = document.getElementById('log');
const msgI = document.getElementById('msg');
const agI  = document.getElementById('agent');
const sendB= document.getElementById('send');
const resB = document.getElementById('resolve');

let lastId = 0, timer = null;

function line(cls, who, text){{
  const p = document.createElement('p');
  p.className = 'line ' + cls;
  p.innerHTML = `<b>${{who}}</b>: ${{text}}`;
  log.appendChild(p);
  log.scrollTop = log.scrollHeight;
}}

async function joinSession(){{
  await fetch(API.join, {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{ session: SID }})
  }});
}}

async function poll(){{
  try{{
    const r = await fetch(`${{API.messages}}?session=${{encodeURIComponent(SID)}}&after_id=${{lastId}}`);
    if(!r.ok) return;
    const data = await r.json();
    (data.messages||[]).forEach(m=>{{
      const t = (m.sender_type||'system').toLowerCase();
      const cls = t === 'agent' ? 'agent' : (t === 'user' ? 'user' : 'sys');
      line(cls, m.author||t.toUpperCase(), (m.body||'').replace(/</g,'&lt;').replace(/>/g,'&gt;'));
      lastId = m.id || lastId;
    }});
  }}catch(e){{}}
}}

async function sendMsg(){{
  const body = msgI.value.trim();
  if(!body) return;
  msgI.value = '';
  const author = agI.value.trim() || 'Agent';
  line('agent', author, body.replace(/</g,'&lt;').replace(/>/g,'&gt;')); // optimistic UI
  try{{
    await fetch(API.send, {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{ session: SID, body }})
    }});
    poll();
  }}catch(e){{}}
}}

async function resolveChat(){{
  try{{
    await fetch(API.resolve, {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{ session: SID, tags: [] }})
    }});
  line('sys', 'System', 'Chat marked as resolved.');
  }}catch(e){{}}
}}

sendB.onclick = sendMsg;
resB.onclick  = resolveChat;
msgI.addEventListener('keydown', e => {{ if(e.key==='Enter') sendMsg(); }});

(async function(){{
  await joinSession();           // AUTO-JOIN on load
  await poll();
  timer = setInterval(poll, 1200);
}})();
</script>
"""
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

