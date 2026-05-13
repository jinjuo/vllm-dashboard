# CHANGELOG — vLLM 监控看板升级

## 2026-05-13 · Phase 3 E1-E4（bajie 设计评审迭代） ✅

### 前端改动（static/index.html）

#### E1 · 微交互提质感
- **Toast 通知** — 加入 translateY 弹入/弹出动画，3s 自动淡出（原来 2.5s 硬消失）
- **状态指示灯** — 纯色小点改为 🟢 正常 / 🟡 降级（部分 API 异常）/ 🔴 错误，一目了然

#### E3 · 颜色体系升级
- **CSS 变量全面重构** — 更深的对比度（#060a14 背景 / #0f172a 卡片 / #e2e8f0 文字）
- **Gradient 风格** — 头部用渐变背景，卡片用垂直渐变，标题用渐变文字
- **Chart.js 色值同步** — 所有图表线色、网格色、tooltip 色同步新 palette
- hover 状态微交互（按钮/卡片 hover 高亮）

#### E2 · GPU 卡微趋势图
- **Sparkline** — 每张 GPU 卡摘要行右侧显示 30 分钟利用率趋势迷你图（canvas 2D 轻量渲染，无需 Chart.js）
- **折叠/展开** — 每张 GPU 卡可折叠只保留摘要行+sparkline，状态持久化到 localStorage
- **历史兼容** — 切换时间范围时 sparkline 从 history API 重新加载

#### E4 · vLLM 指标 Tabs 化
- 6 条线图拆成 3 个 Tab：📈 吞吐量 / ⏳ 排队 / 💾 缓存
  - **吞吐量**：Generation Throughput + Prompt Throughput
  - **排队**：Running + Waiting
  - **缓存**：GPU KV Cache %
- 切换 Tab 时自动 resize 图表，防止 `display: none` 导致的 canvas 尺寸丢失

## 2026-05-13 · Phase 1（B 底座） + Phase 2 前端 A/D 部分

### 后端改动（app.py）

#### B 底座：SQLite 持久化
- **新增** `dashboard.db`（SQLite）双表持久化：`vllm_history` + `gpu_history`
- 每次 `/api/gpu` 和 `/api/vllm` 被调用时自动 INSERT 落盘
- **新增** `/api/history?range=1h|6h|12h|24h` — 从 SQLite 查询历史时序数据
- **新增** `/api/gpu/history?range=1h|6h|12h|24h` — GPU 历史数据端点（解决刷新页面 GPU 时序丢失问题）
- **新增** 7 天过期自动清理（`_maybe_cleanup()`，每小时检查一次）
- **新增** 启动时清理事件 `@app.on_event("startup")`

#### Config 热加载
- **新增** `_check_config()`：每次 API 调用前 stat 检查 config.py 的 mtime，变化则 `importlib.reload(config)`
- 修改 `config.py` 无需重启 uvicorn

#### 代码结构
- 总行数 ~260 行（原来 ~150 行），新增约 110 行
- 完全向后兼容，不改原有 API 响应格式

### 前端改动（static/index.html）

#### 时间范围选择
- **新增** 时间范围按钮组（实时 / 1h / 6h / 24h）
- 点选后端从 SQLite 拉取历史数据重新渲染图表
- 状态栏显示当前范围和采样点数

#### GPU 历史（修复刷新丢数据）
- 非「实时」模式下 GPU 图表从 `/api/gpu/history` 后端取数
- 「实时」模式下 GPU 图延续当前 session 的本地缓冲叠新点

#### 暂停/恢复自动刷新
- **新增** ⏸/▶ 暂停按钮
- 暂停后停止轮询但可手动刷新，恢复后自动补上

#### 视觉优化
- **新增** 顶部状态栏（范围/采样点/轮询间隔）
- **新增** Toast 提示组件（底部右侧自动消失）
- 图表 X 轴刻度上限从 8 提到 12（1h/6h 模式下展示更密）
- 时间戳采用 Unix timestamp 统一格式（前后端一致）

### 部署

- **路径**: X2 `/home/jinjuo/vllm-dashboard/`
- **启动**: `cd ~/vllm-dashboard && uvicorn app:app --host 0.0.0.0 --port 8088`
- 首次启动自动创建 `dashboard.db`
- 已跑通本地语法检验 ✅
