'use strict';

const state = { data: null, symbol: null, chartBars: [], hoverIndex: null };
const $ = (id) => document.getElementById(id);
const qsa = (sel, root=document) => [...root.querySelectorAll(sel)];

function node(tag, cls='', text=null) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== null && text !== undefined) e.textContent = String(text);
  return e;
}
function cleanNumber(v, digits=2) {
  const n = Number(v); return Number.isFinite(n) ? n.toLocaleString(undefined,{maximumFractionDigits:digits,minimumFractionDigits:digits}) : '—';
}
function pct(v, digits=2) {
  const n = Number(v); return Number.isFinite(n) ? `${(n*100).toFixed(digits)}%` : '—';
}
function money(v) {
  const n = Number(v); return Number.isFinite(n) ? new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',maximumFractionDigits:2}).format(n) : '—';
}
function shortHash(v) { return typeof v === 'string' && v ? `${v.slice(0,9)}…${v.slice(-6)}` : '—'; }
function fmtTs(v) {
  if (!v) return '—'; const d = new Date(v); return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString(undefined,{month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
}
function boolLabel(v, t='YES', f='NO') { return v === true ? t : v === false ? f : 'UNVERIFIED'; }
function statusClass(v) {
  const s = String(v||'').toUpperCase();
  if (['PASS','PASSED','HEALTHY','VERIFIED','ACTIVE','ENFORCED','OK','TRUE','RECORDED'].some(x=>s.includes(x))) return 'good';
  if (['HALT','FAIL','BLOCK','DANGER','ERROR','FALSE'].some(x=>s.includes(x))) return 'danger';
  return 'warn';
}
function pill(text, kind='') { return node('span',`pill ${kind}`.trim(),text); }
function kvRow(parent, label, value, cls='') { parent.append(node('span','',label), node('span',cls,value)); }
function sectionHead(title, status, kind='') {
  const wrap=node('div','section-head'), h=node('h3','',title); wrap.append(h); if(status) wrap.append(pill(status,kind)); return wrap;
}
function detailCard(title, cls='') { const c=node('article',`detail-card ${cls}`.trim()); c.append(node('h3','',title)); return c; }
function addListItem(parent, title, description='') { const item=node('div','list-item'); item.append(node('strong','',title)); if(description)item.append(node('p','',description)); parent.append(item); }

async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest('SHA-256', buffer);
  return [...new Uint8Array(digest)].map(b=>b.toString(16).padStart(2,'0')).join('');
}

async function loadVerifiedSnapshot() {
  const bust = `v=${Date.now()}`;
  const [dataResp, hashResp] = await Promise.all([
    fetch(`./data/dashboard.json?${bust}`,{cache:'no-store'}),
    fetch(`./data/dashboard.json.sha256?${bust}`,{cache:'no-store'})
  ]);
  if (!dataResp.ok || !hashResp.ok) throw new Error('Snapshot or integrity sidecar unavailable');
  const raw = await dataResp.arrayBuffer();
  const expected = (await hashResp.text()).trim().toLowerCase();
  const actual = await sha256Hex(raw);
  if (!expected || actual !== expected) throw new Error('Snapshot SHA-256 verification failed');
  const text = new TextDecoder().decode(raw);
  const parsed = JSON.parse(text);
  if (!parsed || parsed.schema_version !== 1 || parsed.project !== "Trip's") throw new Error('Unsupported dashboard snapshot schema');
  return parsed;
}

function showIntegrityLock(message) {
  $('integrityLockMessage').textContent = message || 'Operational state is hidden rather than guessed.';
  $('integrityLock').hidden = false;
}
function hideIntegrityLock() { $('integrityLock').hidden = true; }

function renderStatusStrip(d) {
  const box=$('statusStrip'); box.replaceChildren();
  const sup=d.supervisor||{}, guardian=d.guardian||{}, sys=d.system||{}, evo=d.evolution||{};
  const items=[
    ['Paper Only','SIMULATION MODE',sys.paper_only?'good':'danger'],
    ['Truth Layer Active',sys.truth_layer_active?'VALIDATED ANALYSIS INPUT':'INPUT NOT VERIFIED',sys.truth_layer_active?'good':'warn'],
    [`Guardian ${guardian.status||'UNVERIFIED'}`,'HEALTH IS NOT TRADE AUTHORITY',statusClass(guardian.status)],
    ['Evolution Shadow Mode',evo.mode||'UNVERIFIED',evo.mode==='SHADOW_RESEARCH_ONLY'?'good':'warn'],
    [sup.delivery_state==='OUTBOX_READY_NOT_DELIVERED'?'Supervisor Outbox Ready':sup.delivery_state==='NO_UNDELIVERED_EVENTS'?'Supervisor Local Outbox Clear':'Supervisor Unverified',sup.delivery_state==='OUTBOX_READY_NOT_DELIVERED'?'NOT DELIVERED':sup.delivery_state==='NO_UNDELIVERED_EVENTS'?'NO PENDING LOCAL EVIDENCE':'TRANSPORT STATE UNKNOWN',sup.relay_passed?'good':'warn'],
    ['No Live Execution','REAL CAPITAL PATH ABSENT','warn']
  ];
  for(const [title,sub,kind] of items){const c=node('div',`status-chip ${kind}`);c.append(node('span','status-dot'));const t=node('div','status-chip__text');t.append(node('strong','',title),node('small','',sub));c.append(t);box.append(c)}
}

function renderMetrics(d) {
  const grid=$('metricsGrid'); grid.replaceChildren();
  const p=d.portfolio||{}, diag=d.diagnostics||{}, esc=d.escalations||[], s=d.strategy||{};
  const metrics=[
    ['Open Positions',p.positions?.length ?? '—','No live-money exposure',''],
    ['Pending Orders',p.pending_entries?.length ?? '—','Paper simulation queue',''],
    ['Daily Diagnostics',diag.passed===true?'PASS':diag.passed===false?'FAIL':'UNVERIFIED',diag.action||'No verified report',diag.passed===true?'good':diag.passed===false?'danger':'warn'],
    ['Test Suite',diag.test_total?`${diag.test_passed ?? '—'} / ${diag.test_total}`:'UNVERIFIED',diag.test_total?'Latest deep diagnostic':'No verified test count',diag.test_total&&diag.test_passed===diag.test_total?'good':'warn'],
    ['Escalations',esc.length,'Open unresolved items',esc.length?'warn':''],
    ['Strategy Verdict',s.verdict||'UNVERIFIED','Live trading not authorized','warn']
  ];
  for(const [label,value,sub,kind] of metrics){const c=node('article','metric');c.append(node('div','metric__label',label),node('div',`metric__value ${kind}`.trim(),value),node('div','metric__sub',sub));grid.append(c)}
}

function renderGuardianCard(d) {
  const g=d.guardian||{}, c=$('guardianCard'); c.replaceChildren(sectionHead('Guardian',g.status||'UNVERIFIED',statusClass(g.status)));
  const kv=node('div','kv');
  kvRow(kv,'Health gate',boolLabel(g.health_gate_passed,'PASSED','FAILED'),g.health_gate_passed?'status-text good':'status-text warn');
  kvRow(kv,'Build integrity',boolLabel(d.system?.build_integrity?.verified,'VERIFIED','FAILED'));
  kvRow(kv,'Config integrity',boolLabel(d.system?.config_integrity?.verified,'VERIFIED','FAILED'));
  kvRow(kv,'Runtime integrity',boolLabel(d.system?.runtime_integrity?.verified,'VERIFIED','UNVERIFIED'));
  kvRow(kv,'Repairs',String(g.repairs?.length ?? 0)); kvRow(kv,'Snapshot',fmtTs(g.generated_at)); c.append(kv);
  c.append(node('div','notice','Guardian can veto new activity on critical health failure; it cannot authorize a trade.'));
}

function renderDecisionCard(d) {
  const c=$('decisionCard'); c.replaceChildren(sectionHead('Decision Mirror','ACTIVE','good'));
  const s=d.supervisor||{}, kv=node('div','kv');
  kvRow(kv,'Recent mirrored decisions',String(d.activity?.length ?? 0));
  kvRow(kv,'Undelivered evidence',s.undelivered_events ?? '—',s.undelivered_events>0?'status-text warn':'status-text good');
  kvRow(kv,'Last acknowledged seq',s.last_ack_seq ?? '—');
  kvRow(kv,'Last decision seq',s.last_decision_seq ?? '—');
  kvRow(kv,'Counsel authority',s.advice_execution_authority||'ZERO','status-text good'); c.append(kv);
  c.append(node('div','notice','Observed facts and inference remain separate; raw evidence is not exposed on this public control surface.'));
}

function renderEvolutionCard(d) {
  const e=d.evolution||{}, c=$('evolutionCard'); c.replaceChildren(sectionHead('Evolution Lab','SHADOW MODE','good'));
  const kv=node('div','kv');
  kvRow(kv,'Challenger proposals',String(e.proposals?.length ?? 0)); kvRow(kv,'Champion mutated',boolLabel(e.champion_mutated,'YES','NO'),e.champion_mutated?'status-text danger':'status-text good');
  kvRow(kv,'Auto promotion',boolLabel(e.auto_promotion_allowed,'ALLOWED','BLOCKED'),e.auto_promotion_allowed?'status-text danger':'status-text good');
  kvRow(kv,'Strategy verdict',d.strategy?.verdict||'UNVERIFIED','status-text warn'); c.append(kv);
  c.append(node('div','notice','Promotion requires evidence, hard diagnostics and explicit human approval.'));
}

function renderRiskCard(d) {
  const r=d.risk||{}, c=$('riskCard'); c.replaceChildren(sectionHead('Risk & Guardrails','ENFORCED','good'));
  const kv=node('div','kv');
  kvRow(kv,'Risk / trade',pct(r.max_risk_per_trade_pct)); kvRow(kv,'Daily loss limit',pct(r.max_daily_loss_pct)); kvRow(kv,'Total exposure cap',pct(r.max_total_exposure_pct)); kvRow(kv,'Hard drawdown halt',pct(r.halt_on_drawdown_pct)); kvRow(kv,'No martingale',boolLabel(r.no_martingale,'ENFORCED','NO')); kvRow(kv,'Constitution lock',boolLabel(r.constitution_lock,'ACTIVE','NO')); c.append(kv);
}

function buildActivityTable(activity, limit=null) {
  const wrap=node('div','table-wrap'), table=node('table','table'), thead=node('thead'), hr=node('tr');
  ['TIME','EVENT','SUBSYSTEM','SYMBOL','ACTION','REVIEW'].forEach(x=>hr.append(node('th','',x)));thead.append(hr);table.append(thead);const tbody=node('tbody');
  const rows=(activity||[]).slice(0,limit||activity?.length||0);
  if(!rows.length){const tr=node('tr'),td=node('td','empty','No mirrored decisions in this snapshot.');td.colSpan=6;tr.append(td);tbody.append(tr)}
  for(const e of rows){const tr=node('tr');tr.append(node('td','',fmtTs(e.ts)),node('td','',e.event_kind||'—'),node('td','',e.subsystem||'—'),node('td','',e.symbol||'—'),node('td','',e.action||'—'),node('td',e.requires_supervisor_review?'status-text warn':'muted',e.requires_supervisor_review?'REVIEW':'LOGGED'));tbody.append(tr)}
  table.append(tbody);wrap.append(table);return wrap;
}
function renderActivityCard(d) { const c=$('activityCard'); c.replaceChildren(sectionHead('Recent Activity','MIRRORED','good'),buildActivityTable(d.activity,8)); }

function renderDiagnosticsCard(d) {
  const x=d.diagnostics||{}, c=$('diagnosticsCard'); c.replaceChildren(sectionHead('Hard Test Results',x.passed===true?'PASS':x.passed===false?'FAIL':'UNVERIFIED',x.passed===true?'good':x.passed===false?'danger':'warn'));
  const list=node('div','list'); for(const check of x.checks||[]){const item=node('div','list-item');item.append(node('strong',check.passed?'status-text good':'status-text danger',`${check.passed?'✓':'×'} ${String(check.name||'check').replaceAll('_',' ')}`));list.append(item)} if(!(x.checks||[]).length)addListItem(list,'No verified deep diagnostics','Trip’s will not invent a pass count.'); c.append(list);
}
function renderSupervisorCard(d) {
  const s=d.supervisor||{}, c=$('supervisorCard'); c.replaceChildren(sectionHead('Supervisor Relay',s.relay_passed?'READY':'REVIEW',s.relay_passed?'good':'warn')); const kv=node('div','kv');
  kvRow(kv,'Transport',s.transport_mode||'UNVERIFIED');kvRow(kv,'Delivery',s.delivery_state||'UNVERIFIED',s.delivery_state==='OUTBOX_READY_NOT_DELIVERED'?'status-text warn':'');kvRow(kv,'Undelivered',s.undelivered_events ?? '—');kvRow(kv,'Advice order authority',s.advice_execution_authority||'ZERO','status-text good');c.append(kv);c.append(node('div','notice','A local outbox is not proof that ChatGPT received or reviewed an event.'));
}

function renderSymbolTabs(d) {
  const tabs=$('symbolTabs'); tabs.replaceChildren(); const symbols=Object.keys(d.market||{}); if(!state.symbol||!symbols.includes(state.symbol)) state.symbol=symbols[0]||null;
  for(const symbol of symbols){const b=node('button',`segment ${symbol===state.symbol?'is-active':''}`.trim(),symbol);b.type='button';b.setAttribute('role','tab');b.setAttribute('aria-selected',symbol===state.symbol?'true':'false');b.addEventListener('click',()=>{state.symbol=symbol;renderSymbolTabs(d);renderMarketChart(d)});tabs.append(b)}
}

function renderMarketChart(d) {
  const m=(d.market||{})[state.symbol]||{}; const bar=m.last_closed_bar||{}; const source=m.source_kind?`${m.source||'unknown'} / ${m.source_kind}`:'UNVERIFIED';
  const meta=$('marketMeta'); meta.replaceChildren();meta.append(node('strong','',state.symbol||'—'),node('span','',bar.close!==undefined?`Close ${cleanNumber(bar.close,4)}`:'Close —'),node('span','',`Signal ${m.direction||'—'} · ${m.signal_score==null?'—':Number(m.signal_score).toFixed(2)}`),pill(m.candle_context||'NO CANDLE READ',m.constitution_passed?'good':'warn'));
  $('chartSource').textContent=`Source ${source}`;$('chartTruth').textContent=`Trade eligibility ${m.trusted_for_trade?'VERIFIED':'BLOCKED/UNVERIFIED'}`;
  state.chartBars=Array.isArray(m.chart_series)?m.chart_series:[];state.hoverIndex=null; drawChart();
  $('chartEmpty').hidden=state.chartBars.length>1;
}

function drawChart(pointerX=null) {
  const canvas=$('marketChart'),ctx=canvas.getContext('2d');const rect=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);const w=Math.max(300,rect.width),h=Math.max(180,rect.height);canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
  const bars=state.chartBars;if(bars.length<2)return;const pad={l:10,r:58,t:16,b:38};const cw=w-pad.l-pad.r,ch=h-pad.t-pad.b;const lows=bars.map(b=>Number(b.low)).filter(Number.isFinite), highs=bars.map(b=>Number(b.high)).filter(Number.isFinite);if(!lows.length||!highs.length)return;let min=Math.min(...lows),max=Math.max(...highs);const span=Math.max(max-min,1e-9);min-=span*.08;max+=span*.08;const y=p=>pad.t+(max-p)/(max-min)*ch;const step=cw/bars.length;const body=Math.max(2,Math.min(8,step*.56));
  ctx.strokeStyle='rgba(151,194,205,.10)';ctx.lineWidth=1;ctx.font='10px system-ui';ctx.fillStyle='rgba(150,172,179,.70)';ctx.textAlign='left';for(let i=0;i<=4;i++){const yy=pad.t+ch*i/4;ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(pad.l+cw,yy);ctx.stroke();const value=max-(max-min)*i/4;ctx.fillText(value.toFixed(value<10?3:2),pad.l+cw+7,yy+3)}
  bars.forEach((b,i)=>{const x=pad.l+step*(i+.5),o=Number(b.open),c=Number(b.close),hi=Number(b.high),lo=Number(b.low);if(![o,c,hi,lo].every(Number.isFinite))return;const up=c>=o;ctx.strokeStyle=up?'#72dfe9':'#d8e1e3';ctx.fillStyle=up?'rgba(82,208,220,.82)':'rgba(209,220,223,.82)';ctx.beginPath();ctx.moveTo(x,y(hi));ctx.lineTo(x,y(lo));ctx.stroke();const top=Math.min(y(o),y(c)),height=Math.max(1,Math.abs(y(c)-y(o)));ctx.fillRect(x-body/2,top,body,height)});
  if(pointerX!==null){let i=Math.floor((pointerX-pad.l)/step);i=Math.max(0,Math.min(bars.length-1,i));state.hoverIndex=i;const x=pad.l+step*(i+.5),b=bars[i];ctx.strokeStyle='rgba(119,216,230,.45)';ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(x,pad.t);ctx.lineTo(x,pad.t+ch);ctx.stroke();ctx.setLineDash([]);const lines=[fmtTs(b.ts),`O ${cleanNumber(b.open,4)}  H ${cleanNumber(b.high,4)}`,`L ${cleanNumber(b.low,4)}  C ${cleanNumber(b.close,4)}`];ctx.font='10px system-ui';const tw=Math.max(...lines.map(t=>ctx.measureText(t).width))+18;const tx=Math.min(w-tw-6,Math.max(6,x+10)),ty=10;ctx.fillStyle='rgba(4,13,17,.94)';ctx.strokeStyle='rgba(119,216,230,.28)';ctx.fillRect(tx,ty,tw,54);ctx.strokeRect(tx,ty,tw,54);ctx.fillStyle='#c9dce1';lines.forEach((t,j)=>ctx.fillText(t,tx+9,ty+14+j*16))}
}

