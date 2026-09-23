"""The Mini App page. Self-contained: only the official Telegram Web App SDK is loaded.

The script collects device signals, hashes them with SHA-256 in the browser and
posts ``{initData, fingerprint, signals}`` to ``/api/device``. The server verifies
``initData`` (HMAC on the bot token) and answers with a verdict; the page shows it
and closes itself.
"""

VERIFY_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>🅢🅣🅐🅡🅑🅞🅞🅢🅣 🅟🅡 · проверка устройства</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  :root { color-scheme: light dark; }
  body { margin:0; font-family: -apple-system, system-ui, Roboto, sans-serif;
         background: var(--tg-theme-bg-color, #fff); color: var(--tg-theme-text-color, #111);
         display:flex; min-height:100vh; align-items:center; justify-content:center; text-align:center; }
  .card { padding: 32px 24px; max-width: 360px; }
  .icon { font-size: 56px; line-height: 1; margin-bottom: 16px; }
  h1 { font-size: 20px; margin: 0 0 8px; }
  p { margin: 0; opacity: .8; font-size: 15px; line-height: 1.4; }
  .hint { margin-top: 18px; font-size: 13px; opacity: .6; }
  .spin { display:inline-block; width:18px; height:18px; border:2px solid currentColor; border-right-color:transparent;
          border-radius:50%; animation: r .8s linear infinite; vertical-align:-3px; margin-right:8px; }
  @keyframes r { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="card">
  <div class="icon" id="icon">🛡</div>
  <h1 id="title"><span class="spin"></span>Проверяем устройство…</h1>
  <p id="text">Это займёт пару секунд. Ничего нажимать не нужно.</p>
  <div class="hint" id="hint"></div>
</div>
<script>
(async function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  const $ = (id) => document.getElementById(id);
  function show(icon, title, text, hint) {
    $('icon').textContent = icon; $('title').textContent = title; $('text').textContent = text || '';
    $('hint').textContent = hint || '';
  }
  if (!tg || !tg.initData) {
    show('⚠️', 'Откройте через Telegram', 'Эта страница работает только внутри Telegram: нажмите кнопку «Подтвердить устройство» в боте.');
    return;
  }
  tg.ready(); tg.expand();

  function canvasHash() {
    try {
      const c = document.createElement('canvas'); c.width = 240; c.height = 60;
      const x = c.getContext('2d');
      x.textBaseline = 'alphabetic'; x.fillStyle = '#f60'; x.fillRect(10, 8, 90, 30);
      x.fillStyle = '#069'; x.font = '15px Arial'; x.fillText('KodoStars ✓ 🛡 fp', 4, 28);
      x.fillStyle = 'rgba(102,204,0,.7)'; x.font = '18px Times New Roman'; x.fillText('KodoStars ✓ 🛡 fp', 8, 48);
      return c.toDataURL();
    } catch (e) { return 'n/a'; }
  }
  function webgl() {
    try {
      const c = document.createElement('canvas');
      const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
      if (!gl) return 'n/a';
      const dbg = gl.getExtension('WEBGL_debug_renderer_info');
      const vendor = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR);
      const renderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
      return vendor + ' / ' + renderer;
    } catch (e) { return 'n/a'; }
  }
  function fonts() {
    const list = ['Arial','Verdana','Times New Roman','Courier New','Georgia','Roboto','Segoe UI','Helvetica Neue','Noto Sans','Ubuntu','SF Pro Text'];
    try {
      const c = document.createElement('canvas').getContext('2d');
      const base = (font) => { c.font = '16px ' + font; return c.measureText('mmmmmmmmmmlli').width; };
      const ref = base('monospace');
      return list.filter((f) => base('"' + f + '", monospace') !== ref).join(',');
    } catch (e) { return 'n/a'; }
  }
  async function sha256(text) {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, '0')).join('');
  }

  const n = navigator, s = screen;
  const signals = {
    ua: n.userAgent, platform: n.platform || '', tg_platform: tg.platform, tg_version: tg.version,
    languages: (n.languages || [n.language]).join(','),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
    screen: s.width + 'x' + s.height, dpr: String(window.devicePixelRatio || 1), color_depth: String(s.colorDepth),
    cores: String(n.hardwareConcurrency || 0), memory: String(n.deviceMemory || 0),
    touch: String(n.maxTouchPoints || 0), webgl: webgl(), canvas: '', fonts: fonts(),
  };
  signals.canvas = await sha256(canvasHash());
  const fingerprint = await sha256(Object.keys(signals).sort().map((k) => k + '=' + signals[k]).join('|'));

  try {
    const res = await fetch('/api/device', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ initData: tg.initData, fingerprint, signals }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { throw new Error(data.error || ('HTTP ' + res.status)); }
    if (data.twink) {
      show('⚠️', 'Устройство уже использовалось', 'На этом устройстве есть другой аккаунт. Реферальные бонусы за этот аккаунт не начисляются.', 'Если это ошибка — напишите в поддержку.');
    } else {
      show('✅', 'Устройство подтверждено', 'Можно возвращаться в бота.');
      if (tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
    }
    setTimeout(() => tg.close(), data.twink ? 4000 : 1400);
  } catch (e) {
    show('❌', 'Не удалось проверить', String(e.message || e), 'Закройте окно и попробуйте ещё раз.');
  }
})();
</script>
</body>
</html>
"""

GAME_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>STARBOOX</title><script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#080b13;color:#fff;font-family:system-ui,-apple-system,sans-serif}.app{max-width:520px;margin:auto;padding:18px 14px 90px}.hero,.card{background:linear-gradient(145deg,#171c2d,#0d1120);border:1px solid #29304a;border-radius:22px;padding:18px;margin-bottom:12px}.hero{background:radial-gradient(circle at top right,#462a86,#111629 55%)}h1{margin:0;font-size:23px}.muted{color:#98a2bd;font-size:12px}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:15px}.stat{background:#ffffff0b;border-radius:14px;padding:10px;text-align:center}.stat b,.stat small{display:block}.stat small{font-size:9px;color:#98a2bd;margin-top:3px}.title{font-weight:900;font-size:13px;letter-spacing:.08em;margin-bottom:12px}.wheel{width:210px;height:210px;margin:12px auto;border-radius:50%;display:grid;place-items:center;font-size:52px;background:conic-gradient(#7c3aed 0 45deg,#15b8a6 45deg 90deg,#e84a8a 90deg 135deg,#e7ad31 135deg 180deg,#7c3aed 180deg 225deg,#15b8a6 225deg 270deg,#e84a8a 270deg 315deg,#e7ad31 315deg);border:8px solid #242a42;transition:transform 2.2s cubic-bezier(.2,.8,.2,1)}button{width:100%;border:0;border-radius:14px;padding:13px;color:#fff;background:linear-gradient(90deg,#7048e8,#9c5cff);font-weight:900}.cases{display:grid;grid-template-columns:1fr 1fr;gap:10px}.case{background:#ffffff08;border:1px solid #29304a;border-radius:17px;padding:14px;text-align:center}.case .icon{font-size:42px}.case button{font-size:10px;padding:9px;margin-top:6px}.links{display:flex;gap:8px;margin-top:10px}.links button{background:#ffffff0c}.toast{position:fixed;left:16px;right:16px;bottom:20px;background:#222a43;padding:14px;border-radius:14px;text-align:center;display:none}
</style></head><body><main class="app"><section class="hero"><h1>⭐ STARBOOX</h1><div class="muted" id="name">Загрузка профиля…</div><div class="stats"><div class="stat"><b id="balance">0 ⭐</b><small>БАЛАНС</small></div><div class="stat"><b id="keys">0 🗝</b><small>КЛЮЧИ</small></div><div class="stat"><b id="spins">0 🎟</b><small>СПИНЫ</small></div></div><div class="links"><button onclick="share()">🤝 Пригласить</button><button onclick="reload()">🔄 Обновить</button></div></section><section class="card"><div class="title">🎡 РУЛЕТКА</div><div class="wheel" id="wheel">🎰</div><button id="spin" onclick="spin()">КРУТИТЬ</button></section><section class="card"><div class="title">🧰 КЕЙСЫ</div><div class="cases" id="cases"></div></section></main><div class="toast" id="toast"></div>
<script>
const tg=window.Telegram&&window.Telegram.WebApp;let profile=null,rotation=0;if(tg){tg.ready();tg.expand();}
function toast(s){let e=document.getElementById('toast');e.textContent=s;e.style.display='block';setTimeout(()=>e.style.display='none',2600)}
function rid(){return crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random()}
async function api(path,data={}){let r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...data,initData:tg?.initData||''})});let j=await r.json();if(!r.ok)throw Error(j.error||'Ошибка');return j}
async function reload(){try{profile=await api('/api/game/profile');let u=profile.user;name.textContent=u.name+(u.premium?' · 💎 Premium':'')+' · уровень '+u.level;balance.textContent=u.balance+' ⭐';keys.textContent=u.keys+' 🗝';spins.textContent=u.spins+' 🎟';cases.innerHTML=profile.cases.map(c=>`<div class="case"><div class="icon">${c.icon}</div><b>${c.title}</b><button onclick="openCase('${c.slug}','key')">🗝 ${c.key_price}</button><button onclick="openCase('${c.slug}','stars')">⭐ ${c.stars_price}</button></div>`).join('')}catch(e){toast(e.message)}}
async function spin(){try{spin.disabled=true;let j=await api('/api/game/spin',{requestId:rid()});rotation+=1440+Math.floor(Math.random()*360);wheel.style.transform=`rotate(${rotation}deg)`;setTimeout(()=>toast('Вы выиграли: '+j.reward.amount+' '+j.reward.kind),2100);setTimeout(reload,2300)}catch(e){toast(e.message)}finally{setTimeout(()=>spin.disabled=false,2300)}}
async function openCase(c,p){try{let j=await api('/api/game/case',{case:c,payment:p,requestId:rid()});toast('🎉 '+j.reward.amount+' '+j.reward.kind);await reload()}catch(e){toast(e.message)}}
function share(){let id=profile?.user?.id||'';let url='https://t.me/share/url?url='+encodeURIComponent('https://t.me/{username}?start=ref_'+id)+'&text='+encodeURIComponent('Зарабатывай Stars вместе со мной');tg?.openTelegramLink(url)}
reload();</script></body></html>"""

LANDING_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>🅢🅣🅐🅡🅑🅞🅞🅢🅣 🅟🅡</title>
<style>body{font-family:system-ui,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;background:#0d1117;color:#e6edf3}
a{color:#34d399}.c{text-align:center;padding:32px}</style></head>
<body><div class="c"><h1>⭐ 🅢🅣🅐🅡🅑🅞🅞🅢🅣 🅟🅡</h1><p>Telegram-бот: рефералы, ежедневки, задания и Stars.</p>
<p><a href="https://t.me/{username}">Открыть бота @{username}</a></p></div></body></html>
"""
