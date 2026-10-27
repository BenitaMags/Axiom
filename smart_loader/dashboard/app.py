"""
dashboard/app.py
─────────────────
FastAPI dashboard server for AXIOM.

Endpoints:
  GET /             → Interactive HTML dashboard with live token charts
  GET /api/tokens   → Raw token usage JSON (for external consumers)
  GET /api/security → Latest security scan results
  GET /health       → Health check

Run with: uvicorn dashboard.app:app --reload --port 7788
"""

from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn
except ImportError:
    print("Install: pip install fastapi uvicorn")
    sys.exit(1)

app = FastAPI(title="AXIOM Dashboard", version="1.0.0")

LOGS_DIR     = Path(os.environ.get("AXIOM_LOGS_DIR", "./axiom_logs"))
TOKEN_LOG    = LOGS_DIR / "token_usage.jsonl"
SESSIONS_DIR = LOGS_DIR / "sessions"


# ── Data helpers ───────────────────────────────────────────────────────────────

def _load_token_records(limit: int = 2000) -> list[dict]:
    if not TOKEN_LOG.exists():
        return []
    lines = TOKEN_LOG.read_text().strip().splitlines()
    records = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except Exception:
            pass
    return records


def _fmt_ts(value) -> str:
    """Render a timestamp that may be an epoch float or an ISO string.

    ``token_tracker`` writes ``time.time()`` — a float — while ``telemetry``
    writes ``datetime.utcnow().isoformat()``. This function previously assumed
    the string form and sliced it directly, so ``/api/tokens`` raised
    ``TypeError: 'float' object is not subscriptable`` on every record in a
    real log and the whole dashboard came up empty. Both forms are handled
    here rather than by changing the writer, since existing logs are already
    on disk in the numeric form.
    """
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
        except (OverflowError, OSError, ValueError):
            return ""
    return str(value or "")[:19].replace("T", " ")


def _get_stats(records: list[dict]) -> dict:
    if not records:
        return {}

    by_agent: dict = {}
    timeline: list = []
    by_session: dict = {}

    for r in records:
        a   = r.get("agent", "unknown")
        inp = r.get("input_tokens", 0)
        out = r.get("output_tokens", 0)
        sid = r.get("session_id", "?")

        if a not in by_agent:
            by_agent[a] = {"input": 0, "output": 0, "calls": 0}
        by_agent[a]["input"]  += inp
        by_agent[a]["output"] += out
        by_agent[a]["calls"]  += 1

        timeline.append({
            "ts":    _fmt_ts(r.get("timestamp")),
            "agent": a,
            "in":    inp,
            "out":   out,
            "total": inp + out,
            "sid":   sid[:6],
            "model": r.get("model", ""),
        })

        if sid not in by_session:
            by_session[sid] = {
                "input": 0, "output": 0, "calls": 0,
                "file": r.get("source_file", ""),
                "ts":   _fmt_ts(r.get("timestamp")),
            }
        by_session[sid]["input"]  += inp
        by_session[sid]["output"] += out
        by_session[sid]["calls"]  += 1

    return {
        # .get with a default: a truncated final line (the process was killed
        # mid-write) otherwise takes the whole endpoint down with a KeyError.
        "total_input":  sum(r.get("input_tokens", 0)  for r in records),
        "total_output": sum(r.get("output_tokens", 0) for r in records),
        "total_tokens": sum(r.get("input_tokens", 0) + r.get("output_tokens", 0)
                            for r in records),
        "total_calls":  len(records),
        "by_agent":     by_agent,
        "timeline":     timeline[-200:],
        "by_session":   dict(list(by_session.items())[-30:]),
    }


def _load_sessions() -> list[dict]:
    if not SESSIONS_DIR.exists():
        return []
    sessions = []
    for p in sorted(SESSIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:20]:
        try:
            sessions.append(json.loads(p.read_text()))
        except Exception:
            pass
    return sessions


