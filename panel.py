"""자판기 로컬 웹 패널 (도메인 없음, 127.0.0.1 전용)
실행: python panel.py  (기본 포트 8099)
브라우저: http://127.0.0.1:8099
봇(comet.py)과 별도 실행. 같은 폴더에 있어야 stock/, data/를 공유함.
표준라이브러리만 사용 (추가 pip 설치 없음).
"""
import os
import re
import json
import secrets
import datetime
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_DIR = os.path.join(BASE_DIR, "stock")
DATA_DIR = os.path.join(BASE_DIR, "data")
HOST = "127.0.0.1"
PORT = 8099
MAX_PRICE = 2 ** 31 - 1
MAX_BALANCE = 2 ** 31 - 1


def parse_filename(filename):
    try:
        name = filename.replace(".txt", "").replace(".TXT", "")
        if "_" not in name:
            return {"product_name": name, "price": 0}
        parts = name.split("_")
        last = parts[-1].strip()
        if last.isdigit():
            try:
                price = max(0, min(int(last), MAX_PRICE))
            except (ValueError, OverflowError):
                price = 0
            return {"product_name": "_".join(parts[:-1]), "price": price}
        return {"product_name": name, "price": 0}
    except Exception:
        return {"product_name": filename.replace(".txt", ""), "price": 0}


def safe_name(s):
    s = (s or "").strip()
    if not s or len(s) > 100:
        return None
    if any(c in s for c in '/\\:*?"<>|\n\r'):
        return None
    if s in (".", ".."):
        return None
    return s


def guild_stock_base(guild_id):
    # 서버별 재고 폴더. guild 없으면 기존 공용 stock/
    if not guild_id:
        return STOCK_DIR
    try:
        return os.path.join(STOCK_DIR, str(int(guild_id)))
    except (ValueError, TypeError):
        return STOCK_DIR


def guild_data_file(guild_id, name):
    # data/guilds/<gid>/<name>.json, 없으면 공용 파일로 폴백
    if guild_id:
        try:
            p = os.path.join(DATA_DIR, "guilds", str(int(guild_id)), name + ".json")
            if os.path.exists(p):
                return p
        except (ValueError, TypeError):
            pass
    return os.path.join(DATA_DIR, name + ".json")


def load_bot_guilds():
    for fp in (os.path.join(DATA_DIR, "guilds", "bot_guilds.json"),
               os.path.join(DATA_DIR, "bot_guilds.json")):
        if os.path.exists(fp):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    if isinstance(d, dict) and isinstance(d.get("guilds"), list):
                        out = [g for g in d["guilds"] if isinstance(g, dict) and g.get("id")]
                        if out:
                            return out
            except Exception:
                pass
    # 폴백: 봇 파일이 없으면 stock/·data/guilds/ 폴더명(서버ID)으로 복원
    found = set()
    try:
        if os.path.isdir(STOCK_DIR):
            for item in os.listdir(STOCK_DIR):
                if item.isdigit():
                    found.add(item)
    except OSError:
        pass
    try:
        gd = os.path.join(DATA_DIR, "guilds")
        if os.path.isdir(gd):
            for item in os.listdir(gd):
                if item.isdigit():
                    found.add(item)
    except OSError:
        pass
    return [{"id": gid, "name": gid} for gid in sorted(found)]


def get_categories(guild_id=None):
    base = guild_stock_base(guild_id)
    if not os.path.isdir(base):
        return []
    out = []
    for item in os.listdir(base):
        p = os.path.join(base, item)
        if os.path.isdir(p):
            out.append(item)
    return sorted(out)


def find_product_files(category, product_name, guild_id=None):
    cat_path = os.path.join(guild_stock_base(guild_id), category)
    found = []
    if not os.path.isdir(cat_path):
        return found
    for fn in os.listdir(cat_path):
        if not fn.endswith(".txt"):
            continue
        parsed = parse_filename(fn)
        if parsed["product_name"] == product_name:
            found.append(fn)
    return found


def load_eternal(guild_id):
    fp = guild_data_file(guild_id, "eternal")
    if not os.path.exists(fp):
        return {}
    try:
        with open(fp, "r", encoding="utf-8") as f:
            d = json.load(f)
            if isinstance(d, dict):
                return d
            if isinstance(d, list):
                return {str(x): True for x in d}
    except Exception:
        pass
    return {}


