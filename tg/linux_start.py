#!/usr/bin/env python3
"""
TG-JIQIREN Linux 启动器
功能：自动安装依赖 → 启动 FastAPI 后端 → 打开浏览器
"""
import os
import sys
import subprocess
import time
import socket
import signal
import webbrowser
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TG_SUITE_DIR = os.path.join(BASE_DIR, "tg_suite")


def find_free_port(start=18000, end=18999):
    """在指定范围内自动找一个空闲端口"""
    for port in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"在 {start}-{end} 范围内找不到空闲端口")


PORT = find_free_port()
URL = f"http://127.0.0.1:{PORT}"

python_proc = None


def banner():
    print("\033[36m")
    print("  ████████╗ ██████╗      ███╗   ███╗ █████╗  ██████╗ ██╗")
    print("     ██╔══╝██╔════╝      ████╗ ████║██╔══██╗██╔════╝ ██║")
    print("     ██║   ██║  ███╗     ██╔████╔██║███████║██║  ███╗██║")
    print("     ██║   ██║   ██║     ██║╚██╔╝██║██╔══██║██║   ██║╚═╝")
    print("     ██║   ╚██████╔╝     ██║ ╚═╝ ██║██║  ██║╚██████╔╝██╗")
    print("     ╚═╝    ╚═════╝      ╚═╝     ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═╝")
    print("\033[0m")
    print("  \033[32m天宫 TG 机器人 · Telegram 自动化运营控制台 · Linux 版\033[0m")
    print("  " + "─" * 54)
    print()


def log(msg, level="info"):
    colors = {"info": "\033[94m", "ok": "\033[92m", "warn": "\033[93m", "err": "\033[91m"}
    prefix = {"info": "[*]", "ok": "[✓]", "warn": "[!]", "err": "[✗]"}
    color = colors.get(level, "")
    reset = "\033[0m"
    print(f"  {color}{prefix.get(level, '[*]')} {msg}{reset}")


def find_python():
    """查找可用的 Python 解释器"""
    candidates = [
        os.path.join(TG_SUITE_DIR, "venv", "bin", "python3"),
        os.path.join(TG_SUITE_DIR, ".venv", "bin", "python3"),
        "python3",
        "python",
    ]
    for c in candidates:
        try:
            r = subprocess.run([c, "--version"], capture_output=True, timeout=3)
            if r.returncode == 0:
                return c
        except Exception:
            continue
    return "python3"


def install_deps(python_cmd):
    """安装后端依赖"""
    req_file = os.path.join(TG_SUITE_DIR, "requirements.txt")
    marker = os.path.join(TG_SUITE_DIR, ".deps_ok")

    if os.path.exists(marker):
        log("依赖已就绪，跳过安装", "ok")
        return True

    if not os.path.exists(req_file):
        log("未找到 requirements.txt，跳过安装", "warn")
        return True

    log("首次运行，自动安装依赖（约 30~60 秒）...")
    try:
        result = subprocess.run(
            [python_cmd, "-m", "pip", "install", "-r", req_file, "-q",
             "--disable-pip-version-check"],
            cwd=TG_SUITE_DIR,
            timeout=300
        )
        if result.returncode == 0:
            with open(marker, "w") as f:
                f.write("ok")
            log("依赖安装完成", "ok")
            return True
        else:
            log("部分依赖安装失败，功能可能受影响", "warn")
            return False
    except subprocess.TimeoutExpired:
        log("安装超时，请检查网络连接", "err")
        return False


def load_env():
    """读取 .env 文件返回环境变量字典"""
    env = os.environ.copy()
    env_file = os.path.join(TG_SUITE_DIR, ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


def wait_for_port(port, timeout=30):
    """等待端口就绪"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def start_backend(python_cmd):
    """启动 FastAPI 后端进程"""
    global python_proc
    env = load_env()

    # 优先使用 tg_suite/venv 内置的 python
    venv_python = os.path.join(TG_SUITE_DIR, "venv", "bin", "python3")
    if os.path.exists(venv_python):
        python_cmd = venv_python

    log("正在启动控制台后台服务...")
    log_file_path = os.path.join(BASE_DIR, "server.log")
    log_f = open(log_file_path, "w", encoding="utf-8")

    python_proc = subprocess.Popen(
        [python_cmd, "-m", "uvicorn", "server:app",
         "--host", "127.0.0.1",
         "--port", str(PORT),
         "--no-access-log"],
        cwd=TG_SUITE_DIR,  # 必须在 tg_suite/ 目录下运行
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )

    # 等待 1 秒检查进程是否异常退出
    time.sleep(1)
    if python_proc.poll() is not None:
        log_f.close()
        with open(log_file_path, "r", encoding="utf-8") as rf:
            err_msg = rf.read()
        log(f"后端启动失败:\n{err_msg}", "err")
        sys.exit(1)

    log(f"后端服务已启动 (PID: {python_proc.pid})", "ok")
    return python_proc


def open_browser_when_ready():
    """等端口就绪后自动打开浏览器"""
    if wait_for_port(PORT, timeout=30):
        time.sleep(0.5)
        webbrowser.open(URL)
        log(f"已在浏览器中打开控制台 → {URL}", "ok")
    else:
        log(f"服务启动超时，请手动打开浏览器访问: {URL}", "warn")


def shutdown(signum=None, frame=None):
    """程序退出时清理进程"""
    print()
    log("正在关闭后台服务...")
    if python_proc and python_proc.poll() is None:
        python_proc.terminate()
        try:
            python_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            python_proc.kill()
    log("已完全退出", "ok")
    sys.exit(0)


def main():
    banner()

    # 注册信号处理
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # 1. 找 Python
    python_cmd = find_python()
    log(f"使用 Python: {python_cmd}")

    # 2. 安装依赖
    install_deps(python_cmd)

    # 3. 启动后端
    start_backend(python_cmd)

    # 4. 在后台线程等待并打开浏览器
    threading.Thread(target=open_browser_when_ready, daemon=True).start()

    # 5. 打印访问信息
    print()
    print("  " + "─" * 54)
    log(f"控制台地址: \033[4m{URL}\033[0m", "ok")
    log("按 Ctrl+C 退出程序", "info")
    print("  " + "─" * 54)
    print()

    # 6. 等待后端进程结束（保持主线程存活）
    try:
        python_proc.wait()
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