function renderMarketDetails(d) {
  const grid=$('marketDetailGrid');grid.replaceChildren();for(const [symbol,m] of Object.entries(d.market||{})){const c=detailCard(symbol);c.append(pill(m.source_kind?`${m.source||'—'} · ${m.source_kind}`:'UNVERIFIED',m.trusted_for_trade?'good':'warn'));const kv=node('div','kv');kvRow(kv,'Analysis trusted',boolLabel(m.trusted_for_analysis,'YES','NO'));kvRow(kv,'Trade trusted',boolLabel(m.trusted_for_trade,'YES','NO'),m.trusted_for_trade?'status-text good':'status-text warn');kvRow(kv,'Last bar',fmtTs(m.latest_bar_ts));kvRow(kv,'Data age',m.age_minutes==null?'—':`${Number(m.age_minutes).toFixed(1)} min`);kvRow(kv,'Direction',m.direction||'—');kvRow(kv,'Signal score',m.signal_score==null?'—':Number(m.signal_score).toFixed(2));kvRow(kv,'Forge gate',boolLabel(m.forge_gate_passed,'PASS','REJECT'));kvRow(kv,'Constitution',boolLabel(m.constitution_passed,'PASS','REJECT'));c.append(kv);const h=node('div','hash',`Integrity ${m.integrity_hash||'UNVERIFIED'}`);c.append(h);grid.append(c)}if(!grid.children.length)grid.append(detailCard('No verified market snapshot'));
}
function renderStrategyDetails(d) {const grid=$('strategyDetail');grid.replaceChildren();const s=d.strategy||{};const main=detailCard('Champion Verdict','wide');main.append(pill(s.verdict||'UNVERIFIED','warn'));const kv=node('div','kv');kvRow(kv,'Live authorized',boolLabel(s.live_trading_authorized,'YES','NO'));kvRow(kv,'Synthetic evidence only',boolLabel(s.synthetic_evidence_only));kvRow(kv,'Small-sample warning',boolLabel(s.all_small_sample));kvRow(kv,'Worst synthetic P&L',money(s.worst_net_pnl));kvRow(kv,'Best synthetic P&L',money(s.best_net_pnl));kvRow(kv,'Worst drawdown',pct(s.worst_drawdown_pct));main.append(kv,node('div','notice','A positive synthetic run does not prove an edge. Promotion remains blocked until real multi-regime evidence passes the Forge process.'));grid.append(main);const cap=detailCard('Validated scope');const id=d.identity||{};const list=node('div','list');addListItem(list,id.instrument_scope||'UNVERIFIED',`Interval ${id.bar_interval||'—'}`);addListItem(list,(id.symbols||[]).join(', ')||'No symbols','Only this reviewed allowlist may be used.');cap.append(list);grid.append(cap)}
function renderEvolutionDetails(d){const grid=$('evolutionDetail');grid.replaceChildren();const e=d.evolution||{};const status=detailCard('Promotion Gate');const kv=node('div','kv');kvRow(kv,'Mode',e.mode||'UNVERIFIED');kvRow(kv,'Champion mutated',boolLabel(e.champion_mutated));kvRow(kv,'Auto promotion',boolLabel(e.auto_promotion_allowed,'ALLOWED','BLOCKED'));status.append(kv);grid.append(status);const prop=detailCard('Challenger Proposals','wide'),list=node('div','list');for(const p of e.proposals||[])addListItem(list,`${p.kind||'PROPOSAL'} · ${p.status||'—'}`,p.observation||p.hypothesis||'No summary');if(!list.children.length)addListItem(list,'No verified proposals','Evolution output unavailable.');prop.append(list);grid.append(prop)}
function renderGuardianDetails(d){const grid=$('guardianDetail');grid.replaceChildren();const g=d.guardian||{},status=detailCard('Health State');status.append(pill(g.status||'UNVERIFIED',statusClass(g.status)));const kv=node('div','kv');kvRow(kv,'Health gate',boolLabel(g.health_gate_passed,'PASS','FAIL'));kvRow(kv,'Snapshot',fmtTs(g.generated_at));kvRow(kv,'Auto repairs',String(g.repairs?.length??0));status.append(kv);grid.append(status);const checks=detailCard('Checks','wide'),list=node('div','list');for(const x of g.checks||[])addListItem(list,`${x.passed?'✓':'×'} ${x.name||'check'}`,x.detail||'');if(!list.children.length)addListItem(list,'No verified health checks');checks.append(list);grid.append(checks);const repairs=detailCard('Repair Ledger','full'),rl=node('div','list');for(const r of g.repairs||[])addListItem(rl,r.type||'REPAIR',r.path||'Bounded operational repair');if(!rl.children.length)addListItem(rl,'No repairs in latest snapshot','This does not imply that a repair was unnecessary historically.');repairs.append(rl);grid.append(repairs)}
function renderDiagnosticsDetails(d){const grid=$('diagnosticsDetail');grid.replaceChildren();const x=d.diagnostics||{};const summary=detailCard('Deep Diagnostic');summary.append(pill(x.passed===true?'PASS':x.passed===false?'FAIL':'UNVERIFIED',x.passed===true?'good':x.passed===false?'danger':'warn'));const kv=node('div','kv');kvRow(kv,'Test suite',x.test_total?`${x.test_passed}/${x.test_total}`:'UNVERIFIED');kvRow(kv,'Action',x.action||'—');kvRow(kv,'Security',boolLabel(x.security_passed,'PASS','FAIL'));kvRow(kv,'Generated',fmtTs(x.generated_at));summary.append(kv);grid.append(summary);const checks=detailCard('Verification Battery','wide'),list=node('div','list');for(const c of x.checks||[])addListItem(list,`${c.passed?'PASS':'FAIL'} · ${String(c.name||'').replaceAll('_',' ')}`);checks.append(list);grid.append(checks);const chaos=detailCard('Chaos / Fuzz');const ck=node('div','kv'),ch=x.chaos||{};kvRow(ck,'Chaos pass',boolLabel(ch.passed,'PASS','FAIL'));kvRow(ck,'Invalid sizes',ch.invalid_position_sizes??'—');kvRow(ck,'False data accepts',ch.malformed_data_false_accepts??'—');kvRow(ck,'Validator crashes',ch.validator_crashes??'—');kvRow(ck,'Unsafe counsel accepts',ch.unsafe_supervisor_counsel_accepts??'—');chaos.append(ck);grid.append(chaos)}
function renderSupervisorDetails(d){const grid=$('supervisorDetail');grid.replaceChildren();const s=d.supervisor||{};const relay=detailCard('Relay State');relay.append(pill(s.delivery_state||'UNVERIFIED',s.delivery_state==='OUTBOX_READY_NOT_DELIVERED'?'warn':s.relay_passed?'good':'warn'));const kv=node('div','kv');kvRow(kv,'Transport',s.transport_mode||'—');kvRow(kv,'Undelivered',s.undelivered_events??'—');kvRow(kv,'Last ack seq',s.last_ack_seq??'—');kvRow(kv,'Last decision seq',s.last_decision_seq??'—');kvRow(kv,'Advice authority',s.advice_execution_authority||'ZERO');relay.append(kv);grid.append(relay);const esc=detailCard('Open Escalations','wide'),list=node('div','list');for(const e of d.escalations||[])addListItem(list,`${e.reason||'ESCALATION'} · ${e.symbol||'SYSTEM'}`,`Occurrences ${e.occurrences||1} · ${fmtTs(e.last_seen_at)}`);if(!list.children.length)addListItem(list,'No open escalations','Verified from the authoritative queue snapshot.');esc.append(list);grid.append(esc);const rules=detailCard('Counsel Boundary','full'),rl=node('div','list');addListItem(rl,'Evidence referenced','Current-market claims require independently verified source/time evidence.');addListItem(rl,'Advisory only','Supervisor guidance has zero order authority.');addListItem(rl,'No silent policy mutation','Material strategy/risk changes require review and a new approved build/config fingerprint.');rules.append(rl);grid.append(rules)}
function renderSettingsDetails(d){const grid=$('settingsDetail');grid.replaceChildren();const sys=d.system||{},id=d.identity||{};const integ=detailCard('Integrity Locks','wide'),kv=node('div','kv');kvRow(kv,'Build',boolLabel(sys.build_integrity?.verified,'VERIFIED','FAILED'));kvRow(kv,'Build hash',shortHash(sys.build_integrity?.manifest_hash));kvRow(kv,'Config',boolLabel(sys.config_integrity?.verified,'VERIFIED','FAILED'));kvRow(kv,'Config hash',shortHash(sys.config_integrity?.fingerprint));kvRow(kv,'Runtime',boolLabel(sys.runtime_integrity?.verified,'VERIFIED','UNVERIFIED'));integ.append(kv);grid.append(integ);const scope=detailCard('Scope');const list=node('div','list');addListItem(list,id.instrument_scope||'UNVERIFIED',`${id.bar_interval||'—'} · ${(id.symbols||[]).join(', ')||'No symbols'}`);addListItem(list,'Read-only dashboard','No mutation endpoints exist in this control surface.');scope.append(list);grid.append(scope);const unsupported=detailCard('Explicitly Not Supported','full'),ul=node('div','list');for(const x of d.capabilities?.not_supported||[])addListItem(ul,x);unsupported.append(ul);grid.append(unsupported)}

