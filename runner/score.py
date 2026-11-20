#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总 results/*.json，输出 markdown 报告。
用法: python3 runner/score.py [results/run-xxx.json]
"""
import json
import os
import sys
import glob
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(ROOT, "results")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        files = sorted(glob.glob(os.path.join(RESULTS, "run-*.json")))
        if not files:
            print("没有结果文件"); return
        path = files[-1]

    data = load(path)
    total = len(data)
    ok = sum(1 for r in data if r["success"])
    avg = sum(r["duration_ms"] for r in data) / total / 1000 if total else 0
    blockers = collections.Counter(r["blocked_by"] for r in data)

    # 按任务聚合（多轮）
    by_task = collections.defaultdict(list)
    for r in data:
        by_task[r["id"]].append(r)
    rounds = max((r.get("round", 1) for r in data), default=1)

    print(f"# China Web Agent Bench 报告\n")
    print(f"结果文件：`{os.path.basename(path)}` · 轮数 {rounds}\n")
    print(f"- 总运行：{total} 次")
    print(f"- 成功：{ok}（{ok/total*100:.0f}%）")
    print(f"- 平均耗时：{avg:.1f}s\n")

    print("## 阻塞类型分布\n")
    for b, n in blockers.most_common():
        print(f"- `{b}`：{n}")
    print()

    print("## 逐任务（多轮成功率）\n")
    print("| ID | 任务 | 平台 | 成功/轮数 | 平均耗时 | 阻塞分布 |")
    print("|---|---|---|---|---|---|")
    for tid in sorted(by_task):
        rs = by_task[tid]
        s = sum(1 for r in rs if r["success"])
        a = sum(r["duration_ms"] for r in rs) / len(rs) / 1000
        bl = collections.Counter(r["blocked_by"] for r in rs if not r["success"])
        bltxt = ", ".join(f"{k}×{v}" for k, v in bl.most_common()) or "—"
        print(f"| {tid} | {rs[0]['name'][:18]} | {rs[0]['platform']} | {s}/{len(rs)} | "
              f"{a:.1f}s | {bltxt} |")


if __name__ == "__main__":
    main()
