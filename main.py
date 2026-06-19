"""
ADALUX Meeting Agent – FastAPI server (v2)
- Kontakty z lokální SQLite databáze obcí (žádný web search před hovorem)
- Volá nejprve na úřad, představí se jako výrobce, žádá přepojení na starostu
- Haiku 4.5 pro vedení konverzace (levné)
- System prompt editovatelný přes dashboard, bez nutnosti deploye
- Kompletní přepis hovoru se zapisuje do Excelu
"""

import asyncio
import time
import uuid
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import Response, JSONResponse, FileResponse, HTMLResponse
from pydantic import BaseModel
from twilio.rest import Client

from config import settings
import database
from conversation import (
    CallSession, PRODUCTS, TERMINAL,
    opening_speech, generate_response,
    get_prompt_template, save_prompt_template,
    full_transcript_text,
)
from twiml_builder import gather_response, end_call, voicemail
from call_logger import log_call, get_log_as_rows

# ── App setup ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    Path("data").mkdir(exist_ok=True)
    Path("prompts").mkdir(exist_ok=True)
    if not Path(database.DB_PATH).exists():
        print("⚠️  data/municipalities.db neexistuje – spusť build_database.py před nasazením!")
    yield

app = FastAPI(title="ADALUX Meeting Agent", lifespan=lifespan)
twilio = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

sessions: dict[str, CallSession] = {}
call_times: dict[str, float] = {}

# ── Utility ────────────────────────────────────────────────────────────────

async def _generate(session: CallSession, speech: str) -> dict:
    return await asyncio.to_thread(generate_response, session, speech)

def _xml(content: str) -> Response:
    return Response(content=content, media_type="text/xml")

# ── Campaign orchestration ─────────────────────────────────────────────────

class CampaignRequest(BaseModel):
    count: int = 20
    product: str = "lighting"
    kraj: str | None = None
    min_population: int | None = None
    max_population: int | None = None
    delay: int = settings.CALL_DELAY_SEC


@app.post("/campaign/start")
async def campaign_start(req: CampaignRequest, bg: BackgroundTasks):
    """Vezme dávku obcí z databáze (status pending/callback) a postupně volá."""
    batch = database.get_pending_batch(
        limit=req.count, kraj=req.kraj,
        min_population=req.min_population, max_population=req.max_population,
    )
    if not batch:
        return {"started": False, "reason": "Žádné obce k zavolání (vše už zpracováno nebo filtr je příliš úzký)"}

    bg.add_task(_run_campaign, batch, req.product, req.delay)
    return {"started": True, "count": len(batch), "product": req.product}


async def _run_campaign(batch: list[dict], product: str, delay: int):
    for row in batch:
        try:
            print(f"[CALL] {row['name']} @ {row['phone']}")
            await _initiate_call(row, product)
        except Exception as e:
            print(f"[ERROR] {row['name']}: {e}")
        await asyncio.sleep(delay)


class SingleCallRequest(BaseModel):
    municipality: str
    product: str = "lighting"


@app.post("/call/single")
async def call_single(req: SingleCallRequest):
    row = database.get_municipality(req.municipality)
    if not row:
        raise HTTPException(404, f"Obec '{req.municipality}' nenalezena v databázi")
    if not row.get("phone"):
        raise HTTPException(400, "Obec nemá telefon v databázi")
    result = await _initiate_call(row, req.product)
    return result


class TestCallRequest(BaseModel):
    phone: str               # formát +420XXXXXXXXX
    product: str = "lighting"
    name: str = "TEST"       # zobrazí se jako "obec" v logu, libovolný text


@app.post("/call/test")
async def call_test(req: TestCallRequest):
    """Testovací hovor na libovolné číslo, mimo databázi obcí. Výsledek se NEZAPISUJE do SQLite."""
    fake_row = {
        "id": -1,
        "name": req.name,
        "phone": req.phone,
        "kraj": "TEST",
        "email": "",
        "population": 0,
    }
    result = await _initiate_call(fake_row, req.product)
    return result


