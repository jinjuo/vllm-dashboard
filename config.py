"""vLLM 监控看板 — 配置

运行模式：
  LOCAL_MODE = True  → 直接在本地执行 nvidia-smi / journalctl（部署在 X2 上）
  LOCAL_MODE = False → 通过 SSH 远程执行（部署在 Prod 上）
"""

# ── 运行模式 ──────────────────────────────────────────────────
LOCAL_MODE = True              # X2 部署 = True, Prod 部署 = False

# ── 远程 SSH 模式（LOCAL_MODE=False 时使用） ──────────────────
SSH_HOST = "192.168.1.25"
SSH_PORT = 22
SSH_USER = "jinjuo"
SSH_KEY_PATH = ""
SSH_TIMEOUT = 10

# ── vLLM 日志 ──────────────────────────────────────────────────
VLLM_UNIT = "vllm-qwen36"      # systemd service name

# ── 前端刷新频率 ──────────────────────────────────────────────
REFRESH_SECONDS = 5

# ── Web 服务 ────────────────────────────────────────────────────
WEB_HOST = "0.0.0.0"
WEB_PORT = 8088
