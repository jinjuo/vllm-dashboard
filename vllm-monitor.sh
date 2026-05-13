#!/bin/bash
# vllm-monitor.sh — vLLM 监控看板快速启动/管理脚本
# 用法: ./vllm-monitor.sh start | stop | restart | status | install

set -e

NAME="vLLM 监控看板"
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=8088
PIDFILE="$DIR/.uvicorn.pid"

install_deps() {
  echo "📦 安装依赖..."
  /usr/bin/python3 -m pip install --user -i https://pypi.tuna.tsinghua.edu.cn/simple \
    fastapi uvicorn jinja2 2>/dev/null || true
  echo "✅ 依赖安装完成"
}

start() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "⚠️  服务已在运行 (PID: $(cat $PIDFILE))"
    return 0
  fi

  # 检查依赖
  /usr/bin/python3 -c "import uvicorn" 2>/dev/null || install_deps

  cd "$DIR"
  echo "🚀 启动 ${NAME}..."
  nohup /usr/bin/python3 -m uvicorn app:app \
    --host 0.0.0.0 --port $PORT \
    --app-dir . \
    > "$DIR/dashboard.log" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 2

  if kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
    echo "✅ ${NAME} 已启动 (PID: $(cat $PIDFILE))"
    echo "📡 访问地址: http://localhost:$PORT  或  http://<X2-ZeroTier-IP>:$PORT"
  else
    echo "❌ 启动失败，查看日志: tail -20 $DIR/dashboard.log"
    rm -f "$PIDFILE"
    return 1
  fi
}

stop() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat $PIDFILE)"
    rm -f "$PIDFILE"
    echo "⏹️  ${NAME} 已停止"
  else
    echo "⚠️  服务未在运行"
    rm -f "$PIDFILE"
  fi
}

restart() {
  stop
  sleep 1
  start
}

status() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    PID=$(cat $PIDFILE)
    echo "🟢 ${NAME} 运行中 (PID: $PID)"
    # 显示内存占用
    ps -o pid,rss,%mem,%cmd -p $PID 2>/dev/null | tail -n +2 | while read p rss mem cmd; do
      echo "   内存: $((rss/1024)) MB | ${mem}% CPU"
    done
    curl -s -o /dev/null -w "   HTTP: %{http_code}\n" "http://localhost:$PORT/" 2>/dev/null || echo "   HTTP: 无响应"
  else
    echo "🔴 ${NAME} 未运行"
    rm -f "$PIDFILE"
  fi
}

case "${1:-help}" in
  start)     start ;;
  stop)      stop ;;
  restart)   restart ;;
  status)    status ;;
  install)   install_deps ;;
  *)
    echo "${NAME} 管理脚本"
    echo "用法: $0 {start|stop|restart|status|install}"
    echo ""
    echo "示例:"
    echo "  $0 start    — 启动看板"
    echo "  $0 stop     — 停止看板"
    echo "  $0 restart  — 重启看板"
    echo "  $0 status   — 查看状态"
    echo "  $0 install  — 安装依赖"
    ;;
esac
