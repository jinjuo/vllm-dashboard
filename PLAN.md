# vLLM Dashboard 升级计划

> 最后更新：2026-05-13（E 轮已上线）
> 目标：X2 vLLM 看板从 70→90 分，bajie 设计评审驱动的迭代

---

## ✅ Phase 1 — B+A 底座（已完成，已部署 X2:8088）
- SQLite 持久化（vLLM + GPU 每 15s 落盘，30 天清理）
- `/api/history` + `/api/gpu/history` 查询端点
- Config 热加载
- 时间范围选择器（1h/6h/24h/7d）
- 暂停/恢复轮询
- Toast 通知
- 图表优化（maxTicksLimit, 双 Y 轴）
- 交付：tianbing，已上线 🟢

---

## 🚧 Phase 2 — Interaction D（已完成，已部署 X2:8088）
- D1 Chart.js Zoom 插件（drag/scroll/dblclick 复位）
- D2 悬停十字线 + 数据标注
- D3 GPU 卡片折叠/展开（localStorage 持久）
- D4 一键截图导出（html2canvas）
- 交付：tianbing，已上线 🟢

---

## ✅ Phase 3 — Enhancement E（已完成，已部署 X2:8088）

**执行顺序（tianbing 建议，已采纳）：** E1 → E3 → E2 → E4
*E2（sparkline）依赖 E3 配色体系落地，先定色再画图避免改两遍*

### E1 · 微交互提质感（~20min）
- Toast 自动淡出动画（3s auto-dismiss）
- 状态栏环境状态指示灯（🟢🟡🔴）
- Owner：tianbing
- 依赖：无，纯前端

### E3 · 颜色体系升级（~40min）
- bajie CSS 变量方案已交付（深渊蓝调，`2026-05-13`）
- 直接落地 `:root` 替换 + GPU 卡片左边界色硬编码改 `var(--ok/warn/err)`
- Owner：tianbing（落地）/ bajie（review 色值）
- 依赖：E1（可独立并行）

### E2 · GPU 卡微趋势图（~30min）
- 每张 GPU 卡摘要行 sparkline（30min 利用率趋势）
- 从 `/api/gpu/history` 取数据
- Owner：tianbing
- 依赖：E3 CSS 变量就绪后

### E4 · vLLM 指标 Tabs 化（~45min，实际实现）
- 6 条线图拆成 3 个 Tab：📈 吞吐量 / ⏳ 排队 / 💾 缓存
  - **吞吐量**：Generation + Prompt Throughput
  - **排队**：Running + Waiting
  - **缓存**：GPU KV Cache %
- 切换 Tab 自动 resize 图表防止 display:none 导致 canvas 尺寸丢失
- GPU 大图保持全宽显示在下方（未折叠）
- ⚠️ 与计划（长尾指标折叠）不一致，如倾向原方案可回退
- Owner：tianbing

### 总预估：~2h → 实际 ~1.5h 🟢

---

## 🪜 迭代节奏（checkpoint 巡逻）

| 检查点 | 时间 | 检查内容 | 触发器 | 状态 |
|---|---|---|---|---|
| CP-01 | T+30min | E1 落盘验证（Toast 动画 + 状态灯） | schedule | ✅ 完成 |
| CP-02 | T+70min | E3 落地验证（CSS 变量替换 + 卡片色改 var） | schedule | ✅ 完成 |
| CP-03 | T+110min | E2 sparkline 落盘验证 | schedule | ✅ 完成 |
| CP-04 | T+150min | E4 折叠 + 全量验收 → 通知老板 deploy | schedule | ✅ 完成 |

---

## 🚀 交付标准
1. 每个子步骤跑通即 deploy，不等全部做完
2. deploy 后 boss 能在 X2:8088 看到效果
3. 每个 E 阶段完成更新 CHANGELOG.md
4. 最终验收：bajie 设计评审 + wukong 功能验收
