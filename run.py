#!/usr/bin/env python3
import os
import sys
import subprocess

# ──────────────────────────────────────────────
# 启动前自动检查并安装依赖
# ──────────────────────────────────────────────

def _ensure_venv():
    """确保虚拟环境存在并激活"""
    _HERE = os.path.dirname(os.path.abspath(__file__))
    venv_dir = os.path.join(_HERE, "venv")

    # 如果已经在虚拟环境中，直接返回
    if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        return

    # 创建虚拟环境
    if not os.path.exists(venv_dir):
        print("创建虚拟环境...")
        subprocess.check_call([sys.executable, "-m", "venv", venv_dir])
        print("✅ 虚拟环境创建完成\n")

    # 重新启动脚本在虚拟环境中
    if sys.platform == "win32":
        python_exe = os.path.join(venv_dir, "Scripts", "python.exe")
    else:
        python_exe = os.path.join(venv_dir, "bin", "python")

    os.execv(python_exe, [python_exe] + sys.argv)

def _ensure_deps():
    _HERE = os.path.dirname(os.path.abspath(__file__))
    req_file = os.path.join(_HERE, "requirements.txt")
    missing = []
    pkg_map = {
        "camoufox": "camoufox",
        "curl_cffi": "curl_cffi",
        "patchright": "patchright",
        "quart": "quart",
        "requests": "requests",
        "rich": "rich",
        "playwright": "playwright",
    }
    for mod, pkg in pkg_map.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)

    if missing:
        print(f"正在安装依赖: {', '.join(missing)}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_file, "-q"])
        print("✅ 依赖安装完成\n")

    # 检查 camoufox 浏览器
    try:
        import camoufox
        data_dir = os.path.join(os.path.dirname(camoufox.__file__), "data")
        if not os.path.exists(data_dir) or not os.listdir(data_dir):
            print("正在下载 Camoufox 浏览器...")
            subprocess.check_call([sys.executable, "-m", "camoufox", "fetch"])
            print("✅ 浏览器下载完成\n")
    except Exception:
        pass

    # 安装 Playwright 浏览器
    try:
        import playwright
        pw_browsers = os.path.join(os.path.dirname(playwright.__file__), "driver", "package", ".local-browsers")
        if not os.path.exists(pw_browsers):
            print("正在安装 Playwright 浏览器...")
            subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
            print("✅ Playwright 浏览器安装完成\n")
    except Exception:
        pass

_ensure_venv()
_ensure_deps()

import time
import signal
import requests as std_requests
from config import SERVER_URL, SERVER_ADMIN_PASSWORD, DEFAULT_COUNT, DEFAULT_DELAY
from tavily_core import create_email, register

# ──────────────────────────────────────────────
# Solver 管理
# ──────────────────────────────────────────────

solver_proc = None

def start_solver():
    global solver_proc
    
    # 清理旧进程
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                if any('api_solver.py' in str(c) for c in cmdline):
                    print(f"清理旧 Solver 进程 (PID: {proc.pid})")
                    proc.kill()
                    time.sleep(1)
            except:
                pass
    except ImportError:
        # 没有 psutil，用 lsof 检查端口
        import subprocess
        try:
            result = subprocess.run(['lsof', '-ti', ':5072'], capture_output=True, text=True)
            if result.stdout.strip():
                pid = result.stdout.strip()
                subprocess.run(['kill', '-9', pid])
                time.sleep(1)
        except:
            pass
    
    # 启动 Solver
    print("启动 Turnstile Solver...")
    
    # 获取 Python 路径
    if os.path.exists('venv'):
        if sys.platform == 'win32':
            python_path = os.path.join('venv', 'Scripts', 'python.exe')
        else:
            python_path = os.path.join('venv', 'bin', 'python3')
    else:
        python_path = sys.executable
    
    solver_proc = subprocess.Popen(
        [python_path, 'api_solver.py', '--browser_type', 'chromium', '--thread', '1'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    # 等待启动
    for i in range(30):
        try:
            r = std_requests.get("http://127.0.0.1:5072/", timeout=1)
            if r.status_code == 200:
                print("✅ Solver 已启动\n")
                return True
        except:
            pass
        time.sleep(1)
        if i % 5 == 0:
            print(f"等待 Solver 启动... ({i}s)")
    
    print("❌ Solver 启动超时")
    return False

def stop_solver():
    global solver_proc
    if solver_proc:
        solver_proc.terminate()
        solver_proc.wait()
        solver_proc = None

def signal_handler(sig, frame):
    print("\n\n正在退出...")
    stop_solver()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# ──────────────────────────────────────────────
# 上传到代理服务器
# ──────────────────────────────────────────────

def upload_key(email, api_key):
    try:
        r = std_requests.post(
            f"{SERVER_URL}/api/keys",
            json={"key": api_key, "email": email},
            headers={"Authorization": f"Bearer {SERVER_ADMIN_PASSWORD}"},
            timeout=15,
        )
        if r.status_code in (200, 201):
            print("✅ 已上传服务器")
            return True
        print(f"⚠️  上传失败 {r.status_code}: {r.text[:100]}")
        return False
    except Exception as e:
        print(f"⚠️  上传失败: {e}")
        return False

# ──────────────────────────────────────────────
# 注册流程
# ──────────────────────────────────────────────

def do_register(count, delay, upload):
    success = 0
    failed = 0

    for i in range(count):
        if i > 0:
            print(f"\n⏳ 等待 {delay} 秒...\n")
            time.sleep(delay)

        print(f"{'='*60}")
        print(f"📧 注册 ({i+1}/{count})")
        print(f"{'='*60}\n")

        try:
            email, password = create_email()
            result = register(email, password)

            if result and result != "SUCCESS_NO_KEY":
                success += 1
                if upload:
                    upload_key(email, result)
            elif result == "SUCCESS_NO_KEY":
                success += 1
            else:
                failed += 1

        except Exception as e:
            print(f"❌ 注册异常: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"✅ 成功: {success}  ❌ 失败: {failed}")
    print(f"{'='*60}\n")

# ──────────────────────────────────────────────
# 交互菜单
# ──────────────────────────────────────────────

def menu_register():
    print(f"\n  注册数量 (默认 {DEFAULT_COUNT}): ", end="")
    count_input = input().strip()
    count = int(count_input) if count_input.isdigit() else DEFAULT_COUNT

    print(f"  间隔秒数 (默认 {DEFAULT_DELAY}): ", end="")
    delay_input = input().strip()
    delay = int(delay_input) if delay_input.isdigit() else DEFAULT_DELAY

    print(f"  上传服务器? [Y/n]: ", end="")
    upload_input = input().strip().lower()
    upload = upload_input not in ("n", "no")

    print(f"\n  数量: {count}  间隔: {delay}s  上传: {'是' if upload else '否'}")

    do_register(count, delay, upload)

def main():
    # 启动 Solver
    if not start_solver():
        print("无法启动 Solver，退出")
        return
    
    try:
        while True:
            print("""
┌──────────────────────────────────────────┐
│         Tavily 全自动注册工具              │
├──────────────────────────────────────────┤
│  注册无需邮件验证，邮箱地址随机生成          │
│  账号/密码/API Key 保存至 accounts.txt    │
├──────────────────────────────────────────┤
│  1. 开始注册                              │
│  0. 退出                                 │
└──────────────────────────────────────────┘
选择: """, end="")
            
            choice = input().strip()
            
            if choice == "1":
                menu_register()
            elif choice == "0":
                break
            else:
                print("无效选择")
    
    finally:
        stop_solver()

if __name__ == "__main__":
    main()
