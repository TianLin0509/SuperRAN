"""用真正的 MCP 客户端连一次服务端，验证工具注册与调用。

直接运行：python tests/test_mcp_server.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import get_default_environment, stdio_client  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def _payload(result) -> dict:
    if getattr(result, "structuredContent", None):
        sc = result.structuredContent
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    for c in result.content:
        if getattr(c, "type", "") == "text":
            try:
                return json.loads(c.text)
            except json.JSONDecodeError:
                return {"_text": c.text}
    return {}


async def main() -> None:
    # Windows 上必须继承默认环境（SystemRoot 等），只传自定义 env 会让子进程挂死
    env = {**get_default_environment(), "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"}
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "superwireless.server"],
        env=env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("=" * 68 + "\n1  连接与工具清单\n" + "=" * 68)

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            for t in tools.tools:
                first = (t.description or "").strip().splitlines()[0]
                print(f"  {t.name:<22} {first}")
            expected = {
                "sw_capabilities", "sw_list_presets", "sw_list_scenes", "sw_plan",
                "sw_revise", "sw_generate", "sw_deliver",
                "sw_describe_dataset", "sw_list_datasets",
            }
            check(expected.issubset(set(names)), f"9 个工具全部注册（实际 {len(names)} 个）")

            print("\n" + "=" * 68 + "\n2  sw_capabilities\n" + "=" * 68)
            caps = _payload(await session.call_tool("sw_capabilities", {}))
            engines = {e["name"]: e for e in caps.get("engines", [])}
            for e in engines.values():
                print(f"  {e['name']:<16} {'可用' if e['available'] else '不可用'}")
            check(engines.get("internal_sim", {}).get("available") is True, "internal_sim 报告可用")
            check(engines.get("sionna_rt", {}).get("available") is True, "sionna_rt 报告可用")
            # 引擎不可用时必须如实说明缺什么，不能假装能跑
            qr = engines.get("quadriga_real", {})
            check(qr.get("available") is False and bool(qr.get("missing")), "不可用引擎列出缺失项")

            scenes = _payload(await session.call_tool("sw_list_scenes", {}))
            print(f"\n  射线追踪场景 {len(scenes['scenes'])} 个 "
                  f"(内置 {sum(1 for s in scenes['scenes'] if s['builtin'])} / "
                  f"真实OSM {sum(1 for s in scenes['scenes'] if not s['builtin'])})")
            check(scenes["ray_tracing_available"] is True, "射线追踪可用")
            check(len(scenes["scenes"]) >= 10, "场景清单完整")

            print("\n" + "=" * 68 + "\n3  sw_plan —— 交互提案\n" + "=" * 68)
            prop = _payload(
                await session.call_tool(
                    "sw_plan",
                    {
                        "intent": "帮我验证一个 CSI 压缩的想法，先弄一批单小区 64T4R 的信道数据",
                        "max_questions": 5,
                    },
                )
            )
            rq = prop["round_questions"]
            print(f"  draft_id   {prop['draft_id']}")
            print(f"  任务       {prop['task_label']}")
            print(f"  场景       {prop['preset_label']}")
            print(f"  预估体积   {prop['estimated']['size_mb']} MB")
            print(f"\n  第 {prop['round']} 轮 · {prop['round_focus']}：{prop['round_rationale']}")
            for q in rq:
                print(f"    · {q['question']}  [{q['layer']}]")
                for i, o in enumerate(q["options"], 1):
                    star = "  ← 推荐" if o.get("recommended") else ""
                    print(f"        {i}) {o['label']}{star}")
            print(f"\n  还能调（只给名字）：{'、'.join(prop['also_configurable'][:11])}…")
            check(2 <= len(rq) <= 4, f"一轮 2~4 问（实际 {len(rq)}）")
            check(all(3 <= len(q["options"]) <= 4 for q in rq), "每题 3~4 个选项")
            check(all(q.get("why") for q in rq), "每题都带 why")
            check(len(prop["also_configurable"]) >= 10, "给出可配项关键词列表")
            check(prop["ready_to_go"] and prop["can_generate_now"], "未表态也可直接生成")

            draft_id = prop["draft_id"]

            print("\n" + "=" * 68 + "\n4  sw_revise —— 用户表态\n" + "=" * 68)
            rev = _payload(
                await session.call_tool(
                    "sw_revise",
                    {
                        "draft_id": draft_id,
                        "overrides": {
                            "bs_antenna": "4T4R",
                            "ue_speed_kmh": 60.0,
                            "num_samples": 6,
                            "bandwidth_hz": 20000000.0,
                            "num_ues": 3,
                        },
                    },
                )
            )
            print(f"  改动：")
            for c in rev["changes"]:
                print(f"    {c}")
            check(len(rev["changes"]) >= 4, "差分修正生效")

            print("\n" + "=" * 68 + "\n5  sw_generate\n" + "=" * 68)
            gen = _payload(await session.call_tool("sw_generate", {"draft_id": draft_id}))
            check(gen.get("status") == "ok", "生成成功")
            s = gen["summary"]
            print(f"  dataset_id {gen['dataset_id']}")
            print(f"  形状       {s['shape']}")
            print(f"  耗时       {s['elapsed_s']}s")
            print(f"  SINR       中位数 {s['sinr_dB']['median']} dB")
            print(f"\n  替用户做的决定（会转述给用户）：")
            for a in gen["auto_decided"][:6]:
                print(f"    · {a}")
            check(bool(gen["auto_decided"]), "列出了自动决定的项")
            check(s["shape"]["BS_ant"] == 4, "用户指定的 4T4R 生效")

            ds_id = gen["dataset_id"]

            print("\n" + "=" * 68 + "\n6  sw_deliver —— 取货代码\n" + "=" * 68)
            d1 = _payload(await session.call_tool("sw_deliver", {"dataset_id": ds_id, "want": "信道"}))
            print(f"  第一次点单：{d1['measurements']}")
            check(d1["measurements"] == ["channel"], "只要信道时只给信道")

            d2 = _payload(
                await session.call_tool(
                    "sw_deliver", {"dataset_id": ds_id, "want": "我还想看 PMI 和 SRS RSRP"}
                )
            )
            print(f"  改主意后：{d2['measurements']}")
            check("pmi" in d2["measurements"] and "srs" in d2["measurements"], "自然语言点单生效")
            check(len(d2["code"]) > len(d1["code"]), "取货代码随点单变长")
            print("\n  取货代码片段：")
            for line in d2["code"].splitlines()[:14]:
                print("    " + line)

            print("\n" + "=" * 68 + "\n7  体检拦截（走 MCP 全链路）\n" + "=" * 68)
            blocked = _payload(
                await session.call_tool(
                    "sw_generate",
                    {
                        "intent": "做一个基于到达角的波束搜索算法",
                        "overrides": {"channel_model": "TDL-C"},
                    },
                )
            )
            print(f"  status: {blocked.get('status')}")
            for i in blocked.get("issues", []):
                print(f"  [{i['severity']}] {i['message'][:88]}…")
                print(f"           建议：{i['suggestion']}")
            check(blocked.get("status") == "blocked", "波束任务 + TDL 被 MCP 拦截")

            print("\n" + "=" * 68 + "\n8  sw_describe_dataset / sw_list_datasets\n" + "=" * 68)
            desc = _payload(await session.call_tool("sw_describe_dataset", {"dataset_id": ds_id}))
            print(f"  形状 {desc['shape']}  含角度 {desc['has_angles']}")
            print(f"  可用测量量 {desc['available_measurements']}")
            check(bool(desc.get("available_measurements")), "描述包含可用测量量清单")

            lst = _payload(await session.call_tool("sw_list_datasets", {}))
            print(f"  本机已有 {len(lst['datasets'])} 个数据集")
            check(len(lst["datasets"]) >= 1, "数据集列表可用")


if __name__ == "__main__":
    asyncio.run(main())
    print("\n" + "=" * 68)
    if FAILED:
        print(f"FAILED {len(FAILED)} 项：")
        for f in FAILED:
            print("  - " + f)
        sys.exit(1)
    print("MCP 全链路通过。")