async def _initiate_call(row: dict, product: str) -> dict:
    session = CallSession(municipality_row=row, product=product)
    call_id = uuid.uuid4().hex[:8]

    call = twilio.calls.create(
        to=row["phone"],
        from_=settings.TWILIO_PHONE_NUMBER,
        url=f"{settings.BASE_URL}/webhook/start/{call_id}",
        status_callback=f"{settings.BASE_URL}/webhook/status",
        status_callback_event=["completed", "no-answer", "busy", "failed"],
        machine_detection="Enable",
        machine_detection_timeout=4,
    )

    sessions[call.sid] = session
    sessions[f"id:{call_id}"] = session
    call_times[call.sid] = time.time()

    return {"call_sid": call.sid, "municipality": row["name"], "phone": row["phone"], "status": call.status}

# ── Twilio webhooks ────────────────────────────────────────────────────────

@app.post("/webhook/start/{call_id}")
async def webhook_start(call_id: str, request: Request):
    form = await request.form()
    call_sid = form.get("CallSid", "")
    answered_by = str(form.get("AnsweredBy", "human"))

    session = sessions.get(call_sid) or sessions.get(f"id:{call_id}")
    if not session:
        return _xml(end_call("Omlouváme se, technická chyba. Na shledanou."))

    sessions[call_sid] = session
    call_times.setdefault(call_sid, time.time())

    if "machine" in answered_by or answered_by == "fax":
        session.outcome = "voicemail"
        prod_label = PRODUCTS.get(session.product, PRODUCTS["lighting"])["label"]
        return _xml(voicemail(session.municipality_row.get("name", ""), prod_label))

    opening = opening_speech(session)
    return _xml(gather_response(opening, f"{settings.BASE_URL}/webhook/gather/{call_sid}"))


@app.post("/webhook/gather/{call_sid}")
async def webhook_gather(call_sid: str, request: Request):
    form = await request.form()
    speech = str(form.get("SpeechResult", "")).strip()
    no_input = form.get("no_input") == "1" or not speech

    session = sessions.get(call_sid)
    if not session:
        return _xml(end_call("Omlouváme se, technická chyba."))

    gather_url = f"{settings.BASE_URL}/webhook/gather/{call_sid}"

    if no_input:
        session.no_input_count += 1
        if session.no_input_count >= 2:
            msg = "Nezaslechla jsem vás, zavolám jindy. Na shledanou."
            session.transcript.append(("agent", msg))
            return _xml(end_call(msg))
        msg = "Promiňte, neslyším vás dobře, mohl/a byste zopakovat?"
        session.transcript.append(("agent", msg))
        return _xml(gather_response(msg, gather_url))

    session.no_input_count = 0
    session.transcript.append(("mayor_or_staff", speech))

    if session.turns >= settings.MAX_TURNS:
        session.outcome = "send_email"
        msg = "Děkuji za váš čas. Zašlu vám naši nabídku emailem. Přeji hezký den, na shledanou."
        session.transcript.append(("agent", msg))
        return _xml(end_call(msg))

    result = await _generate(session, speech)
    agent_speech = result.get("speech", "Omlouváme se, zavoláme jindy.")
    outcome = result.get("outcome", "ongoing")

    if outcome in TERMINAL:
        return _xml(end_call(agent_speech))

    return _xml(gather_response(agent_speech, gather_url))


@app.post("/webhook/status")
async def webhook_status(request: Request):
    form = await request.form()
    call_sid = str(form.get("CallSid", ""))
    call_status = str(form.get("CallStatus", ""))
    duration = int(form.get("CallDuration", 0))

    session = sessions.pop(call_sid, None)
    start = call_times.pop(call_sid, None)
    if not session:
        return JSONResponse({"ok": True})

    if call_status in ("no-answer", "busy", "failed") and session.outcome == "ongoing":
        session.outcome = "no_answer"

    log_call(
        municipality_row=session.municipality_row,
        outcome=session.outcome,
        call_duration_sec=duration or (int(time.time() - start) if start else 0),
        mayor_name=session.mayor_name,
        meeting_date=session.meeting_date,
        meeting_time=session.meeting_time,
        meeting_place=session.meeting_place,
        callback_date=session.callback_date,
        notes=session.notes,
        transcript=full_transcript_text(session),
    )

    print(f"[LOG] {session.municipality_row.get('name')} → {session.outcome} ({duration}s)")
    return JSONResponse({"ok": True})

