"""라이선스 발급기 (데스크톱 UI, 사이트 아님)
실행: python license_ui.py  (창이 뜸)
여기서 발급한 키로 웹패널(http://127.0.0.1:8099)에 입장함.
data/licenses.json을 패널과 공유함. 표준라이브러리만 사용.
"""
import os
import re
import json
import secrets
import datetime
import tkinter as tk
from tkinter import messagebox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")


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
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump({"keys": keys}, f, ensure_ascii=False, indent=2)


def gen_license_key():
    alpha = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "CMT-" + "-".join("".join(secrets.choice(alpha) for _ in range(4)) for _ in range(3))


def norm_key(s):
    return re.sub(r"[\s\-]", "", (s or "")).upper()


def is_expired(rec):
    if (rec or {}).get("mode", "perm") != "temp":
        return False
    fl = (rec or {}).get("first_login")
    if not fl:
        return False
    try:
        start = datetime.datetime.fromisoformat(fl)
    except ValueError:
        return False
    try:
        dur = int((rec or {}).get("duration_sec", 0) or 0)
    except (ValueError, TypeError):
        dur = 0
    return (datetime.datetime.now() - start).total_seconds() > dur


def remain_sec(rec):
    try:
        start = datetime.datetime.fromisoformat((rec or {}).get("first_login"))
        dur = int((rec or {}).get("duration_sec", 0) or 0)
        return max(0, int(dur - (datetime.datetime.now() - start).total_seconds()))
    except (ValueError, TypeError):
        return 0


def fmt_remain(s):
    s = max(0, int(s))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d:
        parts.append(f"{d}일")
    if h:
        parts.append(f"{h}시간")
    if m:
        parts.append(f"{m}분")
    if s or not parts:
        parts.append(f"{s}초")
    return " ".join(parts)


def fmt_dur(s):
    try:
        s = int(s or 0)
    except (ValueError, TypeError):
        return "?초"
    if s >= 86400 and s % 86400 == 0:
        return f"{s // 86400}일"
    if s >= 3600 and s % 3600 == 0:
        return f"{s // 3600}시간"
    if s >= 60 and s % 60 == 0:
        return f"{s // 60}분"
    return f"{s}초"


class App:
    def __init__(self, root):
        self.root = root
        root.title("라이선스 발급기")
        root.geometry("680x420")
        root.resizable(False, False)

        top = tk.Frame(root)
        top.pack(fill="x", padx=10, pady=10)
        tk.Label(top, text="메모:").pack(side="left")
        self.memo = tk.Entry(top, width=16)
        self.memo.pack(side="left", padx=4)
        self.mode = tk.StringVar(value="영구")
        tk.OptionMenu(top, self.mode, "영구", "기간제").pack(side="left")
        self.secs = tk.Entry(top, width=8)
        self.secs.pack(side="left", padx=4)
        self.secs.insert(0, "1")
        self.unit = tk.StringVar(value="일")
        tk.OptionMenu(top, self.unit, "초", "분", "시간", "일").pack(side="left")
        tk.Button(top, text="발급", command=self.issue, width=8).pack(side="left", padx=4)

        self.newkey = tk.Label(root, text="", font=("Malgun Gothic", 13, "bold"), fg="#0066cc")
        self.newkey.pack(pady=4)

        mid = tk.Frame(root)
        mid.pack(fill="both", expand=True, padx=10)
        self.listbox = tk.Listbox(mid, font=("Consolas", 11))
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(mid, command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)

        bot = tk.Frame(root)
        bot.pack(fill="x", padx=10, pady=10)
        tk.Button(bot, text="새로고침", command=self.refresh, width=10).pack(side="left", padx=2)
        tk.Button(bot, text="복사", command=self.copy, width=10).pack(side="left", padx=2)
        tk.Button(bot, text="삭제", command=self.revoke, width=10).pack(side="left", padx=2)

        self.keys = []
        self.refresh()

    def refresh(self):
        data = load_licenses()
        self.keys = sorted(data.items(), key=lambda kv: kv[1].get("created", ""))
        self.listbox.delete(0, tk.END)
        for k, v in self.keys:
            label = v.get("label", "")
            if v.get("mode", "perm") == "temp":
                if is_expired(v):
                    state = "만료됨"
                elif v.get("first_login"):
                    state = f"{fmt_remain(remain_sec(v))} 남음"
                else:
                    state = f"{fmt_dur(v.get('duration_sec', 0))} (미사용)"
                self.listbox.insert(tk.END, f"{k}  |  {label}  |  {state}")
            else:
                self.listbox.insert(tk.END, f"{k}  |  {label}  |  영구")

    def issue(self):
        mode = "temp" if self.mode.get() == "기간제" else "perm"
        dur = 0
        if mode == "temp":
            try:
                amount = int(self.secs.get().strip())
            except ValueError:
                amount = 0
            mult = {"초": 1, "분": 60, "시간": 3600, "일": 86400}.get(self.unit.get(), 1)
            dur = amount * mult
            if dur <= 0:
                messagebox.showinfo("알림", "기간제는 1 이상 입력하세요")
                return
        for _ in range(10):
            nk = gen_license_key()
            if norm_key(nk) not in [norm_key(k) for k in load_licenses()]:
                break
        keys = load_licenses()
        rec = {"label": self.memo.get().strip()[:50],
               "created": datetime.datetime.now().isoformat(),
               "mode": mode}
        if mode == "temp":
            rec["duration_sec"] = dur
            rec["first_login"] = None
        keys[nk] = rec
        save_licenses(keys)
        self.memo.delete(0, tk.END)
        self.newkey.config(text=f"발급됨: {nk}")
        self.root.clipboard_clear()
        self.root.clipboard_append(nk)
        self.refresh()

    def selected_key(self):
        sel = self.listbox.curselection()
        if not sel:
            return None
        return self.keys[sel[0]][0]

    def copy(self):
        k = self.selected_key()
        if not k:
            messagebox.showinfo("알림", "목록에서 선택하세요")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(k)
        messagebox.showinfo("복사", "클립보드에 복사됨")

    def revoke(self):
        k = self.selected_key()
        if not k:
            messagebox.showinfo("알림", "목록에서 선택하세요")
            return
        if not messagebox.askyesno("확인", f"이 라이선스를 삭제할까?\n{k}"):
            return
        keys = load_licenses()
        hit = next((x for x in keys if norm_key(x) == norm_key(k)), None)
        if hit:
            del keys[hit]
            save_licenses(keys)
        self.refresh()


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    root = tk.Tk()
    App(root)
    root.mainloop()