function renderBanners(d){const gen=new Date(d.generated_at),age=(Date.now()-gen.getTime())/1000;const stale=age>Number(d.snapshot_policy?.max_display_age_seconds||900);const sb=$('staleBanner');sb.hidden=!stale;if(stale)sb.textContent=`Snapshot is ${Math.max(0,Math.floor(age/60))} minutes old. It is displayed for audit only and must not be interpreted as current system state.`;const hb=$('haltBanner');const integrityFailed=d.system?.build_integrity?.verified!==true||d.system?.config_integrity?.verified!==true||(d.system?.runtime_integrity?.present===true&&d.system?.runtime_integrity?.verified!==true);const halted=d.portfolio?.halted===true||d.guardian?.status==='HALT'||integrityFailed;hb.hidden=!halted;if(halted)hb.textContent=integrityFailed?'INTEGRITY / REVIEW: build, configuration, or authoritative runtime could not be fully verified. Operator claims are audit-only until resolved.':`HALT / REVIEW: ${d.portfolio?.halt_reason||'Guardian or portfolio state requires operator review.'}`}
function renderFooter(d){$('releaseLabel').textContent=`TRIP'S v${d.release||'—'} · ${(d.identity?.instrument_scope||'UNVERIFIED').replaceAll('_',' ')}`}
function renderFullActivity(d){$('activityTableFull').replaceChildren(buildActivityTable(d.activity));}

