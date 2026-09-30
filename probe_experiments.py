#!/usr/bin/env python3
"""第二轮定向实验：逐个确认第一轮暴露出的不确定项。

第一轮动态发现已经证明请求键与命令行参数基本一一对应，剩下的是若干
语义映射问题：house-type 的枚举值如何转成服务端取值、entity_ids 多个
值时如何拼接、entity_material 的 fields 是否随实体类型变化、city 是否
被客户端规范化、以及 analyze 上报时 session_id 从哪里来。本脚本用显式
命令列表逐条验证这些问题，不再依赖 --help 解析。
"""

import argparse
import json
import os
import subprocess
import sys
import time

# 完整实验集：每条实验都刻意只改一个变量，便于把差异归因到具体参数。
FULL_SUITE = [
    ("search_type_second", ["buy", "search", "-c", "北京", "-q", "PROBE_QUERY", "--house-type", "second", "--json"]),
    ("search_type_new", ["buy", "search", "-c", "北京", "-q", "PROBE_QUERY", "--house-type", "new", "--json"]),
    ("search_type_all", ["buy", "search", "-c", "北京", "-q", "PROBE_QUERY", "--house-type", "all", "--json"]),
    ("search_type_default", ["buy", "search", "-c", "北京", "-q", "PROBE_QUERY", "--json"]),
    ("search_type_invalid", ["buy", "search", "-c", "北京", "-q", "PROBE_QUERY", "--house-type", "bogus", "--json"]),
    ("search_nojson", ["buy", "search", "-c", "北京", "-q", "PROBE_QUERY", "--house-type", "second"]),
    ("search_cityraw", ["buy", "search", "-c", "PROBECITY", "-q", "PROBE_QUERY", "--house-type", "second", "--json"]),
    ("decor_price_old", ["decor", "price", "-c", "北京", "--area", "91", "--rooms", "3", "--parlors", "2", "--cookrooms", "5", "--toilets", "7", "--house-type", "old", "--json"]),
    ("decor_price_new", ["decor", "price", "-c", "北京", "--area", "92", "--rooms", "4", "--parlors", "3", "--cookrooms", "6", "--toilets", "8", "--house-type", "new", "--json"]),
    ("decor_price_default", ["decor", "price", "-c", "北京", "--area", "91.5", "--rooms", "3", "--parlors", "2", "--cookrooms", "5", "--toilets", "7", "--json"]),
    ("buy_detail_multi", ["buy", "detail", "-c", "北京", "--id", "ID1", "--id", "ID2", "--json"]),
    ("buy_detail_default_type", ["buy", "detail", "-c", "北京", "--id", "ID1", "--json"]),
    ("buy_detail_resblock", ["buy", "detail", "-c", "北京", "--id", "RB1", "--entity-type", "resblock", "--json"]),
    ("buy_material_default", ["buy", "material", "-c", "北京", "--id", "ID1", "--json"]),
    ("buy_material_newhouse", ["buy", "material", "-c", "北京", "--id", "ID1", "--entity-type", "newhouse", "--json"]),
    ("buy_material_multi", ["buy", "material", "-c", "北京", "--id", "ID1", "--id", "ID2", "--json"]),
    ("rent_material_default", ["rent", "material", "-c", "北京", "--id", "ID1", "--json"]),
    ("rent_material_resblock", ["rent", "material", "-c", "北京", "--id", "ID1", "--entity-type", "resblock", "--json"]),
    ("rent_detail_default", ["rent", "detail", "-c", "北京", "--id", "ID1", "--json"]),
    ("rent_agent_detail_multi", ["rent", "agent-detail", "-c", "北京", "--id", "ID1", "--id", "ID2", "--json"]),
    ("rent_appoint_ok", ["rent", "appoint", "--house-id", "123456", "--house-id", "654321", "--date", "2026-01-02", "--start", "9", "--end", "18", "--agent-ucid", "AU1"]),
    ("rent_appoint_defaults", ["rent", "appoint", "--house-id", "123456", "--date", "2026-01-02", "--pretty"]),
    ("buy_rank_house", ["buy", "rank", "-c", "北京", "--rank-type", "house", "--json"]),
    ("buy_rank_default", ["buy", "rank", "-c", "北京", "--json"]),
    ("buy_contact_biz_default", ["buy", "contact", "-c", "北京", "--agent-ucid", "AU1", "--pretty"]),
    ("buy_contact_biz_new", ["buy", "contact", "-c", "北京", "--biz", "新房", "--agent-ucid", "AU1", "--pretty"]),
    ("buy_contact_biz_second", ["buy", "contact", "-c", "北京", "--biz", "二手房", "--agent-ucid", "AU1", "--pretty"]),
    ("sell_list_nocity", ["sell", "list", "--json"]),
    ("sell_dynamic_nocity", ["sell", "dynamic", "--house-code", "HC1", "--json"]),
    ("buy_market_default", ["buy", "market", "-c", "北京", "--json"]),
    ("buy_resolve_cityraw", ["buy", "resolve", "-c", "PROBECITY", "-q", "PROBE_QUERY", "--json"]),
    ("policy_cityraw", ["policy", "search", "-c", "PROBECITY", "-q", "PROBE_QUERY", "--json"]),
    ("map_geo_cityraw", ["map", "geo", "--address", "ADDR1", "-c", "PROBECITY", "--json"]),
    ("analyze_current", ["analyze", "--current", "--pretty"]),
    ("analyze_minimal", ["analyze", "--external-session-id", "ESID1", "--context-intent", "CI1", "--pretty"]),
    ("analyze_envonly", ["analyze", "--context-intent", "CI1", "--pretty"]),
]