# ── Prompt management (editace bez deploye) ─────────────────────────────────

@app.get("/prompt")
async def get_prompt():
    return {"prompt": get_prompt_template()}


class PromptUpdate(BaseModel):
    prompt: str


@app.post("/prompt")
async def update_prompt(req: PromptUpdate):
    save_prompt_template(req.prompt)
    return {"saved": True}

# ── Management API ─────────────────────────────────────────────────────────

@app.get("/status")
async def status():
    return {
        "active_calls": len([k for k in sessions if not k.startswith("id:")]),
        "db_stats": database.stats(),
    }


@app.get("/debug/config")
async def debug_config():
    """DOČASNÝ diagnostický endpoint - ukáže stav proměnných bez prozrazení celého klíče."""
    sid = settings.TWILIO_ACCOUNT_SID
    token = settings.TWILIO_AUTH_TOKEN
    phone = settings.TWILIO_PHONE_NUMBER
    return {
        "sid_length": len(sid),
        "sid_preview": sid[:6] + "..." if len(sid) > 6 else sid,
        "sid_starts_with_AC": sid.startswith("AC"),
        "sid_has_quotes": '"' in sid or "'" in sid,
        "token_length": len(token),
        "token_has_quotes": '"' in token or "'" in token,
        "phone_value": phone,
        "phone_has_quotes": '"' in phone or "'" in phone,
        "base_url": settings.BASE_URL,
    }


@app.get("/municipality/search")
async def municipality_search(q: str):
    return database.search_municipalities(q)


@app.post("/municipality/reset/{municipality_id}")
async def municipality_reset(municipality_id: int):
    database.reset_status(municipality_id)
    return {"reset": True}


@app.get("/log")
async def log():
    return get_log_as_rows()


@app.get("/log/download")
async def log_download():
    p = Path(settings.EXCEL_LOG_FILE)
    if not p.exists():
        raise HTTPException(404, "Log nenalezen")
    return FileResponse(p, filename="adalux_call_log.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/")