# ── API endpoints ──────────────────────────────────────────────────────────────

@app.get("/api/tokens")
def api_tokens():
    records = _load_token_records()
    return JSONResponse(_get_stats(records))


@app.get("/api/sessions")
def api_sessions():
    return JSONResponse({"sessions": _load_sessions()})


def _load_metrics(limit: int = 200) -> dict:
    """Per-run outcome metrics, newest first, plus a rollup.

    Sessions written before ``metrics`` existed simply lack the key. They are
    counted under ``not_recorded`` rather than being folded into the rates:
    a run whose verification status was never captured is not the same thing
    as a run that failed verification, and averaging the two together would
    quietly understate every rate on this page.
    """
    if not SESSIONS_DIR.exists():
        return {"runs": [], "summary": {}, "available": False}

    paths = sorted(SESSIONS_DIR.glob("*.json"),
                   key=lambda x: x.stat().st_mtime, reverse=True)[:limit]
    runs, legacy = [], 0
    for path in paths:
        try:
            doc = json.loads(path.read_text())
        except Exception:
            continue
        metrics = doc.get("metrics")
        if not metrics:
            legacy += 1
            continue
        verdict = doc.get("verdict") or {}
        runs.append({
            "session":     doc.get("session_id", "?"),
            "ts":          _fmt_ts(doc.get("timestamp")),
            "file":        os.path.basename(doc.get("source_file") or ""),
            "model":       doc.get("model", ""),
            "replaced":    verdict.get("packages_replaced", 0),
            "decisions":   verdict.get("decisions_made", 0),
            "summary":     verdict.get("summary", ""),
            **metrics,
        })

    def _rate(pred, pool):
        pool = [r for r in pool if pred(r) is not None]
        return {"n": len(pool), "yes": sum(1 for r in pool if pred(r))}

    diversified = [r for r in runs if r.get("diversified")]
    composites = [r["diversity"]["composite"] for r in diversified
                  if (r.get("diversity") or {}).get("composite") is not None]
    cfgs = [r["diversity"]["cfg_shape"] for r in diversified
            if (r.get("diversity") or {}).get("cfg_shape") is not None]

    summary = {
        "runs":            len(runs),
        "not_recorded":    legacy,
        "verified":        _rate(lambda r: r.get("verified"), runs),
        "diversified":     len(diversified),
        "with_migration":  sum(1 for r in runs if r.get("replaced", 0) > 0),
        "repair_cycles":   sum(r.get("repair_cycles", 0) for r in runs),
        "mean_composite":  round(sum(composites) / len(composites), 4) if composites else None,
        "mean_cfg_shape":  round(sum(cfgs) / len(cfgs), 4) if cfgs else None,
        "failing_stages":  _tally(r.get("failing_stage") for r in runs),
        "transformations": _tally(t for r in runs for t in (r.get("transformations") or [])),
    }
    return {"runs": runs, "summary": summary, "available": bool(runs)}