# 精简集：只保留能体现版本差异的实验，用于和 0.2.22 对比。
DIFF_SUITE = [
    ("search_type_second", ["buy", "search", "-c", "北京", "-q", "PROBE_QUERY", "--house-type", "second", "--json"]),
    ("search_type_new", ["buy", "search", "-c", "北京", "-q", "PROBE_QUERY", "--house-type", "new", "--json"]),
    ("buy_material_default", ["buy", "material", "-c", "北京", "--id", "ID1", "--json"]),
    ("buy_material_newhouse", ["buy", "material", "-c", "北京", "--id", "ID1", "--entity-type", "newhouse", "--json"]),
    ("rent_material_default", ["rent", "material", "-c", "北京", "--id", "ID1", "--json"]),
    ("rent_detail_default", ["rent", "detail", "-c", "北京", "--id", "ID1", "--json"]),
    ("decor_price_old", ["decor", "price", "-c", "北京", "--area", "91", "--rooms", "3", "--parlors", "2", "--cookrooms", "5", "--toilets", "7", "--house-type", "old", "--json"]),
    ("rent_appoint_ok", ["rent", "appoint", "--house-id", "123456", "--date", "2026-01-02", "--start", "9", "--end", "18", "--agent-ucid", "AU1"]),
    ("analyze_minimal", ["analyze", "--external-session-id", "ESID1", "--context-intent", "CI1", "--pretty"]),
]


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--capture-dir", default="capture")
    parser.add_argument("--suite", choices=["full", "diff"], default="full")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    suite = FULL_SUITE if args.suite == "full" else DIFF_SUITE
    capture_dir = os.path.abspath(args.capture_dir)
    out_root = os.path.join(capture_dir, "experiments", args.label)
    os.makedirs(out_root, exist_ok=True)

    # ts_before/ts_after 用于把服务端抓到的请求与具体实验对齐，因为请求体
    # 里并不包含本地方便识别的痕迹。
    timeline_path = os.path.join(capture_dir, "experiments.jsonl")
    for index, (slug, extra_args) in enumerate(suite, start=1):
        argv = [args.binary] + extra_args
        slug_full = "%02d-%s" % (index, slug)
        log_path = os.path.join(out_root, slug_full + ".txt")
        ts_before = time.time()
        code = run_command(argv, args.timeout, log_path)
        ts_after = time.time()
        with open(timeline_path, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "label": args.label,
                        "suite": args.suite,
                        "slug": slug_full,
                        "argv": argv,
                        "exit": code,
                        "ts_before": ts_before,
                        "ts_after": ts_after,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        print("[%s/%s] %s -> exit %s" % (args.label, args.suite, slug_full, code), flush=True)


if __name__ == "__main__":
    main()
