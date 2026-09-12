import nextcord
from nextcord.ext import commands
from nextcord import SlashOption
from dotenv import load_dotenv
import asyncio
import json
import os
import shutil
import datetime
import requests
import random
import re
import io
import time
try:
    import websocket
    import _thread as thread
    import ssl
except ImportError:
    websocket = None
    thread = None
    ssl = None

load_dotenv()

def _parse_env_int(key, default=0):
    v = os.getenv(key)
    if v is None or str(v).strip() == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default

discordBotToken = os.getenv("DISCORD_TOKEN")
PUSHBULLET_TOKEN = os.getenv("PUSHBULLET_TOKEN", "")
on_run = True

# 관리자 = 서버에서 관리자(Administrator) 권한이 있는 멤버 (admins.json 불필요)
def _member_is_admin(member):
    try:
        perms = getattr(member, "guild_permissions", None)
        return bool(perms is not None and getattr(perms, "administrator", False))
    except Exception:
        return False

def is_admin(interaction, guild_id=None):
    try:
        uid = int(interaction.user.id)
    except Exception:
        return False
    # 1) interaction 소속 서버 (채널에서 누른 경우: 여기서 아니면 끝)
    try:
        guild = getattr(interaction, "guild", None)
        if guild is not None:
            member = getattr(interaction, "member", None)
            if member is None:
                try:
                    member = guild.get_member(uid)
                except Exception:
                    member = None
            return _member_is_admin(member)
    except Exception:
        pass
    # 2) DM 등 서버 정보가 없으면: 지정 서버 → 없으면 봇이 들어있는 전 서버에서 확인
    try:
        gids = []
        if guild_id is not None:
            gids = [guild_id]
        else:
            try:
                gids = [g.id for g in bot.guilds]
            except Exception:
                gids = []
        for gid in gids:
            try:
                g = bot.get_guild(int(gid))
                if g and _member_is_admin(g.get_member(uid)):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False

def get_guild_admins(guild):
    try:
        members = getattr(guild, "members", None) or []
        return [m for m in members if not getattr(m, "bot", False)
                and bool(getattr(getattr(m, "guild_permissions", None), "administrator", False))]
    except Exception:
        return []

RESELLER_ROLE_ID = _parse_env_int("RESELLER_ROLE_ID", 0)
BUYER_ROLE_ID = _parse_env_int("BUYER_ROLE_ID", 0)
ROLE_10만_ID = _parse_env_int("VIP_ROLE_10000", 0)
ROLE_25만_ID = _parse_env_int("VIP_ROLE_30000", 0)
ROLE_50만_ID = _parse_env_int("VIP_ROLE_100000", 0)
ROLE_75만_ID = _parse_env_int("VIP_ROLE_300000", 0)
ROLE_100만_ID = _parse_env_int("VIP_ROLE_500000", 0)

VIP_LEVELS = {
    "50만": {"min_amount": 500000, "discount": 0},
    "30만": {"min_amount": 300000, "discount": 0},
    "10만": {"min_amount": 100000, "discount": 0},
    "3만": {"min_amount": 30000, "discount": 0},
    "1만": {"min_amount": 10000, "discount": 0},
    "구매자": {"min_amount": 1, "discount": 0}
}

def get_vip_level(total_spent):
    for level in ["50만", "30만", "10만", "3만", "1만", "구매자"]:
        if level in VIP_LEVELS and total_spent >= VIP_LEVELS[level]["min_amount"]:
            return level
    return "구매자"

def _guild_file(guild_id, name):
    # data/guilds/<서버ID>/<name>.json, 없으면 기존 공용 파일로 폴백
    try:
        gid = str(int(guild_id))
    except (ValueError, TypeError):
        return os.path.join('data', name + '.json')
    p = os.path.join('data', 'guilds', gid, name + '.json')
    if os.path.exists(p):
        return p
    return os.path.join('data', name + '.json')

def load_guild_json(guild_id, name, default):
    try:
        fp = _guild_file(guild_id, name)
        if os.path.exists(fp):
            with open(fp, 'r', encoding='utf-8') as f:
                d = json.load(f)
                return d if isinstance(d, type(default)) else default
    except Exception as e:
        print(f"{name} 설정 로드 오류: {e}")
    return json.loads(json.dumps(default))

def save_guild_json(guild_id, name, data):
    try:
        gid = str(int(guild_id))
    except (ValueError, TypeError):
        gid = None
    fp = os.path.join('data', 'guilds', gid, name + '.json') if gid else os.path.join('data', name + '.json')
    try:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"{name} 설정 저장 오류: {e}")

def load_channel_config(guild_id=None):
    return load_guild_json(guild_id, 'channels', {})

def save_channel_config(data, guild_id=None):
    save_guild_json(guild_id, 'channels', data)

def get_guild_channels(guild_id):
    d = load_channel_config(guild_id)
    def _i(v):
        try:
            return int(v) if v is not None else None
        except (ValueError, TypeError):
            return None
    av = d.get("auto_vending")
    if av is None:
        auto = []
    elif isinstance(av, list):
        auto = [int(x) for x in av]
    else:
        try:
            auto = [int(av)]
        except (ValueError, TypeError):
            auto = []
    return {
        "admin": _i(d.get("admin")),
        "purchase_log": _i(d.get("purchase_log")),
        "charge": _i(d.get("charge")),
        "charge_log": _i(d.get("charge_log")),
        "stock_management": _i(d.get("stock_management")),
        "auto_vending": auto,
    }

async def resolve_log_channel(text):
    # 채널 멘션/링크/ID에서 채널 객체 찾기 (없으면 None)
    m = re.search(r"(\d{17,20})", text or "")
    if not m:
        return None
    ch = bot.get_channel(int(m.group(1)))
    if ch is None:
        try:
            ch = await bot.fetch_channel(int(m.group(1)))
        except Exception:
            ch = None
    return ch

async def send_charge_log(guild_id, user_id, amount, method, depositor_name=None, balance=None):
    try:
        ch = bot.get_channel(get_guild_channels(guild_id).get("charge_log"))
        if not ch:
            return
        e = nextcord.Embed(title="💸 충전 완료", color=0x00b894)
        e.add_field(name="사용자", value=f"<@{user_id}>", inline=True)
        e.add_field(name="충전 금액", value=f"{amount:,}원", inline=True)
        e.add_field(name="결제 수단", value=method, inline=True)
        if depositor_name:
            e.add_field(name="입금자/PIN", value=str(depositor_name)[:100], inline=False)
        if balance is not None:
            e.add_field(name="충전 후 잔액", value=f"{balance:,}원", inline=True)
        e.timestamp = datetime.datetime.now()
        await ch.send(embed=e)
    except Exception as ex:
        print(f"충전 로그 전송 오류: {ex}")

def load_payment_config(guild_id=None):
    d = load_guild_json(guild_id, 'payment', {})
    try:
        return {"bank": bool(d.get("bank", True)), "gift": bool(d.get("gift", True)),
                "account": str(d.get("account", "") or "")}
    except Exception as e:
        print(f"결제수단 설정 로드 오류: {e}")
        return {"bank": True, "gift": True, "account": ""}

def save_payment_config(cfg, guild_id=None):
    save_guild_json(guild_id, 'payment', {
        "bank": bool(cfg.get("bank", True)), "gift": bool(cfg.get("gift", True)),
        "account": str(cfg.get("account", "") or "")[:100]})

def get_bank_account(guild_id=None):
    try:
        return load_payment_config(guild_id).get("account", "") or BANK_ACCOUNT
    except Exception:
        return BANK_ACCOUNT

def load_appearance_config(guild_id=None):
    d = load_guild_json(guild_id, 'appearance', {})
    try:
        return {"title": str(d.get("title", "") or ""),
                "description": str(d.get("description", "") or ""),
                "image_url": str(d.get("image_url", "") or "").strip()}
    except Exception as e:
        print(f"외형 설정 로드 오류: {e}")
        return {"title": "", "description": "", "image_url": ""}

def save_appearance_config(cfg, guild_id=None):
    save_guild_json(guild_id, 'appearance', {
        "title": str(cfg.get("title", "") or "")[:200],
        "description": str(cfg.get("description", "") or "")[:2000],
        "image_url": str(cfg.get("image_url", "") or "").strip()[:500]})

def get_vending_title(guild_id=None):
    try:
        t = load_appearance_config(guild_id).get("title", "")
        if t:
            return t
    except Exception:
        pass
    return DEFAULT_VENDING_TITLE

def get_vending_description(guild_id=None):
    try:
        d = load_appearance_config(guild_id).get("description", "")
        if d:
            return d
    except Exception:
        pass
    return DEFAULT_VENDING_DESCRIPTION

def get_vending_image_url(guild_id=None):
    try:
        u = load_appearance_config(guild_id).get("image_url", "")
        if u:
            return u
    except Exception:
        pass
    return VENDING_MACHINE_IMAGE_URL

BANK_ACCOUNT = os.getenv("BANK_ACCOUNT", "")

SERVICE_NAME = os.getenv("SERVICE_NAME", "자판기 봇")
VENDING_MACHINE_IMAGE_URL = (os.getenv("VENDING_MACHINE_IMAGE_URL") or "").strip()
VENDING_MACHINE_IMAGE_ENABLED = bool(VENDING_MACHINE_IMAGE_URL)
VENDING_MACHINE_DESCRIPTION = os.getenv(
    "VENDING_MACHINE_DESCRIPTION",
    "자동 충전·구매 자판기입니다.\n환경 변수에서 문구·이미지·계좌를 설정하세요."
)

DEFAULT_VENDING_TITLE = f"** 🔔 {SERVICE_NAME} 봇자판기 **"
DEFAULT_VENDING_DESCRIPTION = VENDING_MACHINE_DESCRIPTION

REVIEW_REQUEST_MESSAGE = os.getenv(
    "REVIEW_REQUEST_MESSAGE",
    "구매해 주셔서 감사합니다. 만족하셨다면 **후기**를 남겨 주시면 큰 도움이 됩니다."
)

MIN_CHARGE_AMOUNT = 1

MAX_BALANCE = 2**31 - 1
MAX_PRICE = 2**31 - 1
MAX_QUANTITY = 10000
MAX_STRING_LENGTH = 2000
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_PRODUCTS = 100
MAX_LOG_ENTRIES = 1000

RPC_MESSAGES = [
    os.getenv("RPC_STATUS", "자판기 봇 | 운영 중")
]
RPC_UPDATE_INTERVAL = 1800
RPC_ERROR_RETRY_INTERVAL = 300

PENDING_CHARGES = {}
KAKAOBANK_NOTIFICATION_BUFFER = []
RECENTLY_PROCESSED_DEPOSITS = {}
RECENTLY_PROCESSED_DEPOSITS_WINDOW = 30

def cleanup_old_pending_charges():
    current_time = datetime.datetime.now().timestamp()
    expired_users = []
    
    for user_id, charge_info in PENDING_CHARGES.items():
        if current_time - charge_info["timestamp"] > 86400:
            expired_users.append(user_id)
    
    for user_id in expired_users:
        del PENDING_CHARGES[user_id]

def safe_add(a, b):
    return max(0, min(int(a) + int(b), MAX_BALANCE))


def safe_multiply(a, b):
    return max(0, min(int(a) * int(b), MAX_PRICE))

def validate_string_length(text, max_length=MAX_STRING_LENGTH):
    if len(text) > max_length:
        raise ValueError(f"문자열이 너무 깁니다 (최대 {max_length}자)")
    return text


def validate_positive_int(value, max_value=None):
    try:
        int_value = int(value)
        if int_value < 0:
            raise ValueError("음수는 허용되지 않습니다")
        if max_value and int_value > max_value:
            raise ValueError(f"값이 너무 큽니다 (최대 {max_value})")
        return int_value
    except (ValueError, TypeError):
        raise ValueError("올바른 정수를 입력해주세요")


def load_json_data(filename):
    try:
        file_path = f'data/{filename}.json'
        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            if file_size > MAX_FILE_SIZE:
                raise ValueError(f"파일이 너무 큽니다 (최대 {MAX_FILE_SIZE}바이트)")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        print(f"JSON 파싱 오류: {e}")
        return {}