def _tally(values) -> dict:
    counts: dict = {}
    for v in values:
        if not v:
            continue
        counts[v] = counts.get(v, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


@app.get("/api/metrics")
def api_metrics():
    return JSONResponse(_load_metrics())


@app.get("/health")
def health():
    return {"status": "ok", "logs_dir": str(LOGS_DIR)}


# ── Dashboard HTML ─────────────────────────────────────────────────────────────

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AXIOM — Token Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600&display=swap');

  :root {
    --bg:       #0a0c10;
    --surface:  #111318;
    --border:   #1e2330;
    --accent:   #00e5ff;
    --accent2:  #ff4081;
    --accent3:  #69ff47;
    --text:     #e2e8f0;
    --muted:    #64748b;
    --font-mono: 'Space Mono', monospace;
    --font-body: 'DM Sans', sans-serif;
    --critical: #ff1744;
    --high:     #ff6d00;
    --medium:   #ffd600;
    --low:      #00e676;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-body);
    min-height: 100vh;
    overflow-x: hidden;
  }

  /* Scanline effect */
  body::before {
    content: '';
    position: fixed; inset: 0;
    background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,229,255,0.015) 2px, rgba(0,229,255,0.015) 4px);
    pointer-events: none; z-index: 999;
  }

  header {
    display: flex; align-items: center; gap: 1.5rem;
    padding: 1.25rem 2rem;
    border-bottom: 1px solid var(--border);
    background: linear-gradient(90deg, rgba(0,229,255,0.06) 0%, transparent 60%);
  }
  .logo {
    font-family: var(--font-mono);
    font-size: 1.4rem;
    font-weight: 700;
    color: var(--accent);
    letter-spacing: 0.15em;
    text-shadow: 0 0 20px rgba(0,229,255,0.5);
  }
  .logo span { color: var(--accent2); }
  .subtitle { color: var(--muted); font-size: 0.8rem; letter-spacing: 0.05em; }

  .refresh-btn {
    margin-left: auto;
    background: transparent;
    border: 1px solid var(--accent);
    color: var(--accent);
    padding: 0.4rem 1rem;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    cursor: pointer;
    transition: all 0.2s;
    letter-spacing: 0.1em;
  }
  .refresh-btn:hover { background: rgba(0,229,255,0.1); box-shadow: 0 0 12px rgba(0,229,255,0.3); }

  .live-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--accent3);
    animation: pulse 2s infinite;
    flex-shrink: 0;
  }
  @keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:0.4;transform:scale(0.8)} }

  main { padding: 2rem; max-width: 1600px; margin: 0 auto; }

  /* KPI row */
  .kpi-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1rem; margin-bottom: 2rem;
  }
  .kpi {
    background: var(--surface);
    border: 1px solid var(--border);
    border-top: 2px solid var(--accent);
    padding: 1.25rem 1.5rem;
    position: relative; overflow: hidden;
  }
  .kpi::before {
    content: '';
    position: absolute; top: 0; left: 0; right: 0; height: 1px;
    background: linear-gradient(90deg, transparent, var(--accent), transparent);
    opacity: 0.6;
  }
  .kpi-label { font-size: 0.7rem; letter-spacing: 0.12em; color: var(--muted); text-transform: uppercase; margin-bottom: 0.5rem; }
  .kpi-value { font-family: var(--font-mono); font-size: 1.9rem; font-weight: 700; color: var(--accent); line-height: 1; }
  .kpi-sub   { font-size: 0.7rem; color: var(--muted); margin-top: 0.3rem; font-family: var(--font-mono); }
  .kpi.danger  { border-top-color: var(--accent2); }
  .kpi.danger .kpi-value { color: var(--accent2); }
  .kpi.green   { border-top-color: var(--accent3); }
  .kpi.green .kpi-value { color: var(--accent3); }

  /* Chart grid */
  .chart-grid {
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 1.5rem; margin-bottom: 1.5rem;
  }
  .chart-grid-3 {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 1.5rem; margin-bottom: 1.5rem;
  }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    padding: 1.5rem;
  }
  .card-title {
    font-family: var(--font-mono);
    font-size: 0.7rem;
    letter-spacing: 0.12em;
    color: var(--accent);
    text-transform: uppercase;
    margin-bottom: 1.25rem;
    display: flex; align-items: center; gap: 0.5rem;
  }
  .card-title::after {
    content: ''; flex: 1; height: 1px;
    background: linear-gradient(90deg, var(--border), transparent);
  }
  canvas { max-height: 280px; }

  /* Sessions table */
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th {
    font-family: var(--font-mono); font-size: 0.65rem; letter-spacing: 0.1em;
    color: var(--muted); text-transform: uppercase; text-align: left;
    padding: 0.6rem 0.75rem; border-bottom: 1px solid var(--border);
  }
  td { padding: 0.55rem 0.75rem; border-bottom: 1px solid rgba(255,255,255,0.04); font-family: var(--font-mono); font-size: 0.78rem; }
  tr:hover td { background: rgba(0,229,255,0.03); }
  .badge {
    display: inline-block; padding: 0.15rem 0.5rem;
    font-size: 0.65rem; font-weight: 700; letter-spacing: 0.08em;
    border-radius: 2px; text-transform: uppercase;
  }
  .badge-critical { background: rgba(255,23,68,0.15);  color: var(--critical); border: 1px solid rgba(255,23,68,0.3); }
  .badge-high     { background: rgba(255,109,0,0.15);  color: var(--high);     border: 1px solid rgba(255,109,0,0.3); }
  .badge-medium   { background: rgba(255,214,0,0.15);  color: var(--medium);   border: 1px solid rgba(255,214,0,0.3); }
  .badge-low      { background: rgba(0,230,118,0.15);  color: var(--low);      border: 1px solid rgba(0,230,118,0.3); }
  .badge-yes      { background: rgba(105,255,71,0.15); color: var(--accent3);  border: 1px solid rgba(105,255,71,0.3); }
  .badge-no       { background: rgba(255,64,129,0.15); color: var(--accent2);  border: 1px solid rgba(255,64,129,0.3); }

  .empty { text-align: center; padding: 3rem; color: var(--muted); font-family: var(--font-mono); font-size: 0.85rem; }
  .section-title { font-family: var(--font-mono); font-size: 0.75rem; letter-spacing: 0.15em; color: var(--muted); text-transform: uppercase; margin-bottom: 1rem; }

  .timeline-row { max-height: 420px; }
  .timeline-row canvas { max-height: 360px; }

  @media (max-width: 900px) {
    .chart-grid   { grid-template-columns: 1fr; }
    .chart-grid-3 { grid-template-columns: 1fr 1fr; }
  }