def save_eternal(guild_id, data):
    if guild_id:
        try:
            fp = os.path.join(DATA_DIR, "guilds", str(int(guild_id)), "eternal.json")
            os.makedirs(os.path.dirname(fp), exist_ok=True)
        except (ValueError, TypeError):
            fp = os.path.join(DATA_DIR, "eternal.json")
    else:
        fp = os.path.join(DATA_DIR, "eternal.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_products(guild_id=None):
    result = []
    eternal = load_eternal(guild_id)
    for cat in get_categories(guild_id):
        cat_path = os.path.join(guild_stock_base(guild_id), cat)
        try:
            files = os.listdir(cat_path)
        except OSError:
            continue
        for fn in files:
            if not fn.endswith(".txt"):
                continue
            parsed = parse_filename(fn)
            name = parsed["product_name"]
            price = parsed["price"]
            if not name or price <= 0:
                continue
            fp = os.path.join(cat_path, fn)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    stock = len([l for l in f.read().splitlines() if l.strip()])
            except OSError:
                stock = 0
            # 같은 상품이 파일 여러 개면 합쳐서 표시
            exist = next((r for r in result if r["category"] == cat and r["name"] == name), None)
            if exist:
                exist["stock"] += stock
                exist["files"].append(fn)
            else:
                result.append({"category": cat, "name": name, "price": price,
                               "stock": stock, "files": [fn],
                               "eternal": bool(eternal.get(name, False))})
    result.sort(key=lambda x: (x["category"], x["price"]))
    return result


def load_json_list(name):
    fp = os.path.join(DATA_DIR, name + ".json")
    if not os.path.exists(fp):
        return [] if name in ("buy", "deposit") else {}
    try:
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return [] if name in ("buy", "deposit") else {}


def load_users():
    data = load_json_list("user")
    return data if isinstance(data, dict) else {}


# ---- 운영자 라이선스 ----
SESSIONS = {}  # session_token -> {"key": license_key, "discord": {...}|None}

def _sess_val(key, discord=None):
    return {"key": key, "discord": discord}

def _sess_key(val):
    if isinstance(val, dict):
        return val.get("key")
    return val  # 구버전: 문자열 그대로

def load_licenses():
    fp = os.path.join(DATA_DIR, "licenses.json")
    if not os.path.exists(fp):
        return {}
    try:
        with open(fp, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d.get("keys", {}) if isinstance(d, dict) else {}
    except Exception:
        return {}

def save_licenses(keys):
    fp = os.path.join(DATA_DIR, "licenses.json")
    try:
        if os.path.exists(fp):
            with open(fp, "rb") as f:
                data = f.read()
            with open(fp + ".bak", "wb") as f:
                f.write(data)
    except Exception:
        pass
    with open(fp, "w", encoding="utf-8") as f:
        json.dump({"keys": keys}, f, ensure_ascii=False, indent=2)

def norm_key(s):
    return re.sub(r"[\s\-]", "", (s or "")).upper()

def license_expired(rec):
    # 기간제만 만료 체크. 기준: 첫 로그인 시각 + duration_sec
    rec = rec or {}
    if rec.get("mode", "perm") != "temp":
        return False
    fl = rec.get("first_login")
    if not fl:
        return False
    try:
        start = datetime.datetime.fromisoformat(fl)
    except ValueError:
        return False
    try:
        dur = int(rec.get("duration_sec", 0) or 0)
    except (ValueError, TypeError):
        dur = 0
    return (datetime.datetime.now() - start).total_seconds() > dur

def get_session(handler):
    raw = handler.headers.get("Cookie") or ""
    tok = ""
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            if k.strip() == "session":
                tok = v.strip()
    if not tok or tok not in SESSIONS:
        return None, None
    key = _sess_key(SESSIONS[tok])
    keys = load_licenses()
    hit = next((k for k in keys if norm_key(k) == norm_key(key or "")), None)
    if hit is None or license_expired(keys[hit]):
        SESSIONS.pop(tok, None)
        save_sessions()
        return None, None
    return tok, SESSIONS[tok]

def get_session_key(handler):
    tok, sess = get_session(handler)
    if sess is None:
        return None
    keys = load_licenses()
    hit = next((k for k in keys if norm_key(k) == norm_key(_sess_key(sess) or "")), None)
    return hit

def is_authed(handler):
    return get_session_key(handler) is not None

def auth_error(handler):
    raw = handler.headers.get("Cookie") or ""
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            if k.strip() == "session" and v.strip():
                return "만료됨. 다시 로그인하세요"
    return "라이선스 필요"

SESSION_FILE = os.path.join(DATA_DIR, "sessions.json")

def save_sessions():
    try:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump({"sessions": SESSIONS}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"세션 저장 오류: {e}")

def _prune_sessions():
    try:
        keys = load_licenses()
    except Exception:
        keys = {}
    for tok in list(SESSIONS):
        k = _sess_key(SESSIONS[tok])
        hit = next((x for x in keys if norm_key(x) == norm_key(k or "")), None)
        if hit is None or license_expired(keys[hit]):
            SESSIONS.pop(tok, None)
    save_sessions()

try:
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            _d = json.load(f)
            if isinstance(_d, dict) and isinstance(_d.get("sessions"), dict):
                SESSIONS.update(_d["sessions"])
                # 구버전 문자열 세션을 dict 형태로 정규화
                for _t, _v in list(SESSIONS.items()):
                    if not isinstance(_v, dict):
                        SESSIONS[_t] = _sess_val(_v)
except Exception as e:
    print(f"세션 로드 오류: {e}")
_prune_sessions()


# ---- 디스코드 로그인 (서버별 관리) ----
OAUTH_STATES = {}  # state -> (발급시각, verifier)
DISCORD_CLIENT_ID = "1547582492314181764"

def redirect_uri_for(handler):
    # 접속 주소 기준으로 자동 생성 (설정 불필요). 포털 Redirects에 등록돼 있어야 함.
    try:
        host = (handler.headers.get("Host") or "").split(",")[0].strip()
    except Exception:
        host = ""
    bare = host.split(":")[0]
    if not bare or bare in ("127.0.0.1", "localhost"):
        return "http://127.0.0.1:8099/api/discord/callback"
    return f"https://{bare}/api/discord/callback"

UA = {"User-Agent": "VendingPanel/1.0"}

def discord_api(path, token):
    req = urllib.request.Request("https://discord.com/api/v10" + path,
                                 headers={"Authorization": "Bearer " + token,
                                          "User-Agent": UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())

def visible_guilds(handler):
    # 디스코드 연결된 본인이 관리자인 서버 ∩ 봇이 들어있는 서버. 미연결이면 빈 집합.
    _, sess = get_session(handler)
    d = (sess or {}).get("discord") if isinstance(sess, dict) else None
    bots = {str(g.get("id")) for g in load_bot_guilds() if g.get("id")}
    if not d:
        return set()
    try:
        mine = {str(g.get("id")) for g in (d.get("guilds") or [])
                if int(g.get("permissions", 0) or 0) & 0x8}
    except (ValueError, TypeError):
        mine = set()
    return mine & bots

def guild_allowed(handler, gid):
    if not gid:
        return False
    try:
        return str(int(gid)) in visible_guilds(handler)
    except (ValueError, TypeError):
        return False

def guild_deny(handler):
    _, sess = get_session(handler)
    d = (sess or {}).get("discord") if isinstance(sess, dict) else None
    if not d:
        return "디스코드 연결 후 이용하세요"
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        gid = qs.get("guild", ["?"])[0]
    except Exception:
        gid = "?"
    who = "@" + str(d.get("username", "?"))
    return f"권한 없는 서버예요 (계정 {who} / 서버 {gid}). 서버 주인이 맞으면 디스코드 연결 해제→연결로 갱신하세요"


def load_payment(guild_id=None):
    fp = guild_data_file(guild_id, "payment")
    if not os.path.exists(fp):
        return {"bank": True, "gift": True, "account": ""}
    try:
        with open(fp, "r", encoding="utf-8") as f:
            d = json.load(f)
            return {"bank": bool(d.get("bank", True)), "gift": bool(d.get("gift", True)),
                    "account": str(d.get("account", "") or "")}
    except Exception:
        return {"bank": True, "gift": True, "account": ""}


def save_payment(cfg, guild_id=None):
    if guild_id:
        try:
            fp = os.path.join(DATA_DIR, "guilds", str(int(guild_id)), "payment.json")
            os.makedirs(os.path.dirname(fp), exist_ok=True)
        except (ValueError, TypeError):
            fp = os.path.join(DATA_DIR, "payment.json")
    else:
        fp = os.path.join(DATA_DIR, "payment.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump({"bank": bool(cfg.get("bank", True)), "gift": bool(cfg.get("gift", True)),
                   "account": str(cfg.get("account", "") or "")[:100]},
                  f, ensure_ascii=False, indent=2)


PAGE = """<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>자판기 패널 (로컬)</title>
<style>
body{font-family:Malgun Gothic,system-ui;margin:0;background:#141414;color:#eee}
header{background:#1f1f1f;padding:12px 16px;position:sticky;top:0;display:flex;gap:12px;align-items:center}
header b{font-size:18px}header span{color:#888;font-size:13px}
nav{display:flex;gap:8px;padding:12px 16px}
nav button{background:#2a2a2a;color:#eee;border:1px solid #444;padding:8px 14px;border-radius:8px;cursor:pointer}
nav button.on{background:#5865F2;border-color:#5865F2}
main{padding:0 16px 40px;max-width:1000px}
.card{background:#1f1f1f;border:1px solid #333;border-radius:10px;padding:14px;margin:10px 0}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{border-bottom:1px solid #333;padding:7px 6px;text-align:left}
th{color:#aaa}input,select,textarea{background:#111;color:#eee;border:1px solid #444;border-radius:6px;padding:7px}
button.act{background:#5865F2;color:#fff;border:0;padding:7px 12px;border-radius:6px;cursor:pointer}
button.danger{background:#a33;color:#fff;border:0;padding:7px 12px;border-radius:6px;cursor:pointer}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.mut{color:#999;font-size:12px}
pre{white-space:pre-wrap;background:#111;padding:10px;border-radius:8px;max-height:300px;overflow:auto}
</style></head><body>
<header><b>🎛 자판기 패널</b><select id="guild" style="max-width:220px"></select><span>로컬 전용 · 봇과 별도 실행</span><span style="margin-left:auto" id="dbox"><span id="dstat" class="mut"></span><button class="act" id="dbtn" onclick="discordGo()">디스코드 연결</button> <button class="act" onclick="logout()">로그아웃</button></span></header>
<nav>
<button data-t="products" class="on">상품·재고</button>
<button data-t="users">유저·잔액</button>
<button data-t="logs">구매·충전 로그</button>
<button data-t="status">상태</button>
</nav>
<main>
<section id="t-products">
<div class="card"><b>카테고리 추가·삭제</b><div class="row" style="margin-top:8px">
<input id="nc" placeholder="예: 문화상품권"><button class="act" onclick="mkCat()">추가</button>
<select id="dc"></select><button class="danger" onclick="delCat()">카테고리 삭제</button></div>
<div class="mut">삭제는 해당 카테고리 폴더 + 상품 파일 전체 삭제 (되돌리기 없음)</div></div>
<div class="card"><b>상품 추가</b><div class="row" style="margin-top:8px">
<select id="pc"></select><input id="pn" placeholder="상품명"><input id="pp" type="number" placeholder="가격" style="width:120px">
<button class="act" onclick="mkProd()">추가</button></div>
<div class="mut">파일 규칙: stock/카테고리/상품명_가격.txt, 한 줄 = 재고 1개</div></div>
<div class="card"><b>상품 목록</b> <button class="act" onclick="loadProd()">새로고침</button>
<table><thead><tr><th>카테고리</th><th>상품</th><th>가격</th><th>재고</th><th>관리</th></tr></thead><tbody id="plist"></tbody></table></div>
<div class="card"><b>재고 보기·충전</b><div class="row">
<select id="sc"></select><select id="sp"></select><button class="act" onclick="viewStock()">보기</button></div>
<textarea id="stockbox" rows="8" style="width:100%;margin-top:8px" placeholder="한 줄에 재고 1개"></textarea>
<div class="row" style="margin-top:8px"><button class="act" onclick="addStock()">추가(덧붙이기)</button>
<button class="act" onclick="setStock()">덮어쓰기</button><span class="mut" id="stockinfo"></span></div></div>
</section>
<section id="t-users" style="display:none">
<div class="card"><div class="row"><b>유저 목록</b> <button class="act" onclick="loadUsers()">새로고침</button>
<input id="uq" placeholder="서버ID·유저ID 검색" style="width:220px" oninput="filterUsers()"></div>
<table><thead><tr><th>서버ID</th><th>user_id</th><th>잔액</th><th>총구매</th><th>등급</th><th>잔액 조정</th></tr></thead><tbody id="ulist"></tbody></table></div>
</section>
<section id="t-logs" style="display:none">
<div class="card"><div class="row"><b>로그</b><select id="ltype"><option value="buy">구매</option><option value="deposit">충전</option></select>
<button class="act" onclick="loadLogs()">보기</button></div><pre id="logs"></pre></div>
</section>
<section id="t-status" style="display:none"><div class="card"><b>상태</b> <button class="act" onclick="loadStatus()">새로고침</button><pre id="status"></pre></div>
<div class="card"><b>충전수단 설정</b><div class="row" style="margin-top:8px">
<label><input type="checkbox" id="pm-bank"> 🏦 계좌이체</label>
<label><input type="checkbox" id="pm-gift"> 🎫 문화상품권</label>
<button class="act" onclick="savePay()">저장</button></div>
<div class="row" style="margin-top:8px"><input id="pm-acct" placeholder="계좌번호 (예: 토스뱅크 1000-1234-5678 홍길동)" style="width:340px">
<button class="act" onclick="savePay()">계좌 저장</button></div>
<div class="mut">끄면 봇 충전 방식 선택에서 안 뜸. 둘 다 끄면 충전 불가 메시지 표시. 계좌는 계좌이체 안내 DM에 표시됨.</div></div></section>
</main>
<script>
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
document.querySelectorAll('nav button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
['products','users','logs','status'].forEach(t=>document.getElementById('t-'+t).style.display=t===b.dataset.t?'block':'none');
});
function G(){let s=document.getElementById('guild');return s?s.value||'':''}
async function api(p,m,b){m=m||'GET';let g=G();
if(!p.includes('/api/license')&&!p.includes('/api/guilds')){
if(m==='GET'){p+=(p.includes('?')?'&':'?')+'guild='+encodeURIComponent(g)}
else{b=Object.assign({guild:g},b||{})}}
let o={method:m,headers:{'Content-Type':'application/json'}};if(b)o.body=JSON.stringify(b);
let r=await fetch(p,o);if(r.status===401){alert('만료됨. 다시 로그인하세요');location.href='/';return {ok:false,error:'만료'}}let t=await r.text();try{return JSON.parse(t)}catch(e){return {ok:false,error:t}}}
async function loadGuilds(){let r=await fetch('/api/guilds');let d=await r.json();let s=document.getElementById('guild');s.innerHTML='';
if(!(d.guilds||[]).length){s.innerHTML='<option value="">(디스코드 연결 후 이용 가능)</option>';}
else{
let saved=localStorage.getItem('guild')||'';
d.guilds.forEach(g=>{s.innerHTML+=`<option value="${g.id}">${g.name}</option>`});
if(saved&&[...s.options].some(o=>o.value===saved))s.value=saved;
else localStorage.setItem('guild',s.value);}
s.onchange=()=>{localStorage.setItem('guild',s.value);loadProd();loadUsers();loadLogs();loadStatus()}
loadDiscord();}
async function loadDiscord(){let d=await api('/api/discord/status');let st=document.getElementById('dstat');let btn=document.getElementById('dbtn');
if(d.connected){st.textContent='@'+d.username+' ';btn.textContent='연결 해제';btn.onclick=discordOut;}
else{st.textContent='미연결 ';btn.textContent='디스코드 연결';btn.onclick=discordGo;}}
function discordGo(){location.href='/api/discord/login'}
async function discordOut(){await api('/api/discord/unlink','POST',{});loadDiscord();loadGuilds();}
async function loadProd(){if(!G())return;let d=await api('/api/products');if(!d.ok){alert(d.error);return}
let tb=document.getElementById('plist');tb.innerHTML='';let pc=document.getElementById('pc');let sc=document.getElementById('sc');let sp=document.getElementById('sp');let dc=document.getElementById('dc');
pc.innerHTML='';sc.innerHTML='';sp.innerHTML='';dc.innerHTML='';
let cats=(d.categories&&d.categories.length)?d.categories:[...new Set(d.products.map(p=>p.category))];
cats.forEach(c=>{pc.innerHTML+=`<option>${c}</option>`;sc.innerHTML+=`<option>${c}</option>`;dc.innerHTML+=`<option>${c}</option>`});
d.products.forEach(p=>{sp.innerHTML+=`<option data-c="${p.category}">${p.name}</option>`;
tb.innerHTML+=`<tr><td>${p.category}</td><td>${p.name}</td><td>${p.price.toLocaleString()}</td><td>${p.eternal?'♾️ 영구':p.stock}</td>
<td><button class="act" onclick="chPrice('${p.category}','${p.name}')">가격변경</button>
<button class="act" onclick="toggleEternal('${p.category}','${p.name}',${p.eternal?0:1})">${p.eternal?'영구해제':'영구'}</button>
<button class="danger" onclick="delProd('${p.category}','${p.name}')">삭제</button></td></tr>`});}
async function mkCat(){let v=document.getElementById('nc').value;let d=await api('/api/category/create','POST',{category:v});alert(d.ok?'완료':d.error);loadProd()}
async function delCat(){let c=document.getElementById('dc').value;if(!c){alert('삭제할 카테고리 선택');return}if(!confirm(`[${c}] 카테고리 + 상품 전체 삭제?`))return;let d=await api('/api/category/delete','POST',{category:c});alert(d.ok?'삭제됨':d.error);loadProd()}
async function mkProd(){let d=await api('/api/product/create','POST',{category:document.getElementById('pc').value,name:document.getElementById('pn').value,price:+document.getElementById('pp').value});alert(d.ok?'완료':d.error);loadProd()}
async function chPrice(c,n){let v=prompt('새 가격');if(!v)return;let d=await api('/api/product/price','POST',{category:c,name:n,new_price:+v});alert(d.ok?'완료':d.error);loadProd()}
async function toggleEternal(c,n,en){let d=await api('/api/product/eternal','POST',{category:c,name:n,enable:!!en});alert(d.ok?(en?'영구 설정됨 (재고 1줄이 계속 나감)':'영구 해제됨'):d.error);loadProd()}
async function delProd(c,n){if(!confirm('삭제?'))return;let d=await api('/api/product/delete','POST',{category:c,name:n});alert(d.ok?'완료':d.error);loadProd()}
async function viewStock(){let c=document.getElementById('sc').value;let s=document.getElementById('sp');let n=s.options[s.selectedIndex]?.text;if(!n)return;
let d=await api('/api/stock?category='+encodeURIComponent(c)+'&name='+encodeURIComponent(n));if(!d.ok){alert(d.error);return}
document.getElementById('stockbox').value=(d.lines||[]).join('\\n');document.getElementById('stockinfo').textContent=`${d.lines.length}개`;}
async function addStock(){let c=document.getElementById('sc').value;let s=document.getElementById('sp');let n=s.options[s.selectedIndex]?.text;
let d=await api('/api/stock/add','POST',{category:c,name:n,items_text:document.getElementById('stockbox').value});alert(d.ok?('재고 '+d.stock+'개'):d.error);}
async function setStock(){if(!confirm('덮어쓸까?'))return;let c=document.getElementById('sc').value;let s=document.getElementById('sp');let n=s.options[s.selectedIndex]?.text;
let d=await api('/api/stock/set','POST',{category:c,name:n,items_text:document.getElementById('stockbox').value});alert(d.ok?('재고 '+d.stock+'개'):d.error);}
async function loadUsers(){if(!G()){document.getElementById('ulist').innerHTML='';return}let d=await api('/api/users');let tb=document.getElementById('ulist');tb.innerHTML='';
Object.entries(d.users||{}).forEach(([uid,u])=>{let parts=String(uid).split(':');let gid=parts.length>1?parts[0]:'-';let disp=parts.length>1?parts.slice(1).join(':'):uid;
tb.innerHTML+=`<tr><td>${gid}</td><td>${disp}</td><td>${(u.balance||0).toLocaleString()}</td><td>${(u.total_spent||0).toLocaleString()}</td><td>${u.vip_level||''}</td>
<td><input id="amt-${uid}" type="number" style="width:100px" placeholder="금액">
<button class="act" onclick="adj('${uid}','add')">+</button><button class="act" onclick="adj('${uid}','sub')">-</button>
<button class="act" onclick="adj('${uid}','set')">설정</button></td></tr>`});filterUsers();}
async function adj(id,m){let v=+document.getElementById('amt-'+id).value;let d=await api('/api/user/balance','POST',{user_id:id,mode:m,amount:v});alert(d.ok?('잔액 '+d.balance):d.error);loadUsers()}
function filterUsers(){let q=(document.getElementById('uq').value||'').trim();document.querySelectorAll('#ulist tr').forEach(tr=>{tr.style.display=(!q||tr.textContent.includes(q))?'':'none'})}
async function loadLogs(){if(!G()){document.getElementById('logs').textContent='';return}let t=document.getElementById('ltype').value;let d=await api('/api/logs?type='+t);document.getElementById('logs').textContent=JSON.stringify(d.logs||[],null,2).slice(-8000)}
async function loadStatus(){if(!G()){document.getElementById('status').textContent='';return}let d=await api('/api/status');document.getElementById('status').textContent=JSON.stringify(d,null,2);
let p=await api('/api/payment');if(p.ok){document.getElementById('pm-bank').checked=!!p.bank;document.getElementById('pm-gift').checked=!!p.gift;document.getElementById('pm-acct').value=p.account||''}}
async function savePay(){let d=await api('/api/payment','POST',{bank:document.getElementById('pm-bank').checked,gift:document.getElementById('pm-gift').checked,account:document.getElementById('pm-acct').value});alert(d.ok?'저장됨':d.error)}
async function logout(){await api('/api/license/logout','POST',{});location.href='/'}
loadGuilds().then(()=>{loadProd();loadUsers()});
</script></body></html>"""

GATE = """<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>자판기 패널 - 라이선스</title>
<style>
body{font-family:Malgun Gothic,system-ui;margin:0;background:#141414;color:#eee;display:flex;justify-content:center;align-items:center;min-height:100vh}
.card{background:#1f1f1f;border:1px solid #333;border-radius:12px;padding:28px;width:360px;text-align:center}
input{width:100%;box-sizing:border-box;background:#111;color:#eee;border:1px solid #444;border-radius:6px;padding:10px;margin:10px 0;font-size:15px;text-align:center}
button{background:#5865F2;color:#fff;border:0;padding:10px 18px;border-radius:8px;cursor:pointer;font-size:15px;width:100%}
.mut{color:#999;font-size:12px;margin-top:10px}
a{color:#8af}
</style></head><body>
<div class="card">
<h2>🎛 자판기 패널</h2>
<div class="mut">운영자 라이선스를 입력하세요</div>
<input id="key" placeholder="CMT-XXXX-XXXX-XXXX" onkeydown="if(event.key==='Enter')login()">
<button onclick="login()">입장</button>
<div class="mut">키가 없으면 발급기 프로그램(license_ui.py)을 실행해서 발급</div>
</div>
<script>
async function api(p,m,b){let o={method:m||'GET',headers:{'Content-Type':'application/json'}};if(b)o.body=JSON.stringify(b);
let r=await fetch(p,o);return r.json()}
async function login(){let v=document.getElementById('key').value;
let d=await api('/api/license/login','POST',{key:v});
if(d.ok){location.href='/'}else{alert(d.error||'실패')}}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200, set_cookie=None, cookie_age=None, clear_cookie=False):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie:
            ck = f"session={set_cookie}; HttpOnly; Path=/"
            if cookie_age:
                ck += f"; Max-Age={int(cookie_age)}"
            self.send_header("Set-Cookie", ck)
        if clear_cookie:
            self.send_header("Set-Cookie", "session=; Max-Age=0; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, url):
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_page(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > 2_000_000:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path, qs = u.path, urllib.parse.parse_qs(u.query)
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/":
            self.send_page(PAGE if is_authed(self) else GATE)
            return
        if path == "/api/license/state":
            return self.send_json({"ok": True, "has_keys": bool(load_licenses())})
        if path == "/api/discord/login":
            if not is_authed(self):
                return self.send_json({"ok": False, "error": auth_error(self)}, 401)
            import hashlib
            import base64
            st = secrets.token_hex(8)
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            OAUTH_STATES[st] = (datetime.datetime.now().timestamp(), verifier)
            q = urllib.parse.urlencode({
                "client_id": DISCORD_CLIENT_ID, "redirect_uri": redirect_uri_for(self),
                "response_type": "code", "scope": "identify guilds", "state": st,
                "code_challenge": challenge, "code_challenge_method": "S256"})
            self.redirect("https://discord.com/oauth2/authorize?" + q)
            return
        if path == "/api/discord/callback":
            if not is_authed(self):
                self.redirect("/")
                return
            code = qs.get("code", [""])[0]
            st = qs.get("state", [""])[0]
            saved = OAUTH_STATES.pop(st, None)
            ts = saved[0] if saved else 0
            verifier = saved[1] if saved else ""
            redir = redirect_uri_for(self)
            err = None
            if not code or not ts or datetime.datetime.now().timestamp() - ts > 600:
                err = "인증 요청이 만료됐어요. 다시 연결해주세요"
            else:
                try:
                    # PKCE 방식: 시크릿 없이 교환
                    data = urllib.parse.urlencode({
                        "client_id": DISCORD_CLIENT_ID,
                        "grant_type": "authorization_code", "code": code,
                        "redirect_uri": redir, "code_verifier": verifier}).encode()
                    req = urllib.request.Request("https://discord.com/api/oauth2/token", data=data,
                                                     headers={"User-Agent": UA["User-Agent"]})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        tok = json.loads(r.read().decode()).get("access_token", "")
                    me = discord_api("/users/@me", tok)
                    guilds = discord_api("/users/@me/guilds", tok)
                    tok2, sess = get_session(self)
                    if tok2 is None:
                        err = "세션이 끊겼어요. 다시 로그인하세요"
                    else:
                        sess["discord"] = {
                            "id": str(me.get("id", "")),
                            "username": me.get("username", ""),
                            "guilds": [{"id": str(g.get("id", "")), "name": g.get("name", ""),
                                        "permissions": str(g.get("permissions", "0"))} for g in guilds],
                        }
                        save_sessions()
                except Exception as e:
                    err = f"디스코드 연결 실패: {e}"
            if err:
                self.send_page("<html><body style='background:#141414;color:#eee;font-family:sans-serif'>"
                               f"<h3>{err}</h3><a href='/'>돌아가기</a></body></html>")
                return
            self.redirect("/")
            return
        if path == "/api/discord/status":
            if not is_authed(self):
                return self.send_json({"ok": False, "error": auth_error(self)}, 401)
            _, sess = get_session(self)
            d = (sess or {}).get("discord") if isinstance(sess, dict) else None
            if d:
                return self.send_json({"ok": True, "connected": True, "username": d.get("username", "")})
            return self.send_json({"ok": True, "connected": False})
        if path.startswith("/api/") and not is_authed(self):
            return self.send_json({"ok": False, "error": auth_error(self)}, 401)
        if path == "/api/guilds":
            bots = {str(g.get("id")): {"id": str(g.get("id")), "name": g.get("name", "")}
                    for g in load_bot_guilds() if g.get("id")}
            vis = visible_guilds(self)
            _, sess = get_session(self)
            d = (sess or {}).get("discord") if isinstance(sess, dict) else None
            return self.send_json({"ok": True, "guilds": [bots[i] for i in vis if i in bots],
                                    "discord_connected": bool(d)})
        if path == "/api/products":
            gid = qs.get("guild", [""])[0] or None
            if not guild_allowed(self, gid):
                return self.send_json({"ok": False, "error": guild_deny(self)}, 403)
            return self.send_json({"ok": True, "products": get_products(gid), "categories": get_categories(gid)})
        if path == "/api/users":
            gid = qs.get("guild", [""])[0] or None
            if not guild_allowed(self, gid):
                return self.send_json({"ok": False, "error": guild_deny(self)}, 403)
            users = load_users()
            if gid:
                try:
                    users = {k: v for k, v in users.items() if str(k).startswith(str(int(gid)) + ":")}
                except (ValueError, TypeError):
                    users = {}
            return self.send_json({"ok": True, "users": users})
        if path == "/api/payment":
            gid = qs.get("guild", [""])[0] or None
            if not guild_allowed(self, gid):
                return self.send_json({"ok": False, "error": guild_deny(self)}, 403)
            p = load_payment(gid)
            return self.send_json({"ok": True, "bank": p["bank"], "gift": p["gift"], "account": p["account"]})
        if path == "/api/status":
            gid = qs.get("guild", [""])[0] or None
            if not guild_allowed(self, gid):
                return self.send_json({"ok": False, "error": guild_deny(self)}, 403)
            prods = get_products(gid)
            users = load_users()
            if gid:
                try:
                    users = {k: v for k, v in users.items() if str(k).startswith(str(int(gid)) + ":")}
                except (ValueError, TypeError):
                    users = {}
            return self.send_json({"ok": True, "categories": len(get_categories(gid)),
                                    "products": len(prods),
                                    "total_stock": sum(p["stock"] for p in prods),
                                    "users": len(users)})
        if path == "/api/stock":
            gid = qs.get("guild", [""])[0] or None
            if not guild_allowed(self, gid):
                return self.send_json({"ok": False, "error": guild_deny(self)}, 403)
            cat = safe_name(qs.get("category", [""])[0])
            name = (qs.get("name", [""])[0] or "").strip()
            if not cat or not name:
                return self.send_json({"ok": False, "error": "category/name 필요"}, 400)
            files = find_product_files(cat, name, gid)
            if not files:
                return self.send_json({"ok": False, "error": "상품 없음"}, 404)
            lines = []
            for fn in files:
                try:
                    with open(os.path.join(guild_stock_base(gid), cat, fn), "r", encoding="utf-8") as f:
                        lines += [l.rstrip("\n") for l in f if l.strip()]
                except OSError:
                    pass
            return self.send_json({"ok": True, "lines": lines})
        if path == "/api/logs":
            gid = qs.get("guild", [""])[0] or None
            if not guild_allowed(self, gid):
                return self.send_json({"ok": False, "error": guild_deny(self)}, 403)
            t = qs.get("type", ["buy"])[0]
            if t not in ("buy", "deposit"):
                t = "buy"
            data = load_json_list(t)
            if isinstance(data, dict):
                data = list(data.values())
            if gid:
                try:
                    data = [r for r in data if str(r.get("guild_id", "")) == str(int(gid))]
                except (ValueError, TypeError):
                    data = []
            return self.send_json({"ok": True, "logs": data[-100:]})
        return self.send_json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        b = self.body_json()
        if path == "/api/license/login":
            keys = load_licenses()
            want = norm_key(b.get("key"))
            hit = next((k for k in keys if norm_key(k) == want), None)
            if not hit:
                return self.send_json({"ok": False, "error": "라이선스가 올바르지 않아요"}, 401)
            rec = keys[hit]
            if rec.get("mode", "perm") == "temp":
                if not rec.get("first_login"):
                    # 첫 로그인이 시간 기준점
                    rec["first_login"] = datetime.datetime.now().isoformat()
                    save_licenses(keys)
                elif license_expired(rec):
                    return self.send_json({"ok": False, "error": "만료된 라이선스예요"}, 401)
            tok = secrets.token_hex(16)
            SESSIONS[tok] = _sess_val(hit)
            save_sessions()
            # 쿠키 유지: 영구 1년, 기간제는 남은 시간
            age = 31536000
            if rec.get("mode", "perm") == "temp":
                try:
                    start = datetime.datetime.fromisoformat(rec.get("first_login"))
                    dur = int(rec.get("duration_sec", 0) or 0)
                    age = max(60, int(dur - (datetime.datetime.now() - start).total_seconds()))
                except (ValueError, TypeError):
                    age = 3600
            return self.send_json({"ok": True}, set_cookie=tok, cookie_age=age)
        if path == "/api/license/logout":
            raw = self.headers.get("Cookie") or ""
            for part in raw.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    if k.strip() == "session":
                        SESSIONS.pop(v.strip(), None)
            save_sessions()
            return self.send_json({"ok": True}, clear_cookie=True)
        if path == "/api/discord/unlink":
            if not is_authed(self):
                return self.send_json({"ok": False, "error": auth_error(self)}, 401)
            tok, sess = get_session(self)
            if isinstance(sess, dict) and "discord" in sess:
                sess.pop("discord", None)
                save_sessions()
            return self.send_json({"ok": True})
        if not is_authed(self):
            return self.send_json({"ok": False, "error": auth_error(self)}, 401)
        if path == "/api/category/create":
            cat = safe_name(b.get("category"))
            gid = b.get("guild") or None
            if not guild_allowed(self, gid):
                return self.send_json({"ok": False, "error": guild_deny(self)}, 403)
            if not cat:
                return self.send_json({"ok": False, "error": "카테고리명 오류"}, 400)
            os.makedirs(os.path.join(guild_stock_base(gid), cat), exist_ok=True)
            return self.send_json({"ok": True})
        if path == "/api/category/delete":
            import shutil
            cat = safe_name(b.get("category"))
            gid = b.get("guild") or None
            if not guild_allowed(self, gid):
                return self.send_json({"ok": False, "error": guild_deny(self)}, 403)
            if not cat:
                return self.send_json({"ok": False, "error": "카테고리명 오류"}, 400)
            target = os.path.join(guild_stock_base(gid), cat)
            if not os.path.isdir(target):
                return self.send_json({"ok": False, "error": "카테고리 없음"}, 404)
            try:
                shutil.rmtree(target)
            except OSError as e:
                return self.send_json({"ok": False, "error": str(e)}, 500)
            return self.send_json({"ok": True})
        if path == "/api/product/create":
            cat = safe_name(b.get("category"))
            name = (b.get("name") or "").strip()
            gid = b.get("guild") or None
            base = guild_stock_base(gid)
            if not guild_allowed(self, gid):
                return self.send_json({"ok": False, "error": guild_deny(self)}, 403)
            try:
                price = int(b.get("price") or 0)
            except (ValueError, TypeError):
                price = 0
            if not cat or not os.path.isdir(os.path.join(base, cat)):
                return self.send_json({"ok": False, "error": "카테고리 없음"}, 400)
            if not safe_name(name) or price <= 0 or price > MAX_PRICE:
                return self.send_json({"ok": False, "error": "상품명/가격 오류"}, 400)
            if find_product_files(cat, name, gid):
                return self.send_json({"ok": False, "error": "이미 있는 상품"}, 400)
            open(os.path.join(base, cat, f"{name}_{price}.txt"), "a", encoding="utf-8").close()
            return self.send_json({"ok": True})
        if path == "/api/product/price":
            cat = safe_name(b.get("category"))
            name = (b.get("name") or "").strip()
            gid = b.get("guild") or None
            base = guild_stock_base(gid)
            if not guild_allowed(self, gid):
                return self.send_json({"ok": False, "error": guild_deny(self)}, 403)
            try:
                np = int(b.get("new_price") or 0)
            except (ValueError, TypeError):
                np = 0
            if not cat or not name or np <= 0 or np > MAX_PRICE:
                return self.send_json({"ok": False, "error": "입력 오류"}, 400)
            files = find_product_files(cat, name, gid)
            if not files:
                return self.send_json({"ok": False, "error": "상품 없음"}, 404)
            all_lines = []
            for fn in files:
                try:
                    with open(os.path.join(base, cat, fn), "r", encoding="utf-8") as f:
                        all_lines += [l for l in f.read().splitlines() if l.strip()]
                except OSError:
                    pass
            for fn in files:
                try:
                    os.remove(os.path.join(base, cat, fn))
                except OSError:
                    pass
            with open(os.path.join(base, cat, f"{name}_{np}.txt"), "w", encoding="utf-8") as f:
                for l in all_lines:
                    f.write(l + "\n")
            return self.send_json({"ok": True})
        if path == "/api/product/eternal":
            name = (b.get("name") or "").strip()
            gid = b.get("guild") or None
            if not guild_allowed(self, gid):
                return self.send_json({"ok": False, "error": guild_deny(self)}, 403)
            if not name:
                return self.send_json({"ok": False, "error": "상품명 오류"}, 400)
            try:
                data = load_eternal(gid)
                if b.get("enable"):
                    data[name] = True
                else:
                    data.pop(name, None)
                save_eternal(gid, data)
            except OSError as e:
                return self.send_json({"ok": False, "error": str(e)}, 500)
            return self.send_json({"ok": True})
        if path == "/api/product/delete":
            cat = safe_name(b.get("category"))
            name = (b.get("name") or "").strip()
            gid = b.get("guild") or None
            base = guild_stock_base(gid)
            if not guild_allowed(self, gid):
                return self.send_json({"ok": False, "error": guild_deny(self)}, 403)
            files = find_product_files(cat or "", name, gid)
            if not files:
                return self.send_json({"ok": False, "error": "상품 없음"}, 404)
            for fn in files:
                try:
                    os.remove(os.path.join(base, cat, fn))
                except OSError:
                    pass
            return self.send_json({"ok": True})
        if path in ("/api/stock/add", "/api/stock/set"):
            cat = safe_name(b.get("category"))
            name = (b.get("name") or "").strip()
            gid = b.get("guild") or None
            base = guild_stock_base(gid)
            if not guild_allowed(self, gid):
                return self.send_json({"ok": False, "error": guild_deny(self)}, 403)
            items = [l.strip() for l in str(b.get("items_text") or "").splitlines() if l.strip()]
            if not cat or not name:
                return self.send_json({"ok": False, "error": "입력 오류"}, 400)
            files = find_product_files(cat, name, gid)
            if not files:
                return self.send_json({"ok": False, "error": "상품 없음"}, 404)
            target = os.path.join(base, cat, files[0])
            try:
                if path == "/api/stock/add":
                    with open(target, "a", encoding="utf-8") as f:
                        for l in items:
                            f.write(l + "\n")
                else:
                    with open(target, "w", encoding="utf-8") as f:
                        for l in items:
                            f.write(l + "\n")
                # 합쳐진 파일 외 나머지 중복파일 제거 (add/set 후 단일화)
                if len(files) > 1:
                    extra = []
                    with open(target, "r", encoding="utf-8") as f:
                        extra = [l for l in f.read().splitlines() if l.strip()]
                    for fn in files[1:]:
                        try:
                            with open(os.path.join(base, cat, fn), "r", encoding="utf-8") as f:
                                extra += [l for l in f.read().splitlines() if l.strip()]
                            os.remove(os.path.join(base, cat, fn))
                        except OSError:
                            pass
                    with open(target, "w", encoding="utf-8") as f:
                        for l in extra:
                            f.write(l + "\n")
                with open(target, "r", encoding="utf-8") as f:
                    cnt = len([l for l in f.read().splitlines() if l.strip()])
                return self.send_json({"ok": True, "stock": cnt})
            except OSError as e:
                return self.send_json({"ok": False, "error": str(e)}, 500)
        if path == "/api/payment":
            if not guild_allowed(self, b.get("guild") or None):
                return self.send_json({"ok": False, "error": guild_deny(self)}, 403)
            try:
                save_payment({"bank": b.get("bank", True), "gift": b.get("gift", True),
                              "account": str(b.get("account", "") or "")}, b.get("guild") or None)
            except OSError as e:
                return self.send_json({"ok": False, "error": str(e)}, 500)
            return self.send_json({"ok": True})
        if path == "/api/user/balance":
            users = load_users()
            uid = str(b.get("user_id") or "").strip()
            # 구 형식 키(콜론 없음)는 선택된 서버 기준으로 확인
            ugid = uid.split(":")[0] if ":" in uid else (b.get("guild") or None)
            if not guild_allowed(self, ugid):
                return self.send_json({"ok": False, "error": guild_deny(self)}, 403)
            mode = b.get("mode") or "add"
            try:
                amt = int(b.get("amount") or 0)
            except (ValueError, TypeError):
                amt = 0
            if uid not in users:
                return self.send_json({"ok": False, "error": "유저 없음"}, 404)
            cur = int(users[uid].get("balance", 0) or 0)
            if mode == "add":
                cur += amt
            elif mode == "sub":
                cur -= amt
            elif mode == "set":
                cur = amt
            else:
                return self.send_json({"ok": False, "error": "mode 오류"}, 400)
            users[uid]["balance"] = max(0, min(cur, MAX_BALANCE))
            try:
                with open(os.path.join(DATA_DIR, "user.json"), "w", encoding="utf-8") as f:
                    json.dump(users, f, ensure_ascii=False, indent=2)
            except OSError as e:
                return self.send_json({"ok": False, "error": str(e)}, 500)
            return self.send_json({"ok": True, "balance": users[uid]["balance"]})
        return self.send_json({"ok": False, "error": "not found"}, 404)


if __name__ == "__main__":
    os.makedirs(STOCK_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"패널 실행: http://{HOST}:{PORT} (로컬 전용, Ctrl+C 종료)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