def save_json_data(filename, data):
    os.makedirs('data', exist_ok=True)
    with open(f'data/{filename}.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_category_emoji(category_name):
    emoji_data = load_json_data('category_emojis')
    if emoji_data and category_name in emoji_data:
        return emoji_data[category_name]
    return ""

def get_product_emoji(category_name, product_name):
    emoji_data = load_json_data('product_emojis')
    if emoji_data and category_name in emoji_data and product_name in emoji_data[category_name]:
        return emoji_data[category_name][product_name]
    return ""


def _user_key(guild_id, user_id):
    # 서버별 분리: 같은 유저라도 서버마다 잔액·정보가 따로 저장됨
    try:
        g = int(guild_id or 0)
    except (ValueError, TypeError):
        g = 0
    return f"{g}:{int(user_id)}"


def get_user_data(guild_id, user_id):
    user_data = load_user_data()
    user_key = _user_key(guild_id, user_id)
    
    if user_key not in user_data:
        user_data[user_key] = {
            'user_id': int(user_id),
            'guild_id': int(guild_id or 0),
            'balance': 0,
            'total_spent': 0,
            'vip_level': '유저',
            'created_at': datetime.datetime.now().isoformat(),
            'last_updated': datetime.datetime.now().isoformat()
        }
        save_user_data(user_data)
    
    return user_data[user_key]


def update_user_data(guild_id, user_id, updates):
    user_data = load_user_data()
    user_key = _user_key(guild_id, user_id)
    
    if user_key not in user_data:
        user_data[user_key] = {
            'user_id': int(user_id),
            'guild_id': int(guild_id or 0),
                'balance': 0,
                'total_spent': 0,
            'vip_level': '유저',
            'created_at': datetime.datetime.now().isoformat(),
            'last_updated': datetime.datetime.now().isoformat()
        }
    for key, value in updates.items():
        if key in ['balance', 'total_spent']:
            user_data[user_key][key] = max(0, min(value, MAX_BALANCE))
        elif key == 'vip_level':
            user_data[user_key][key] = str(value)[:50]
        else:
            user_data[user_key][key] = value
    user_data[user_key]['last_updated'] = datetime.datetime.now().isoformat()
    total_spent = user_data[user_key]['total_spent']
    user_data[user_key]['vip_level'] = get_vip_level(total_spent)
    save_user_data(user_data)
    return user_data[user_key]
def add_buy_record(guild_id, user_id, product_name, quantity, price, total_price, discount_amount, final_price, purchased_items=None):
    buy_history = load_buy_history()
    if purchased_items is None:
        purchased_items = []
    buy_record = {
        'id': len(buy_history) + 1,
        'guild_id': guild_id,
        'user_id': user_id,
        'product_name': product_name,
        'quantity': quantity,
        'price': price,
        'total_price': total_price,
        'discount_amount': discount_amount,
        'final_price': final_price,
        'timestamp': datetime.datetime.now().isoformat(),
        'date': datetime.datetime.now().strftime('%Y년 %m월 %d일 %H:%M'),
        'items': purchased_items
    }
    
    buy_history.append(buy_record)
    if len(buy_history) > MAX_LOG_ENTRIES:
        buy_history = buy_history[-MAX_LOG_ENTRIES:]
    
    save_buy_history(buy_history)
    return buy_record
def add_deposit_record(guild_id, user_id, amount, method, receipt_image=None, transaction_hash=None, depositor_name=None):
    deposit_history = load_deposit_history()
    
    deposit_record = {
        'id': len(deposit_history) + 1,
        'guild_id': guild_id,
        'user_id': user_id,
        'amount': amount,
        'method': method, 
        'receipt_image': receipt_image,
        'transaction_hash': transaction_hash,
        'depositor_name': depositor_name, 
        'timestamp': datetime.datetime.now().isoformat(),
        'date': datetime.datetime.now().strftime('%Y년 %m월 %d일 %H:%M')
    }
    
    deposit_history.append(deposit_record)
    if len(deposit_history) > MAX_LOG_ENTRIES:
        deposit_history = deposit_history[-MAX_LOG_ENTRIES:]
    
    save_deposit_history(deposit_history)
    return deposit_record
def _gid(interaction):
    try:
        g = getattr(interaction, 'guild', None)
        return g.id if g else None
    except Exception:
        return None

def guild_stock_dir(guild_id=None):
    # 서버별 재고 폴더: stock/<서버ID>
    if guild_id is None:
        return 'stock'
    try:
        return os.path.join('stock', str(int(guild_id)))
    except (ValueError, TypeError):
        return 'stock'

def ensure_guild_stock(guild_id):
    # 서버 폴더가 없으면 기존 공용 재고를 복사해 자동 이전
    base = guild_stock_dir(guild_id)
    if guild_id is None:
        os.makedirs(base, exist_ok=True)
        return base
    if os.path.isdir(base):
        return base
    os.makedirs(base, exist_ok=True)
    try:
        if os.path.isdir('stock'):
            for item in os.listdir('stock'):
                if item == os.path.basename(base):
                    continue
                src = os.path.join('stock', item)
                if os.path.isdir(src) and not item.isdigit():
                    dst = os.path.join(base, item)
                    if not os.path.exists(dst):
                        shutil.copytree(src, dst)
    except Exception as e:
        print(f"재고 이전 오류: {e}")
    return base

def load_eternal(guild_id=None):
    d = load_guild_json(guild_id, 'eternal', {})
    if isinstance(d, dict):
        return d
    if isinstance(d, list):  # 구 형식 호환
        return {str(x): True for x in d}
    return {}

def is_eternal_product(guild_id, product_name):
    try:
        return bool(load_eternal(guild_id).get(str(product_name), False))
    except Exception:
        return False

def set_eternal_product(guild_id, product_name, on):
    d = load_eternal(guild_id)
    if on:
        d[str(product_name)] = True
    else:
        d.pop(str(product_name), None)
    save_guild_json(guild_id, 'eternal', d)

def get_eternal_line(guild_id, product_name):
    # 영구 재고로 계속 나갈 한 줄 (첫 번째 줄)
    base = guild_stock_dir(guild_id)
    if not os.path.isdir(base):
        return None
    for category in get_categories_from_stock_folder(guild_id):
        cp = os.path.join(base, category)
        if not os.path.isdir(cp):
            continue
        try:
            for fn in sorted(os.listdir(cp)):
                if not fn.endswith('.txt'):
                    continue
                parsed = parse_filename(fn)
                if not parsed or parsed.get('product_name') != product_name:
                    continue
                try:
                    with open(os.path.join(cp, fn), 'r', encoding='utf-8') as f:
                        for line in f:
                            if line.strip():
                                return line.strip()
                except OSError:
                    continue
        except OSError:
            continue
    return None

def remove_product_stock(guild_id, product_name, quantity):
    quantity = validate_positive_int(quantity, MAX_QUANTITY)
    validate_string_length(product_name, 100)
    stock_base_dir = guild_stock_dir(guild_id)
    if not os.path.exists(stock_base_dir):
        print(f"STOCK 폴더가 존재하지 않습니다.")
        return []
    removed_items = []
    remaining_quantity = quantity
    for category in get_categories_from_stock_folder(guild_id):
        if remaining_quantity <= 0:
            break
        category_path = os.path.join(stock_base_dir, category)
        if not os.path.isdir(category_path):
            continue
        for filename in os.listdir(category_path):
            if filename.endswith('.txt') and remaining_quantity > 0:
                parsed = parse_filename(filename)
                if not parsed or 'product_name' not in parsed:
                    continue
                file_product_name = parsed['product_name']
                if file_product_name == product_name:
                    file_path = os.path.join(category_path, filename)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            lines = f.readlines()
                        lines_to_keep = []
                        for i, line in enumerate(lines):
                            line = line.strip()
                            if line and remaining_quantity > 0:
                                removed_items.append(line)
                                remaining_quantity -= 1
                            else:
                                lines_to_keep.append(lines[i])
                        with open(file_path, 'w', encoding='utf-8') as f:
                            if lines_to_keep:
                                f.writelines(lines_to_keep)
                    except Exception:
                        pass
    return removed_items
def get_product_stock_count(guild_id, product_name):
    stock_base_dir = guild_stock_dir(guild_id)
    if not os.path.exists(stock_base_dir):
        return 0
    count = 0
    for category in get_categories_from_stock_folder(guild_id):
        category_path = os.path.join(stock_base_dir, category)
        if not os.path.isdir(category_path):
            continue
        try:
            for filename in os.listdir(category_path):
                if filename.endswith('.txt'):
                    parsed = parse_filename(filename)
                    file_product_name = parsed['product_name']
                    if file_product_name == product_name:
                        file_path = os.path.join(category_path, filename)
                        try:
                            with open(file_path, 'r', encoding='utf-8') as f:
                                lines = f.readlines()
                            count += len([line.strip() for line in lines if line.strip()])
                        except Exception as e:
                            print(f"재고 파일 읽기 오류: {filename} - {e}")
        except Exception as e:
            print(f"카테고리 읽기 오류: {category} - {e}")
    return count
def parse_filename(filename):
    try:
        name_without_ext = filename.replace('.txt', '').replace('.TXT', '')
        if '_' not in name_without_ext:
            return {'product_name': name_without_ext, 'price': 0, 'timestamp': None}
        parts = name_without_ext.split('_')
        if len(parts) < 2:
            return {'product_name': name_without_ext, 'price': 0, 'timestamp': None}
        last = parts[-1].strip()
        if last.isdigit() and len(last) >= 10:
            try:
                ts = int(last)
                if ts < 0 or ts > 2**63 - 1:
                    ts = None
            except (ValueError, OverflowError):
                ts = None
            if ts is None or len(parts) < 3:
                return {'product_name': name_without_ext, 'price': 0, 'timestamp': None}
            pp = parts[-2].strip()
            if not pp.isdigit():
                return {'product_name': '_'.join(parts[:-2]), 'price': 0, 'timestamp': ts}
            try:
                price = max(0, min(int(pp), MAX_PRICE))
            except (ValueError, OverflowError):
                price = 0
            return {'product_name': '_'.join(parts[:-2]), 'price': price, 'timestamp': ts}
        if last.isdigit():
            try:
                price = max(0, min(int(last), MAX_PRICE))
            except (ValueError, OverflowError):
                price = 0
            return {'product_name': '_'.join(parts[:-1]), 'price': price, 'timestamp': None}
        return {'product_name': name_without_ext, 'price': 0, 'timestamp': None}
    except Exception as e:
        print(f"파일명 파싱 오류: {filename} - {e}")
        return {'product_name': filename.replace('.txt', '').replace('.TXT', ''), 'price': 0, 'timestamp': None}
def get_product_info_from_stock(product_name, category_name=None, guild_id=None):
    stock_base_dir = guild_stock_dir(guild_id)
    if not os.path.exists(stock_base_dir):
        return None
    
    categories = [category_name] if category_name else get_categories_from_stock_folder(guild_id)
    
    for category in categories:
        if category_name and category != category_name:
            continue
        category_path = os.path.join(stock_base_dir, category)
        if os.path.exists(category_path) and os.path.isdir(category_path):
            for filename in os.listdir(category_path):
                if filename.endswith('.txt'):
                    parsed = parse_filename(filename)
                    file_product_name = parsed['product_name']
                    if file_product_name == product_name:
                        price = parsed['price']
                        return {
                            'name': product_name,
                            'category': category,
                            'price': price,
                            'filename': filename
                        }
    return None
def get_categories_from_stock_folder(guild_id=None):
    if guild_id is not None:
        ensure_guild_stock(guild_id)
    stock_base_dir = guild_stock_dir(guild_id)
    if not os.path.exists(stock_base_dir):
        return []
    categories = []
    try:
        for item in os.listdir(stock_base_dir):
            item_path = os.path.join(stock_base_dir, item)
            if os.path.isdir(item_path):
                categories.append(item)
    except Exception as e:
        print(f"카테고리 읽기 오류: {e}")
    
    return sorted(categories)
def get_products_from_category(category_name, guild_id=None):
    stock_base_dir = guild_stock_dir(guild_id)
    category_path = os.path.join(stock_base_dir, category_name)
    
    if not os.path.exists(category_path) or not os.path.isdir(category_path):
        return {}
    products = {}
    try:
        file_count = 0
        for filename in os.listdir(category_path):
            if file_count >= MAX_PRODUCTS:
                print(f"최대 제품 수({MAX_PRODUCTS})에 도달했습니다.")
                break
            if filename.endswith('.txt'):
                file_path = os.path.join(category_path, filename)
                try:
                    file_size = os.path.getsize(file_path)
                    if file_size > MAX_FILE_SIZE:
                        print(f"파일이 너무 큽니다: {filename}")
                        continue
                except:
                    continue
                parsed = parse_filename(filename)
                if not parsed or 'product_name' not in parsed or 'price' not in parsed:
                    continue
                product_name = parsed['product_name']
                price = parsed['price']
                if price <= 0:
                    continue
                if len(product_name) > 100:
                    print(f"제품명이 너무 깁니다: {product_name}")
                    continue
                try:
                    # 빈 파일도 읽을 수 있도록 처리
                    with open(file_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        if len(lines) > MAX_LOG_ENTRIES:
                            lines = lines[:MAX_LOG_ENTRIES]
                        stock_count = len([line.strip() for line in lines if line.strip()])
                except FileNotFoundError:
                    # 파일이 없으면 재고 0으로 처리
                    stock_count = 0
                except Exception:
                    # 기타 오류도 재고 0으로 처리
                    stock_count = 0
                
                # 재고가 0개여도 제품은 표시 (파일이 존재하면 제품 정보 유지)
                if product_name in products:
                    products[product_name]['stock'] = safe_add(products[product_name]['stock'], stock_count)
                else:
                    products[product_name] = {
                        'name': product_name,
                        'price': price,
                        'stock': stock_count,
                        'category': category_name,
                        'description': f"{product_name} 제품입니다."
                    }
                
                file_count += 1
    except Exception as e:
        print(f"카테고리 {category_name} 읽기 오류: {e}")
    
    products = dict(sorted(products.items(), key=lambda x: x[1].get('price', 0)))
    return products
def get_products_from_stock_folder(guild_id=None):
    stock_base_dir = guild_stock_dir(guild_id)
    if not os.path.exists(stock_base_dir):
        return {}
    all_products = {}
    for category in get_categories_from_stock_folder(guild_id):
        all_products.update(get_products_from_category(category, guild_id))
    return all_products
def add_purchase_log(guild_id, user_id, product_name, quantity, total_price, discount_amount=0, purchased_items=None):
    final_price = total_price - discount_amount
    return add_buy_record(guild_id, user_id, product_name, quantity, total_price, total_price, discount_amount, final_price, purchased_items=purchased_items)
def get_guild_roles(guild_id):
    d = load_guild_json(guild_id, 'roles', {})
    def _r(key, envkey):
        try:
            v = d.get(key)
            if v is not None:
                return int(v)
        except (ValueError, TypeError):
            pass
        return _parse_env_int(envkey, 0)
    return {
        "reseller": _r("reseller", "RESELLER_ROLE_ID"),
        "buyer": _r("buyer", "BUYER_ROLE_ID"),
        "vip_10000": _r("vip_10000", "VIP_ROLE_10000"),
        "vip_30000": _r("vip_30000", "VIP_ROLE_30000"),
        "vip_100000": _r("vip_100000", "VIP_ROLE_100000"),
        "vip_300000": _r("vip_300000", "VIP_ROLE_300000"),
        "vip_500000": _r("vip_500000", "VIP_ROLE_500000"),
    }

def is_reseller(user, guild=None):
    rid = get_guild_roles(guild.id if guild else None)["reseller"]
    if hasattr(user, 'roles') and user.roles:
        return any(role.id == rid for role in user.roles)
    if guild and hasattr(user, 'id'):
        member = guild.get_member(user.id)
        if member and hasattr(member, 'roles') and member.roles:
            return any(role.id == rid for role in member.roles)
    return False

def calculate_reseller_bonus(amount, user, guild=None):
    if is_reseller(user, guild):
        bonus = int(amount * 0.3)
        return amount + bonus
    return amount

def apply_reseller_bonus_to_charge(amount, user_id, guild):
    member = guild.get_member(user_id) if guild else None
    if member:
        return calculate_reseller_bonus(amount, member, guild)
    else:
        user_obj = bot.get_user(user_id)
        if user_obj:
            return calculate_reseller_bonus(amount, user_obj, guild)
    return amount

def calculate_discount(total_price, vip_level):
    return 0

def get_role_id_by_vip_level(vip_level, guild_id=None):
    r = get_guild_roles(guild_id)
    role_map = {
        "50만": r["vip_500000"],
        "30만": r["vip_300000"],
        "10만": r["vip_100000"],
        "3만": r["vip_30000"],
        "1만": r["vip_10000"],
        "구매자": r["buyer"],
    }
    return role_map.get(vip_level, r["buyer"])

def get_achieved_vip_role_ids(total_spent, guild_id=None):
    role_ids = []
    for level in TIER_ORDER:
        if level in VIP_LEVELS and total_spent >= VIP_LEVELS[level]["min_amount"]:
            role_ids.append(get_role_id_by_vip_level(level, guild_id))
    return role_ids

TIER_ORDER = ["구매자", "1만", "3만", "10만", "30만", "50만"]
TIER_DISPLAY_NAMES = {
    "구매자": "🐾 ✦ 𝗖𝗮𝘁 ✦ │ 1원이라도 구매 시 지급",
    "1만": "💖 ✦ ℂ𝕦𝕥𝕖 ℂ𝕒𝕥 ✦ │ 1+ 구매 시 지급",
    "3만": "💰 ✦ 𝐑𝐢𝐜𝐡 𝐂𝐚𝐭 ✦ │ 3+ 구매 시 지급",
    "10만": "👑 ✦ 𝐑𝐨𝐲𝐚𝐥 𝐂𝐚𝐭𝐬 ✦ │ 10+ 구매 시 지급",
    "30만": "💎 ✦ 𝐋𝐮𝐱𝐮𝐫𝐲 𝐂𝐚𝐭 ✦ │ 30+ 구매 시 지급",
    "50만": "🌟 ✦ 𝐆𝐨𝐝 𝐨𝐟 𝐂𝐚𝐭𝐬 ✦ │ 50+ 구매 시 지급",
}

def get_grade_benefit_text(total_spent):
    return ""

def get_grade_info_embed_text():
    return ""

async def pending_charge_timeout_monitor():
    while True:
        try:
            now = datetime.datetime.now().timestamp()
            to_process = []
            for user_id, info in list(PENDING_CHARGES.items()):
                ts = info.get("timestamp", 0)
                if ts and now - ts > 300:
                    to_process.append((user_id, info))
            for uid, info in to_process:
                try:
                    amount = info.get("amount", 0)
                    depositor_name = info.get("depositor_name", "알 수 없음")
                    
                    if not info.get("timeout_sent", False):
                        try:
                            user = bot.get_user(uid)
                            if user:
                                em = nextcord.Embed(title="⚠️ 충전 확인 요청", color=0xffa500)
                                em.add_field(name="안내", value="입금 확인이 지연되고 있습니다. 빠른 시일 내로 확인 후 승인해드리겠습니다.", inline=False)
                                await user.send(embed=em)
                                info["timeout_sent"] = True
                                PENDING_CHARGES[uid] = info
                        except Exception:
                            pass
                    
                    try:
                        charge_channel = bot.get_channel(get_guild_channels(info.get("guild_id")).get("charge"))
                        if charge_channel:
                            timeout_embed = nextcord.Embed(title="⏰ 충전 확인 요청 (5분 타임아웃)", color=0xffa500)
                            timeout_embed.add_field(name="사용자", value=f"<@{uid}>", inline=True)
                            timeout_embed.add_field(name="입금자명", value=depositor_name, inline=True)
                            timeout_embed.add_field(name="금액", value=f"{amount:,}원", inline=True)
                            timeout_embed.add_field(name="안내", value="자동 입금 확인이 지연되어 관리자 승인이 필요합니다.", inline=False)
                            timeout_embed.timestamp = datetime.datetime.now()
                            await charge_channel.send(embed=timeout_embed, view=BankRequestApproveView(uid, amount, depositor_name, None, info.get("guild_id")))
                    except Exception as e:
                        print(f"타임아웃 충전 로그 전송 오류: {e}")
                except Exception as e:
                    print(f"타임아웃 처리 오류: {e}")
        except Exception:
            pass
        await asyncio.sleep(60)

pushbullet_ws = None
pushbullet_ws_thread = None
pushbullet_reconnect_delay = 5
pushbullet_max_reconnect_delay = 60
pushbullet_is_reconnecting = False
pushbullet_last_connect_time = 0
pushbullet_initial_start_done = False
pushbullet_ws_thread_running = False

def parse_bank_notification(package_name, body, title=""):
    body_normalized = body.replace("\n", " ")
    message_parts = body_normalized.replace("원", "").replace(",", "").split(' ')
    displayname = ""
    count = 0
    
    try:
        if package_name == "com.IBK.SmartPush.app":
            sp = body_normalized.split(" ")
            if len(sp) >= 3:
                displayname = sp[2]
                count = int(sp[1].replace("원", "").replace(",", ""))
            print(f"BankAPI[SUCCESS]: com.IBK.SmartPush.app - {displayname}, {count}원")
        elif package_name == "com.nh.mobilenoti":
            if len(message_parts) >= 6:
                displayname = message_parts[5]
                count_str = message_parts[1].replace("입금", "").replace("원", "").replace(",", "")
                count = int(count_str)
            print(f"BankAPI[SUCCESS]: com.nh.mobilenoti - {displayname}, {count}원")
        elif package_name == "com.wooribank.smart.npib":
            sp = body_normalized.split(" ")
            if len(sp) >= 6:
                displayname = sp[1]
                count = int(sp[5].replace("원", "").replace(",", ""))
            print(f"BankAPI[SUCCESS]: com.wooribank.smart.npib - {displayname}, {count}원")
        elif package_name == "com.kakaobank.channel":
            global KAKAOBANK_NOTIFICATION_BUFFER
            current_time = datetime.datetime.now().timestamp()
            KAKAOBANK_NOTIFICATION_BUFFER = [
                notif for notif in KAKAOBANK_NOTIFICATION_BUFFER
                if current_time - notif['timestamp'] <= 10
            ]
            KAKAOBANK_NOTIFICATION_BUFFER.append({
                'title': title,
                'body': body,
                'timestamp': current_time
            })
            all_texts = []
            for notif in KAKAOBANK_NOTIFICATION_BUFFER:
                if notif.get('title'):
                    all_texts.append(notif['title'])
                if notif.get('body'):
                    all_texts.append(notif['body'])
            all_bodies = " ".join(all_texts)
            arrow_match = re.search(r"([가-힣]{2,4})\s*→", all_bodies)
            if arrow_match:
                cand = arrow_match.group(1).strip()
                if cand and cand not in ("입금", "출금", "이체", "충전", "통장"):
                    displayname = cand
            if not displayname:
                name_match = re.search(r"([가-힣]{2,4})\s*→\s*입출금통장", all_bodies)
                if name_match:
                    displayname = name_match.group(1).strip()
            amt_match = re.search(r"입금\s*([\d,]+)\s*원", all_bodies)
            if not amt_match:
                amt_match = re.search(r"([\d,]+)\s*원\s*입금", all_bodies)
            if not amt_match:
                amt_match = re.search(r"[+\-]?\s*([\d,]+)\s*원", all_bodies)
            if not amt_match:
                amt_match = re.search(r"([\d,]+)\s*원", all_bodies)
            if not amt_match:
                large_number_match = re.search(r"([1-9]\d{3,})", all_bodies)
                if large_number_match and int(large_number_match.group(1)) >= 1000:
                    amt_match = large_number_match
            if amt_match:
                count = int(amt_match.group(1).replace(",", ""))
            if not displayname:
                skip_words = ("입금", "출금", "이체", "충전", "통장", "계좌", "농협", "우리", "카카오")
                name_pattern = re.search(r"([가-힣]{2,4})", all_bodies)
                if name_pattern:
                    potential_name = name_pattern.group(1)
                    if not re.search(r"\d", potential_name) and "통장" not in potential_name and potential_name not in skip_words:
                        displayname = potential_name
            if count > 0 and displayname:
                print(f"BankAPI[SUCCESS]: com.kakaobank.channel - {displayname}, {count}원 (버퍼 사용)")
            else:
                print(f"BankAPI[INFO]: com.kakaobank.channel - 버퍼 대기 (입금자: {displayname or '없음'}, 금액: {count or 0}원)")
                return None, None
        elif package_name == "com.samsung.android.messaging":
            amt_match = re.search(r"입금\s*([\d,]+)\s*원", body) or re.search(r"입금([\d,]+)원", body)
            if amt_match:
                count = int(amt_match.group(1).replace(",", ""))
            name_match = re.search(r"\d+-\*+-\d+-\d+\s+([가-힣]{2,4})\s+잔액", body)
            if name_match:
                displayname = name_match.group(1).strip()
            if not displayname:
                parts = body.replace("\n", " ").split()
                for i, p in enumerate(parts):
                    if re.fullmatch(r"[가-힣]{2,4}", p) and p not in ("농협", "입금", "잔액"):
                        displayname = p
                        break
            print(f"BankAPI[SUCCESS]: com.samsung.android.messaging - {displayname}, {count}원")
        else:
            amt_match = re.search(r"입금\s*([\d,]+)원", body)
            if not amt_match:
                amt_match = re.search(r"([\d,]+)원\s*입금", body)
            if amt_match:
                count = int(amt_match.group(1).replace(",", ""))
                lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
                for ln in lines:
                    if re.search(r"[가-힣]{2,4}", ln) and "원" not in ln and "잔액" not in ln:
                        displayname = re.sub(r"[^가-힣]", "", ln)
                        if 2 <= len(displayname) <= 4:
                            break
            print(f"BankAPI[GENERIC]: {package_name} - {displayname}, {count}원")
        
        if count > 0 and displayname:
            return displayname, count
        return None, None
    except Exception as e:
        print(f"BankAPI[ERROR]: 파싱 오류 - {e}")
        return None, None

async def process_pushbullet_notification(package_name, body, title=""):
    if not body or not package_name:
        return False
    
    bank_packages = [
        "com.IBK.SmartPush.app",
        "com.nh.mobilenoti",
        "com.wooribank.smart.npib",
        "com.kakaobank.channel",
        "com.samsung.android.messaging",
    ]
    combined_text = f"{title} {body}" if title else body
    
    if package_name not in bank_packages:
        if "입금" not in combined_text:
            return False
    
    depositor, amount = parse_bank_notification(package_name, body, title)
    
    if not depositor or not amount or amount <= 0:
        return False
    
    def _norm_depositor(n: str) -> str:
        return re.sub(r"[^가-힣]", "", n).strip() or n.strip()
    deposit_key = (_norm_depositor(depositor), amount)
    now_ts = datetime.datetime.now().timestamp()
    global RECENTLY_PROCESSED_DEPOSITS
    RECENTLY_PROCESSED_DEPOSITS = {
        k: v for k, v in RECENTLY_PROCESSED_DEPOSITS.items()
        if now_ts - v <= RECENTLY_PROCESSED_DEPOSITS_WINDOW
    }
    if deposit_key in RECENTLY_PROCESSED_DEPOSITS:
        print(f"PushBullet 중복 알림 스킵 (이미 처리됨): {depositor} {amount}원")
        return False
    
    def normalize_name(n: str) -> str:
        normalized = re.sub(r"[^가-힣]", "", n)
        bank_words = ["입출금통장", "통장", "계좌", "입금", "출금"]
        for word in bank_words:
            normalized = normalized.replace(word, "")
        return normalized
    
    def names_match(name1: str, name2: str) -> bool:
        norm1 = normalize_name(name1)
        norm2 = normalize_name(name2)
        if norm1 == norm2 or name1.strip() == name2.strip():
            return True
        if norm1 in norm2 or norm2 in norm1:
            min_len = min(len(norm1), len(norm2))
            if min_len >= 2:
                return True
        if len(norm1) >= 2 and len(norm2) >= 2:
            if norm1[0] == norm2[0]:
                if len(norm1) == len(norm2) or abs(len(norm1) - len(norm2)) <= 1:
                    if norm1[-1] == norm2[-1]:
                        return True
                    if len(norm1) >= 2 and len(norm2) >= 2:
                        if norm1[:2] == norm2[:2]:
                            return True
        return False
    
    cleanup_old_pending_charges()
    match_user_id = None
    for uid, info in PENDING_CHARGES.items():
        pending_name = str(info.get("depositor_name", ""))
        if info.get("amount") == amount:
            if names_match(pending_name, depositor):
                match_user_id = uid
                break
    
    if not match_user_id:
        print(f"PushBullet 자동승인 매칭 실패 - 금액: {amount}원, 추출된 입금자명: '{depositor}', 대기중인 요청: {[(uid, info.get('depositor_name'), info.get('amount')) for uid, info in PENDING_CHARGES.items()]}")
        return False
    
    guild_id = list(bot.guilds)[0].id if bot.guilds else None
    if not guild_id:
        return False
    
    # 중복 알림 즉시 차단: 매칭 직후 처리 표시 (충전 처리 전에 등록해야 2번째 알림이 스킵됨)
    RECENTLY_PROCESSED_DEPOSITS[deposit_key] = now_ts
    
    credited_amount = amount
    try:
        guild = bot.get_guild(guild_id)
        if guild:
            credited_amount = apply_reseller_bonus_to_charge(credited_amount, match_user_id, guild)
    except Exception:
        pass
    
    user_data = get_user_data(guild_id, match_user_id)
    user_data['balance'] = safe_add(user_data['balance'], credited_amount)
    save_user_data(load_user_data())
    update_user_data(guild_id, match_user_id, user_data)
    
    def normalize_depositor_name(name: str) -> str:
        normalized = re.sub(r"[^가-힣]", "", name)
        bank_words = ["입출금통장", "통장", "계좌", "입금", "출금"]
        for word in bank_words:
            normalized = normalized.replace(word, "")
        return normalized.strip() if normalized.strip() else name
    
    clean_depositor = normalize_depositor_name(depositor)
    add_deposit_record(guild_id, match_user_id, credited_amount, method="계좌이체 (PushBullet)", depositor_name=clean_depositor)
    await send_charge_log(guild_id, match_user_id, credited_amount, "계좌이체 (PushBullet)", clean_depositor, user_data.get("balance"))
    
    try:
        member = bot.get_user(match_user_id)
        if member:
            dm = nextcord.Embed(title="승인되었습니다", color=0x00ff00)
            dm.add_field(name="입금자명", value=clean_depositor, inline=True)
            if credited_amount > amount:
                dm.add_field(name="입금 금액", value=f"{amount:,}원", inline=True)
                dm.add_field(name="리셀러 추가 충전 (30%)", value=f"+{credited_amount - amount:,}원", inline=True)
            dm.add_field(name="총 충전 금액", value=f"{credited_amount:,}원", inline=True)
            dm.add_field(name="충전 후 잔액", value=f"{user_data['balance']:,}원", inline=True)
            await member.send(embed=dm)
    except Exception:
        pass
    
    try:
        charge_channel = bot.get_channel(get_guild_channels(guild_id).get("charge"))
        if charge_channel:
            ok = nextcord.Embed(title="승인되었습니다", color=0x2ecc71)
            ok.add_field(name="입금자명", value=clean_depositor, inline=True)
            if credited_amount > amount:
                ok.add_field(name="입금 금액", value=f"{amount:,}원", inline=True)
                ok.add_field(name="리셀러 추가 충전 (30%)", value=f"+{credited_amount - amount:,}원", inline=True)
            ok.add_field(name="총 충전 금액", value=f"{credited_amount:,}원", inline=True)
            ok.add_field(name="충전 후 잔액", value=f"{user_data['balance']:,}원", inline=True)
            ok.add_field(name="사용자", value=f"<@{match_user_id}>", inline=False)
            await charge_channel.send(embed=ok)
    except Exception:
        pass
    
    try:
        charge_info = PENDING_CHARGES.get(match_user_id, {})
        request_message_id = charge_info.get("message_id")
        if request_message_id:
            req_channel = bot.get_channel(get_guild_channels(guild_id).get("charge"))
            if req_channel:
                try:
                    request_msg = await req_channel.fetch_message(request_message_id)
                    if request_msg and request_msg.components:
                        disabled_view = nextcord.ui.View(timeout=None)
                        for component in request_msg.components:
                            for item in component.children:
                                if isinstance(item, nextcord.ui.Button):
                                    disabled_btn = nextcord.ui.Button(
                                        label=item.label or "승인",
                                        style=item.style,
                                        emoji=item.emoji if item.emoji else None,
                                        disabled=True,
                                        custom_id=item.custom_id
                                    )
                                    disabled_view.add_item(disabled_btn)
                        await request_msg.edit(view=disabled_view)
                except (nextcord.errors.NotFound, nextcord.errors.HTTPException) as e:
                    print(f"충전 요청 메시지 버튼 비활성화 실패: {e}")
    except Exception as e:
        print(f"PushBullet 자동 승인 버튼 비활성화 오류: {e}")
    
    
    try:
        del PENDING_CHARGES[match_user_id]
    except Exception:
        pass
    
    return True

def pushbullet_ws_on_message(ws, message):
    try:
        obj = json.loads(message)
        
        if obj.get("type") == "push":
            push = obj.get("push", {})
            package_name = push.get("package_name", "")
            body = push.get("body", "")
            title = push.get("title", "")
            
            if not body or not package_name:
                return
            
            print(f"PushBullet 알림 수신: {package_name}\n제목: {title}\n본문: {body}")
            
            if bot and bot.loop:
                future = asyncio.run_coroutine_threadsafe(
                    process_pushbullet_notification(package_name, body, title),
                    bot.loop
                )
                try:
                    future.result(timeout=10)
                except Exception as e:
                    print(f"PushBullet 알림 처리 오류: {e}")
    except Exception as e:
        print(f"PushBullet WebSocket 메시지 처리 오류: {e}")

def pushbullet_ws_on_error(ws, error):
    print(f"PushBullet WebSocket 오류: {error}")
    if PUSHBULLET_TOKEN and not pushbullet_is_reconnecting:
        print("PushBullet WebSocket 오류로 인한 재연결 시도...")
        schedule_reconnect()

def pushbullet_ws_on_close(ws, close_status_code, close_msg):
    print(f"PushBullet WebSocket 연결 종료 (코드: {close_status_code}, 메시지: {close_msg})")
    if PUSHBULLET_TOKEN and close_status_code != 1000 and not pushbullet_is_reconnecting:
        print("PushBullet WebSocket 비정상 종료로 인한 재연결 시도...")
        schedule_reconnect()

def pushbullet_ws_on_open(ws):
    global pushbullet_reconnect_delay, pushbullet_is_reconnecting, pushbullet_last_connect_time
    print("PushBullet WebSocket 연결됨 (1개만 유지)")
    pushbullet_reconnect_delay = 5
    pushbullet_is_reconnecting = False
    pushbullet_last_connect_time = time.time()

def schedule_reconnect():
    global pushbullet_is_reconnecting
    if pushbullet_is_reconnecting:
        return
    pushbullet_is_reconnecting = True
    
    def reconnect_after_delay():
        global pushbullet_reconnect_delay, pushbullet_is_reconnecting, pushbullet_last_connect_time
        scheduled_at = time.time()
        print(f"PushBullet WebSocket 재연결 시도 (지연: {pushbullet_reconnect_delay}초)...")
        time.sleep(pushbullet_reconnect_delay)
        pushbullet_is_reconnecting = False
        pushbullet_reconnect_delay = min(pushbullet_reconnect_delay * 2, pushbullet_max_reconnect_delay)
        if pushbullet_last_connect_time > scheduled_at:
            return
        if PUSHBULLET_TOKEN:
            asyncio.run_coroutine_threadsafe(
                start_pushbullet_websocket(),
                bot.loop
            )
    
    if thread:
        thread.start_new_thread(reconnect_after_delay, ())

def run_pushbullet_websocket():
    global pushbullet_ws, pushbullet_is_reconnecting, pushbullet_ws_thread_running
    if not PUSHBULLET_TOKEN or not websocket:
        pushbullet_is_reconnecting = False
        pushbullet_ws_thread_running = False
        return
    try:
        ws_url = f"wss://stream.pushbullet.com/websocket/{PUSHBULLET_TOKEN}"
        pushbullet_ws = websocket.WebSocketApp(
            ws_url,
            on_message=pushbullet_ws_on_message,
            on_error=pushbullet_ws_on_error,
            on_close=pushbullet_ws_on_close
        )
        pushbullet_ws.on_open = pushbullet_ws_on_open
        sslopt = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False} if ssl else {}
        pushbullet_ws.run_forever(
            sslopt=sslopt,
            ping_interval=30,
            ping_timeout=10
        )
    except Exception as e:
        print(f"PushBullet WebSocket 실행 오류: {e}")
        pushbullet_is_reconnecting = False
        if PUSHBULLET_TOKEN:
            schedule_reconnect()
    finally:
        pushbullet_ws_thread_running = False

async def start_pushbullet_websocket():
    global pushbullet_ws_thread, pushbullet_ws, pushbullet_ws_thread_running
    
    if not PUSHBULLET_TOKEN:
        print("PushBullet 토큰이 설정되지 않았습니다.")
        return
    if not websocket:
        print("websocket-client 라이브러리가 설치되지 않았습니다. 'pip install websocket-client'를 실행하세요.")
        return
    if pushbullet_ws_thread_running:
        return
    pushbullet_ws = None
    await bot.wait_until_ready()
    if thread:
        pushbullet_ws_thread_running = True
        pushbullet_ws_thread = thread.start_new_thread(run_pushbullet_websocket, ())
    else:
        print("스레드 모듈을 사용할 수 없습니다.")

async def pushbullet_notification_monitor():
    global pushbullet_initial_start_done
    await bot.wait_until_ready()
    await asyncio.sleep(5)
    if pushbullet_initial_start_done:
        return
    if PUSHBULLET_TOKEN:
        pushbullet_initial_start_done = True
        await start_pushbullet_websocket()
    else:
        print("PushBullet 토큰이 설정되지 않아 WebSocket을 시작하지 않습니다.")
class Bot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


bot = Bot(command_prefix="!", intents=nextcord.Intents.all(), help_command=None)


class BankRequestApproveView(nextcord.ui.View):
    def __init__(self, user_id: int, amount: int, depositor_name: str, channel_id: int = None, guild_id=None):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.amount = amount
        self.depositor_name = depositor_name
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.user_message_id = None
    def _is_admin(self, interaction: nextcord.Interaction) -> bool:
        return is_admin(interaction, self.guild_id)
    async def _approve_common(self, interaction: nextcord.Interaction, use_multiplier: bool):
        if not self._is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        try:
            guild_id = self.guild_id or (interaction.guild.id if interaction.guild else list(bot.guilds)[0].id)
            credited_amount = self.amount
            
            credited_amount = apply_reseller_bonus_to_charge(credited_amount, self.user_id, interaction.guild)
            
            user_data = get_user_data(guild_id, self.user_id)
            user_data['balance'] = safe_add(user_data['balance'], credited_amount)
            update_user_data(guild_id, self.user_id, user_data)
            
            def normalize_depositor_name(name: str) -> str:
                normalized = re.sub(r"[^가-힣]", "", name)
                bank_words = ["입출금통장", "통장", "계좌", "입금", "출금"]
                for word in bank_words:
                    normalized = normalized.replace(word, "")
                return normalized.strip() if normalized.strip() else name
            
            clean_depositor = normalize_depositor_name(self.depositor_name)
            add_deposit_record(guild_id, self.user_id, credited_amount, method="계좌이체", depositor_name=clean_depositor)
            await send_charge_log(guild_id, self.user_id, credited_amount, "계좌이체", clean_depositor, user_data.get("balance"))
            member = bot.get_user(self.user_id)
            if member:
                try:
                    dm = nextcord.Embed(title="승인되었습니다", color=0x00ff00)
                    dm.add_field(name="입금자명", value=clean_depositor, inline=True)
                    if credited_amount > self.amount:
                        dm.add_field(name="입금 금액", value=f"{self.amount:,}원", inline=True)
                        dm.add_field(name="리셀러 추가 충전 (30%)", value=f"+{credited_amount - self.amount:,}원", inline=True)
                    dm.add_field(name="총 충전 금액", value=f"{credited_amount:,}원", inline=True)
                    dm.add_field(name="충전 후 잔액", value=f"{user_data['balance']:,}원", inline=True)
                    await member.send(embed=dm)
                except:
                    pass
            confirm = nextcord.Embed(title="승인되었습니다", color=0x2ecc71)
            confirm.add_field(name="입금자명", value=clean_depositor, inline=True)
            if credited_amount > self.amount:
                confirm.add_field(name="입금 금액", value=f"{self.amount:,}원", inline=True)
                confirm.add_field(name="리셀러 추가 충전 (30%)", value=f"+{credited_amount - self.amount:,}원", inline=True)
            confirm.add_field(name="총 충전 금액", value=f"{credited_amount:,}원", inline=True)
            confirm.add_field(name="충전 후 잔액", value=f"{user_data['balance']:,}원", inline=True)
            confirm.add_field(name="사용자", value=f"<@{self.user_id}>", inline=False)
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=confirm)
                else:
                    await interaction.followup.send(embed=confirm)
            except (nextcord.errors.NotFound, nextcord.errors.HTTPException):
                pass
            
            try:
                charge_request_channel = bot.get_channel(get_guild_channels(guild_id).get("charge"))
                if charge_request_channel:
                    request_confirm = nextcord.Embed(title="승인되었습니다", color=0x2ecc71)
                    request_confirm.add_field(name="입금자명", value=clean_depositor, inline=True)
                    if credited_amount > self.amount:
                        request_confirm.add_field(name="입금 금액", value=f"{self.amount:,}원", inline=True)
                        request_confirm.add_field(name="리셀러 추가 충전 (30%)", value=f"+{credited_amount - self.amount:,}원", inline=True)
                    request_confirm.add_field(name="총 충전 금액", value=f"{credited_amount:,}원", inline=True)
                    request_confirm.add_field(name="충전 후 잔액", value=f"{user_data['balance']:,}원", inline=True)
                    request_confirm.add_field(name="사용자", value=f"<@{self.user_id}>", inline=False)
                    await charge_request_channel.send(embed=request_confirm)
            except Exception:
                pass
            except (nextcord.errors.NotFound, nextcord.errors.HTTPException):
                pass
            for child in self.children:
                if isinstance(child, nextcord.ui.Button):
                    child.disabled = True
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass
            if self.user_id in PENDING_CHARGES:
                try:
                    del PENDING_CHARGES[self.user_id]
                except Exception:
                    pass
        except Exception as e:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"승인 중 오류: {e}", ephemeral=True)
                else:
                    await interaction.followup.send(f"승인 중 오류: {e}", ephemeral=True)
            except (nextcord.errors.NotFound, nextcord.errors.HTTPException):
                pass
    @nextcord.ui.button(label="승인", style=nextcord.ButtonStyle.secondary, custom_id="bank_req_approve")
    async def approve_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self._approve_common(interaction, use_multiplier=False)
    @nextcord.ui.button(label="금액수정승인", style=nextcord.ButtonStyle.secondary, custom_id="bank_req_approve_multi")
    async def approve_multi_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if not self._is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        await interaction.response.send_modal(MultiApprovalModal(self.user_id, self.amount, self.depositor_name))