</style>
</head>
<body>

<header>
  <div class="live-dot"></div>
  <div>
    <div class="logo">AX<span>IO</span>M</div>
    <div class="subtitle">Token Usage · Security · Pipeline Monitor</div>
  </div>
  <button class="refresh-btn" onclick="loadData()">⟳ REFRESH</button>
</header>

<main>
  <!-- KPI Row -->
  <div class="kpi-row" id="kpiRow">
    <div class="kpi"><div class="kpi-label">Total Tokens</div><div class="kpi-value" id="kTotal">—</div><div class="kpi-sub">all time</div></div>
    <div class="kpi danger"><div class="kpi-label">Input Tokens</div><div class="kpi-value" id="kInput">—</div><div class="kpi-sub">prompts sent</div></div>
    <div class="kpi green"><div class="kpi-label">Output Tokens</div><div class="kpi-value" id="kOutput">—</div><div class="kpi-sub">completions recv'd</div></div>
    <div class="kpi"><div class="kpi-label">LLM Calls</div><div class="kpi-value" id="kCalls">—</div><div class="kpi-sub">total invocations</div></div>
    <div class="kpi"><div class="kpi-label">Sessions</div><div class="kpi-value" id="kSessions">—</div><div class="kpi-sub">analyses run</div></div>
    <div class="kpi danger"><div class="kpi-label">Avg Tokens/Call</div><div class="kpi-value" id="kAvg">—</div><div class="kpi-sub">input + output</div></div>
  </div>

  <!-- Timeline chart (full width) -->
  <div class="card timeline-row" style="margin-bottom:1.5rem">
    <div class="card-title">📈 Token Usage Over Time</div>
    <canvas id="timelineChart"></canvas>
  </div>

  <!-- Chart grid -->
  <div class="chart-grid">
    <div class="card">
      <div class="card-title">📊 Input vs Output by Agent</div>
      <canvas id="agentChart"></canvas>
    </div>
    <div class="card">
      <div class="card-title">🥧 Token Distribution</div>
      <canvas id="pieChart"></canvas>
    </div>
  </div>

  <div class="chart-grid-3">
    <div class="card">
      <div class="card-title">⚡ Calls per Agent</div>
      <canvas id="callsChart"></canvas>
    </div>
    <div class="card">
      <div class="card-title">🔥 Tokens per Session</div>
      <canvas id="sessionChart"></canvas>
    </div>
    <div class="card">
      <div class="card-title">📉 Input/Output Ratio</div>
      <canvas id="ratioChart"></canvas>
    </div>
  </div>

  <!-- ── Migration metrics ──────────────────────────────────────────────────
       Token counts say what a run COST. These say what it ACHIEVED, which is
       the question a client running AXIOM on their own code actually has.
       Hidden entirely until at least one run has recorded metrics, so an
       installation that has never run the pipeline shows nothing rather than
       a wall of zeros that reads like failure. -->
  <div id="metricsPanel" style="display:none">

    <div class="kpi-row" style="margin-top:1.5rem">
      <div class="kpi"><div class="kpi-label">Runs Measured</div><div class="kpi-value" id="mRuns">—</div><div class="kpi-sub" id="mLegacy">&nbsp;</div></div>
      <div class="kpi green"><div class="kpi-label">Verified</div><div class="kpi-value" id="mVerified">—</div><div class="kpi-sub">passed the verifier</div></div>
      <div class="kpi"><div class="kpi-label">Migrations</div><div class="kpi-value" id="mMigrated">—</div><div class="kpi-sub">a package was replaced</div></div>
      <div class="kpi"><div class="kpi-label">Diversified</div><div class="kpi-value" id="mDiversified">—</div><div class="kpi-sub">logic restructured</div></div>
      <div class="kpi green"><div class="kpi-label">Mean CFG Shape</div><div class="kpi-value" id="mCfg">—</div><div class="kpi-sub">1.0 = fully restructured</div></div>
      <div class="kpi danger"><div class="kpi-label">Repair Cycles</div><div class="kpi-value" id="mRepairs">—</div><div class="kpi-sub">verifier sent work back</div></div>
    </div>

    <div class="card" style="margin-bottom:1.5rem">
      <div class="card-title">🔎 Per-Run Outcomes</div>
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Session</th><th>File</th><th>Substitution</th><th>Verified</th>
            <th>Equivalence</th><th>Diversified</th><th>CFG</th><th>Lines</th>
            <th>Repairs</th><th>Failed At</th>
          </tr></thead>
          <tbody id="metricsBody"></tbody>
        </table>
      </div>
    </div>

    <div class="chart-grid">
      <div class="card">
        <div class="card-title">🧬 Transformations Applied</div>
        <canvas id="transformChart"></canvas>
      </div>
      <div class="card">
        <div class="card-title">🚧 Where Runs Failed</div>
        <canvas id="failStageChart"></canvas>
      </div>
    </div>

  </div>

