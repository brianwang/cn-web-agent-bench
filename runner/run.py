#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
China Web Agent Bench runner — 最小 web agent 执行器。
LLM 根据页面快照决定下一步操作，Playwright 执行，记录结果。

用法:
  python3 runner/run.py                 # 跑全部
  python3 runner/run.py --task T09      # 跑单个
  python3 runner/run.py --task T09 --headed
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TASKS = os.path.join(ROOT, "tasks")
RESULTS = os.path.join(ROOT, "results")
CLAUDE = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
CDP = os.environ.get("CDP_URL", "http://127.0.0.1:9335")

MAX_STEPS = 8
BLOCKERS = ("login", "slider", "captcha", "anti-bot", "js-render", "timeout", "other")


def detect_blocker(page, text):
    """结构化阻塞检测：看 URL 主机 + 页面主体，而不是关键字散匹配"""
    from urllib.parse import urlparse
    host = (urlparse(page.url).hostname or "").lower()
    if "login" in host or "passport" in host or "signin" in host:
        return "login"
    head = text[:600]
    login_signals = ("扫码登录", "密码登录", "验证码登录", "登录/注册", "打开知乎App")
    if sum(1 for s in login_signals if s in head) >= 2:
        return "login"
    if "非法访问" in text or "访问受限" in text or "异常流量" in text or "人机验证" in text:
        return "anti-bot"
    if any(k in text for k in ("拖动滑块", "滑动验证", "滑块验证", "请完成验证", "请向右滑块")):
        return "slider"
    if "验证码" in text and ("获取验证码" in text or "图形验证" in text):
        return "captcha"
    return None


def normalize_reason(reason):
    r = (reason or "").lower()
    for b in BLOCKERS:
        if b in r:
            return b
    if "登录" in r:
        return "login"
    if "滑块" in r or "滑动" in r:
        return "slider"
    if "验证码" in r:
        return "captcha"
    if "非法" in r or "风控" in r or "反爬" in r:
        return "anti-bot"
    if "超时" in r:
        return "timeout"
    return "other"


def load_task(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def ask_llm(task, page_text, history):
    """让 LLM 决定下一步操作，返回 JSON"""
    prompt = (
        "你是网页自动化 agent。根据当前页面内容和任务，决定下一步操作。\n"
        f"任务：{task['name']}\n"
        f"成功判据：{task.get('success_criteria')}\n"
        f"已执行：{history}\n"
        "当前页面内容（截断）：\n"
        f"{page_text[:2500]}\n\n"
        "只输出 JSON，格式：\n"
        '{"action":"goto|fill|click|scroll|done|give_up","selector":"CSS选择器或空","value":"输入值或空","reason":"一句理由"}\n'
        "规则：如果已经能看到成功判据要求的数据，action=done，value 里放提取到的数据；"
        "如果被拦住且无法继续，action=give_up，reason 必须用这几个词之一开头："
        "login（登录墙）/ slider（滑块）/ captcha（验证码）/ anti-bot（反爬检测）/ js-render（渲染失败）/ timeout（超时）。"
        "注意：页面上出现「登录」按钮不等于登录墙——只有当整个页面主体都是登录表单、无法进行任何查询操作时才算 login。"
    )
    try:
        r = subprocess.run([CLAUDE, "-p", prompt, "--output-format", "text"],
                           capture_output=True, text=True, timeout=120, cwd="/tmp")
        txt = (r.stdout or "").strip()
        m = re.search(r"\{.*\}", txt, re.S)
        if m:
            return json.loads(m.group(0))
    except Exception as e:  # noqa: BLE001
        return {"action": "give_up", "reason": f"llm-error: {e}"}
    return {"action": "give_up", "reason": "llm-no-json"}


def run_task(task, headed=False):
    from playwright.sync_api import sync_playwright
    res = {"id": task["id"], "name": task["name"], "platform": task["platform"],
           "success": False, "duration_ms": 0, "blocked_by": "none",
           "extracted": None, "steps": [], "error": None}
    t0 = time.time()
    p = sync_playwright().start()
    browser = None
    try:
        if headed:
            browser = p.chromium.launch(headless=False, args=["--no-sandbox"],
                                        executable_path="/usr/bin/google-chrome")
        else:
            # 优先复用 9335 已登录 Chrome
            try:
                browser = p.chromium.connect_over_cdp(CDP)
            except Exception:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox"],
                                            executable_path="/usr/bin/google-chrome")
        ctx = browser.contexts[0] if hasattr(browser, "contexts") and browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.goto(task["url"], timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        login_streak = 0
        for step in range(MAX_STEPS):
            text = page.locator("body").inner_text()
            blk = detect_blocker(page, text)
            if blk in ("slider", "captcha", "anti-bot"):
                res["blocked_by"] = blk
                break
            if blk == "login":
                login_streak += 1
                if login_streak >= 2:      # 连续两步都是登录页才算真阻塞
                    res["blocked_by"] = "login"
                    break
            else:
                login_streak = 0
            act = ask_llm(task, text, res["steps"])
            res["steps"].append(act)
            a = act.get("action")
            if a == "done":
                res["success"] = True
                res["extracted"] = act.get("value")
                res["blocked_by"] = "none"
                break
            if a == "give_up":
                res["blocked_by"] = normalize_reason(act.get("reason"))
                break
            try:
                if a == "goto" and act.get("value"):
                    page.goto(act["value"], timeout=30000, wait_until="domcontentloaded")
                elif a == "fill" and act.get("selector"):
                    page.fill(act["selector"], act.get("value", ""), timeout=8000)
                elif a == "click" and act.get("selector"):
                    page.click(act["selector"], timeout=8000)
                elif a == "scroll":
                    page.mouse.wheel(0, 800)
                page.wait_for_timeout(2000)
            except Exception as e:  # noqa: BLE001
                res["steps"][-1]["error"] = repr(e)[:100]
    except Exception as e:  # noqa: BLE001
        res["error"] = repr(e)[:200]
    finally:
        res["duration_ms"] = int((time.time() - t0) * 1000)
        try:
            p.stop()
        except Exception:
            pass
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--report")
    ap.add_argument("--repeat", type=int, default=1, help="重复轮数")
    args = ap.parse_args()

    files = sorted(f for f in os.listdir(TASKS) if f.endswith(".yaml"))
    if args.task:
        files = [f for f in files if f.startswith(args.task)]

    out = []
    for rnd in range(args.repeat):
        if args.repeat > 1:
            print(f"\n===== 第 {rnd+1}/{args.repeat} 轮 =====", flush=True)
        for f in files:
            task = load_task(os.path.join(TASKS, f))
            print(f"▶ {task['id']} {task['name']}", flush=True)
            res = run_task(task, args.headed)
            res["round"] = rnd + 1
            out.append(res)
            print(f"  {'✅' if res['success'] else '❌'} {res['duration_ms']}ms "
                  f"blocked={res['blocked_by']} extracted={str(res['extracted'])[:60]}", flush=True)

    os.makedirs(RESULTS, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(RESULTS, f"run-{ts}.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    ok = sum(1 for r in out if r["success"])
    print(f"\n结果 {ok}/{len(out)} 成功 → {path}")


if __name__ == "__main__":
    main()
