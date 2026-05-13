"""vLLM 监控看板 — FastAPI 后端

数据源：SSH → X2 (jinjuo@192.168.1.25) 执行 nvidia-smi + journalctl
启动: cd vllm-dashboard && uvicorn app:app --host 0.0.0.0 --port 8088
"""

import re
import subprocess
import time
from pathlib import Path
import sqlite3
import os
import importlib
import threading
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import jinja2

import config

app = FastAPI(title="vLLM Dashboard")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

SSH_BASE = f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout={config.SSH_TIMEOUT} {config.SSH_USER}@{config.SSH_HOST}"

# ── SQLite 持久化 ──────────────────────────────────────────
DB_PATH = Path(__file__).parent / "dashboard.db"

def _init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vllm_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL, gen_tps REAL, prom_tps REAL,
            running INTEGER, waiting INTEGER, kv_pct REAL, pfx_hit REAL
        );
        CREATE TABLE IF NOT EXISTS gpu_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL, gpu_index INTEGER NOT NULL,
            mem_used_mb REAL, mem_total_mb REAL,
            temp_c REAL, util_pct REAL, power_w REAL, power_limit_w REAL
        );
        CREATE INDEX IF NOT EXISTS idx_vllm_ts ON vllm_history(ts);
        CREATE INDEX IF NOT EXISTS idx_gpu_ts ON gpu_history(ts);
    """)
    conn.commit()
    conn.close()

_init_db()

# ── Config 热加载 ──────────────────────────────────────────
_config_mtime = os.path.getmtime(config.__file__)

def _check_config():
    global _config_mtime
    try:
        mtime = os.path.getmtime(config.__file__)
        if mtime > _config_mtime:
            importlib.reload(config)
            _config_mtime = mtime
            print(f"[config] hot-reloaded at {time.time():.0f}")
    except Exception:
        pass

# ── SQLite 写入辅助 ────────────────────────────────────────

def _save_vllm(ts, d):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO vllm_history(ts,gen_tps,prom_tps,running,waiting,kv_pct,pfx_hit) VALUES(?,?,?,?,?,?,?)",
            (ts, d.get("gen_tps"), d.get("prom_tps"), d.get("running"),
             d.get("waiting"), d.get("kv_pct"), d.get("pfx_hit"))
        )
        conn.commit()
    finally:
        conn.close()

def _save_gpu(ts, cards):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        for c in cards:
            conn.execute(
                "INSERT INTO gpu_history(ts,gpu_index,mem_used_mb,mem_total_mb,temp_c,util_pct,power_w,power_limit_w) VALUES(?,?,?,?,?,?,?,?)",
                (ts, c["index"], c["mem_used_mb"], c["mem_total_mb"],
                 c["temp_c"], c["util_pct"], c["power_w"], c["power_limit_w"])
            )
        conn.commit()
    finally:
        conn.close()

# ── 过期清理（7 天） ────────────────────────────────────────
CLEANUP_HOURS = 7 * 24
_last_cleanup = 0.0

def _maybe_cleanup():
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < 3600:
        return
    cutoff = now - CLEANUP_HOURS * 3600
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("DELETE FROM vllm_history WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM gpu_history WHERE ts < ?", (cutoff,))
        conn.commit()
        _last_cleanup = now
    finally:
        conn.close()

# ── 内存历史缓冲区（最近 120 个采样点） ──────────────────────
MAX_HISTORY = 120
_history = []  # list[dict]


# ── 命令执行（支持本地 / SSH 双模式） ────────────────────────

def _run_cmd(cmd: str) -> str:
    """执行命令返回 stdout。LOCAL_MODE=True 走本地，False 走 SSH。失败抛 RuntimeError。"""
    if config.LOCAL_MODE:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
    else:
        full = f"{SSH_BASE} \"{cmd}\""
        result = subprocess.run(full, shell=True, capture_output=True, text=True, timeout=config.SSH_TIMEOUT + 10)
    if result.returncode != 0:
        raise RuntimeError(f"Command error: {result.stderr.strip()[:200]}")
    return result.stdout

# 保持向后兼容
_run_ssh = _run_cmd


# ── GPU 端点 ───────────────────────────────────────────────────

@app.get("/api/gpu")
async def gpu_status():
    """nvidia-smi 查询：每张卡的显存 / 温度 / 利用率 / 功耗"""
    query = (
        "--query-gpu=index,name,memory.used,memory.total,"
        "temperature.gpu,utilization.gpu,power.draw,power.limit"
        " --format=csv,nounits,noheader"
    )
    raw = _run_ssh(f"nvidia-smi {query}")
    cards = []
    for line in raw.strip().split("\n"):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 8:
            cards.append({
                "index": int(parts[0]),
                "name": parts[1],
                "mem_used_mb": _to_float(parts[2]),
                "mem_total_mb": _to_float(parts[3]),
                "temp_c": _to_float(parts[4]),
                "util_pct": _to_float(parts[5]),
                "power_w": _to_float(parts[6]),
                "power_limit_w": _to_float(parts[7]),
            })
    _check_config()
    _save_gpu(time.time(), cards)
    _maybe_cleanup()
    return {"cards": cards}


# ── vLLM 端点 ──────────────────────────────────────────────────

# 存储上一次 Prometheus 计数器值用于计算 throughput rate
_vllm_prom_last = {}  # {metric_name: (value, timestamp)}

def _parse_prom_float(text, name):
    """从 Prometheus 纯文本中提取 gauge/counter 值。"""
    for line in text.split("\n"):
        if line.startswith(name + "{"):
            try:
                return float(line.rsplit(" ", 1)[-1])
            except:
                pass
    return None

@app.get("/api/vllm")
async def vllm_status():
    """从 vLLM Prometheus metrics（localhost:30001/metrics）获取性能指标。"""
    import httpx
    import time

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("http://localhost:30001/metrics", timeout=10.0)
            r.raise_for_status()
            text = r.text
    except Exception as e:
        return {"latest": {}, "avg_gen_tps": 0, "history": [], "error": str(e)}

    # 解析 Prometheus 指标
    gen_tok  = _parse_prom_float(text, "vllm:generation_tokens_total") or 0
    prom_tok = _parse_prom_float(text, "vllm:prompt_tokens_total") or 0
    running  = _parse_prom_float(text, "vllm:num_requests_running") or 0
    waiting  = _parse_prom_float(text, "vllm:num_requests_waiting") or 0
    kv_pct   = _parse_prom_float(text, "vllm:kv_cache_usage_perc") or 0
    pfx_hits = _parse_prom_float(text, "vllm:prefix_cache_hits_total") or 0
    pfx_qrys = _parse_prom_float(text, "vllm:prefix_cache_queries_total") or 0

    now = time.time()

    # 计算 throughput rates（计数器差值 / 时间差）
    global _vllm_prom_last
    gen_tps = 0
    prom_tps = 0
    if _vllm_prom_last:
        dt = now - _vllm_prom_last.get("ts", now)
        if dt > 0:
            dg = gen_tok - _vllm_prom_last.get("gen_tok", gen_tok)
            dp = prom_tok - _vllm_prom_last.get("prom_tok", prom_tok)
            gen_tps = round(max(0, dg / dt), 1)
            prom_tps = round(max(0, dp / dt), 1)
    _vllm_prom_last = {"ts": now, "gen_tok": gen_tok, "prom_tok": prom_tok}

    # 构造返回数据
    pfx_hit = round(pfx_hits / pfx_qrys * 100, 1) if pfx_qrys > 0 else 0

    latest = {
        "gen_tps": gen_tps,
        "prom_tps": prom_tps,
        "running": int(running),
        "waiting": int(waiting),
        "kv_pct": round(kv_pct * 100, 1),  # Prometheus 是 0-1 分数
        "pfx_hit": pfx_hit,
    }

    _check_config()
    ts = time.time()

    # ── 落盘到 SQLite ─────────────────────────────────
    _save_vllm(ts, latest)
    _maybe_cleanup()

    # ── 记录到内存历史（带时间戳） ──────────────────────
    snapshot = {"ts": ts, **latest}
    _history.append(snapshot)
    if len(_history) > MAX_HISTORY:
        _history.pop(0)

    # ── 从 SQLite 读取最近历史（供前端图表） ─────────────
    raw_history = [snapshot]

    return {
        "latest": latest,
        "avg_gen_tps": round(sum(h.get("gen_tps", 0) for h in _history if h.get("gen_tps", 0) > 0) / max(len([h for h in _history if h.get("gen_tps", 0) > 0]), 1), 1),
        "history": raw_history,
    }


@app.get("/api/models")
async def list_models():
    """从 vLLM API（同 Prometheus 端口 30001）获取已部署模型列表。"""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("http://localhost:30001/v1/models", timeout=5.0)
            r.raise_for_status()
            data = r.json()
            models = [m["id"] for m in data.get("data", []) if m.get("id")]
            return {"models": models, "default": models[0] if models else ""}
    except Exception as e:
        return {"models": [], "default": "", "error": str(e)}


# ── 时序历史数据（供前端画图） ──────────────────────────────

@app.get("/api/history")
async def history_data(range: str = ""):
    """时序历史数据。?range=1h|6h|12h|24h 从 SQLite 查询，默认返回内存缓冲。"""
    if range:
        hours = {"1h": 1, "6h": 6, "12h": 12, "24h": 24}.get(range, 1)
        cutoff = time.time() - hours * 3600
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT ts,gen_tps,prom_tps,running,waiting,kv_pct,pfx_hit FROM vllm_history WHERE ts >= ? ORDER BY ts",
                (cutoff,)
            ).fetchall()
            data = [dict(r) for r in rows]
            return {"data": data, "range": range}
        finally:
            conn.close()
    return {"data": _history}


# ── GPU 时序历史（新增） ─────────────────────────────────

@app.get("/api/gpu/history")
async def gpu_history_data(range: str = "1h"):
    """GPU 时序历史。?range=1h|6h|12h|24h，默认 1h。"""
    hours = {"1h": 1, "6h": 6, "12h": 12, "24h": 24}.get(range, 1)
    cutoff = time.time() - hours * 3600
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT ts,gpu_index,mem_used_mb,mem_total_mb,temp_c,util_pct,power_w,power_limit_w FROM gpu_history WHERE ts >= ? ORDER BY ts,gpu_index",
            (cutoff,)
        ).fetchall()
        data = [dict(r) for r in rows]
        return {"data": data, "range": range}
    finally:
        conn.close()


# ── 首页 ──────────────────────────────────────────────────────

@app.get("/")
def index():
    from fastapi.responses import HTMLResponse as HTMLResp
    from starlette.responses import Response
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(Path(__file__).parent / "static"))
    )
    tmpl = env.get_template("index.html")
    body = tmpl.render(refresh_sec=config.REFRESH_SECONDS)
    return Response(content=body, media_type="text/html",
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate",
                             "Pragma": "no-cache", "Expires": "0"})


# ── 工具 ───────────────────────────────────────────────────────

def _to_float(x):
    """兼容 str 和 regex match object，提取第一个数字。"""
    if isinstance(x, str):
        m = re.search(r'[\d.]+', x)
        return float(m.group()) if m else 0.0
    # regex match object
    try:
        return float(x.group(1)) if x and x.group(1) else 0.0
    except (AttributeError, ValueError, TypeError):
        return 0.0

def _to_int(x):
    """兼容 str 和 regex match object，提取第一个整数。"""
    if isinstance(x, str):
        m = re.search(r'\d+', x)
        return int(m.group()) if m else 0
    try:
        return int(x.group(1)) if x and x.group(1) else 0
    except (AttributeError, ValueError, TypeError):
        return 0


# ── Prometheus gauge 解析 ─────────────────────────────────────

def _get_gauge(metrics_text, metric_name):
    escaped = re.escape(metric_name)
    pat = re.compile(r'^' + escaped + r'(?:\{[^}]*\})?\s+([\d.eE+-]+)$', re.MULTILINE)
    m = pat.search(metrics_text)
    if m:
        try: return float(m.group(1))
        except ValueError: pass
    return 0.0


# ── Token Stats ────────────────────────────────────────────────

_stats_db_path = str(Path(__file__).parent / "token_stats.db")
_stats_lock = threading.Lock()
_sampler_thread = None

class TokenStats:
    def __init__(self, db_path):
        self._path = db_path
        self._init_db()
    def _init_db(self):
        with _stats_lock:
            conn = sqlite3.connect(self._path)
            conn.execute("CREATE TABLE IF NOT EXISTS samples(ts REAL PRIMARY KEY, gen_tokens REAL, prom_tokens REAL, epoch_id INTEGER DEFAULT 0, cum_gen REAL DEFAULT 0, cum_prom REAL DEFAULT 0)")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.commit(); conn.close()
    def _query(self, sql, params=()):
        conn = sqlite3.connect(self._path)
        rows = conn.execute(sql, params).fetchall(); conn.close()
        return rows
    def stats(self):
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=now.weekday())
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        def _delta_for(date_str):
            ts0 = datetime.strptime(date_str, "%Y-%m-%d").timestamp()
            rows = self._query("SELECT cum_gen, cum_prom FROM samples WHERE ts >= ? ORDER BY ts", (ts0,))
            if len(rows) < 2: return 0.0, 0.0
            g = sum(max(0, rows[i][0]-rows[i-1][0]) for i in range(1, len(rows)))
            p = sum(max(0, rows[i][1]-rows[i-1][1]) for i in range(1, len(rows)))
            return g, p
        tg,tp = _delta_for(today_start.strftime("%Y-%m-%d"))
        wg,wp = _delta_for(week_start.strftime("%Y-%m-%d"))
        mg,mp = _delta_for(month_start.strftime("%Y-%m-%d"))
        last_row = self._query("SELECT gen_tokens, prom_tokens, cum_gen, cum_prom FROM samples ORDER BY ts DESC LIMIT 1")
        if last_row:
            lr = last_row[0]
            try:
                metrics = _run_cmd("curl -s http://localhost:30001/metrics --max-time 3")
                live_gen = _get_gauge(metrics, "vllm:generation_tokens_total")
                live_prom = _get_gauge(metrics, "vllm:prompt_tokens_total")
                TG = lr[2] + max(0, live_gen - lr[0])
                TP = lr[3] + max(0, live_prom - lr[1])
            except Exception:
                TG, TP = lr[2], lr[3]
        else:
            TG, TP = 0.0, 0.0
        return {"today":{"gen":round(tg),"prom":round(tp)},"this_week":{"gen":round(wg),"prom":round(wp)},
                "this_month":{"gen":round(mg),"prom":round(mp)},"total":{"gen":round(TG),"prom":round(TP)}}
    def record(self, ts, gen_tokens, prom_tokens):
        with _stats_lock:
            conn = sqlite3.connect(self._path)
            last = conn.execute("SELECT gen_tokens, prom_tokens, epoch_id, cum_gen, cum_prom FROM samples ORDER BY ts DESC LIMIT 1").fetchone()
            if last is None:
                epoch, cum_g, cum_p = 0, gen_tokens, prom_tokens
            elif gen_tokens < last[0]:
                epoch = last[2] + 1
                cum_g = last[3] + last[0]
                cum_p = last[4] + last[1]
            else:
                epoch = last[2]
                cum_g = last[3] + max(0, gen_tokens - last[0])
                cum_p = last[4] + max(0, prom_tokens - last[1])
            conn.execute("INSERT OR REPLACE INTO samples(ts, gen_tokens, prom_tokens, epoch_id, cum_gen, cum_prom) VALUES(?,?,?,?,?,?)",
                         (ts, gen_tokens, prom_tokens, epoch, cum_g, cum_p))
            conn.commit(); conn.close()

_token_stats = TokenStats(_stats_db_path)

def _token_sampler(stats, interval=30):
    while True:
        try:
            metrics = _run_cmd("curl -s http://localhost:30001/metrics --max-time 3")
            gt = _get_gauge(metrics, "vllm:generation_tokens_total")
            pt = _get_gauge(metrics, "vllm:prompt_tokens_total")
            if gt > 0 or pt > 0: stats.record(time.time(), gt, pt)
        except Exception: pass
        time.sleep(interval)

def _start_sampler():
    global _sampler_thread
    try:
        metrics = _run_cmd("curl -s http://localhost:30001/metrics --max-time 3")
        gt = _get_gauge(metrics, 'vllm:generation_tokens_total')
        pt = _get_gauge(metrics, 'vllm:prompt_tokens_total')
        if gt > 0 or pt > 0:
            rows = _token_stats._query('SELECT COUNT(*) FROM samples')
            if rows and rows[0][0] == 0:
                _token_stats.record(time.time(), gt, pt)
    except Exception: pass
    if _sampler_thread and _sampler_thread.is_alive(): return
    _sampler_thread = threading.Thread(target=_token_sampler, args=(_token_stats, 30), daemon=True)
    _sampler_thread.start()

def _stop_sampler():
    global _sampler_thread
    _sampler_thread = None


# ── Token 统计端点 ─────────────────────────────────────────────

@app.get("/api/tokens")
async def token_stats_api():
    """返回 Token 消费统计：今日/本周/本月/累计。"""
    return _token_stats.stats()


# ── 机箱传感器端点 ─────────────────────────────────────────────

SENSORS_QUERY = (
    "sensors 2>/dev/null | grep -E '°C|RPM'"
)

@app.get("/api/chassis")
async def chassis_sensors():
    """返回机箱传感器温度 + 风扇转速（sensors 命令输出解析）。"""
    try:
        raw = _run_cmd(SENSORS_QUERY)
    except Exception as e:
        return {"error": str(e)}
    temps, fans = [], []
    for line in raw.split("\n"):
        line = line.strip()
        if not line or "°C" not in line and "RPM" not in line:
            continue
        if "°C" in line:
            m = re.match(r'^([\w\s]+?):\s*\+?([\d.]+)°C', line)
            if m:
                name = m.group(1).strip()
                val = round(float(m.group(2)), 1)
                # 只保留有意义的传感器 > 0°C 且不是每核温度
                if (val > 0 and val < 100 and not name.startswith("Core")
                    and not name.startswith("PCH_") and not name.startswith("Sensor")
                    and name not in ("temp1", "CPU", "GPU")):
                    temps.append({"sensor": name, "temp_c": val})
        elif "RPM" in line:
            m = re.match(r'^(fan\d+):\s*(\d+)\s*RPM', line)
            if m and int(m.group(2)) > 0:
                fans.append({"label": m.group(1), "rpm": int(m.group(2))})
    return {"temps": temps, "fans": fans}


@app.get("/api/cpu")
async def cpu_info():
    """返回 CPU + 内存使用情况（top + free 命令解析）。"""
    try:
        # 并行获取 CPU 和内存
        top_out = _run_cmd("top -bn1")
        free_out = _run_cmd("free -b")
        uptime_out = _run_cmd("uptime")
    except Exception as e:
        return {"error": str(e)}

    # 解析 top: %Cpu(s):  3.0 us,  1.3 sy,  0.0 ni, 95.2 id,  0.0 wa,  0.5 hi,  0.0 si,  0.0 st
    cpu_pct = 0.0
    cpu_line = None
    for line in top_out.split("\n"):
        if line.startswith("%Cpu"):
            cpu_line = line
            break
    if cpu_line:
        # 用 idle 来计算总使用率
        id_m = re.search(r'([\d.]+)\s+id', cpu_line)
        if id_m:
            cpu_pct = round(100.0 - float(id_m.group(1)), 1)

    # 解析 free（支持中/英文 locale，中文 locale 标签和数字间无空格）
    mem_total = mem_used = mem_avail = mem_free = 0
    swap_total = swap_used = 0
    for line in free_out.split("\n"):
        # 移除行首标签（可能粘连在第一个数字上）
        line2 = line.lstrip()
        # 尝试分离标签和数字
        for sep in (" ：", ":", "：", "  "):
            if sep in line2:
                line2 = line2.split(sep, 1)[-1]
                break
        parts = line2.strip().split()
        if len(parts) < 2:
            continue
        try:
            total_val = int(parts[0])
        except ValueError:
            continue
        if len(parts) >= 6 and total_val > 1_000_000_000:  # 内存行
            mem_total = total_val
            mem_used = int(parts[1])
            mem_free = int(parts[2])
            mem_avail = int(parts[-1])  # available 是最后一列
        elif len(parts) >= 2:  # Swap 行（通常只有 3 列: total used free）
            swap_total = total_val
            swap_used = int(parts[1])

    mem_pct = round(mem_used / mem_total * 100, 1) if mem_total > 0 else 0
    swap_pct = round(swap_used / swap_total * 100, 1) if swap_total > 0 else 0

    # 解析 uptime load average
    load1 = load5 = load15 = 0.0
    if "load average:" in uptime_out:
        m = re.search(r'load average:\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)', uptime_out)
        if m:
            load1, load5, load15 = float(m.group(1)), float(m.group(2)), float(m.group(3))

    # CPU 核心数
    try:
        cores = int(_run_cmd("nproc").strip())
    except:
        cores = 1

    return {
        "cpu_pct": cpu_pct,
        "cpu_line": cpu_line,
        "cores": cores,
        "load_avg": [load1, load5, load15],
        "mem_total_mb": round(mem_total / (1024*1024), 1),
        "mem_used_mb": round(mem_used / (1024*1024), 1),
        "mem_avail_mb": round(mem_avail / (1024*1024), 1),
        "mem_pct": mem_pct,
        "swap_total_mb": round(swap_total / (1024*1024), 1),
        "swap_used_mb": round(swap_used / (1024*1024), 1),
        "swap_pct": swap_pct,
    }


# ── 启动/停止 ──────────────────────────────────────────────────

@app.on_event("startup")
def _startup_cleanup():
    _maybe_cleanup()
    _start_sampler()

@app.on_event("shutdown")
def _shutdown():
    _stop_sampler()
