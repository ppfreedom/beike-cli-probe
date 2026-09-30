#!/usr/bin/env python3
"""第三轮实验：确认预约字段与上报响应的解析契约。

预约工具在第二轮暴露了两个细节：--agent-ucid 必须是数字，house_id_list
是数字数组。这里补一次带 agent_ucid 的完整调用。

上报部分要解决的是另一个问题：CLI 调用 report_user_intent 时会读取服务端
签发的 session_id，但我们不知道它从响应体的哪个位置读。因此让假服务端按
arguments.raw_query 的值返回五种不同形状的响应（VAR1..VAR5），逐个尝试，
每轮之后再用 analyze --current 查看会话 ID 是否已被缓存，并列出 ~/.beike
下实际写出的文件。
"""

import argparse
import json
import os
import subprocess
import time


def run_command(argv, timeout_seconds, log_path):
    started = time.time()
    try:
        completed = subprocess.run(argv, capture_output=True, timeout=timeout_seconds, text=True)
        code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        code = -999
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + "\n[TIMEOUT]"
    if not isinstance(stdout, str):
        stdout = stdout.decode("utf-8", "replace")
    if not isinstance(stderr, str):
        stderr = stderr.decode("utf-8", "replace")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write("$ %s\n" % " ".join(argv))
        handle.write("elapsed: %.2fs exit: %s\n" % (time.time() - started, code))
        handle.write("---- stdout ----\n%s\n---- stderr ----\n%s\n" % (stdout, stderr))
    return code


def list_state_dir(home):
    """列出 CLI 状态目录，确认会话 ID 是否被持久化，以及落在哪个文件。"""
    root = os.path.join(home, ".beike")
    entries = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    content = handle.read()[:2000]
            except OSError as exc:
                content = "<unreadable: %s>" % exc
            entries.append({"path": path, "content": content})
    return entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--capture-dir", default="capture")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    capture_dir = os.path.abspath(args.capture_dir)
    out_root = os.path.join(capture_dir, "protocol", args.label)
    timeline_path = os.path.join(capture_dir, "protocol.jsonl")
    home = os.path.expanduser("~")

    # 第三轮的顺序是刻意的：先做与响应形状无关的字段实验，再做会往
    # ~/.beike 写状态的 VAR 系列，这样状态目录的变化只可能来自 VAR 系列。
    experiments = [
        ("appoint_agent", ["rent", "appoint", "--house-id", "123456", "--house-id", "654321", "--date", "2026-01-02", "--start", "8", "--end", "17", "--agent-ucid", "12345678"]),
        ("appoint_noagent", ["rent", "appoint", "--house-id", "123456", "--date", "2026-01-02"]),
        ("detail_bogus_entity_type", ["buy", "detail", "-c", "北京", "--id", "ID1", "--entity-type", "bogus", "--json"]),
        ("decor_price_bogus_type", ["decor", "price", "-c", "北京", "--area", "91", "--rooms", "3", "--parlors", "2", "--cookrooms", "5", "--toilets", "7", "--house-type", "bogus", "--json"]),
        ("decor_price_numeric_type", ["decor", "price", "-c", "北京", "--area", "91", "--rooms", "3", "--parlors", "2", "--cookrooms", "5", "--toilets", "7", "--house-type", "2", "--json"]),
        ("search_numeric_type", ["buy", "search", "-c", "北京", "-q", "PROBE_QUERY", "--house-type", "2", "--json"]),
        ("report_var1", ["analyze", "--external-session-id", "ESID1", "--context-intent", "CI1", "--raw-query", "VAR1", "--pretty"]),
        ("report_var1_current", ["analyze", "--current", "--pretty"]),
        ("report_var2", ["analyze", "--external-session-id", "ESID1", "--context-intent", "CI1", "--raw-query", "VAR2", "--pretty"]),
        ("report_var2_current", ["analyze", "--current", "--pretty"]),
        ("report_var3", ["analyze", "--external-session-id", "ESID1", "--context-intent", "CI1", "--raw-query", "VAR3", "--pretty"]),
        ("report_var3_current", ["analyze", "--current", "--pretty"]),
        ("report_var4", ["analyze", "--external-session-id", "ESID1", "--context-intent", "CI1", "--raw-query", "VAR4", "--pretty"]),
        ("report_var4_current", ["analyze", "--current", "--pretty"]),
        ("report_var5", ["analyze", "--external-session-id", "ESID1", "--context-intent", "CI1", "--raw-query", "VAR5", "--pretty"]),
        ("report_var5_current", ["analyze", "--current", "--pretty"]),
    ]

    for index, (slug, extra_args) in enumerate(experiments, start=1):
        argv = [args.binary] + extra_args
        slug_full = "%02d-%s" % (index, slug)
        ts_before = time.time()
        code = run_command(argv, args.timeout, os.path.join(out_root, slug_full + ".txt"))
        ts_after = time.time()
        state = list_state_dir(home)
        with open(timeline_path, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "label": args.label,
                        "slug": slug_full,
                        "argv": argv,
                        "exit": code,
                        "ts_before": ts_before,
                        "ts_after": ts_after,
                        "beike_state": state,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        print("[%s] %s -> exit %s, state files: %d" % (args.label, slug_full, code, len(state)), flush=True)


if __name__ == "__main__":
    main()