function render(d){state.data=d;renderBanners(d);renderStatusStrip(d);renderMetrics(d);renderGuardianCard(d);renderDecisionCard(d);renderEvolutionCard(d);renderRiskCard(d);renderActivityCard(d);renderDiagnosticsCard(d);renderSupervisorCard(d);renderSymbolTabs(d);renderMarketChart(d);renderMarketDetails(d);renderStrategyDetails(d);renderEvolutionDetails(d);renderGuardianDetails(d);renderDiagnosticsDetails(d);renderSupervisorDetails(d);renderSettingsDetails(d);renderFullActivity(d);renderFooter(d)}

function bindNav(){qsa('.nav__item').forEach(btn=>btn.addEventListener('click',()=>{const v=btn.dataset.view;qsa('.nav__item').forEach(x=>{x.classList.toggle('is-active',x===btn);x.removeAttribute('aria-current')});btn.setAttribute('aria-current','page');qsa('.view').forEach(p=>p.classList.toggle('is-active',p.dataset.viewPanel===v));window.scrollTo({top:0,behavior:'smooth'})}))}
function bindChart(){const canvas=$('marketChart');canvas.addEventListener('pointermove',e=>{const r=canvas.getBoundingClientRect();drawChart(e.clientX-r.left)});canvas.addEventListener('pointerleave',()=>drawChart(null));window.addEventListener('resize',()=>drawChart(null),{passive:true})}
function updateClock(){const d=new Date();$('utcDate').textContent=d.toLocaleDateString(undefined,{month:'short',day:'2-digit',year:'numeric',timeZone:'UTC'});$('utcTime').textContent=`${d.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,timeZone:'UTC'})} UTC`}
async function refresh(){try{$('refreshBtn').disabled=true;const d=await loadVerifiedSnapshot();hideIntegrityLock();render(d)}catch(err){showIntegrityLock(err instanceof Error?err.message:'Unknown verification error')}finally{$('refreshBtn').disabled=false}}

document.addEventListener('DOMContentLoaded',()=>{bindNav();bindChart();updateClock();setInterval(updateClock,1000);$('refreshBtn').addEventListener('click',refresh);$('retryIntegrity').addEventListener('click',refresh);refresh()});