</main>

<script>
const AGENT_COLORS = {
  resolver:            '#00e5ff',
  axiom:               '#ff4081',
  connector:           '#69ff47',
  connector_transform: '#ffab40',
  security:            '#e040fb',
  profiler:            '#40c4ff',
};
const DEFAULT_COLOR = '#64748b';

let charts = {};

function fmt(n) {
  if (n === undefined || n === null) return '—';
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return n.toString();
}

function agentColor(a) { return AGENT_COLORS[a] || DEFAULT_COLOR; }

function destroyCharts() {
  Object.values(charts).forEach(c => c && c.destroy());
  charts = {};
}

function buildTimeline(timeline) {
  const ctx = document.getElementById('timelineChart').getContext('2d');
  if (charts.timeline) charts.timeline.destroy();

  // Bucket by minute, accumulate totals
  const buckets = {};
  timeline.forEach(p => {
    const k = p.ts.substring(0,16);
    if (!buckets[k]) buckets[k] = { input:0, output:0 };
    buckets[k].input  += p.in;
    buckets[k].output += p.out;
  });
  const labels  = Object.keys(buckets).slice(-60);
  const inputs  = labels.map(k => buckets[k].input);
  const outputs = labels.map(k => buckets[k].output);

  charts.timeline = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label:'Input tokens',  data:inputs,  borderColor:'#ff4081', backgroundColor:'rgba(255,64,129,0.08)', borderWidth:1.5, pointRadius:2, fill:true, tension:0.35 },
        { label:'Output tokens', data:outputs, borderColor:'#00e5ff', backgroundColor:'rgba(0,229,255,0.08)', borderWidth:1.5, pointRadius:2, fill:true, tension:0.35 },
      ]
    },
    options: {
      responsive:true, maintainAspectRatio:true,
      interaction: { mode:'index', intersect:false },
      plugins: { legend:{ labels:{ color:'#94a3b8', font:{family:'Space Mono', size:11} } } },
      scales: {
        x: { ticks:{ color:'#475569', font:{family:'Space Mono',size:9}, maxRotation:45, maxTicksLimit:15 }, grid:{ color:'#1e2330' } },
        y: { ticks:{ color:'#475569', font:{family:'Space Mono',size:9} }, grid:{ color:'#1e2330' } },
      },
    }
  });
}