class ProductBuyView(nextcord.ui.View):
    def __init__(self, product_data, guilid):
        super().__init__(timeout=None)
        self.product_data = product_data
        self.guildid = guilid
    @nextcord.ui.button(emoji="✅", style=nextcord.ButtonStyle.secondary, custom_id="button_3_1")
    async def button_callback1(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        user_data = get_user_data(self.guildid, interaction.user.id)
        products = get_products_from_stock_folder(self.guildid)
        
        product_name = self.product_data[0]
        
        if product_name not in products:
            embed = nextcord.Embed(title="⛔ㆍ오류", color=0xff0000)
            embed.add_field(name="", value="제품을 찾을 수 없습니다.", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        product_info = products[product_name]
        
        try:
            quantity = validate_positive_int(self.product_data[1], MAX_QUANTITY)
    
            unit_price = product_info['price']
            if unit_price <= 0:
                embed = nextcord.Embed(title="⛔ㆍ오류", color=0xff0000)
                embed.add_field(name="", value="제품 가격이 올바르지 않습니다.", inline=False)
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            total_price = safe_multiply(unit_price, quantity)
            
            if quantity <= 0:
                embed = nextcord.Embed(title="⛔ㆍ오류", color=0xff0000)
                embed.add_field(name="", value="수량은 1개 이상이어야 합니다.", inline=False)
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
                
            if total_price <= 0:
                embed = nextcord.Embed(title="⛔ㆍ오류", color=0xff0000)
                embed.add_field(name="", value="가격이 올바르지 않습니다.", inline=False)
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
        except ValueError as e:
            embed = nextcord.Embed(title="⛔ㆍ오류", color=0xff0000)
            embed.add_field(name="", value=f"데이터 검증 오류: {str(e)}", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        current_stock = get_product_stock_count(self.guildid, product_name)
        eternal = is_eternal_product(self.guildid, product_name)
        pinned = get_eternal_line(self.guildid, product_name) if eternal else None
        if eternal and not pinned:
            embed = nextcord.Embed(title="⛔ㆍ재고 없음", color=0xff0000)
            embed.add_field(name="", value="영구 상품의 재고 한 줄을 먼저 등록해주세요.", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if not eternal and current_stock < quantity:
            embed = nextcord.Embed(title="⛔ㆍ재고 부족", color=0xff0000)
            embed.add_field(name="", value=f"재고가 부족해요.\n현재 재고: {current_stock}개, 요청 수량: {quantity}개", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        vip_level = get_vip_level(user_data['total_spent'])
        discount_amount = calculate_discount(total_price, vip_level)
        discount_percent_for_display = VIP_LEVELS[vip_level]['discount']
        final_price = total_price - discount_amount
        
        if final_price <= 0:
            embed = nextcord.Embed(title="⛔ㆍ오류", color=0xff0000)
            embed.add_field(name="", value="최종 결제 금액이 올바르지 않습니다.", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        if user_data['balance'] >= final_price:
            try:
                user_data['balance'] = safe_add(user_data['balance'], -final_price)
                user_data['total_spent'] = safe_add(user_data['total_spent'], final_price)
            except ValueError as e:
                embed = nextcord.Embed(title="⛔ㆍ오류", color=0xff0000)
                embed.add_field(name="", value=f"계산 오류가 발생했습니다: {str(e)}", inline=False)
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            new_vip_level = get_vip_level(user_data['total_spent'])
            user_data['vip_level'] = new_vip_level
            
            update_user_data(self.guildid, interaction.user.id, user_data)
            
            if eternal:
                purchased_items = [pinned] * quantity
            else:
                purchased_items = remove_product_stock(self.guildid, product_name, quantity)
            add_purchase_log(self.guildid, interaction.user.id, product_name, quantity, total_price, discount_amount, purchased_items=purchased_items)
            if purchased_items:
                items_content = "\n".join(purchased_items)
                items_file = nextcord.File(fp=io.BytesIO(items_content.encode('utf-8')), filename=f"{product_name}_구매내역.txt")
                
                embed = nextcord.Embed(title="✅ㆍ구매 성공", color=0xfffffe)
                embed.add_field(name="제품", value=f"- {product_name} {quantity}개", inline=False)
                embed.add_field(name="구매한 아이템", value="첨부된 텍스트 파일을 확인하세요", inline=False)
                
                if discount_amount > 0:
                    embed.add_field(name="할인", value=f"할인: {discount_amount:,}원 ({discount_percent_for_display}%)", inline=False)
                embed.add_field(name="결제 금액", value=f"{final_price:,}원", inline=False)
                embed.timestamp = datetime.datetime.now()
                await interaction.response.edit_message(view=None)
                await interaction.followup.send(embed=embed, file=items_file, ephemeral=False)
            else:
                embed = nextcord.Embed(title="✅ㆍ구매 성공", color=0xfffffe)
                embed.add_field(name="제품", value=f"- {product_name} {quantity}개", inline=False)
                embed.add_field(name="구매한 아이템", value="재고가 없습니다", inline=False)
                
                if discount_amount > 0:
                    embed.add_field(name="할인", value=f"할인: {discount_amount:,}원 ({discount_percent_for_display}%)", inline=False)
                embed.add_field(name="결제 금액", value=f"{final_price:,}원", inline=False)
                embed.timestamp = datetime.datetime.now()
                await interaction.response.edit_message(view=None)
                await interaction.followup.send(embed=embed, ephemeral=False)
            member = bot.get_guild(self.guildid).get_member(interaction.user.id)
            embedVar = nextcord.Embed(
                title="🛒 구매 완료",
                description=f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n**{interaction.user.name}**님의 구매가 완료되었습니다!\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
                color=random.randint(0, 0xFFFFFF)
            )
            
            embedVar.add_field(
                name="📦 구매 제품",
                value=f"```\n{product_name} × {quantity}개\n```",
                inline=True
            )
            
            if discount_amount > 0:
                embedVar.add_field(
                    name="💰 결제 금액",
                    value=f"```\n{final_price:,}원\n(할인: {discount_amount:,}원)\n```",
                    inline=True
                )
            else:
                embedVar.add_field(
                    name="💰 결제 금액",
                    value=f"```\n{final_price:,}원\n```",
                    inline=True
                )
            
            embedVar.add_field(
                name="⏰ 구매 시간",
                value=f"<t:{int(datetime.datetime.now().timestamp())}:R>",
                inline=True
            )
           
            embedVar.set_thumbnail(url=interaction.user.display_avatar.url)
            embedVar.set_footer(text=f"구매자: {interaction.user.name} • ID: {interaction.user.id}", icon_url=interaction.user.display_avatar.url)
            embedVar.timestamp = datetime.datetime.now()
            
            e_channel = bot.get_channel(get_guild_channels(self.guildid).get("purchase_log"))
            if e_channel:
                message = await e_channel.send(embed=embedVar)
                await message.add_reaction("❤️")
            admin_channel = bot.get_channel(get_guild_channels(self.guildid).get("admin"))
            if admin_channel:
                admin_embed = nextcord.Embed(title="🛒 관리자 구매 로그", color=random.randint(0, 0xFFFFFF))
                admin_embed.add_field(name="구매자", value=f"<@{interaction.user.id}> ({interaction.user.name})", inline=True)
                admin_embed.add_field(name="제품", value=f"{product_name} {quantity}개", inline=True)
                admin_embed.add_field(name="결제금액", value=f"{final_price:,}원", inline=True)
                admin_embed.add_field(name="할인", value=f"{discount_amount:,}원", inline=True)
                admin_embed.add_field(name="구매 시간", value=f"<t:{int(datetime.datetime.now().timestamp())}:F>", inline=True)
                
                if purchased_items:
                    items_text = "\n".join(purchased_items)
                    admin_embed.add_field(name="📦 구매한 아이템", value=f"```\n{items_text}\n```"[:1024], inline=False)
                else:
                    admin_embed.add_field(name="📦 구매한 아이템", value="```\n재고가 없습니다\n```", inline=False)
                
                await admin_channel.send(embed=admin_embed)
            try:
                review_embed = nextcord.Embed(title="⭐ 후기 부탁드려요", color=0xffd700)
                review_embed.description = REVIEW_REQUEST_MESSAGE
                review_embed.set_footer(text=f"{product_name} 구매 감사합니다")
                await interaction.user.send(embed=review_embed)
            except (nextcord.Forbidden, nextcord.HTTPException, Exception):
                pass
            for role_id in get_achieved_vip_role_ids(user_data["total_spent"], self.guildid):
                role = bot.get_guild(self.guildid).get_role(role_id)
                if role and role not in member.roles:
                    try:
                        await member.add_roles(role)
                    except Exception:
                        pass
            self.stop()
        else:
            embed = nextcord.Embed(title="⛔ㆍ잔액 부족", color=0xff0000)
            embed.add_field(name="",
                            value=f"- 잔액이 부족해요.\n- **{interaction.user.name}**님의 잔액은 **{user_data['balance']:,}**원 입니다.\n- 충전 후 다시 시도해주세요.",
                            inline=False)
            embed.timestamp = datetime.datetime.now()
            await interaction.response.send_message(embed=embed, ephemeral=True)
    @nextcord.ui.button(emoji="❌", style=nextcord.ButtonStyle.secondary, custom_id="button_3_2")
    async def button_callback2(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        embed = nextcord.Embed(title="⛔ㆍ구매 취소", color=0xff0000)
        embed.add_field(name="", value="구매가 취소되었어요.", inline=False)
        embed.timestamp = datetime.datetime.now()
        await interaction.response.edit_message(view=None, embed=embed)
        self.stop()
def save_user_data(user_data):
    try:
        os.makedirs('data', exist_ok=True)
        with open('data/user.json', 'w', encoding='utf-8') as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"사용자 데이터 저장 오류: {e}")
def load_user_data():
    try:
        if os.path.exists('data/user.json'):
            with open('data/user.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        print(f"사용자 데이터 로드 오류: {e}")
        return {}
def save_buy_history(buy_data):
    try:
        os.makedirs('data', exist_ok=True)
        with open('data/buy.json', 'w', encoding='utf-8') as f:
            json.dump(buy_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"구매 내역 저장 오류: {e}")
def load_buy_history():
    try:
        if os.path.exists('data/buy.json'):
            with open('data/buy.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        print(f"구매 내역 로드 오류: {e}")
        return []

def get_user_buy_history_since(guild_id, user_id, since_date_str=None, limit=5):
    buy_history = load_buy_history()
    out = []
    if since_date_str:
        try:
            since = datetime.datetime.strptime(since_date_str + " 00:00:00", "%Y-%m-%d %H:%M:%S")
        except Exception:
            since = None
    else:
        since = None
    for r in buy_history:
        if r.get("guild_id") != guild_id or r.get("user_id") != user_id:
            continue
        if since is not None:
            ts = r.get("timestamp") or r.get("date", "")
            try:
                if isinstance(ts, str) and "T" in ts:
                    t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if t.tzinfo:
                        t = t.replace(tzinfo=None)
                else:
                    continue
                if t < since:
                    continue
            except Exception:
                continue
        out.append(r)
    out.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return out[:limit]

def save_deposit_history(deposit_data):
    try:
        os.makedirs('data', exist_ok=True)
        with open('data/deposit.json', 'w', encoding='utf-8') as f:
            json.dump(deposit_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"충전 내역 저장 오류: {e}")
def load_deposit_history():
    try:
        if os.path.exists('data/deposit.json'):
            with open('data/deposit.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        print(f"충전 내역 로드 오류: {e}")
        return []

class ChargeMethodView(nextcord.ui.View):
    def __init__(self, guild_id=None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        cfg = load_payment_config(guild_id)
        if cfg.get("bank", True):
            b = nextcord.ui.Button(label="계좌이체", emoji="🏦", style=nextcord.ButtonStyle.secondary, custom_id="bank_transfer")
            b.callback = self.bank_transfer_button
            self.add_item(b)
        if cfg.get("gift", True):
            g = nextcord.ui.Button(label="문화상품권", emoji="🎫", style=nextcord.ButtonStyle.primary, custom_id="gift_charge")
            g.callback = self.gift_charge_button
            self.add_item(g)
    async def bank_transfer_button(self, interaction: nextcord.Interaction):
        await interaction.response.send_modal(BankTransferModal())
    async def gift_charge_button(self, interaction: nextcord.Interaction):
        await interaction.response.send_modal(GiftChargeModal())

class PurchaseHistorySelectView(nextcord.ui.View):
    def __init__(self, guild_id, user_id, records, timeout=120):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.user_id = user_id
        self.records = records
        if records:
            options = []
            for i, r in enumerate(records):
                product_name = (r.get("product_name") or "제품")[: 80]
                date_str = (r.get("date") or r.get("timestamp", "")[:16])[: 20]
                label = f"{product_name} - {date_str}"[: 100]
                options.append(nextcord.SelectOption(label=label, value=str(i), description=f"{r.get('final_price', 0):,}원"))
            sel = nextcord.ui.Select(
                placeholder="최근 5건 중 선택",
                options=options,
                custom_id="purchase_history_select"
            )
            sel.callback = self._on_select
            self.add_item(sel)

    async def _on_select(self, interaction: nextcord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("본인만 선택할 수 있습니다.", ephemeral=True)
            return
        values = interaction.data.get("values", [])
        if not values:
            await interaction.response.send_message("선택된 항목이 없습니다.", ephemeral=True)
            return
        idx = int(values[0])
        r = self.records[idx]
        product_name = r.get("product_name", "-")
        quantity = r.get("quantity", 0)
        price = r.get("price", 0)
        final_price = r.get("final_price", 0)
        date_str = r.get("date") or (r.get("timestamp", "")[:16] if r.get("timestamp") else "-")
        embed = nextcord.Embed(title="📦 구매 내역", color=0x8B5CF6)
        embed.add_field(name="제품", value=f"**{product_name}**", inline=False)
        embed.add_field(name="수량", value=f"{quantity}개", inline=True)
        embed.add_field(name="단가", value=f"{price:,}원", inline=True)
        embed.add_field(name="결제 금액", value=f"{final_price:,}원", inline=True)
        embed.add_field(name="구매 일시", value=date_str, inline=False)
        items = r.get("items") or []
        if items:
            items_text = "\n".join(items)
            if len(items_text) > 1000:
                items_text = items_text[:997] + "..."
            embed.add_field(name="📦 구매한 제품 정보", value=f"```\n{items_text}\n```", inline=False)
        else:
            embed.add_field(name="📦 구매한 제품 정보", value="*(저장된 내역 없음)*", inline=False)
        embed.set_footer(text="구매 후 여기서 확인할 수 있어요")
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ProductListView(nextcord.ui.View):
    def __init__(self, guild_id=None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        buy_btn = nextcord.ui.Button(label="구매", style=nextcord.ButtonStyle.secondary, custom_id="button_4_2")
        buy_btn.callback = self.handle_buy
        self.add_item(buy_btn)
        product_btn = nextcord.ui.Button(label="제품", style=nextcord.ButtonStyle.secondary, custom_id="button_4_1")
        product_btn.callback = self.product_button_handler
        self.add_item(product_btn)
        charge_btn = nextcord.ui.Button(label="충전", style=nextcord.ButtonStyle.secondary, custom_id="button_4_4")
        charge_btn.callback = self.handle_charge
        self.add_item(charge_btn)
        info_btn = nextcord.ui.Button(label="정보", style=nextcord.ButtonStyle.secondary, custom_id="button_4_3")
        info_btn.callback = self.handle_myinfo
        self.add_item(info_btn)
    async def product_button_handler(self, interaction: nextcord.Interaction):
        if not on_run:
            return
        await interaction.response.defer(ephemeral=True)
        embed = nextcord.Embed(title="🛍️ㆍ상품 조회", color=0xfffffe)
        embed.add_field(name="", value="조회할 상품의 카테고리를 선택하세요.", inline=False)
        await interaction.followup.send(embed=embed, view=CategorySelectView(interaction.guild.id), ephemeral=True)
    async def handle_buy(self, interaction: nextcord.Interaction):
        if not on_run:
            return
        await interaction.response.defer(ephemeral=True)
        embed = nextcord.Embed(title="🛒ㆍ구매할 카테고리 선택", color=0xfffffe)
        embed.add_field(name="", value="구매할 상품의 카테고리를 선택하세요.", inline=False)
        await interaction.followup.send(embed=embed, view=CategorySelectView(interaction.guild.id, is_purchase=True), ephemeral=True)
    async def handle_charge(self, interaction: nextcord.Interaction):
        if not on_run:
            return
        await interaction.response.defer(ephemeral=True)
        _gid = interaction.guild.id if interaction.guild else None
        cfg = load_payment_config(_gid)
        if not cfg.get("bank", True) and not cfg.get("gift", True):
            await interaction.followup.send("사용 가능한 충전 수단이 없어요. 관리자에게 문의해주세요.", ephemeral=True)
            return
        embed = nextcord.Embed(title="💰ㆍ충전 방식 선택", color=0x00ff00)
        embed.add_field(name="", value="충전 방식을 선택해주세요.", inline=False)
        await interaction.followup.send(embed=embed, view=ChargeMethodView(_gid), ephemeral=True)
    async def handle_purchase_history(self, interaction: nextcord.Interaction):
        if not on_run:
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id if interaction.guild else 0
        records = get_user_buy_history_since(guild_id, interaction.user.id, None, 5)
        if not records:
            no_embed = nextcord.Embed(
                title="📦 구매 내역",
                description="구매 내역이 없습니다.",
                color=0x8B5CF6
            )
            no_embed.set_footer(text="구매 후 여기서 확인할 수 있어요")
            await interaction.followup.send(embed=no_embed, ephemeral=True)
            return
        embed = nextcord.Embed(
            title="📦 구매 내역",
            description="아래에서 확인할 구매 건을 선택하세요. (최근 5건)",
            color=0x8B5CF6
        )
        await interaction.followup.send(embed=embed, view=PurchaseHistorySelectView(guild_id, interaction.user.id, records), ephemeral=True)
    async def handle_myinfo(self, interaction: nextcord.Interaction):
        if not on_run:
            return
        await interaction.response.defer(ephemeral=True)
        user_data = get_user_data(interaction.guild.id, interaction.user.id)
        
        # 이용금액 기준 등급 역할 자동 부여 (내정보 열 때 동기화 — 달성한 모든 등급 부여)
        if interaction.guild and user_data.get("total_spent", 0) >= 1:
            member = interaction.guild.get_member(interaction.user.id)
            if member:
                for role_id in get_achieved_vip_role_ids(user_data["total_spent"], interaction.guild.id if interaction.guild else None):
                    role = interaction.guild.get_role(role_id)
                    if role and role not in member.roles:
                        try:
                            await member.add_roles(role)
                        except Exception:
                            pass
        
        if user_data['total_spent'] == 0:
            display_level = "유저"
        else:
            display_level = get_vip_level(user_data["total_spent"])
        
        level_colors = {
            "유저": 0x808080,
            "구매자": 0x00ff00,
            "10만": 0x1abc9c,
            "25만": 0x3498db,
            "50만": 0x9b59b6,
            "75만": 0xe67e22,
            "100만": 0xffd700
        }
        level_emojis = {
            "유저": "👤",
            "구매자": "🛒",
            "10만": "🎫",
            "25만": "⭐",
            "50만": "🌟",
            "75만": "💫",
            "100만": "✨"
        }
        display_name = TIER_DISPLAY_NAMES.get(display_level, display_level) if display_level != "유저" else display_level
        emoji = level_emojis.get(display_level, "👤")
        color = level_colors.get(display_level, 0x808080)
        
        embed = nextcord.Embed(
            title=f"{emoji} 내 정보",
            description=f"━━━━━━━━━━━━━━━━━━━━━━━\n**{interaction.user.name}**님의 정보입니다\n━━━━━━━━━━━━━━━━━━━━━━━",
            color=color
        )
        
        embed.add_field(
            name="💰 잔액",
            value=f"```\n{user_data['balance']:,}원\n```",
            inline=True
        )
        
        embed.add_field(
            name="💳 총 구매액",
            value=f"```\n{user_data['total_spent']:,}원\n```",
            inline=True
        )
        
        # 등급 혜택 제거됨
        # 등급 안내 제거됨
        
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"ID: {interaction.user.id} • {interaction.guild.name}", icon_url=interaction.user.display_avatar.url)
        await interaction.followup.send(embed=embed, ephemeral=True)
class CategorySelect(nextcord.ui.Select):
    def __init__(self, guilid, is_purchase=False):
        self.is_purchase = is_purchase
        categories = get_categories_from_stock_folder(guilid)
        options = []
        
        if categories:
            for category in categories[:25]:
                emoji = get_category_emoji(category)
                label = f"{emoji} {category}" if emoji else category
                options.append(nextcord.SelectOption(label=label, description=f"{category} 카테고리", value=category))
        else:
            options.append(nextcord.SelectOption(label='전체', description='모든 제품', value='all'))
        
        if not options:
            options = [nextcord.SelectOption(label='카테고리가 없습니다', description='제품이 없습니다', value='none')]
            super().__init__(custom_id='category_dropdown', placeholder='선택할 카테고리가 없습니다', min_values=1, max_values=1, options=options, disabled=True)
        else:
            placeholder = '구매할 카테고리를 선택해주세요.' if is_purchase else '조회할 카테고리를 선택해주세요.'
            super().__init__(custom_id='category_dropdown', placeholder=placeholder, min_values=1, max_values=1, options=options)
    async def callback(self, interaction: nextcord.Interaction):
        if interaction.response.is_done():
            return
        await interaction.response.defer(ephemeral=True)
        category = self.values[0]
        
        if category == 'all':
            products = get_products_from_stock_folder(_gid(interaction))
            embed_title = "🛍️ㆍ전체 상품 목록"
        elif category == 'none':
            await interaction.followup.send("표시할 제품이 없습니다.", ephemeral=True)
            return
        else:
            products = get_products_from_category(category, _gid(interaction))
            embed_title = "🛍️ㆍ제품 목록"
        if not products:
            embed = nextcord.Embed(title=embed_title, color=0xfffffe)
            embed.add_field(name="", value="등록된 상품이 없습니다.", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        if self.is_purchase:
            purchase_view = ProductSelectView(interaction.guild.id, category)
            embed = ProductListPaginationView.create_embed(products, category, embed_title, 0, interaction.guild.id if interaction.guild else None)
            view = ProductListPaginationView(products, category, embed_title, 0, purchase_view, interaction.guild.id)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            embed = ProductListPaginationView.create_embed(products, category, embed_title, 0, interaction.guild.id if interaction.guild else None)
            view = ProductListPaginationView(products, category, embed_title, 0, None, interaction.guild.id)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
class ProductListPaginationView(nextcord.ui.View):
    PRODUCTS_PER_PAGE = 8
    
    def __init__(self, products, category, embed_title, current_page=0, purchase_view=None, guild_id=None):
        super().__init__(timeout=300)
        self.products = products
        self.category = category
        self.embed_title = embed_title
        self.current_page = current_page
        self.purchase_view = purchase_view
        self.guild_id = guild_id
        self.total_pages = (len(products) + self.PRODUCTS_PER_PAGE - 1) // self.PRODUCTS_PER_PAGE if products else 1
        
        if self.purchase_view:
            for item in self.purchase_view.children:
                if isinstance(item, nextcord.ui.Select):
                    self.add_item(item)
        
        if self.current_page > 0:
            prev_btn = nextcord.ui.Button(label="<", style=nextcord.ButtonStyle.secondary, custom_id="product_list_prev")
            prev_btn.callback = self.prev_page
            self.add_item(prev_btn)
        
        next_btn = nextcord.ui.Button(label=">", style=nextcord.ButtonStyle.secondary, custom_id="product_list_next")
        next_btn.callback = self.next_page
        self.add_item(next_btn)
    
    @staticmethod
    def create_embed(products, category, embed_title, page=0, guild_id=None):
        embed = nextcord.Embed(title=embed_title, color=0xfffffe)
        
        if not products:
            embed.add_field(name="", value="등록된 상품이 없습니다.", inline=False)
            return embed
        
        PRODUCTS_PER_PAGE = 8
        total_pages = (len(products) + PRODUCTS_PER_PAGE - 1) // PRODUCTS_PER_PAGE
        start_idx = page * PRODUCTS_PER_PAGE
        end_idx = min(start_idx + PRODUCTS_PER_PAGE, len(products))
        
        products_list = list(products.items())
        products_list.sort(key=lambda x: x[1].get('price', 0))
        
        for i in range(start_idx, end_idx):
            if i >= len(products_list):
                break
            product_name, product_data = products_list[i]
            
            product_emoji = ""
            if category and category != 'all':
                product_emoji = get_product_emoji(category, product_name)
            else:
                for cat in get_categories_from_stock_folder():
                    emoji = get_product_emoji(cat, product_name)
                    if emoji:
                        product_emoji = emoji
                        break
            
            field_name = f"{product_emoji} {product_name}" if product_emoji else product_name
            if guild_id is not None and is_eternal_product(guild_id, product_name):
                field_value = f"{product_data['price']:,}원\n재고 ♾️ 영구"
            else:
                field_value = f"{product_data['price']:,}원\n재고 {product_data['stock']:,}개"
            embed.add_field(name=field_name, value=field_value, inline=False)
        
        if total_pages > 1:
            embed.set_footer(text=f"페이지 {page + 1} / {total_pages}")
        
        return embed
    
    async def settings_callback(self, interaction: nextcord.Interaction):
        if not is_admin(interaction):
            embed = nextcord.Embed(title="⛔ 권한 없음", color=0xff0000)
            embed.add_field(name="", value="관리자만 설정에 접근할 수 있습니다.", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        embed = nextcord.Embed(title="⚙️ 설정", color=0x00ff00)
        embed.add_field(name="", value="재고 관리 및 설정 메뉴입니다.", inline=False)
        embed.add_field(name="재고 관리", value="재고수정 버튼을 사용하여 재고를 관리하세요.", inline=False)
        embed.add_field(name="카테고리 추가", value="카테고리 추가 버튼을 눌러 새로운 카테고리를 추가합니다.", inline=False)
        embed.add_field(name="제품 추가", value="제품 추가 버튼을 눌러 새로운 제품을 추가합니다.", inline=False)
        embed.add_field(name="가격수정", value="가격수정 버튼을 눌러 제품 가격을 수정합니다.", inline=False)
        embed.add_field(name="재고수정", value="재고수정 버튼을 눌러 제품 재고를 수정합니다.", inline=False)
        await interaction.response.send_message(embed=embed, view=StockManagementView(interaction.guild.id if interaction.guild else None), ephemeral=True)
    
    async def prev_page(self, interaction: nextcord.Interaction):
        await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
            embed = self.create_embed(self.products, self.category, self.embed_title, self.current_page, self.guild_id)
            view = ProductListPaginationView(self.products, self.category, self.embed_title, self.current_page, self.purchase_view, self.guild_id)
            await interaction.edit_original_message(embed=embed, view=view)
    
    async def next_page(self, interaction: nextcord.Interaction):
        await interaction.response.defer()
        self.current_page += 1
        if self.current_page >= self.total_pages:
            self.current_page = 0
        embed = self.create_embed(self.products, self.category, self.embed_title, self.current_page)
        view = ProductListPaginationView(self.products, self.category, self.embed_title, self.current_page, self.purchase_view, self.guild_id)
        await interaction.edit_original_message(embed=embed, view=view)
    
    async def on_timeout(self):
        for item in self.children:
            if isinstance(item, nextcord.ui.Button):
                item.disabled = True
class CategorySelectView(nextcord.ui.View):
    def __init__(self, guilid, is_purchase=False):
        super().__init__(timeout=None)
        self.add_item(CategorySelect(guilid, is_purchase))
class ProductSelectView(nextcord.ui.View):
    def __init__(self, guilid, category=None):
        super().__init__(timeout=None)
        self.add_item(ProductSelectSelect(guilid, category))
class ProductSelectSelect(nextcord.ui.Select):
    def __init__(self, guilid, category=None):
        self.guilid = guilid
        if category and category != 'all':
            products = get_products_from_category(category, guilid)
        else:
            products = get_products_from_stock_folder(guilid)
        
        products_list = list(products.items())
        products_list.sort(key=lambda x: x[1].get('price', 0))
        
        options = []
        for product_name, product_data in products_list[:25]:
            product_emoji = ""
            if category and category != 'all':
                product_emoji = get_product_emoji(category, product_name)
            
            label = f"{product_emoji} {product_data['name']}" if product_emoji else product_data['name']
            if is_eternal_product(guilid, product_name):
                description = f"{product_data['price']:,}원 | ♾️ 영구"
            else:
                description = f"{product_data['price']:,}원 | 재고 {product_data['stock']:,}개"
            value = product_name
            options.append(nextcord.SelectOption(label=label, description=description, value=value))
        if not options:
            options = [nextcord.SelectOption(label='상품이 없습니다', description='재고가 없습니다', value='none')]
            super().__init__(custom_id='my_dropdown', placeholder='선택할 상품이 없습니다', min_values=1, max_values=1, options=options, disabled=True)
        else:
            placeholder = '구매하실 제품을 선택해주세요.'
            super().__init__(custom_id='my_dropdown', placeholder=placeholder, min_values=1, max_values=1, options=options)
    async def callback(self, interaction: nextcord.Interaction):
        products = get_products_from_stock_folder(_gid(interaction))
        product_name = self.values[0]
        
        if product_name not in products:
            await interaction.response.send_message("제품을 찾을 수 없습니다.", ephemeral=True)
            return
            
        product_data = products[product_name]
        if product_data['stock'] == 0:
            await interaction.response.send_message("재고가 부족해요.", ephemeral=True)
            return
        await interaction.response.send_modal(PurChaseInfo(product_data['name'], product_data['stock'], is_eternal_product(interaction.guild.id if interaction.guild else None, product_data['name'])))
class BankTransferModal(nextcord.ui.Modal):
    def __init__(self):
        super().__init__(
            title="계좌이체 충전",
            custom_id="bank_transfer_modal",
            timeout=None
        )
        
        self.depositor_name = nextcord.ui.TextInput(
            label="입금자명을 입력하세요",
            placeholder="예: 홍길동",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="depositor_name"
        )
        self.add_item(self.depositor_name)
        
        self.charge_amount = nextcord.ui.TextInput(
            label="충전할 금액을 입력하세요",
            placeholder="예: 10000",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="charge_amount"
        )
        self.add_item(self.charge_amount)
    async def callback(self, interaction: nextcord.Interaction) -> None:
        depositor_name = self.depositor_name.value
        charge_amount_str = self.charge_amount.value
        
        # 응답 전송 헬퍼 함수
        async def safe_response(message_or_embed, ephemeral=True):
            try:
                if not interaction.response.is_done():
                    if isinstance(message_or_embed, str):
                        await interaction.response.send_message(message_or_embed, ephemeral=ephemeral)
                    else:
                        await interaction.response.send_message(embed=message_or_embed, ephemeral=ephemeral)
                else:
                    if isinstance(message_or_embed, str):
                        await interaction.followup.send(message_or_embed, ephemeral=ephemeral)
                    else:
                        await interaction.followup.send(embed=message_or_embed, ephemeral=ephemeral)
            except nextcord.errors.NotFound:
                # Interaction이 만료된 경우 무시
                pass
            except Exception as e:
                print(f"모달 응답 전송 오류: {e}")
        
        try:
            charge_amount = int(charge_amount_str)
            if charge_amount < MIN_CHARGE_AMOUNT:
                await safe_response(f"최소 충전 금액은 {MIN_CHARGE_AMOUNT:,}원입니다.", ephemeral=True)
                return
            if charge_amount > MAX_PRICE:
                await safe_response(f"최대 충전 금액은 {MAX_PRICE:,}원입니다.", ephemeral=True)
                return
        except ValueError:
            await safe_response("올바른 금액을 입력해주세요.", ephemeral=True)
            return
        
        try:
            PENDING_CHARGES[interaction.user.id] = {
                "amount": charge_amount,
                "depositor_name": depositor_name,
                "guild_id": interaction.guild.id if interaction.guild else None,
                "timestamp": datetime.datetime.now().timestamp()
            }
            
            user = interaction.user
            
            dm_ok = False
            try:
                dm_embed = nextcord.Embed(title="🏦ㆍ계좌이체 충전 안내", color=0x00ff00)
                dm_embed.add_field(name="계좌 정보", value=f"**{get_bank_account(interaction.guild.id if interaction.guild else None)}**", inline=False)
                dm_embed.add_field(name="충전 금액", value=f"**{charge_amount:,}원**", inline=True)
                dm_embed.add_field(name="입금자명", value=f"**{depositor_name}**", inline=True)
                dm_embed.add_field(name="충전 방법", value="위 계좌로 입금하세요. 입금 확인 시 자동 승인됩니다.", inline=False)
                dm_embed.timestamp = datetime.datetime.now()
                await user.send(embed=dm_embed)
                dm_ok = True
            except Exception:
                pass
            
            view = BankRequestApproveView(user.id, charge_amount, depositor_name, interaction.channel.id if interaction.channel else None, interaction.guild.id if interaction.guild else None)
            
            try:
                req_channel = bot.get_channel(get_guild_channels(interaction.guild.id if interaction.guild else None).get("charge"))
                if req_channel:
                    rand_code = random.randint(100000, 999999)
                    request_embed = nextcord.Embed(title="💸 충전요청", color=0x00b894)
                    request_embed.add_field(name="사용자이름", value=user.name, inline=True)
                    request_embed.add_field(name="입금자명", value=depositor_name, inline=True)
                    request_embed.add_field(name="금액", value=f"{charge_amount:,}원", inline=True)
                    request_embed.add_field(name="랜덤값", value=str(rand_code), inline=True)
                    request_msg = await req_channel.send(embed=request_embed, view=view)
                    PENDING_CHARGES[interaction.user.id]["message_id"] = request_msg.id
            except Exception as ex:
                print(f"BANK_REQUEST 채널 전송 오류: {ex}")
            
            if dm_ok:
                await safe_response("DM으로 충전 안내를 보내드렸습니다. DM을 확인해주세요.", ephemeral=True)
            else:
                await safe_response("DM 전송에 실패했습니다. DM 설정을 확인해주세요.", ephemeral=True)
        
        except Exception as e:
            error_embed = nextcord.Embed(title="❌ 오류 발생", color=0xff0000)
            error_embed.add_field(name="오류", value="충전 안내 처리 중 오류가 발생했습니다.", inline=False)
            await safe_response(error_embed, ephemeral=True)
class GiftChargeModal(nextcord.ui.Modal):
    def __init__(self):
        super().__init__(title="문화상품권 충전", custom_id="gift_charge_modal", timeout=None)
        self.pin_input = nextcord.ui.TextInput(
            label="컬쳐랜드 PIN 번호 (18자리)",
            placeholder="예: 1234-1234-1234-123456",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="gift_pin",
            max_length=24,
        )
        self.add_item(self.pin_input)
        self.amount_input = nextcord.ui.TextInput(
            label="충전할 금액",
            placeholder="예: 10000",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="gift_amount",
            max_length=12,
        )
        self.add_item(self.amount_input)
    async def callback(self, interaction: nextcord.Interaction) -> None:
        raw_pin = (self.pin_input.value or "").strip()
        pin = re.sub(r"[\s\-]", "", raw_pin)
        if len(pin) != 18 or not pin.isdigit():
            await interaction.response.send_message("컬쳐랜드 PIN 번호는 숫자 18자리예요. (예: 1234-1234-1234-123456)", ephemeral=True)
            return
        try:
            amount = validate_positive_int(self.amount_input.value, MAX_PRICE)
            if amount < MIN_CHARGE_AMOUNT:
                await interaction.response.send_message(f"최소 충전 금액은 {MIN_CHARGE_AMOUNT:,}원입니다.", ephemeral=True)
                return
        except ValueError as e:
            await interaction.response.send_message(f"올바른 금액을 입력해주세요: {e}", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        PENDING_CHARGES[interaction.user.id] = {
            "amount": amount,
            "depositor_name": f"문상PIN:{raw_pin}",
            "pin": raw_pin,
            "method": "gift",
            "guild_id": interaction.guild.id if interaction.guild else None,
            "timestamp": datetime.datetime.now().timestamp(),
        }
        view = GiftChargeApproveView(interaction.user.id, amount, raw_pin, interaction.guild.id if interaction.guild else None)
        req_embed = nextcord.Embed(title="🎫 문화상품권 충전요청", color=0xf1c40f)
        req_embed.add_field(name="사용자", value=f"<@{interaction.user.id}> ({interaction.user.name})", inline=False)
        req_embed.add_field(name="금액", value=f"{amount:,}원", inline=True)
        req_embed.add_field(name="PIN 번호", value=f"`{raw_pin}`", inline=False)
        req_embed.timestamp = datetime.datetime.now()
        sent = False
        try:
            req_channel = bot.get_channel(get_guild_channels(interaction.guild.id if interaction.guild else None).get("charge"))
            if req_channel:
                msg = await req_channel.send(embed=req_embed, view=view)
                PENDING_CHARGES[interaction.user.id]["message_id"] = msg.id
                sent = True
        except Exception as ex:
            print(f"문상 충전요청 채널 전송 오류: {ex}")
        for m in get_guild_admins(interaction.guild):
            try:
                await m.send(embed=req_embed, view=view)
                sent = True
            except Exception:
                continue
        if sent:
            await interaction.followup.send("문화상품권 충전 요청을 관리자에게 보냈어요. 승인되면 잔액에 들어와요.", ephemeral=True)
        else:
            await interaction.followup.send("관리자에게 요청을 보내지 못했어요. 관리자 채널/DM 설정을 확인해주세요.", ephemeral=True)


class GiftChargeApproveView(nextcord.ui.View):
    def __init__(self, user_id: int, amount: int, pin: str, guild_id=None):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.amount = amount
        self.pin = pin
        self.guild_id = guild_id
        self.done = False
    def _is_admin(self, interaction: nextcord.Interaction) -> bool:
        return is_admin(interaction, self.guild_id)
    @nextcord.ui.button(label="승인", style=nextcord.ButtonStyle.success, custom_id="gift_charge_approve")
    async def approve(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if not self._is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        if self.done:
            await interaction.response.send_message("이미 처리된 요청입니다.", ephemeral=True)
            return
        self.done = True
        await interaction.response.defer()
        guild_id = interaction.guild.id if interaction.guild else (list(bot.guilds)[0].id if bot.guilds else 0)
        credited = apply_reseller_bonus_to_charge(self.amount, self.user_id, interaction.guild)
        user_data = get_user_data(guild_id, self.user_id)
        user_data["balance"] = safe_add(user_data.get("balance", 0), credited)
        update_user_data(guild_id, self.user_id, user_data)
        add_deposit_record(guild_id, self.user_id, credited, method="문화상품권", depositor_name=f"문상PIN:{self.pin}")
        await send_charge_log(guild_id, self.user_id, credited, "문화상품권", f"문상PIN:{self.pin}", user_data.get("balance"))
        try:
            buyer = bot.get_user(self.user_id) or await bot.fetch_user(self.user_id)
            if buyer:
                dm = nextcord.Embed(title="충전되었습니다", color=0x00ff00)
                dm.add_field(name="충전 수단", value="문화상품권", inline=True)
                dm.add_field(name="충전 금액", value=f"{credited:,}원", inline=True)
                dm.add_field(name="충전 후 잔액", value=f"{user_data['balance']:,}원", inline=True)
                await buyer.send(embed=dm)
        except Exception:
            pass
        for child in self.children:
            if isinstance(child, nextcord.ui.Button):
                child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass
        if self.user_id in PENDING_CHARGES:
            try:
                del PENDING_CHARGES[self.user_id]
            except Exception:
                pass
        await interaction.followup.send(f"승인 완료. <@{self.user_id}>에게 {credited:,}원 충전했어요.", ephemeral=True)
    @nextcord.ui.button(label="거절", style=nextcord.ButtonStyle.danger, custom_id="gift_charge_reject")
    async def reject(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if not self._is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        if self.done:
            await interaction.response.send_message("이미 처리된 요청입니다.", ephemeral=True)
            return
        self.done = True
        for child in self.children:
            if isinstance(child, nextcord.ui.Button):
                child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass
        try:
            buyer = bot.get_user(self.user_id) or await bot.fetch_user(self.user_id)
            if buyer:
                await buyer.send("문화상품권 충전이 거절되었어요. PIN 번호를 확인해주세요.")
        except Exception:
            pass
        if self.user_id in PENDING_CHARGES:
            try:
                del PENDING_CHARGES[self.user_id]
            except Exception:
                pass
        await interaction.response.send_message("거절했어요.", ephemeral=True)
class MultiApprovalModal(nextcord.ui.Modal):
    def __init__(self, user_id, original_amount, depositor_name=None):
        super().__init__(
            title="금액수정 승인",
            custom_id="multi_approval",
            timeout=None
        )
        self.user_id = user_id
        self.original_amount = original_amount
        self.depositor_name = depositor_name
        
        self.amount_field = nextcord.ui.TextInput(
            label="승인할 금액을 입력하세요",
            placeholder=f"요청 금액: {original_amount:,}원",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="multi_amount"
        )
        self.add_item(self.amount_field)
    
    async def callback(self, interaction: nextcord.Interaction) -> None:
        if interaction.response.is_done():
            return
        
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있는 기능입니다.", ephemeral=True)
            return
        
        try:
            amount = validate_positive_int(self.amount_field.value, MAX_PRICE)
            
            if amount <= 0:
                await interaction.response.send_message("금액은 0보다 커야 합니다.", ephemeral=True)
                return
            
            if amount > MAX_PRICE:
                await interaction.response.send_message(f"금액은 {MAX_PRICE:,}원을 초과할 수 없습니다.", ephemeral=True)
                return
            
            user = bot.get_user(self.user_id)
            if user:
                credited_amount = apply_reseller_bonus_to_charge(amount, self.user_id, interaction.guild)
                
                user_data = get_user_data(interaction.guild.id, self.user_id)
                user_data['balance'] = safe_add(user_data['balance'], credited_amount)
                update_user_data(interaction.guild.id, self.user_id, user_data)
                
                depositor_name = self.depositor_name
                if not depositor_name and self.user_id in PENDING_CHARGES:
                    depositor_name = PENDING_CHARGES[self.user_id].get('depositor_name')
                
                add_deposit_record(interaction.guild.id, self.user_id, credited_amount, "계좌이체 (금액수정 승인)", None, depositor_name=depositor_name)
                await send_charge_log(interaction.guild.id, self.user_id, credited_amount, "계좌이체 (금액수정 승인)", depositor_name, user_data.get("balance"))
                
                embed = nextcord.Embed(title="승인되었습니다", color=0x00ff00)
                if depositor_name:
                    embed.add_field(name="입금자명", value=depositor_name, inline=True)
                if credited_amount > amount:
                    embed.add_field(name="입금 금액", value=f"{amount:,}원", inline=True)
                    embed.add_field(name="리셀러 추가 충전 (30%)", value=f"+{credited_amount - amount:,}원", inline=True)
                embed.add_field(name="총 충전 금액", value=f"{credited_amount:,}원", inline=True)
                embed.add_field(name="충전 후 잔액", value=f"{user_data['balance']:,}원", inline=True)
                await user.send(embed=embed)
            
            admin_embed = nextcord.Embed(title="승인되었습니다", color=0x00ff00)
            depositor_name = self.depositor_name
            if not depositor_name and self.user_id in PENDING_CHARGES:
                depositor_name = PENDING_CHARGES[self.user_id].get('depositor_name')
            if depositor_name:
                admin_embed.add_field(name="입금자명", value=depositor_name, inline=True)
            if credited_amount > amount:
                admin_embed.add_field(name="입금 금액", value=f"{amount:,}원", inline=True)
                admin_embed.add_field(name="리셀러 추가 충전 (30%)", value=f"+{credited_amount - amount:,}원", inline=True)
            admin_embed.add_field(name="총 충전 금액", value=f"{credited_amount:,}원", inline=True)
            user_data = get_user_data(interaction.guild.id, self.user_id)
            admin_embed.add_field(name="충전 후 잔액", value=f"{user_data['balance']:,}원", inline=True)
            admin_embed.set_footer(text=f"관리자: {interaction.user.name}")
            admin_embed.timestamp = datetime.datetime.now()
            
            await interaction.response.send_message(embed=admin_embed)
            
            if self.user_id in PENDING_CHARGES:
                try:
                    del PENDING_CHARGES[self.user_id]
                except Exception:
                    pass
            
        except ValueError:
            await interaction.response.send_message("올바른 금액을 입력해주세요.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"금액수정 승인 처리 중 오류가 발생했습니다: {e}", ephemeral=True)

class PurChaseInfo(nextcord.ui.Modal):
    def __init__(self, product_title, invent, eternal=False):
        super().__init__(
            title=f"{product_title}ㆍ제품 구매",
            custom_id="purchase",
            timeout=None
        )
        if eternal:
            placeholder_text = "♾️ 영구 상품 (원하는 수량 입력)"
        else:
            placeholder_text = f"현재 재고: {invent}개 (최대 {invent}개까지 구매 가능)" if invent > 0 else "재고가 없습니다."
        self.field = nextcord.ui.TextInput(
            label="구매할 수량을 입력해주세요.",
            placeholder=placeholder_text,
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="amont",
        )
        self.add_item(self.field)
        self.product_title = product_title
        self.invent = invent
    async def callback(self, interaction: nextcord.Interaction) -> None:
        # 3초 이내 응답 필수 — 먼저 defer로 응답한 뒤 나머지는 followup으로 처리
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except nextcord.errors.NotFound:
            return  # interaction 이미 만료
        _send = lambda msg, **kw: interaction.followup.send(msg, ephemeral=True, **kw)

        try:
            amount = validate_positive_int(self.children[0].value, MAX_QUANTITY)
            if amount <= 0:
                await _send("수량은 1개 이상이어야 합니다.")
                return
        except ValueError as e:
            await _send(f"올바른 수량을 입력해주세요: {str(e)}")
            return
        except Exception:
            await _send(f"정확한 수량을 입력해주세요.\n입력 : {self.children[0].value}")
            return

        if interaction.guild is None:
            await _send("이 기능은 서버에서만 사용할 수 있습니다.")
            return

        current_stock = get_product_stock_count(interaction.guild.id, self.product_title)
        if not is_eternal_product(interaction.guild.id, self.product_title) and current_stock < amount:
            await _send("⛔ㆍ재고가 부족해요.")
            return

        products = get_products_from_stock_folder(_gid(interaction))
        if self.product_title not in products:
            await _send("제품을 찾을 수 없습니다.")
            return

        product_data = products[self.product_title]
        user_data = get_user_data(interaction.guild.id, interaction.user.id)

        if product_data['price'] <= 0:
            await _send("제품 가격이 올바르지 않습니다.")
            return

        try:
            total_price = safe_multiply(product_data['price'], amount)
            if total_price <= 0:
                await _send("계산된 가격이 올바르지 않습니다.")
                return
        except ValueError as e:
            await _send(f"가격 계산 오류: {str(e)}")
            return

        vip_level = get_vip_level(user_data['total_spent'])
        discount_amount = calculate_discount(total_price, vip_level)
        discount_percent_for_display = VIP_LEVELS[vip_level]['discount']
        final_price = total_price - discount_amount

        if final_price <= 0:
            await _send("최종 결제 금액이 올바르지 않습니다.")
            return

        embed = nextcord.Embed(title="구매 확인", color=0xfffffe)
        name_string = f"{self.product_title} {str(amount)}개"
        embed.add_field(name=f"`{name_string}` - **{total_price:,}원**", value="", inline=False)
        if discount_amount > 0:
            embed.add_field(name="할인", value=f"할인: {discount_amount:,}원 ({discount_percent_for_display}%)", inline=False)
            embed.add_field(name="최종 결제 금액", value=f"**{final_price:,}원**", inline=False)
        embed.add_field(name="구매하시겠습니까?", value=f"- **{interaction.user.name}**님은 **{user_data['balance']:,}원** 보유중 입니다.", inline=False)

        product_data_array = [self.product_title, str(amount), total_price]
        try:
            await interaction.user.send(embed=embed, view=ProductBuyView(product_data_array, interaction.guild.id))
            try:
                await _send("💬ㆍDM을 확인해주세요.")
            except (nextcord.errors.NotFound, nextcord.errors.HTTPException):
                pass
        except nextcord.Forbidden:
            try:
                await _send("DM 전송에 실패했습니다. DM 설정을 확인해주세요.")
            except (nextcord.errors.NotFound, nextcord.errors.HTTPException):
                pass
        except Exception:
            try:
                await _send("DM 전송에 실패했습니다. DM 설정을 확인해주세요.")
            except (nextcord.errors.NotFound, nextcord.errors.HTTPException):
                pass
@bot.slash_command(name="자판기", description=f"{SERVICE_NAME} | 자동판매기를 세팅합니다.")
async def callback(interaction: nextcord.Interaction, 채널: nextcord.TextChannel = SlashOption(description="자판기를 보낼 채널", required=True)):
    if on_run:
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
            return
        
        embed = nextcord.Embed(title=get_vending_title(interaction.guild.id), description=get_vending_description(interaction.guild.id), color=0xfffffe)
        
        await interaction.response.send_message(f"자판기를 <#{채널.id}>에 전송 중입니다...", ephemeral=True)
        
        _vimg = get_vending_image_url(interaction.guild.id)
        if _vimg:
            embed.set_image(url=_vimg)
        embed.set_footer(text="Made By Pa")
        await 채널.send(embed=embed, view=ProductListView(guild_id=interaction.guild.id))
        
        await interaction.edit_original_message(content=f"자판기가 <#{채널.id}>에 성공적으로 전송되었습니다.")
@bot.slash_command(name="정보", description=f"{SERVICE_NAME} | 서버 정보를 확인합니다.")
async def callback(interaction: nextcord.Interaction):
    if on_run:
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
            return
        
        _ch = get_guild_channels(interaction.guild.id)
        def ch_ref(cid):
            return f"<#{cid}>" if cid is not None else "미설정"
        embed = nextcord.Embed(title=f"📄ㆍ{interaction.guild.name}", color=0xfffffe)
        embed.add_field(name=f" ",
        value=f"**서버 ID** : `{interaction.guild.id}`\n**서버 이름** : `{interaction.guild.name}`\n**구매 로그** : {ch_ref(_ch.get('purchase_log'))}\n**충전 요청** : {ch_ref(_ch.get('charge'))}\n**충전 완료 로그** : {ch_ref(_ch.get('charge_log'))}\n**최소충전금** : `{MIN_CHARGE_AMOUNT:,}원`\n**결제 수단** : `계좌이체`",
                                inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
@bot.slash_command(name="도움말", description=f"{SERVICE_NAME} | 명령어 리스트를 확인합니다.")
async def callback(interaction: nextcord.Interaction):
    if on_run:
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
            return
        
        embed = nextcord.Embed(title=f"📄ㆍ명령어 목록 (관리자 전용)", color=0xfffffe)
        embed.add_field(
            name="슬래시 명령어",
            value=(
                "**`/자판기`** — 자동판매기 메시지를 지정 채널에 전송\n"
                "**`/정보`** — 서버 정보 확인\n"
                "**`/도움말`** — 이 명령어 목록 확인\n"
                "**`/자동충전토큰`** — PushBullet 자동충전 토큰 상태·통신 확인\n"
                "**`/잔액관리`** — 유저 잔액 추가/차감\n"
                "**`/내정보`** — 멘션한 사용자의 잔액·등급·구매액 확인\n"
                "**`/리셀러설정`** — 멘션한 사용자에게 리셀러 역할 부여/제거\n"
                "**`/역할설정`** — 서버별 역할 지정 (리셀러/구매자/VIP)\n"
                "**`/어드민목록`** — 관리자 권한 멤버 목록 조회\n"
                "**`/자판기문구`** — 자판기 안내 문구·이미지 설정\n"
                "**`/채널설정`** — 구매/충전/재고 등 채널 종류별로 채널 지정\n"
                "**`/구매로그`** — 구매 로그 채널 지정 (멘션/링크/ID)\n"
                "**`/충전로그`** — 충전 완료 로그 채널 지정 (멘션/링크/ID)"
            ),
            inline=False
        )
        embed.set_footer(text="위 명령어는 모두 관리자만 사용할 수 있습니다.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
@bot.slash_command(name="자동충전토큰", description=f"{SERVICE_NAME} | PushBullet 자동충전 토큰 상태 및 통신을 확인합니다.")
async def pushbullet_token_command(interaction: nextcord.Interaction):
    if on_run:
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        current_status = "✅ 설정됨" if PUSHBULLET_TOKEN else "❌ 설정 안 됨"
        token_preview = ""
        if PUSHBULLET_TOKEN:
            token_preview = f"`{PUSHBULLET_TOKEN[:10]}...`"
        
        api_status = "❌ 테스트 안 됨"
        api_message = ""
        websocket_status = "❌ 연결 안 됨"
        websocket_message = ""
        
        if PUSHBULLET_TOKEN:
            try:
                headers = {
                    "Access-Token": PUSHBULLET_TOKEN,
                    "Content-Type": "application/json"
                }
                response = requests.get("https://api.pushbullet.com/v2/users/me", headers=headers, timeout=5)
                if response.status_code == 200:
                    user_data = response.json()
                    api_status = "✅ 통신 성공"
                    api_message = f"사용자: {user_data.get('name', '알 수 없음')}"
                elif response.status_code == 401:
                    api_status = "❌ 인증 실패"
                    api_message = "토큰이 유효하지 않습니다."
                else:
                    api_status = f"⚠️ 오류 ({response.status_code})"
                    api_message = response.text[:100] if response.text else "알 수 없는 오류"
            except requests.exceptions.Timeout:
                api_status = "❌ 타임아웃"
                api_message = "서버 응답 시간 초과"
            except Exception as e:
                api_status = "❌ 오류"
                api_message = str(e)[:100]
            
            try:
                import sys
                current_module = sys.modules[__name__]
                
                ws_obj = getattr(current_module, 'pushbullet_ws', None)
                ws_thread = getattr(current_module, 'pushbullet_ws_thread', None)
                
                if ws_obj:
                    try:
                        if hasattr(ws_obj, 'sock') and ws_obj.sock:
                            if hasattr(ws_obj.sock, 'connected') and ws_obj.sock.connected:
                                websocket_status = "✅ 연결됨"
                                websocket_message = "WebSocket이 정상적으로 연결되어 있습니다."
                            else:
                                websocket_status = "⚠️ 연결 끊김"
                                websocket_message = "WebSocket 소켓이 있지만 연결이 끊어졌습니다."
                        else:
                            websocket_status = "⚠️ 연결 중"
                            websocket_message = "WebSocket 연결이 진행 중입니다."
                    except:
                        websocket_status = "⚠️ 상태 확인 중"
                        websocket_message = "WebSocket 객체가 있지만 상태를 확인할 수 없습니다."
                elif ws_thread and ws_thread.is_alive():
                    websocket_status = "⚠️ 실행 중"
                    websocket_message = "WebSocket 스레드가 실행 중입니다. 연결 확인 중..."
                else:
                    websocket_status = "❌ 연결 안 됨"
                    websocket_message = "WebSocket이 시작되지 않았습니다. 봇 재시작이 필요할 수 있습니다."
            except Exception as e:
                websocket_status = "❌ 확인 불가"
                websocket_message = f"상태 확인 오류: {str(e)[:50]}"
        else:
            api_status = "❌ 토큰 없음"
            api_message = "토큰이 설정되지 않았습니다."
            websocket_status = "❌ 토큰 없음"
            websocket_message = "토큰이 설정되지 않았습니다."
        
        embed = nextcord.Embed(title="📱 자동 충전 토큰 상태", color=0x00ff00)
        embed.add_field(name="토큰 설정 상태", value=current_status, inline=False)
        if token_preview:
            embed.add_field(name="토큰 미리보기", value=token_preview, inline=False)
        
        embed.add_field(name="API 통신 상태", value=f"{api_status}\n{api_message}", inline=False)
        embed.add_field(name="WebSocket 연결 상태", value=f"{websocket_status}\n{websocket_message}", inline=False)
        
        await interaction.followup.send(embed=embed, ephemeral=True)

@bot.slash_command(name="잔액관리", description=f"{SERVICE_NAME} | 유저 잔액을 관리합니다.")
async def callback(interaction: nextcord.Interaction, 멤버: nextcord.Member, 금액: str, 선택: str = SlashOption(
    description="추가 혹은 차감",
    required=True,
    choices=["추가", "차감"])):
    if on_run:
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
            return
        
        try:
            userid = 멤버.id
            select_user = await bot.fetch_user(userid)
        except Exception as e:
            await interaction.response.send_message(f"사용자 id를 확인해주세요", ephemeral=True)
            return
        
        if select_user:
            user_data = get_user_data(interaction.guild.id, select_user.id)
            
            try:
                amount = validate_positive_int(금액, MAX_PRICE)
            except ValueError as e:
                await interaction.response.send_message(f"올바른 금액을 입력해주세요: {str(e)}", ephemeral=True)
                return
            
            if 선택 == "추가":
                try:
                    user_data['balance'] = safe_add(user_data['balance'], amount)
                    update_user_data(interaction.guild.id, select_user.id, user_data)
                    await send_charge_log(interaction.guild.id, select_user.id, amount,
                                          "잔액관리 (수동 추가)", f"관리자:{interaction.user.name}",
                                          user_data.get("balance"))
                    
                    embed = nextcord.Embed(title="💰ㆍ잔액 추가 완료", color=0x00ff00)
                    embed.add_field(name="대상 사용자", value=f"**{select_user.name}**", inline=True)
                    embed.add_field(name="추가 금액", value=f"**{amount:,}원**", inline=True)
                    embed.add_field(name="현재 잔액", value=f"**{user_data['balance']:,}원**", inline=True)
                    embed.set_thumbnail(url=select_user.display_avatar.url)
                    embed.set_footer(text=f"관리자: {interaction.user.name} | ID: {select_user.id}")
                    embed.timestamp = datetime.datetime.now()
                    
                    await interaction.response.send_message(embed=embed)
                except ValueError as e:
                    await interaction.response.send_message(f"잔액 추가 중 오류가 발생했습니다: {str(e)}", ephemeral=True)
            elif 선택 == "차감":
                try:
                    if user_data['balance'] < amount:
                        embed = nextcord.Embed(title="❌ㆍ잔액 부족", color=0xff0000)
                        embed.add_field(name="대상 사용자", value=f"**{select_user.name}**", inline=True)
                        embed.add_field(name="차감 시도 금액", value=f"**{amount:,}원**", inline=True)
                        embed.add_field(name="현재 잔액", value=f"**{user_data['balance']:,}원**", inline=True)
                        embed.set_thumbnail(url=select_user.display_avatar.url)
                        embed.set_footer(text=f"관리자: {interaction.user.name} | ID: {select_user.id}")
                        embed.timestamp = datetime.datetime.now()
                        
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                        return
                    
                    user_data['balance'] = safe_add(user_data['balance'], -amount)
                    update_user_data(interaction.guild.id, select_user.id, user_data)
                    await send_charge_log(interaction.guild.id, select_user.id, -amount,
                                          "잔액관리 (수동 차감)", f"관리자:{interaction.user.name}",
                                          user_data.get("balance"))
                    
                    embed = nextcord.Embed(title="💰ㆍ잔액 차감 완료", color=0xff6b6b)
                    embed.add_field(name="대상 사용자", value=f"**{select_user.name}**", inline=True)
                    embed.add_field(name="차감 금액", value=f"**{amount:,}원**", inline=True)
                    embed.add_field(name="현재 잔액", value=f"**{user_data['balance']:,}원**", inline=True)
                    embed.set_thumbnail(url=select_user.display_avatar.url)
                    embed.set_footer(text=f"관리자: {interaction.user.name} | ID: {select_user.id}")
                    embed.timestamp = datetime.datetime.now()
                    
                    await interaction.response.send_message(embed=embed)
                except ValueError as e:
                    await interaction.response.send_message(f"잔액 차감 중 오류가 발생했습니다: {str(e)}", ephemeral=True)
@bot.slash_command(name="내정보", description=f"{SERVICE_NAME} | 멘션한 사용자 정보를 확인합니다. (관리자 전용)")
async def callback(interaction: nextcord.Interaction, 멤버: nextcord.Member = SlashOption(description="정보를 볼 사용자 (맨션)", required=True)):
    if on_run:
        if interaction.guild is None:
            await interaction.response.send_message("이 명령어는 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
            return
        target_user = 멤버
        user_data = get_user_data(interaction.guild.id, target_user.id)
        
        if user_data['total_spent'] == 0:
            display_level = "유저"
        else:
            display_level = get_vip_level(user_data["total_spent"])
        
        level_colors = {
            "유저": 0x808080,
            "구매자": 0x00ff00,
            "10만": 0x1abc9c,
            "25만": 0x3498db,
            "50만": 0x9b59b6,
            "75만": 0xe67e22,
            "100만": 0xffd700
        }
        level_emojis = {
            "유저": "👤",
            "구매자": "🛒",
            "10만": "🎫",
            "25만": "⭐",
            "50만": "🌟",
            "75만": "💫",
            "100만": "✨"
        }
        display_name = TIER_DISPLAY_NAMES.get(display_level, display_level) if display_level != "유저" else display_level
        color = level_colors.get(display_level, 0x808080)
        
        embed = nextcord.Embed(
            title=f"{level_emojis.get(display_level, '👤')} 사용자 정보",
            description=f"━━━━━━━━━━━━━━━━━━━━━━━━━\n**{target_user.name}**님의 정보입니다\n━━━━━━━━━━━━━━━━━━━━━━━━━",
            color=color
        )
        
        embed.add_field(
            name="💰 잔액",
            value=f"```\n{user_data['balance']:,}원\n```",
            inline=True
        )
        
        embed.add_field(
            name="💳 총 구매액",
            value=f"```\n{user_data['total_spent']:,}원\n```",
            inline=True
        )
        
        # 등급 혜택 제거됨
        
        embed.set_thumbnail(url=target_user.display_avatar.url)
        embed.set_footer(text=f"ID: {target_user.id} • {interaction.guild.name}", icon_url=target_user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=False)

class StockEditModal(nextcord.ui.Modal):
    def __init__(self, 작업, 타입, 이름, 카테고리, 기존가격=0):
        super().__init__(
            title=f"재고수정 - {작업}",
            custom_id="stock_edit_modal",
            timeout=None
        )
        self.작업 = 작업
        self.타입 = 타입
        self.이름 = 이름
        self.카테고리 = 카테고리
        self.기존가격 = 기존가격
        
        self.product_name_field = nextcord.ui.TextInput(
            label="제품명 (기존)",
            placeholder=f"제품명: {이름}",
            required=False,
            style=nextcord.TextInputStyle.short,
            custom_id="product_name"
        )
        self.add_item(self.product_name_field)
        
        self.category_field = nextcord.ui.TextInput(
            label="카테고리 (기존)",
            placeholder=f"카테고리: {카테고리 if 카테고리 else '없음'}",
            required=False,
            style=nextcord.TextInputStyle.short,
            custom_id="category"
        )
        self.add_item(self.category_field)
        
        self.price_field = nextcord.ui.TextInput(
            label="가격 (수정)",
            placeholder=f"현재 가격: {기존가격:,}원" if 기존가격 > 0 else "가격을 입력하세요",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="price"
        )
        self.add_item(self.price_field)
    
    async def callback(self, interaction: nextcord.Interaction) -> None:
        if interaction.response.is_done():
            return
        
        try:
            price_str = self.price_field.value.strip()
            if not price_str:
                await interaction.response.send_message("가격을 입력해주세요.", ephemeral=True)
                return
            
            try:
                new_price = int(price_str)
                if new_price <= 0:
                    await interaction.response.send_message("가격은 0보다 커야 합니다.", ephemeral=True)
                    return
            except ValueError:
                await interaction.response.send_message("올바른 가격을 입력해주세요.", ephemeral=True)
                return
            
            stock_base_dir = ensure_guild_stock(_gid(interaction))
            os.makedirs(stock_base_dir, exist_ok=True)
            
            if self.작업 == "추가":
                category_path = os.path.join(stock_base_dir, self.카테고리)
                if not os.path.exists(category_path):
                    os.makedirs(category_path, exist_ok=True)
                
                timestamp = int(datetime.datetime.now().timestamp() * 1000)
                filename = f"{self.이름}_{new_price}_{timestamp}.txt"
                file_path = os.path.join(category_path, filename)
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write("")
                
                embed = nextcord.Embed(title="✅ㆍ제품 추가 완료", color=0x00ff00)
                embed.add_field(name="제품명", value=self.이름, inline=True)
                embed.add_field(name="카테고리", value=self.카테고리, inline=True)
                embed.add_field(name="가격", value=f"{new_price:,}원", inline=True)
                if self.기존가격 > 0:
                    embed.add_field(name="기존 가격", value=f"{self.기존가격:,}원", inline=True)
                await interaction.response.send_message(embed=embed, ephemeral=True)
            
            elif self.작업 == "가격수정":
                category_path = os.path.join(stock_base_dir, self.카테고리)
                if not os.path.exists(category_path):
                    await interaction.response.send_message(f"존재하지 않는 카테고리입니다: {self.카테고리}", ephemeral=True)
                    return
                
                updated_count = 0
                for filename in os.listdir(category_path):
                    if filename.endswith('.txt'):
                        parsed = parse_filename(filename)
                        if not parsed or 'product_name' not in parsed:
                            continue
                        file_product_name = parsed['product_name']
                        if file_product_name == self.이름:
                            old_file_path = os.path.join(category_path, filename)
                            try:
                                with open(old_file_path, 'r', encoding='utf-8') as f:
                                    content = f.read()
                                
                                timestamp = parsed.get('timestamp')
                                if timestamp is None:
                                    timestamp = str(int(datetime.datetime.now().timestamp() * 1000))
                                else:
                                    timestamp = str(timestamp)
                                
                                new_filename = f"{self.이름}_{new_price}_{timestamp}.txt"
                                new_file_path = os.path.join(category_path, new_filename)
                                
                                with open(new_file_path, 'w', encoding='utf-8') as f:
                                    f.write(content)
                                
                                os.remove(old_file_path)
                                updated_count += 1
                            except Exception as e:
                                print(f"가격 수정 오류: {filename} - {e}")
                
                if updated_count == 0:
                    await interaction.response.send_message(f"제품을 찾을 수 없습니다: {self.이름}", ephemeral=True)
                    return
                
                embed = nextcord.Embed(title="✅ㆍ가격 수정 완료", color=0xffd700)
                embed.add_field(name="제품명", value=self.이름, inline=True)
                embed.add_field(name="카테고리", value=self.카테고리, inline=True)
                embed.add_field(name="기존 가격", value=f"{self.기존가격:,}원", inline=True)
                embed.add_field(name="새 가격", value=f"{new_price:,}원", inline=True)
                embed.add_field(name="수정된 파일 수", value=f"{updated_count}개", inline=False)
                await interaction.response.send_message(embed=embed, ephemeral=True)
        
        except Exception as e:
            await interaction.response.send_message(f"작업 중 오류가 발생했습니다: {str(e)}", ephemeral=True)
class CategoryAddModal(nextcord.ui.Modal):
    def __init__(self):
        super().__init__(title="카테고리 추가", custom_id="category_add_modal", timeout=None)
        self.name_field = nextcord.ui.TextInput(
            label="카테고리명",
            placeholder="예: 제품명",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="category_name"
        )
        self.add_item(self.name_field)
        
        self.emoji_field = nextcord.ui.TextInput(
            label="이모지 (선택사항)",
            placeholder="예: 🎮 또는 <:emoji_name:1234567890>",
            required=False,
            style=nextcord.TextInputStyle.short,
            custom_id="category_emoji"
        )
        self.add_item(self.emoji_field)
    
    async def callback(self, interaction: nextcord.Interaction):
        if interaction.response.is_done():
            return
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        
        category_name = self.name_field.value.strip()
        if not category_name:
            await interaction.response.send_message("카테고리명을 입력해주세요.", ephemeral=True)
            return
        
        emoji = self.emoji_field.value.strip() if self.emoji_field.value else ""
        
        stock_base_dir = ensure_guild_stock(_gid(interaction))
        category_path = os.path.join(stock_base_dir, category_name)
        if os.path.exists(category_path):
            await interaction.response.send_message(f"이미 존재하는 카테고리입니다: {category_name}", ephemeral=True)
            return
        
        os.makedirs(category_path, exist_ok=True)
        
        if emoji:
            emoji_data = load_json_data('category_emojis')
            if not emoji_data:
                emoji_data = {}
            emoji_data[category_name] = emoji
            save_json_data('category_emojis', emoji_data)
        
        embed = nextcord.Embed(title="✅ㆍ카테고리 추가 완료", color=0x00ff00)
        display_name = f"{emoji} {category_name}" if emoji else category_name
        embed.add_field(name="카테고리명", value=display_name, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
class ProductAddModal(nextcord.ui.Modal):
    def __init__(self):
        super().__init__(title="제품 추가", custom_id="product_add_modal", timeout=None)
        self.name_field = nextcord.ui.TextInput(
            label="제품명",
            placeholder="예: 제품명",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="product_name"
        )
        self.add_item(self.name_field)
        
        self.emoji_field = nextcord.ui.TextInput(
            label="이모지 (선택사항)",
            placeholder="예: 🎮 또는 <:emoji_name:1234567890>",
            required=False,
            style=nextcord.TextInputStyle.short,
            custom_id="product_emoji"
        )
        self.add_item(self.emoji_field)
        
        self.category_field = nextcord.ui.TextInput(
            label="카테고리명",
            placeholder="예: 제품명",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="category"
        )
        self.add_item(self.category_field)
        
        self.price_field = nextcord.ui.TextInput(
            label="가격",
            placeholder="예: 10000",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="price"
        )
        self.add_item(self.price_field)
    
    async def callback(self, interaction: nextcord.Interaction):
        if interaction.response.is_done():
            return
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        
        product_name = self.name_field.value.strip()
        emoji = self.emoji_field.value.strip() if self.emoji_field.value else ""
        category_name = self.category_field.value.strip()
        price_str = self.price_field.value.strip()
        
        if not product_name or not category_name or not price_str:
            await interaction.response.send_message("제품명, 카테고리명, 가격을 입력해주세요.", ephemeral=True)
            return
        
        try:
            price = int(price_str)
            if price <= 0:
                await interaction.response.send_message("가격은 0보다 커야 합니다.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("올바른 가격을 입력해주세요.", ephemeral=True)
            return
        
        stock_base_dir = ensure_guild_stock(_gid(interaction))
        category_path = os.path.join(stock_base_dir, category_name)
        if not os.path.exists(category_path):
            os.makedirs(category_path, exist_ok=True)
        
        if emoji:
            emoji_data = load_json_data('product_emojis')
            if not emoji_data:
                emoji_data = {}
            if category_name not in emoji_data:
                emoji_data[category_name] = {}
            emoji_data[category_name][product_name] = emoji
            save_json_data('product_emojis', emoji_data)
        
        existing_info = get_product_info_from_stock(product_name, category_name, _gid(interaction))
        timestamp = int(datetime.datetime.now().timestamp() * 1000)
        filename = f"{product_name}_{price}_{timestamp}.txt"
        file_path = os.path.join(category_path, filename)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write("")
        
        embed = nextcord.Embed(title="✅ㆍ제품 추가 완료", color=0x00ff00)
        embed.add_field(name="제품명", value=product_name, inline=True)
        embed.add_field(name="카테고리", value=category_name, inline=True)
        embed.add_field(name="가격", value=f"{price:,}원", inline=True)
        if existing_info:
            embed.add_field(name="기존 정보", value=f"기존 가격: {existing_info.get('price', 0):,}원", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
class CategoryRemoveSelect(nextcord.ui.Select):
    def __init__(self, guild_id=None):
        self.guild_id = guild_id
        categories = get_categories_from_stock_folder(guild_id)
        options = []
        
        if categories:
            for category in categories[:25]:
                options.append(nextcord.SelectOption(label=category, description=f"{category} 카테고리", value=category))
        else:
            options.append(nextcord.SelectOption(label='카테고리가 없습니다', description='카테고리를 먼저 추가해주세요', value='none'))
        
        if not options:
            options = [nextcord.SelectOption(label='카테고리가 없습니다', description='카테고리를 먼저 추가해주세요', value='none')]
        
        super().__init__(custom_id='category_remove_select', placeholder="제거할 카테고리를 선택하세요", min_values=1, max_values=1, options=options)
    
    async def callback(self, interaction: nextcord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        
        category_name = self.values[0]
        if category_name == 'none':
            await interaction.response.send_message("카테고리를 먼저 추가해주세요.", ephemeral=True)
            return
        
        stock_base_dir = ensure_guild_stock(_gid(interaction))
        category_path = os.path.join(stock_base_dir, category_name)
        if not os.path.exists(category_path):
            await interaction.response.send_message(f"존재하지 않는 카테고리입니다: {category_name}", ephemeral=True)
            return
        
        files = [f for f in os.listdir(category_path) if f.endswith('.txt')]
        if files:
            await interaction.response.send_message(f"카테고리 내에 제품이 있어 삭제할 수 없습니다. 먼저 제품을 제거해주세요.", ephemeral=True)
            return
        
        os.rmdir(category_path)
        embed = nextcord.Embed(title="✅ㆍ카테고리 제거 완료", color=0xff6b6b)
        embed.add_field(name="카테고리명", value=category_name, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
class CategoryRemoveSelectView(nextcord.ui.View):
    def __init__(self, guild_id=None):
        super().__init__(timeout=None)
        self.add_item(CategoryRemoveSelect(guild_id))
class ProductRemoveModal(nextcord.ui.Modal):
    def __init__(self):
        super().__init__(title="제품 제거", custom_id="product_remove_modal", timeout=None)
        self.name_field = nextcord.ui.TextInput(
            label="제품명",
            placeholder="예: 제품명",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="product_name"
        )
        self.add_item(self.name_field)
        
        self.category_field = nextcord.ui.TextInput(
            label="카테고리명",
            placeholder="예: 제품명",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="category"
        )
        self.add_item(self.category_field)
    
    async def callback(self, interaction: nextcord.Interaction):
        if interaction.response.is_done():
            return
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        
        product_name = self.name_field.value.strip()
        category_name = self.category_field.value.strip()
        
        if not product_name or not category_name:
            await interaction.response.send_message("모든 항목을 입력해주세요.", ephemeral=True)
            return
        
        stock_base_dir = ensure_guild_stock(_gid(interaction))
        category_path = os.path.join(stock_base_dir, category_name)
        if not os.path.exists(category_path):
            await interaction.response.send_message(f"존재하지 않는 카테고리입니다: {category_name}", ephemeral=True)
            return
        
        removed_count = 0
        for filename in os.listdir(category_path):
            if filename.endswith('.txt'):
                parsed = parse_filename(filename)
                if not parsed or 'product_name' not in parsed:
                    continue
                file_product_name = parsed['product_name']
                if file_product_name == product_name:
                        file_path = os.path.join(category_path, filename)
                        try:
                            os.remove(file_path)
                            removed_count += 1
                        except Exception as e:
                            print(f"파일 삭제 오류: {filename} - {e}")
        
        if removed_count == 0:
            await interaction.response.send_message(f"제품을 찾을 수 없습니다: {product_name}", ephemeral=True)
            return
        
        embed = nextcord.Embed(title="✅ㆍ제품 제거 완료", color=0xff6b6b)
        embed.add_field(name="제품명", value=product_name, inline=True)
        embed.add_field(name="카테고리", value=category_name, inline=True)
        embed.add_field(name="삭제된 파일 수", value=f"{removed_count}개", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
class PriceEditModal(nextcord.ui.Modal):
    def __init__(self):
        super().__init__(title="가격수정", custom_id="price_edit_modal", timeout=None)
        self.name_field = nextcord.ui.TextInput(
            label="제품명",
            placeholder="예: 제품명",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="product_name"
        )
        self.add_item(self.name_field)
        
        self.category_field = nextcord.ui.TextInput(
            label="카테고리명",
            placeholder="예: 제품명",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="category"
        )
        self.add_item(self.category_field)
        
        self.price_field = nextcord.ui.TextInput(
            label="새 가격",
            placeholder="예: 15000",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="new_price"
        )
        self.add_item(self.price_field)
    
    async def callback(self, interaction: nextcord.Interaction):
        if interaction.response.is_done():
            return
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        
        product_name = self.name_field.value.strip()
        category_name = self.category_field.value.strip()
        price_str = self.price_field.value.strip()
        
        if not product_name or not category_name or not price_str:
            await interaction.response.send_message("모든 항목을 입력해주세요.", ephemeral=True)
            return
        
        try:
            new_price = int(price_str)
            if new_price <= 0:
                await interaction.response.send_message("가격은 0보다 커야 합니다.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("올바른 가격을 입력해주세요.", ephemeral=True)
            return
        
        existing_info = get_product_info_from_stock(product_name, category_name, _gid(interaction))
        if not existing_info:
            await interaction.response.send_message(f"제품을 찾을 수 없습니다: {product_name}", ephemeral=True)
            return
        
        stock_base_dir = ensure_guild_stock(_gid(interaction))
        category_path = os.path.join(stock_base_dir, category_name)
        if not os.path.exists(category_path):
            await interaction.response.send_message(f"존재하지 않는 카테고리입니다: {category_name}", ephemeral=True)
            return
        
        updated_count = 0
        for filename in os.listdir(category_path):
            if filename.endswith('.txt'):
                parsed = parse_filename(filename)
                if not parsed or 'product_name' not in parsed:
                    continue
                file_product_name = parsed['product_name']
                if file_product_name == product_name:
                        old_file_path = os.path.join(category_path, filename)
                        try:
                            with open(old_file_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                            
                            timestamp = parsed.get('timestamp')
                            if timestamp is None:
                                timestamp = str(int(datetime.datetime.now().timestamp() * 1000))
                            else:
                                timestamp = str(timestamp)
                            
                            new_filename = f"{product_name}_{new_price}_{timestamp}.txt"
                            new_file_path = os.path.join(category_path, new_filename)
                            
                            with open(new_file_path, 'w', encoding='utf-8') as f:
                                f.write(content)
                            
                            os.remove(old_file_path)
                            updated_count += 1
                        except Exception as e:
                            print(f"가격 수정 오류: {filename} - {e}")
        
        if updated_count == 0:
            await interaction.response.send_message(f"제품을 찾을 수 없습니다: {product_name}", ephemeral=True)
            return
        
        embed = nextcord.Embed(title="✅ㆍ가격 수정 완료", color=0xffd700)
        embed.add_field(name="제품명", value=product_name, inline=True)
        embed.add_field(name="카테고리", value=category_name, inline=True)
        embed.add_field(name="기존 가격", value=f"{existing_info.get('price', 0):,}원", inline=True)
        embed.add_field(name="새 가격", value=f"{new_price:,}원", inline=True)
        embed.add_field(name="수정된 파일 수", value=f"{updated_count}개", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
class StockCategorySelect(nextcord.ui.Select):
    def __init__(self, action_type, guild_id=None):
        self.action_type = action_type
        self.guild_id = guild_id
        categories = get_categories_from_stock_folder(guild_id)
        options = []
        
        if categories:
            for category in categories[:25]:
                options.append(nextcord.SelectOption(label=category, description=f"{category} 카테고리", value=category))
        else:
            options.append(nextcord.SelectOption(label='카테고리가 없습니다', description='카테고리를 먼저 추가해주세요', value='none'))
        
        if not options:
            options = [nextcord.SelectOption(label='카테고리가 없습니다', description='카테고리를 먼저 추가해주세요', value='none')]
        
        placeholder = {
            "add": "제품을 추가할 카테고리를 선택하세요",
            "remove": "제품을 제거할 카테고리를 선택하세요",
            "price_edit": "가격을 수정할 제품의 카테고리를 선택하세요",
            "stock_edit": "재고를 수정할 제품의 카테고리를 선택하세요",
            "stock_delete": "재고를 삭제할 제품의 카테고리를 선택하세요",
        }.get(action_type, "카테고리를 선택하세요")
        
        super().__init__(custom_id=f'stock_category_select_{action_type}', placeholder=placeholder, min_values=1, max_values=1, options=options)
    
    async def callback(self, interaction: nextcord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        
        category = self.values[0]
        if category == 'none':
            await interaction.response.send_message("카테고리를 먼저 추가해주세요.", ephemeral=True)
            return
        
        if self.action_type == "add":
            embed = nextcord.Embed(title="📦 제품 추가", color=0x00ff00)
            embed.add_field(name="카테고리", value=category, inline=False)
            embed.add_field(name="", value="제품명과 가격을 입력하세요.", inline=False)
            await interaction.response.send_message(embed=embed, view=StockProductAddView(category, _gid(interaction)), ephemeral=True)
        elif self.action_type == "remove":
            products = get_products_from_category(category, _gid(interaction))
            if not products:
                await interaction.response.send_message(f"{category} 카테고리에 제품이 없습니다.", ephemeral=True)
                return
            embed = nextcord.Embed(title="🗑️ 제품 제거", color=0xff6b6b)
            embed.add_field(name="카테고리", value=category, inline=False)
            embed.add_field(name="", value="제거할 제품을 선택하세요.", inline=False)
            await interaction.response.send_message(embed=embed, view=StockProductSelectView(category, "remove", _gid(interaction)), ephemeral=True)
        elif self.action_type == "price_edit":
            products = get_products_from_category(category, _gid(interaction))
            if not products:
                await interaction.response.send_message(f"{category} 카테고리에 제품이 없습니다.", ephemeral=True)
                return
            embed = nextcord.Embed(title="💰 가격수정", color=0xffd700)
            embed.add_field(name="카테고리", value=category, inline=False)
            embed.add_field(name="", value="가격을 수정할 제품을 선택하세요.", inline=False)
            await interaction.response.send_message(embed=embed, view=StockProductSelectView(category, "price_edit", _gid(interaction)), ephemeral=True)
        elif self.action_type == "stock_edit":
            products = get_products_from_category(category, _gid(interaction))
            if not products:
                await interaction.response.send_message(f"{category} 카테고리에 제품이 없습니다.", ephemeral=True)
                return
            embed = nextcord.Embed(title="📦 재고수정", color=0x3498db)
            embed.add_field(name="카테고리", value=category, inline=False)
            embed.add_field(name="", value="재고를 수정할 제품을 선택하세요.", inline=False)
            await interaction.response.send_message(embed=embed, view=StockProductSelectView(category, "stock_edit", _gid(interaction)), ephemeral=True)
        elif self.action_type == "stock_delete":
            products = get_products_from_category(category, _gid(interaction))
            if not products:
                await interaction.response.send_message(f"{category} 카테고리에 제품이 없습니다.", ephemeral=True)
                return
            embed = nextcord.Embed(title="🗑️ 재고 삭제", color=0xff6b6b)
            embed.add_field(name="카테고리", value=category, inline=False)
            embed.add_field(name="", value="재고를 삭제할 제품을 선택하세요. (제품 파일은 유지됩니다)", inline=False)
            await interaction.response.send_message(embed=embed, view=StockProductSelectView(category, "stock_delete", _gid(interaction)), ephemeral=True)
class StockCategorySelectView(nextcord.ui.View):
    def __init__(self, action_type, guild_id=None):
        super().__init__(timeout=None)
        self.add_item(StockCategorySelect(action_type, guild_id))
class StockProductSelect(nextcord.ui.Select):
    def __init__(self, category, action_type, guild_id=None):
        self.category = category
        self.action_type = action_type
        self.guild_id = guild_id
        products = get_products_from_category(category, guild_id)
        options = []
        
        for product_name, product_data in list(products.items())[:25]:
            product_emoji = get_product_emoji(category, product_name)
            label = f"{product_emoji} {product_data['name']}" if product_emoji else product_data['name']
            description = f"{product_data['price']:,}원 | 재고 {product_data['stock']:,}개"
            value = product_name
            options.append(nextcord.SelectOption(label=label, description=description, value=value))
        
        if not options:
            options = [nextcord.SelectOption(label='제품이 없습니다', description='제품이 없습니다', value='none')]
        
        placeholder = {
            "remove": "제거할 제품을 선택하세요",
            "price_edit": "가격을 수정할 제품을 선택하세요",
            "stock_edit": "재고를 수정할 제품을 선택하세요",
            "stock_delete": "재고를 삭제할 제품을 선택하세요",
        }.get(action_type, "제품을 선택하세요")
        
        super().__init__(custom_id=f'stock_product_select_{action_type}', placeholder=placeholder, min_values=1, max_values=1, options=options)
    
    async def callback(self, interaction: nextcord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        
        product_name = self.values[0]
        if product_name == 'none':
            await interaction.response.send_message("제품을 찾을 수 없습니다.", ephemeral=True)
            return
        
        if self.action_type == "remove":
            stock_base_dir = ensure_guild_stock(_gid(interaction))
            category_path = os.path.join(stock_base_dir, self.category)
            if not os.path.exists(category_path):
                await interaction.response.send_message(f"존재하지 않는 카테고리입니다: {self.category}", ephemeral=True)
                return
            
            removed_count = 0
            for filename in os.listdir(category_path):
                if filename.endswith('.txt'):
                    name_part = filename.replace('.txt', '')
                    if '_' in name_part:
                        file_product_name = name_part.split('_')[0]
                        if file_product_name == product_name:
                            file_path = os.path.join(category_path, filename)
                            try:
                                os.remove(file_path)
                                removed_count += 1
                            except Exception as e:
                                print(f"파일 삭제 오류: {filename} - {e}")
            
            if removed_count == 0:
                await interaction.response.send_message(f"제품을 찾을 수 없습니다: {product_name}", ephemeral=True)
                return
            
            embed = nextcord.Embed(title="✅ㆍ제품 제거 완료", color=0xff6b6b)
            embed.add_field(name="제품명", value=product_name, inline=True)
            embed.add_field(name="카테고리", value=self.category, inline=True)
            embed.add_field(name="삭제된 파일 수", value=f"{removed_count}개", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        elif self.action_type == "price_edit":
            existing_info = get_product_info_from_stock(product_name, self.category, _gid(interaction))
            if not existing_info:
                await interaction.response.send_message(f"제품을 찾을 수 없습니다: {product_name}", ephemeral=True)
                return
            
            await interaction.response.send_modal(StockPriceEditModal(product_name, self.category, existing_info.get('price', 0)))
        elif self.action_type == "stock_delete":
            stock_base_dir = ensure_guild_stock(_gid(interaction))
            category_path = os.path.join(stock_base_dir, self.category)
            if not os.path.exists(category_path):
                await interaction.response.send_message(f"존재하지 않는 카테고리입니다: {self.category}", ephemeral=True)
                return
            
            product_files = []
            for filename in os.listdir(category_path):
                if filename.endswith('.txt'):
                    parsed = parse_filename(filename)
                    if not parsed or 'product_name' not in parsed:
                        continue
                    file_product_name = parsed['product_name']
                    if file_product_name == product_name:
                        product_files.append((filename, os.path.join(category_path, filename)))
            
            if not product_files:
                await interaction.response.send_message(f"제품을 찾을 수 없습니다: {product_name}", ephemeral=True)
                return
            
            deleted_count = 0
            for filename, file_path in product_files:
                try:
                    # 파일 내용만 삭제 (파일은 유지)
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write("")
                    deleted_count += 1
                except Exception as e:
                    print(f"재고 삭제 오류: {filename} - {e}")
            
            if deleted_count == 0:
                await interaction.response.send_message(f"재고 삭제 중 오류가 발생했습니다.", ephemeral=True)
                return
            
            embed = nextcord.Embed(title="✅ㆍ재고 삭제 완료", color=0xff6b6b)
            embed.add_field(name="제품명", value=product_name, inline=True)
            embed.add_field(name="카테고리", value=self.category, inline=True)
            embed.add_field(name="삭제된 파일 수", value=f"{deleted_count}개", inline=True)
            embed.add_field(name="", value="제품 파일은 유지되었고, 재고 내용만 삭제되었습니다.", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        elif self.action_type == "stock_edit":
            existing_info = get_product_info_from_stock(product_name, self.category, _gid(interaction))
            if not existing_info:
                await interaction.response.send_message(f"제품을 찾을 수 없습니다: {product_name}", ephemeral=True)
                return
            
            current_stock = existing_info.get('stock', 0)
            
            existing_stock_lines = []
            stock_base_dir = ensure_guild_stock(_gid(interaction))
            category_path = os.path.join(stock_base_dir, self.category)
            if os.path.exists(category_path):
                for filename in os.listdir(category_path):
                    if filename.endswith('.txt'):
                        parsed = parse_filename(filename)
                        if not parsed or 'product_name' not in parsed:
                            continue
                        file_product_name = parsed['product_name']
                        if file_product_name == product_name:
                            file_path = os.path.join(category_path, filename)
                            try:
                                with open(file_path, 'r', encoding='utf-8') as f:
                                    lines = f.readlines()
                                    existing_stock_lines = [line.strip() for line in lines if line.strip()]
                                break
                            except Exception as e:
                                print(f"재고 파일 읽기 오류: {filename} - {e}")
            
            if existing_stock_lines:
                existing_stock_text = "\n".join(existing_stock_lines)
                if len(existing_stock_text) > 4000:
                    embed = nextcord.Embed(
                        title="⚠️ 재고 수정 불가",
                        description=f"기존 재고가 Discord의 최대 입력 길이(4000자)를 초과합니다.\n\n현재 재고 텍스트 길이: {len(existing_stock_text):,}자\n\n재고를 수정하려면 먼저 일부 재고를 삭제하여 4000자 이하로 만든 후 다시 시도해주세요.",
                        color=0xff6b6b
                    )
                    embed.add_field(name="현재 재고 수", value=f"{len(existing_stock_lines):,}개", inline=True)
                    embed.add_field(name="텍스트 길이", value=f"{len(existing_stock_text):,}자", inline=True)
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return
            
            await interaction.response.send_modal(StockQuantityEditModal(product_name, self.category, current_stock, existing_stock_lines))
class StockProductSelectView(nextcord.ui.View):
    def __init__(self, category, action_type, guild_id=None):
        super().__init__(timeout=None)
        self.add_item(StockProductSelect(category, action_type, guild_id))
class StockProductAddView(nextcord.ui.View):
    def __init__(self, category, guild_id=None):
        super().__init__(timeout=None)
        self.category = category
        self.guild_id = guild_id
    
    @nextcord.ui.button(label="제품 추가하기", style=nextcord.ButtonStyle.secondary, custom_id="stock_product_add_confirm")
    async def add_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        await interaction.response.send_modal(ProductAddModalWithCategory(self.category))
class ProductAddModalWithCategory(nextcord.ui.Modal):
    def __init__(self, category):
        super().__init__(title="제품 추가", custom_id="product_add_modal_category", timeout=None)
        self.category = category
        
        self.name_field = nextcord.ui.TextInput(
            label="제품명",
            placeholder="예: 제품명",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="product_name"
        )
        self.add_item(self.name_field)
        
        self.emoji_field = nextcord.ui.TextInput(
            label="이모지 (선택사항)",
            placeholder="예: 🎮 또는 <:emoji_name:1234567890>",
            required=False,
            style=nextcord.TextInputStyle.short,
            custom_id="product_emoji"
        )
        self.add_item(self.emoji_field)
        
        self.price_field = nextcord.ui.TextInput(
            label="가격",
            placeholder="예: 10000",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="price"
        )
        self.add_item(self.price_field)
    
    async def callback(self, interaction: nextcord.Interaction):
        if interaction.response.is_done():
            return
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        
        product_name = self.name_field.value.strip()
        emoji = self.emoji_field.value.strip() if self.emoji_field.value else ""
        price_str = self.price_field.value.strip()
        
        if not product_name or not price_str:
            await interaction.response.send_message("제품명과 가격을 입력해주세요.", ephemeral=True)
            return
        
        try:
            price = int(price_str)
            if price <= 0:
                await interaction.response.send_message("가격은 0보다 커야 합니다.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("올바른 가격을 입력해주세요.", ephemeral=True)
            return
        
        stock_base_dir = ensure_guild_stock(_gid(interaction))
        category_path = os.path.join(stock_base_dir, self.category)
        if not os.path.exists(category_path):
            os.makedirs(category_path, exist_ok=True)
        
        if emoji:
            emoji_data = load_json_data('product_emojis')
            if not emoji_data:
                emoji_data = {}
            if self.category not in emoji_data:
                emoji_data[self.category] = {}
            emoji_data[self.category][product_name] = emoji
            save_json_data('product_emojis', emoji_data)
        
        existing_info = get_product_info_from_stock(product_name, self.category, _gid(interaction))
        timestamp = int(datetime.datetime.now().timestamp() * 1000)
        filename = f"{product_name}_{price}_{timestamp}.txt"
        file_path = os.path.join(category_path, filename)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write("")
        
        embed = nextcord.Embed(title="✅ㆍ제품 추가 완료", color=0x00ff00)
        display_name = f"{emoji} {product_name}" if emoji else product_name
        embed.add_field(name="제품명", value=display_name, inline=True)
        embed.add_field(name="카테고리", value=self.category, inline=True)
        embed.add_field(name="가격", value=f"{price:,}원", inline=True)
        if existing_info:
            embed.add_field(name="기존 정보", value=f"기존 가격: {existing_info.get('price', 0):,}원", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
class StockQuantityEditModal(nextcord.ui.Modal):
    def __init__(self, product_name, category, current_stock, existing_stock_lines=None):
        super().__init__(title="재고수정", custom_id="stock_quantity_edit_modal", timeout=None)
        self.product_name = product_name
        self.category = category
        self.current_stock = current_stock
        
        existing_stock_text = ""
        if existing_stock_lines:
            existing_stock_text = "\n".join(existing_stock_lines)
            if len(existing_stock_text) > 4000:
                truncated_lines = []
                current_length = 0
                for line in existing_stock_lines:
                    line_with_newline = line + "\n"
                    if current_length + len(line_with_newline) > 3900:
                        truncated_lines.append("... (더 많은 재고가 있습니다)")
                        break
                    truncated_lines.append(line)
                    current_length += len(line_with_newline)
                existing_stock_text = "\n".join(truncated_lines)
        
        placeholder_text = "기존 재고 라인을 수정하거나 삭제할 수 있습니다.\n라인을 삭제하려면 해당 라인을 지우세요."
        if existing_stock_text:
            preview = existing_stock_text[:200] + "..." if len(existing_stock_text) > 200 else existing_stock_text
            placeholder_text = f"기존 재고 (미리보기):\n{preview}"
        
        if len(placeholder_text) > 100:
            placeholder_text = placeholder_text[:97] + "..."
        
        text_input_kwargs = dict(
            label="기존 재고 (수정/삭제 가능)",
            placeholder=placeholder_text,
            required=False,
            style=nextcord.TextInputStyle.paragraph,
            custom_id="existing_stock"
        )
        if existing_stock_text:
            text_input_kwargs["value"] = existing_stock_text
        
        try:
            self.existing_stock_field = nextcord.ui.TextInput(**text_input_kwargs)
        except TypeError:
            text_input_kwargs.pop("value", None)
            self.existing_stock_field = nextcord.ui.TextInput(**text_input_kwargs)
        self.add_item(self.existing_stock_field)
        if existing_stock_text:
            for attr_name in ("value", "default", "_value", "_default"):
                if hasattr(self.existing_stock_field, attr_name):
                    try:
                        setattr(self.existing_stock_field, attr_name, existing_stock_text)
                        break
                    except Exception:
                        continue
            underlying = getattr(self.existing_stock_field, "_underlying", None)
            if underlying is not None and hasattr(underlying, "value"):
                try:
                    underlying.value = existing_stock_text
                except Exception:
                    pass
        
        self.existing_stock_lines = existing_stock_lines or []
        
        self.stock_lines_field = nextcord.ui.TextInput(
            label="추가할 재고 라인",
            placeholder="한 줄에 하나씩 재고 항목을 입력하세요.\n예:\n1762546348879\n1762546348880\n1762546348881",
            required=False,
            style=nextcord.TextInputStyle.paragraph,
            custom_id="stock_lines"
        )
        self.add_item(self.stock_lines_field)
    
    async def callback(self, interaction: nextcord.Interaction):
        if interaction.response.is_done():
            return
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        
        stock_base_dir = ensure_guild_stock(_gid(interaction))
        category_path = os.path.join(stock_base_dir, self.category)
        if not os.path.exists(category_path):
            await interaction.response.send_message(f"존재하지 않는 카테고리입니다: {self.category}", ephemeral=True)
            return
        
        product_files = []
        for filename in os.listdir(category_path):
            if filename.endswith('.txt'):
                parsed = parse_filename(filename)
                if not parsed or 'product_name' not in parsed:
                    continue
                file_product_name = parsed['product_name']
                if file_product_name == self.product_name:
                    product_files.append(filename)
        
        if not product_files:
            await interaction.response.send_message(f"제품을 찾을 수 없습니다: {self.product_name}", ephemeral=True)
            return
        
        target_file = product_files[0]
        file_path = os.path.join(category_path, target_file)
        
        try:
            DISCORD_TEXT_INPUT_MAX_LENGTH = 4000
            
            existing_stock_str = self.existing_stock_field.value.strip() if self.existing_stock_field.value else ""
            
            if existing_stock_str and len(existing_stock_str) > DISCORD_TEXT_INPUT_MAX_LENGTH:
                await interaction.response.send_message(
                    f"⚠️ 기존 재고 필드의 입력값이 Discord의 최대 입력 길이(4000자)를 초과합니다.\n\n"
                    f"현재 입력 길이: {len(existing_stock_str):,}자\n"
                    f"최대 허용 길이: {DISCORD_TEXT_INPUT_MAX_LENGTH:,}자\n\n"
                    f"Discord에서 설정한 값에 따라 수정할 수 없습니다. 재고를 분할하여 입력해주세요.",
                    ephemeral=True
                )
                return
            
            # 모바일에서 텍스트를 지웠을 때도 삭제가 반영되도록 처리
            if existing_stock_str:
                # 빈 문자열이 아닌 경우에만 파싱
                existing_stock_lines = [line.strip() for line in existing_stock_str.split('\n') if line.strip()]
            else:
                # 빈 문자열이면 재고를 모두 삭제한 것으로 처리
                existing_stock_lines = []
            
            new_stock_lines_str = self.stock_lines_field.value.strip() if self.stock_lines_field.value else ""
            
            if new_stock_lines_str and len(new_stock_lines_str) > DISCORD_TEXT_INPUT_MAX_LENGTH:
                await interaction.response.send_message(
                    f"⚠️ 추가할 재고 필드의 입력값이 Discord의 최대 입력 길이(4000자)를 초과합니다.\n\n"
                    f"현재 입력 길이: {len(new_stock_lines_str):,}자\n"
                    f"최대 허용 길이: {DISCORD_TEXT_INPUT_MAX_LENGTH:,}자\n\n"
                    f"Discord에서 설정한 값에 따라 수정할 수 없습니다. 재고를 분할하여 입력해주세요.",
                    ephemeral=True
                )
                return
            
            new_stock_lines = [line.strip() for line in new_stock_lines_str.split('\n') if line.strip()]
            
            if existing_stock_lines and new_stock_lines:
                combined_text = "\n".join(existing_stock_lines + new_stock_lines)
                if len(combined_text) > DISCORD_TEXT_INPUT_MAX_LENGTH:
                    await interaction.response.send_message(
                        f"⚠️ 기존 재고와 추가할 재고를 합친 길이가 Discord의 최대 입력 길이(4000자)를 초과합니다.\n\n"
                        f"합친 텍스트 길이: {len(combined_text):,}자\n"
                        f"최대 허용 길이: {DISCORD_TEXT_INPUT_MAX_LENGTH:,}자\n\n"
                        f"Discord에서 설정한 값에 따라 수정할 수 없습니다. 재고를 분할하여 입력해주세요.",
                        ephemeral=True
                    )
                    return
            
            total_lines = len(existing_stock_lines) + len(new_stock_lines)
            if total_lines > MAX_QUANTITY:
                await interaction.response.send_message(f"총 재고 수는 최대 {MAX_QUANTITY:,}개까지 가능합니다.", ephemeral=True)
                return
            
            all_stock_lines = existing_stock_lines + new_stock_lines
            
            final_lines = []
            for line in all_stock_lines:
                if len(line) > MAX_STRING_LENGTH:
                    line = line[:MAX_STRING_LENGTH]
                if line:
                    final_lines.append(f"{line}\n")
            
            final_stock_text = "".join(final_lines)
            if len(final_stock_text) > DISCORD_TEXT_INPUT_MAX_LENGTH:
                await interaction.response.send_message(
                    f"⚠️ 최종 재고 텍스트 길이가 Discord의 최대 입력 길이(4000자)를 초과합니다.\n\n"
                    f"최종 텍스트 길이: {len(final_stock_text):,}자\n"
                    f"최대 허용 길이: {DISCORD_TEXT_INPUT_MAX_LENGTH:,}자\n\n"
                    f"Discord에서 설정한 값에 따라 수정할 수 없습니다. 재고를 분할하여 입력해주세요.",
                    ephemeral=True
                )
                return
            
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.writelines(final_lines)
            except Exception as e:
                await interaction.response.send_message(f"파일 쓰기 중 오류가 발생했습니다: {str(e)}", ephemeral=True)
                return
            
            old_count = self.current_stock
            new_count = len(final_lines)
            added_count = len(new_stock_lines)
            removed_count = old_count - len(existing_stock_lines) if old_count > len(existing_stock_lines) else 0
            
        except Exception as e:
            await interaction.response.send_message(f"재고 수정 중 오류가 발생했습니다: {str(e)}", ephemeral=True)
            return
        
        updated_stock = get_product_stock_count(interaction.guild.id if interaction.guild else None, self.product_name)
        
        embed = nextcord.Embed(title="✅ㆍ재고 수정 완료", color=0x00ff00)
        embed.add_field(name="제품명", value=self.product_name, inline=True)
        embed.add_field(name="카테고리", value=self.category, inline=True)
        if added_count > 0:
            embed.add_field(name="추가된 재고", value=f"{added_count:,}개", inline=True)
        if removed_count > 0:
            embed.add_field(name="삭제된 재고", value=f"{removed_count:,}개", inline=True)
        embed.add_field(name="이전 재고", value=f"{old_count:,}개", inline=True)
        embed.add_field(name="현재 재고", value=f"{updated_stock:,}개", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
class StockPriceEditModal(nextcord.ui.Modal):
    def __init__(self, product_name, category, existing_price):
        super().__init__(title="가격수정", custom_id="stock_price_edit_modal", timeout=None)
        self.product_name = product_name
        self.category = category
        self.existing_price = existing_price
        
        self.name_field = nextcord.ui.TextInput(
            label="제품명 (기존)",
            placeholder=f"제품명: {product_name}",
            required=False,
            style=nextcord.TextInputStyle.short,
            custom_id="product_name_display"
        )
        self.add_item(self.name_field)
        
        self.category_field = nextcord.ui.TextInput(
            label="카테고리 (기존)",
            placeholder=f"카테고리: {category}",
            required=False,
            style=nextcord.TextInputStyle.short,
            custom_id="category_display"
        )
        self.add_item(self.category_field)
        
        existing_emoji = get_product_emoji(category, product_name)
        self.emoji_field = nextcord.ui.TextInput(
            label="이모지 (선택사항)",
            placeholder=f"예: 🎮 또는 <:emoji_name:1234567890> (현재: {existing_emoji if existing_emoji else '없음'})",
            required=False,
            style=nextcord.TextInputStyle.short,
            custom_id="product_emoji"
        )
        self.add_item(self.emoji_field)
        
        self.price_field = nextcord.ui.TextInput(
            label="새 가격",
            placeholder=f"현재 가격: {existing_price:,}원 (새 가격을 입력하세요)",
            required=True,
            style=nextcord.TextInputStyle.short,
            custom_id="new_price"
        )
        self.add_item(self.price_field)
    
    async def callback(self, interaction: nextcord.Interaction):
        if interaction.response.is_done():
            return
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        
        emoji = self.emoji_field.value.strip() if self.emoji_field.value else ""
        if emoji:
            emoji_data = load_json_data('product_emojis')
            if not emoji_data:
                emoji_data = {}
            if self.category not in emoji_data:
                emoji_data[self.category] = {}
            emoji_data[self.category][self.product_name] = emoji
            save_json_data('product_emojis', emoji_data)
        elif emoji == "" and self.emoji_field.value is not None:
            emoji_data = load_json_data('product_emojis')
            if emoji_data and self.category in emoji_data and self.product_name in emoji_data[self.category]:
                del emoji_data[self.category][self.product_name]
                if not emoji_data[self.category]:
                    del emoji_data[self.category]
                save_json_data('product_emojis', emoji_data)
        
        price_str = self.price_field.value.strip()
        if not price_str:
            await interaction.response.send_message("가격을 입력해주세요.", ephemeral=True)
            return
        
        try:
            new_price = int(price_str)
            if new_price <= 0:
                await interaction.response.send_message("가격은 0보다 커야 합니다.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("올바른 가격을 입력해주세요.", ephemeral=True)
            return
        
        stock_base_dir = ensure_guild_stock(_gid(interaction))
        category_path = os.path.join(stock_base_dir, self.category)
        if not os.path.exists(category_path):
            await interaction.response.send_message(f"존재하지 않는 카테고리입니다: {self.category}", ephemeral=True)
            return
        
        updated_count = 0
        for filename in os.listdir(category_path):
            if filename.endswith('.txt'):
                name_part = filename.replace('.txt', '')
                if '_' in name_part:
                    file_product_name = name_part.split('_')[0]
                    if file_product_name == self.product_name:
                        old_file_path = os.path.join(category_path, filename)
                        try:
                            parsed = parse_filename(filename)
                            with open(old_file_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                            
                            timestamp = parsed.get('timestamp')
                            if timestamp is None:
                                timestamp = str(int(datetime.datetime.now().timestamp() * 1000))
                            else:
                                timestamp = str(timestamp)
                            
                            new_filename = f"{self.product_name}_{new_price}_{timestamp}.txt"
                            new_file_path = os.path.join(category_path, new_filename)
                            
                            with open(new_file_path, 'w', encoding='utf-8') as f:
                                f.write(content)
                            
                            os.remove(old_file_path)
                            updated_count += 1
                        except Exception as e:
                            print(f"가격 수정 오류: {filename} - {e}")
        
        if updated_count == 0:
            await interaction.response.send_message(f"제품을 찾을 수 없습니다: {self.product_name}", ephemeral=True)
            return
        
        embed = nextcord.Embed(title="✅ㆍ가격 수정 완료", color=0xffd700)
        product_emoji = get_product_emoji(self.category, self.product_name)
        display_name = f"{product_emoji} {self.product_name}" if product_emoji else self.product_name
        embed.add_field(name="제품명", value=display_name, inline=True)
        category_emoji = get_category_emoji(self.category)
        category_display = f"{category_emoji} {self.category}" if category_emoji else self.category
        embed.add_field(name="카테고리", value=category_display, inline=True)
        embed.add_field(name="기존 가격", value=f"{self.existing_price:,}원", inline=True)
        embed.add_field(name="새 가격", value=f"{new_price:,}원", inline=True)
        if emoji:
            embed.add_field(name="이모지", value=f"{emoji} (수정됨)", inline=True)
        embed.add_field(name="수정된 파일 수", value=f"{updated_count}개", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
class StockDeleteView(nextcord.ui.View):
    def __init__(self, guild_id=None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
    
    @nextcord.ui.button(label="재고 삭제", style=nextcord.ButtonStyle.danger, custom_id="stock_delete_button")
    async def stock_delete_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        embed = nextcord.Embed(title="🗑️ 재고 삭제", color=0xff6b6b)
        embed.add_field(name="", value="재고를 삭제할 제품의 카테고리를 선택하세요.", inline=False)
        await interaction.response.send_message(embed=embed, view=StockCategorySelectView("stock_delete", self.guild_id), ephemeral=True)

class StockManagementView(nextcord.ui.View):
    def __init__(self, guild_id=None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
    
    @nextcord.ui.button(label="카테고리 추가", style=nextcord.ButtonStyle.secondary, custom_id="stock_category_add")
    async def category_add_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        await interaction.response.send_modal(CategoryAddModal())
    
    @nextcord.ui.button(label="제품 추가", style=nextcord.ButtonStyle.secondary, custom_id="stock_product_add")
    async def product_add_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        embed = nextcord.Embed(title="📦 제품 추가", color=0x00ff00)
        embed.add_field(name="", value="제품을 추가할 카테고리를 선택하세요.", inline=False)
        await interaction.response.send_message(embed=embed, view=StockCategorySelectView("add", self.guild_id), ephemeral=True)
    
    @nextcord.ui.button(label="카테고리 제거", style=nextcord.ButtonStyle.secondary, custom_id="stock_category_remove")
    async def category_remove_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        embed = nextcord.Embed(title="🗑️ 카테고리 제거", color=0xff6b6b)
        embed.add_field(name="", value="제거할 카테고리를 선택하세요.", inline=False)
        await interaction.response.send_message(embed=embed, view=CategoryRemoveSelectView(self.guild_id), ephemeral=True)
    
    @nextcord.ui.button(label="제품 제거", style=nextcord.ButtonStyle.secondary, custom_id="stock_product_remove")
    async def product_remove_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        embed = nextcord.Embed(title="🗑️ 제품 제거", color=0xff6b6b)
        embed.add_field(name="", value="제품을 제거할 카테고리를 선택하세요.", inline=False)
        await interaction.response.send_message(embed=embed, view=StockCategorySelectView("remove", self.guild_id), ephemeral=True)
    
    @nextcord.ui.button(label="가격수정", style=nextcord.ButtonStyle.secondary, custom_id="stock_price_edit")
    async def price_edit_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        embed = nextcord.Embed(title="💰 가격수정", color=0xffd700)
        embed.add_field(name="", value="가격을 수정할 제품의 카테고리를 선택하세요.", inline=False)
        await interaction.response.send_message(embed=embed, view=StockCategorySelectView("price_edit", self.guild_id), ephemeral=True)
    
    @nextcord.ui.button(label="재고수정", style=nextcord.ButtonStyle.secondary, custom_id="stock_quantity_edit")
    async def stock_edit_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        embed = nextcord.Embed(title="📦 재고수정", color=0x3498db)
        embed.add_field(name="", value="재고를 수정할 제품의 카테고리를 선택하세요.", inline=False)
        await interaction.response.send_message(embed=embed, view=StockCategorySelectView("stock_edit", self.guild_id), ephemeral=True)
@bot.slash_command(name="리셀러설정", description=f"{SERVICE_NAME} | 사용자에게 리셀러 역할을 부여합니다. (관리자 전용)")
async def reseller_set_command(interaction: nextcord.Interaction, 멤버: nextcord.Member):
    if on_run:
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
            return
        
        try:
            _rid = get_guild_roles(interaction.guild.id if interaction.guild else None)["reseller"]
            reseller_role = interaction.guild.get_role(_rid) if interaction.guild else None
            if not reseller_role:
                await interaction.response.send_message(f"리셀러 역할이 설정되지 않았어요. /역할설정 으로 지정해주세요. (현재 ID: {_rid})", ephemeral=True)
                return
            
            has_role = reseller_role in 멤버.roles
            
            if has_role:
                await 멤버.remove_roles(reseller_role)
                action = "제거"
                action_text = "리셀러 역할이 제거되었습니다."
            else:
                await 멤버.add_roles(reseller_role)
                action = "부여"
                action_text = "리셀러 역할이 부여되었습니다."
            
            embed = nextcord.Embed(title="✅ 리셀러 역할 설정 완료", color=0x00ff00)
            embed.add_field(name="대상 사용자", value=f"**{멤버.name}** ({멤버.mention})", inline=False)
            embed.add_field(name="작업", value=action, inline=True)
            embed.add_field(name="상태", value=action_text, inline=True)
            embed.add_field(name="혜택", value="리셀러는 모든 충전 시 30% 추가 충전 혜택을 받습니다.", inline=False)
            embed.set_thumbnail(url=멤버.display_avatar.url)
            embed.set_footer(text=f"관리자: {interaction.user.name} | ID: {멤버.id}")
            embed.timestamp = datetime.datetime.now()
            
            await interaction.response.send_message(embed=embed)
            
        except nextcord.errors.Forbidden:
            await interaction.response.send_message("봇에게 역할을 관리할 권한이 없습니다.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"리셀러 역할 설정 중 오류가 발생했습니다: {str(e)}", ephemeral=True)

@bot.slash_command(name="역할설정", description=f"{SERVICE_NAME} | 서버별 역할을 지정합니다. (관리자 전용)")
async def role_set_command(
    interaction: nextcord.Interaction,
    종류: str = SlashOption(description="지정할 역할 종류", required=True,
        choices=["리셀러", "구매자", "1만", "3만", "10만", "30만", "50만"]),
    역할: nextcord.Role = SlashOption(description="디스코드 역할 선택", required=True),
):
    if on_run:
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("이 명령어는 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        key_map = {"리셀러": "reseller", "구매자": "buyer", "1만": "vip_10000", "3만": "vip_30000",
                   "10만": "vip_100000", "30만": "vip_300000", "50만": "vip_500000"}
        gid = interaction.guild.id
        # load raw file to preserve unset keys
        raw = load_guild_json(gid, 'roles', {})
        if not isinstance(raw, dict):
            raw = {}
        raw[key_map[종류]] = 역할.id
        save_guild_json(gid, 'roles', raw)
        await interaction.response.send_message(f"{종류} 역할을 {역할.mention} 로 설정했어요.", ephemeral=True)

@bot.slash_command(name="어드민목록", description=f"{SERVICE_NAME} | 어드민 목록을 봅니다.")
async def admin_list_command(interaction: nextcord.Interaction):
    if on_run:
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("이 명령어는 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        admins = get_guild_admins(interaction.guild)
        if not admins:
            await interaction.response.send_message("관리자 권한을 가진 멤버가 없습니다.", ephemeral=True)
            return
        lines = [f"• {m.mention} (**{m.name}**) · `{m.id}`" for m in admins]
        embed = nextcord.Embed(title="📋 어드민 목록", color=0x5865F2)
        embed.add_field(name="총 인원", value=f"{len(admins)}명", inline=True)
        embed.add_field(name="목록", value="\n".join(lines) or "없음", inline=False)
        embed.set_footer(text=f"요청: {interaction.user.name}")
        embed.timestamp = datetime.datetime.now()
        await interaction.response.send_message(embed=embed, ephemeral=True)

CHANNEL_TYPE_LABELS = {
    "admin": "관리자 채널",
    "purchase_log": "구매 로그 채널",
    "charge": "충전 채널",
    "charge_log": "충전 완료 로그 채널",
    "stock_management": "재고 관리 채널",
    "auto_vending": "자판기 채널"
}

class ChannelTypeSelectView(nextcord.ui.View):
    def __init__(self, timeout=60):
        super().__init__(timeout=timeout)
        opts = [nextcord.SelectOption(label=CHANNEL_TYPE_LABELS[k], value=k) for k in CHANNEL_TYPE_LABELS]
        self.select = nextcord.ui.Select(placeholder="설정할 채널 종류 선택", options=opts, custom_id="channel_type_select")
        self.select.callback = self._select_callback
        self.add_item(self.select)

    async def _select_callback(self, interaction: nextcord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        value = self.select.values[0]
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("서버에서 실행해주세요.", ephemeral=True)
            return
        channels = list(guild.text_channels)[:25]
        if not channels:
            await interaction.response.send_message("선택 가능한 채널이 없습니다.", ephemeral=True)
            return
        options = [nextcord.SelectOption(label=f"#{ch.name}", value=str(ch.id), description=ch.name[:50]) for ch in channels]
        view = nextcord.ui.View(timeout=60)
        sel = nextcord.ui.Select(placeholder="채널 선택", options=options, custom_id="channel_select")
        async def channel_cb(inter: nextcord.Interaction):
            if not is_admin(inter):
                await inter.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
                return
            ch_id = int(sel.values[0])
            _gid = inter.guild.id if inter.guild else None
            data = load_channel_config(_gid)
            if value == "auto_vending":
                data["auto_vending"] = [ch_id]
            else:
                data[value] = ch_id
            save_channel_config(data, _gid)

            name = CHANNEL_TYPE_LABELS.get(value, value)
            await inter.response.edit_message(content=f"**{name}**이(가) <#{ch_id}> (으)로 설정되었습니다.", view=None, embed=None)
        sel.callback = channel_cb
        view.add_item(sel)
        embed = nextcord.Embed(description="채널을 선택하세요.", color=0x5865F2)
        await interaction.response.edit_message(embed=embed, view=view)

@bot.slash_command(name="자판기문구", description=f"{SERVICE_NAME} | 자판기 안내 문구·이미지를 설정합니다. (관리자 전용)")
async def vending_text_command(
    interaction: nextcord.Interaction,
    제목: str = SlashOption(description="자판기 제목 (비우면 변경 안 함)", required=False, default=None),
    설명: str = SlashOption(description="자판기 설명 문구 (비우면 변경 안 함)", required=False, default=None),
    이미지: str = SlashOption(description="이미지 URL (없음 입력 시 삭제)", required=False, default=None),
):
    if on_run:
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
            return
        if 제목 is None and 설명 is None and 이미지 is None:
            cur = load_appearance_config(interaction.guild.id if interaction.guild else None)
            embed = nextcord.Embed(title="🔔 현재 자판기 문구", color=0x5865F2)
            embed.add_field(name="제목", value=cur["title"] or f"(기본값) {DEFAULT_VENDING_TITLE}", inline=False)
            embed.add_field(name="설명", value=(cur["description"] or DEFAULT_VENDING_DESCRIPTION)[:1000], inline=False)
            embed.add_field(name="이미지", value=cur["image_url"] or "(없음)", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        cur = load_appearance_config(interaction.guild.id if interaction.guild else None)
        if 제목 is not None:
            cur["title"] = (제목 or "").strip()
        if 설명 is not None:
            cur["description"] = (설명 or "").strip()
        if 이미지 is not None:
            v = (이미지 or "").strip()
            cur["image_url"] = "" if v in ("없음", "없애기", "삭제", "none", "None") else v
        save_appearance_config(cur, interaction.guild.id if interaction.guild else None)
        preview = nextcord.Embed(title=cur["title"] or DEFAULT_VENDING_TITLE,
                                 description=cur["description"] or DEFAULT_VENDING_DESCRIPTION,
                                 color=0xfffffe)
        if cur["image_url"]:
            preview.set_image(url=cur["image_url"])
        await interaction.response.send_message("저장됨. 미리보기:", embed=preview, ephemeral=True)

@bot.slash_command(name="구매로그", description=f"{SERVICE_NAME} | 구매 로그 채널을 설정합니다. (관리자 전용)")
async def purchase_log_command(
    interaction: nextcord.Interaction,
    채널: str = SlashOption(description="채널 멘션(#채널)/링크/ID", required=True),
):
    if on_run:
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("이 명령어는 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        ch = await resolve_log_channel(채널)
        if ch is None:
            await interaction.response.send_message("채널 멘션(#채널), 링크, 또는 ID를 입력해주세요. 봇이 있는 서버의 채널이어야 해요.", ephemeral=True)
            return
        data = load_channel_config(interaction.guild.id)
        data["purchase_log"] = ch.id
        save_channel_config(data, interaction.guild.id)
        await interaction.response.send_message(f"구매 로그 채널을 <#{ch.id}> 로 설정했어요. 이제 구매가 일어나면 여기에 로그가 떠요.", ephemeral=True)

@bot.slash_command(name="충전로그", description=f"{SERVICE_NAME} | 충전 완료 로그 채널을 설정합니다. (관리자 전용)")
async def charge_log_command(
    interaction: nextcord.Interaction,
    채널: str = SlashOption(description="채널 멘션(#채널)/링크/ID", required=True),
):
    if on_run:
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("이 명령어는 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        ch = await resolve_log_channel(채널)
        if ch is None:
            await interaction.response.send_message("채널 멘션(#채널), 링크, 또는 ID를 입력해주세요. 봇이 있는 서버의 채널이어야 해요.", ephemeral=True)
            return
        data = load_channel_config(interaction.guild.id)
        data["charge_log"] = ch.id
        save_channel_config(data, interaction.guild.id)
        await interaction.response.send_message(f"충전 완료 로그 채널을 <#{ch.id}> 로 설정했어요. 이제 충전이 승인되면 여기에 로그가 떠요.", ephemeral=True)

@bot.slash_command(name="채널설정", description=f"{SERVICE_NAME} | 채널을 드롭다운으로 설정합니다. (관리자 전용)")
async def channel_config_command(interaction: nextcord.Interaction):
    if on_run:
        if not is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
            return
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        embed = nextcord.Embed(title="채널 설정", description="설정할 채널 종류를 선택한 뒤, 다음 단계에서 채널을 선택하세요.", color=0x5865F2)
        await interaction.response.send_message(embed=embed, view=ChannelTypeSelectView(), ephemeral=True)

@bot.event
async def on_ready():
    
    total_members = 0
    for guild in bot.guilds:
        total_members += (guild.member_count or 0)
    
    activity = nextcord.Activity(
        type=nextcord.ActivityType.watching,
        name=RPC_MESSAGES[0]
    )
    await bot.change_presence(activity=activity)
    
    print(f"{SERVICE_NAME} | {bot.user}으로 로그인됨 (ID: {bot.user.id})")
    print(f"{SERVICE_NAME} | {len(bot.guilds)}개 서버에서 {total_members:,}명에게 서비스 제공중")
    print(f"{SERVICE_NAME} | RPC 상태: 냥코 최저가 ㄱ | 24시간 영업중")

    try:
        save_guild_json(None, 'bot_guilds',
                        {"guilds": [{"id": g.id, "name": g.name} for g in bot.guilds]})
    except Exception as e:
        print(f"서버 목록 저장 오류: {e}")

    bot.loop.create_task(stock_monitor())
    bot.loop.create_task(update_rpc_status())
    bot.loop.create_task(pending_charge_timeout_monitor())
    bot.loop.create_task(pushbullet_notification_monitor())

@bot.event
async def on_guild_join(guild):
    try:
        save_guild_json(None, 'bot_guilds',
                        {"guilds": [{"id": g.id, "name": g.name} for g in bot.guilds]})
    except Exception as e:
        print(f"서버 목록 저장 오류: {e}")
    try:
        ensure_guild_stock(guild.id)
    except Exception:
        pass
    target = None
    try:
        if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
            target = guild.system_channel
        else:
            for ch in guild.text_channels:
                try:
                    if ch.permissions_for(guild.me).send_messages:
                        target = ch
                        break
                except Exception:
                    continue
    except Exception:
        target = None
    if target:
        try:
            embed = nextcord.Embed(title=f"🔔 {SERVICE_NAME} 초기 설정", color=0x00ff00)
            embed.add_field(name="1️⃣ 자판기 설치", value="`/자판기` 로 판매 메시지를 보낼 채널 지정", inline=False)
            embed.add_field(name="2️⃣ 로그 채널", value="`/구매로그` `/충전로그` 로 채널 지정 (멘션/링크/ID)", inline=False)
            embed.add_field(name="3️⃣ 역할 지정", value="`/역할설정` 으로 리셀러·구매자·VIP 역할 지정", inline=False)
            embed.add_field(name="4️⃣ 상품 등록", value="`/자판기` 메시지의 설정 버튼 또는 웹 패널에서 등록", inline=False)
            embed.set_footer(text="관리자(Administrator) 권한이 있는 멤버만 설정 명령어를 쓸 수 있어요")
            await target.send(embed=embed)
        except Exception as e:
            print(f"입장 안내 전송 오류: {e}")

@bot.event
async def on_guild_remove(guild):
    try:
        save_guild_json(None, 'bot_guilds',
                        {"guilds": [{"id": g.id, "name": g.name} for g in bot.guilds]})
    except Exception as e:
        print(f"서버 목록 저장 오류: {e}")
async def update_rpc_status():
    while True:
        try:
            total_members = 0
            for guild in bot.guilds:
                total_members += (guild.member_count or 0)
            rpc_message = RPC_MESSAGES[0]
            activity_types = [
                nextcord.ActivityType.watching,
                nextcord.ActivityType.playing,
                nextcord.ActivityType.listening,
                nextcord.ActivityType.streaming
            ]
            activity_type = random.choice(activity_types)
            activity = nextcord.Activity(
                type=activity_type,
                name=rpc_message
            )
            await bot.change_presence(activity=activity)
            
            print(f"{SERVICE_NAME} | RPC 상태 업데이트: {rpc_message}")
            
            await asyncio.sleep(RPC_UPDATE_INTERVAL)
            
        except Exception as e:
            print(f"{SERVICE_NAME} | RPC 상태 업데이트 오류: {e}")
            await asyncio.sleep(RPC_ERROR_RETRY_INTERVAL)
async def stock_monitor():
    stock_counts = {}
    
    def get_all_stock_files():
        files = {}
        try:
            guilds = list(bot.guilds) if bot.guilds else [None]
        except Exception:
            guilds = [None]
        for g in guilds:
            gid = g.id if g else None
            stock_base_dir = guild_stock_dir(gid)
            if not os.path.exists(stock_base_dir):
                continue
            prefix = f"{gid}/" if gid is not None else ""
            # 카테고리 폴더 확인
            categories = get_categories_from_stock_folder(gid)
            for category in categories:
                category_path = os.path.join(stock_base_dir, category)
                if os.path.exists(category_path) and os.path.isdir(category_path):
                    for filename in os.listdir(category_path):
                        if filename.endswith('.txt'):
                            file_key = f"{prefix}{category}/{filename}"
                            file_path = os.path.join(category_path, filename)
                            files[file_key] = {'path': file_path, 'category': category, 'filename': filename}
            
            # 루트 폴더의 파일들
            try:
                for filename in os.listdir(stock_base_dir):
                    if filename.endswith('.txt'):
                        file_path = os.path.join(stock_base_dir, filename)
                        if os.path.isfile(file_path):
                            files[f"{prefix}{filename}"] = {'path': file_path, 'category': None, 'filename': filename}
            except OSError:
                pass
        
        return files
    
    # 초기 재고 수집
    all_files = get_all_stock_files()
    for file_key, file_info in all_files.items():
        try:
            with open(file_info['path'], 'r', encoding='utf-8') as f:
                lines = f.readlines()
                stock_counts[file_key] = len([line.strip() for line in lines if line.strip()])
        except:
            stock_counts[file_key] = 0
    
    await bot.wait_until_ready()
    
    while not bot.is_closed():
        try:
            current_files = get_all_stock_files()
            for file_key, file_info in current_files.items():
                try:
                    with open(file_info['path'], 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        current_count = len([line.strip() for line in lines if line.strip()])
                    stock_counts[file_key] = current_count
                except Exception as e:
                    print(f"파일 읽기 오류: {file_key} - {e}")
            # 삭제된 파일 제거
            deleted_files = set(stock_counts.keys()) - set(current_files.keys())
            for file_key in deleted_files:
                del stock_counts[file_key]
            
        except Exception as e:
            print(f"재고 모니터링 오류: {e}")
        
        await asyncio.sleep(30)
@bot.event
async def on_message(message):
    await bot.process_commands(message)
bot.run(discordBotToken)
