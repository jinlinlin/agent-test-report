"""`python -m report_agent.bot` 入口：启动飞书应用机器人长连接（常驻进程）。"""
import sys

from .app import serve

if __name__ == "__main__":
    sys.exit(serve())