function buildAgentChart(byAgent) {
  const ctx = document.getElementById('agentChart').getContext('2d');
  if (charts.agent) charts.agent.destroy();
  const agents  = Object.keys(byAgent);
  const inputs  = agents.map(a => byAgent[a].input);
  const outputs = agents.map(a => byAgent[a].output);
  charts.agent = new Chart(ctx, {
    type:'bar',
    data:{
      labels:agents,
      datasets:[
        { label:'Input',  data:inputs,  backgroundColor:agents.map(a => agentColor(a)+'55'), borderColor:agents.map(a=>agentColor(a)), borderWidth:1.5 },
        { label:'Output', data:outputs, backgroundColor:agents.map(a => agentColor(a)+'22'), borderColor:agents.map(a=>agentColor(a)), borderWidth:1, borderDash:[3,3] },
      ]
    },
    options:{
      responsive:true,maintainAspectRatio:true,
      plugins:{ legend:{ labels:{ color:'#94a3b8', font:{family:'Space Mono',size:10} } } },
      scales:{
        x:{ ticks:{ color:'#475569', font:{family:'Space Mono',size:9} }, grid:{ color:'#1e2330' } },
        y:{ ticks:{ color:'#475569', font:{family:'Space Mono',size:9} }, grid:{ color:'#1e2330' } },
      }
    }
  });
}

function buildPieChart(byAgent) {
  const ctx = document.getElementById('pieChart').getContext('2d');
  if (charts.pie) charts.pie.destroy();
  const agents = Object.keys(byAgent);
  const totals = agents.map(a => byAgent[a].input + byAgent[a].output);
  charts.pie = new Chart(ctx, {
    type:'doughnut',
    data:{ labels:agents, datasets:[{ data:totals, backgroundColor:agents.map(a=>agentColor(a)+'cc'), borderColor:'#0a0c10', borderWidth:3 }] },
    options:{
      responsive:true, maintainAspectRatio:true,
      plugins:{ legend:{ position:'right', labels:{ color:'#94a3b8', font:{family:'Space Mono',size:10}, padding:12 } } }
    }
  });
}

function buildCallsChart(byAgent) {
  const ctx = document.getElementById('callsChart').getContext('2d');
  if (charts.calls) charts.calls.destroy();
  const agents = Object.keys(byAgent);
  charts.calls = new Chart(ctx, {
    type:'bar',
    data:{
      labels:agents,
      datasets:[{ label:'LLM Calls', data:agents.map(a=>byAgent[a].calls), backgroundColor:agents.map(a=>agentColor(a)+'88'), borderColor:agents.map(a=>agentColor(a)), borderWidth:1.5 }]
    },
    options:{
      responsive:true, maintainAspectRatio:true, indexAxis:'y',
      plugins:{ legend:{ display:false } },
      scales:{
        x:{ ticks:{ color:'#475569', font:{family:'Space Mono',size:9} }, grid:{ color:'#1e2330' } },
        y:{ ticks:{ color:'#94a3b8', font:{family:'Space Mono',size:9} }, grid:{ color:'#1e2330' } },
      }
    }
  });
}

