# vLLM 监控看板

**项目**: 监控 X2 服务器上的 vLLM 服务 (qwen3.6-27B-FP8)
**技术栈**: FastAPI + Chart.js 实时时序图
**运行模式**: 本地部署在 X2 上（本地 nvidia-smi + journalctl）

## 快速开始

```bash
# 首次使用 — 安装依赖
bash vllm-monitor.sh install

# 启动看板
bash vllm-monitor.sh start

# 查看状态
bash vllm-monitor.sh status

# 停止看板
bash vllm-monitor.sh stop
```

## 访问

- 本地: `http://localhost:8088/`
- ZeroTier: `http://<X2-ZeroTier-IP>:8088/`
- LAN: `http://192.168.1.25:8088/`

## 监控指标

### GPU
- 显存使用 / 总量（进度条 + 时序图）
- 温度、利用率、功耗（多轴图）

### vLLM
- Generation Throughput (tokens/s)
- Prompt Throughput (tokens/s)
- Running / Waiting Requests
- KV Cache Usage %
- Prefix Cache Hit Rate %

## 配置

编辑 `config.py`：
- `LOCAL_MODE = True` — 本地模式（部署在 X2 上）
- `LOCAL_MODE = False` — SSH 模式（部署在其他机器，SSH 连 X2）
- `REFRESH_SECONDS = 5` — 前端刷新频率
- `VLLM_UNIT = "vllm-qwen36"` — systemd 服务名

## 防火墙

首次部署需要放行端口：
```bash
sudo ufw allow 8088/tcp
```

## 文件结构

```
vllm-dashboard/
├── app.py              # FastAPI 后端
├── config.py           # 配置文件
├── vllm-monitor.sh     # 启动/管理脚本
├── static/
│   └── index.html      # 单页前端（Chart.js 时序图）
└── README.md           # 本文件
```