async def dashboard():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="cs">
<head><meta charset="UTF-8"><title>ADALUX Agent</title>
<style>
  body{margin:0;background:#07080a;color:#e8ecf4;font-family:system-ui;padding:24px;max-width:920px}
  h1{color:#f0c040;margin-bottom:4px}p{color:#6b7280;margin:0 0 24px}
  .card{background:#0f1114;border:1px solid #1e2230;border-radius:12px;padding:20px;margin-bottom:16px}
  label{display:block;color:#6b7280;font-size:12px;margin-bottom:6px}
  input,select,textarea{background:#1c2028;border:1px solid #1e2230;color:#e8ecf4;
    padding:8px 12px;border-radius:8px;width:100%;box-sizing:border-box;font-size:14px;margin-bottom:10px;font-family:inherit}
  textarea{height:80px;resize:vertical}
  textarea.prompt-edit{height:420px;font-family:monospace;font-size:12px;line-height:1.5}
  button{background:linear-gradient(135deg,#b8860b,#f0c040);color:#000;border:none;
    padding:10px 20px;border-radius:8px;font-weight:700;cursor:pointer;font-size:14px}
  button.ghost{background:#1c2028;color:#f0c040;border:1px solid #f0c04044}
  #status,#promptStatus{color:#22c55e;font-size:13px;margin-top:8px}
  .row{display:flex;gap:10px}
  .row > div{flex:1}
  .stat{display:inline-block;background:#1c2028;border-radius:8px;padding:8px 14px;margin:0 8px 8px 0;font-size:13px}
  .stat b{color:#f0c040}
  a{color:#f0c040}
  table{border-collapse:collapse;width:100%}
  th{border:1px solid #1e2230;padding:6px 10px;color:#d4a017;text-align:left;font-size:12px}
  td{border:1px solid #1e2230;padding:5px 10px;font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis}
</style></head>
<body>
<h1>ADALUX Meeting Agent</h1>
<p>Autonomní telefonní agent — kontakty z lokální databáze obcí</p>

<div class="card">
  <h3 style="color:#f0c040;margin:0 0 14px">📊 Stav databáze</h3>
  <div id="dbstats">Načítám...</div>
</div>

<div class="card">
  <h3 style="color:#f0c040;margin:0 0 14px">📞 Spustit kampáň</h3>
  <div class="row">
    <div>
      <label>Počet obcí k zavolání</label>
      <input type="number" id="count" value="20" min="1" max="500">
    </div>
    <div>
      <label>Produkt</label>
      <select id="product"><option value="lighting">☀️ Solární osvětlení</option>
        <option value="charging">⚡ Nabíjecí stanice</option></select>
    </div>
  </div>
  <div class="row">
    <div>
      <label>Kraj (volitelné)</label>
      <input id="kraj" placeholder="např. Moravskoslezský">
    </div>
    <div>
      <label>Min. počet obyvatel (volitelné)</label>
      <input type="number" id="minpop" placeholder="např. 500">
    </div>
  </div>
  <button onclick="startCampaign()">▶ Spustit kampáň</button>
  <div id="status"></div>
</div>

<div class="card">
  <h3 style="color:#f0c040;margin:0 0 14px">✏️ Skript hovoru (system prompt)</h3>
  <p style="margin:0 0 10px">Upravíš zde — uloží se okamžitě, bez nutnosti nasazení.</p>
  <textarea class="prompt-edit" id="promptText"></textarea>
  <button onclick="savePrompt()">💾 Uložit skript</button>
  <div id="promptStatus"></div>
</div>

<div class="card">
  <h3 style="color:#f0c040;margin:0 0 14px">📋 Log hovorů</h3>
  <button onclick="window.location='/log/download'">⬇ Stáhnout Excel</button>
  <button class="ghost" onclick="loadLog()">Obnovit</button>
  <div id="log" style="margin-top:14px;overflow-x:auto"></div>
</div>

<script>
async function loadStats(){
  const r = await fetch('/status'); const d = await r.json();
  const s = d.db_stats;
  let html = `<div class="stat">Celkem obcí: <b>${s.total}</b></div>`;
  const labels = {pending:'Čeká',done:'Hotovo',callback:'Zpětné volání'};
  for(const [k,v] of Object.entries(s.by_status)){
    html += `<div class="stat">${labels[k]||k}: <b>${v}</b></div>`;
  }
  document.getElementById('dbstats').innerHTML = html;
}

async function startCampaign(){
  const body = {
    count: parseInt(document.getElementById('count').value)||20,
    product: document.getElementById('product').value,
  };
  const kraj = document.getElementById('kraj').value.trim();
  const minpop = document.getElementById('minpop').value;
  if(kraj) body.kraj = kraj;
  if(minpop) body.min_population = parseInt(minpop);

  document.getElementById('status').textContent='⏳ Spouštím kampáň...';
  const r = await fetch('/campaign/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d = await r.json();
  document.getElementById('status').textContent = d.started ? `✅ Spuštěno ${d.count} hovorů` : `⚠️ ${d.reason}`;
}

async function loadPrompt(){
  const r = await fetch('/prompt'); const d = await r.json();
  document.getElementById('promptText').value = d.prompt;
}
async function savePrompt(){
  const prompt = document.getElementById('promptText').value;
  await fetch('/prompt',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt})});
  document.getElementById('promptStatus').textContent = '✅ Uloženo — platí pro další hovory';
  setTimeout(()=>document.getElementById('promptStatus').textContent='', 3000);
}

async function loadLog(){
  const r = await fetch('/log'); const rows = await r.json();
  if(!rows.length){document.getElementById('log').innerHTML='<span style="color:#6b7280">Žádné hovory</span>';return}
  const headers = Object.keys(rows[0]);
  let html='<table><tr>'+headers.map(h=>`<th>${h}</th>`).join('')+'</tr>';
  rows.slice().reverse().forEach(row=>{
    html+='<tr>'+headers.map(h=>`<td title="${(row[h]??'').toString().replace(/"/g,'&quot;')}">${row[h]??''}</td>`).join('')+'</tr>';
  });
  html+='</table>';
  document.getElementById('log').innerHTML=html;
}

loadStats(); loadPrompt(); loadLog();
setInterval(loadStats, 20000);
setInterval(loadLog, 30000);
</script>
</body></html>""")