function buildSessionChart(bySessions) {
  const ctx = document.getElementById('sessionChart').getContext('2d');
  if (charts.sess) charts.sess.destroy();
  const ids    = Object.keys(bySessions).slice(-15);
  const totals = ids.map(s => bySessions[s].input + bySessions[s].output);
  charts.sess = new Chart(ctx, {
    type:'bar',
    data:{
      labels: ids.map(s=>s.substring(0,6)),
      datasets:[{ label:'Tokens', data:totals, backgroundColor:'rgba(0,229,255,0.35)', borderColor:'#00e5ff', borderWidth:1.5 }]
    },
    options:{
      responsive:true, maintainAspectRatio:true,
      plugins:{ legend:{ display:false } },
      scales:{
        x:{ ticks:{ color:'#475569', font:{family:'Space Mono',size:8} }, grid:{ color:'#1e2330' } },
        y:{ ticks:{ color:'#475569', font:{family:'Space Mono',size:9} }, grid:{ color:'#1e2330' } },
      }
    }
  });
}

function buildRatioChart(byAgent) {
  const ctx = document.getElementById('ratioChart').getContext('2d');
  if (charts.ratio) charts.ratio.destroy();
  const agents = Object.keys(byAgent);
  const ratios = agents.map(a => {
    const { input, output } = byAgent[a];
    const total = input + output;
    return total ? Math.round((output / total) * 100) : 0;
  });
  charts.ratio = new Chart(ctx, {
    type:'radar',
    data:{
      labels:agents,
      datasets:[{ label:'Output%', data:ratios, backgroundColor:'rgba(105,255,71,0.15)', borderColor:'#69ff47', borderWidth:1.5, pointBackgroundColor:'#69ff47' }]
    },
    options:{
      responsive:true, maintainAspectRatio:true,
      plugins:{ legend:{ labels:{ color:'#94a3b8', font:{family:'Space Mono',size:10} } } },
      scales:{ r:{ ticks:{ color:'#475569', backdropColor:'transparent', font:{size:8} }, grid:{ color:'#1e2330' }, angleLines:{ color:'#1e2330' }, pointLabels:{ color:'#94a3b8', font:{family:'Space Mono',size:9} } } }
    }
  });
}

// ── Migration metrics ────────────────────────────────────────────────────────

// null means "never established" — a run with the verifier disabled has no
// verdict, which is a different statement from a run that failed one. The
// dash keeps those apart on screen instead of showing both as a red cross.
function triState(v, yes, no) {
  if (v === null || v === undefined) return '<span style="color:var(--muted)">—</span>';
  return v ? `<span class="badge badge-yes">${yes}</span>`
           : `<span class="badge badge-no">${no}</span>`;
}

function buildBarChart(id, tally, color) {
  const labels = Object.keys(tally);
  if (!labels.length) return;
  charts[id] = new Chart(document.getElementById(id), {
    type: 'bar',
    data: { labels, datasets: [{ data: labels.map(k=>tally[k]), backgroundColor: color,
                                 borderRadius: 3, barPercentage: 0.6 }] },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { beginAtZero: true, ticks: { precision: 0, color: '#64748b' },
             grid: { color: 'rgba(255,255,255,0.04)' } },
        y: { ticks: { color: '#94a3b8', font: { size: 11 } }, grid: { display: false } },
      },
    },
  });
}

function renderMetrics(data) {
  const panel = document.getElementById('metricsPanel');
  if (!data.available) { panel.style.display = 'none'; return; }
  panel.style.display = 'block';

  const s = data.summary || {};
  document.getElementById('mRuns').textContent        = fmt(s.runs || 0);
  document.getElementById('mLegacy').textContent      =
    s.not_recorded ? `${s.not_recorded} older run(s) without metrics` : 'all runs recorded';
  const v = s.verified || { n: 0, yes: 0 };
  document.getElementById('mVerified').textContent    = v.n ? `${v.yes}/${v.n}` : '—';
  document.getElementById('mMigrated').textContent    = fmt(s.with_migration || 0);
  document.getElementById('mDiversified').textContent = fmt(s.diversified || 0);
  document.getElementById('mCfg').textContent         =
    s.mean_cfg_shape === null || s.mean_cfg_shape === undefined ? '—' : s.mean_cfg_shape.toFixed(2);
  document.getElementById('mRepairs').textContent     = fmt(s.repair_cycles || 0);

  const body = document.getElementById('metricsBody');
  body.innerHTML = (data.runs || []).map(r => {
    const d = r.diversity || {};
    const cfg = d.cfg_shape === undefined ? '—' : d.cfg_shape.toFixed(2);
    const lines = (r.lines_before && r.lines_after)
      ? `${r.lines_before} → ${r.lines_after}` : '—';
    return `<tr>
      <td style="color:var(--accent)">${r.session}</td>
      <td>${r.file || '—'}</td>
      <td style="color:var(--muted)">${r.summary || '—'}</td>
      <td>${triState(r.verified, 'PASS', 'FAIL')}</td>
      <td>${r.equivalence || '<span style="color:var(--muted)">—</span>'}</td>
      <td>${triState(r.diversified, 'YES', 'NO')}</td>
      <td>${cfg}</td>
      <td>${lines}</td>
      <td>${r.repair_cycles || 0}</td>
      <td style="color:${r.failing_stage ? 'var(--accent2)' : 'var(--muted)'}">${r.failing_stage || '—'}</td>
    </tr>`;
  }).join('');

  buildBarChart('transformChart', s.transformations || {}, '#69ff47');
  buildBarChart('failStageChart', s.failing_stages  || {}, '#ff4081');
}

async function loadData() {
  try {
    const tokData = await fetch('/api/tokens').then(r=>r.json());

    // KPIs
    document.getElementById('kTotal').textContent    = fmt(tokData.total_tokens || 0);
    document.getElementById('kInput').textContent    = fmt(tokData.total_input  || 0);
    document.getElementById('kOutput').textContent   = fmt(tokData.total_output || 0);
    document.getElementById('kCalls').textContent    = fmt(tokData.total_calls  || 0);
    document.getElementById('kSessions').textContent = fmt(Object.keys(tokData.by_session || {}).length);
    const calls = tokData.total_calls || 1;
    const total = (tokData.total_input || 0) + (tokData.total_output || 0);
    document.getElementById('kAvg').textContent = fmt(Math.round(total / calls));

    const byAgent = tokData.by_agent || {};
    const timeline = tokData.timeline || [];
    const bySess   = tokData.by_session || {};

    // Rebuild charts
    if (timeline.length) buildTimeline(timeline);
    if (Object.keys(byAgent).length) {
      buildAgentChart(byAgent);
      buildPieChart(byAgent);
      buildCallsChart(byAgent);
      buildRatioChart(byAgent);
    }
    if (Object.keys(bySess).length) buildSessionChart(bySess);

    // Fetched separately so a metrics failure can never blank the token
    // charts, which are the part that works on every installation.
    try {
      renderMetrics(await fetch('/api/metrics').then(r=>r.json()));
    } catch(me) {
      console.error('Metrics load error:', me);
    }

  } catch(e) {
    console.error('Dashboard load error:', e);
  }
}

// Auto-refresh every 15s
loadData();
setInterval(loadData, 15000);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML


# ── Entry point ────────────────────────────────────────────────────────────────

def serve(host: str = "127.0.0.1", port: int = 7788):
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    serve()
