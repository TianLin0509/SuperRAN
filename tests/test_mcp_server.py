"""用真正的 MCP 客户端连一次服务端，验证工具注册与调用。

直接运行：python tests/test_mcp_server.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
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
    env = {
        **get_default_environment(),
        **{key: value for key, value in os.environ.items() if key.startswith("SUPERRAN_")},
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "superran.server"],
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
                "sr_capabilities", "sr_list_presets", "sr_list_scenes", "sr_plan",
                "sr_revise", "sr_generate", "sr_deliver",
                "sr_describe_dataset", "sr_list_datasets", "sr_mcs_info", "sr_bler_curve",
                "sr_tdd_mcs", "sr_compare_system_results",
            }
            check(expected.issubset(set(names)), f"12 个核心工具全部注册（实际 {len(names)} 个）")
            sim_tool = next((t for t in tools.tools if t.name == "sr_system_sim"), None)
            if sim_tool is None:
                sim_schema = {}
            else:
                try:
                    sim_schema = sim_tool.inputSchema or {}
                except AttributeError:
                    # MCP 1.x/2.x 的 Tool 模型分别暴露 camelCase / snake_case。
                    sim_schema = sim_tool.input_schema or {}
            sim_props = sim_schema.get("properties", {})
            mu_tuning = {
                "mu_corr_threshold", "mu_olla_step_up_db", "mu_olla_step_down_db"}
            check(sim_tool is not None and mu_tuning.issubset(sim_props),
                  "系统仿真 MCP 公开 MU 相关性门限与 MU-OLLA 独立步长")
            discrete = {"seed", "num_replications", "pf_window_tti",
                        "file_bytes", "small_file_bytes"}
            check(all(sim_props.get(k, {}).get("type") == "integer" for k in discrete),
                  "MCP schema 把 seed/重复次数/PF 窗口/字节数声明为整数")
            traffic_kpi_args = {
                "packet_size_cdf", "interarrival_cdf", "traffic_profiles",
                "target_prb_utilization", "load_calibration_axis",
                "load_calibration_formal_refinements",
                "replication_workers", "algorithm_label", "tti_trace_mode",
                "tti_trace_max_points", "kpi_focus", "kpi_intent"}
            check(traffic_kpi_args.issubset(sim_props),
                  "MCP schema 公开 CDF、多 profile、目标 PRB 校准、算法标签、TTI trace 与 Agent KPI 编排")
            check("harq_combining" in sim_props,
                  "MCP schema 公开一次重传的 IR/CC 合并选择")
            p0_args = {
                "frequency_selective", "max_layers_per_rbg",
                "max_logical_prb_per_tti", "srs_resource_allocation",
                "srs_pci_mod3"}
            check(p0_args.issubset(sim_props),
                  "MCP schema 公开 SRS 分配、逐 RBG 频选与 layer-PRB 资源预算")
            worker_schema = json.dumps(
                sim_props.get("replication_workers", {}), ensure_ascii=False)
            check("integer" in worker_schema and "string" in worker_schema,
                  "重复实验 workers 支持显式整数与 auto，而不是无效字符串旋钮")
            compare_tool = next(
                (tool for tool in tools.tools if tool.name == "sr_compare_system_results"),
                None,
            )
            if compare_tool is None:
                compare_props = {}
            else:
                compare_schema = getattr(
                    compare_tool, "inputSchema", None) or compare_tool.input_schema
                compare_props = compare_schema.get("properties", {})
            check(compare_tool is not None
                  and {"result_ids", "baseline_result_id", "primary_kpi"}
                  .issubset(compare_props),
                  "多算法 KPI 工具公开结果句柄、基线和预注册主 KPI")

            print("\n" + "=" * 68 + "\n1.5  表驱动 BLER 查询\n" + "=" * 68)
            curve = _payload(await session.call_tool(
                "sr_bler_curve",
                {"mcs": 15, "tx_mode": "newtx", "sinr_db_list": [14.0, 14.05]},
            ))
            check(curve.get("source_id") == "preset_20b_256qam", "MCP 返回预置曲线来源")
            check("system_retx_model" in curve
                  and "extra_code_rate_rows_status" in curve,
                  "MCP 曲线返回系统重传口径与未保留额外码率行状态")
            check(abs(curve.get("required_sinr_db", 0.0) - 14.0421) < 1e-3,
                  "MCP 返回 MCS15 NewTx 10% BLER 门限")
            queried = curve.get("query", {}).get("bler", [])
            check(len(queried) == 2 and abs(queried[0] - 0.132) < 1e-12 and
                  abs(queried[1] - 0.0949) < 1e-12,
                  "MCP 在原始 SINR 网格点逐值返回 BLER")

            mcs3 = _payload(await session.call_tool(
                "sr_mcs_info", {"table": 3, "show_bler_anchors": True}
            ))
            check(len(mcs3.get("mcs_table", [])) == 28 and
                  mcs3.get("verify", {}).get("consistent") is True,
                  "MCP 表 3 覆盖 28 档且完整性自检通过")
            cqi_rows = mcs3.get("cqi_table", [])
            check([row.get("requested_mcs") for row in cqi_rows]
                  == [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28]
                  and cqi_rows[-1].get("mcs") == 27
                  and cqi_rows[-1].get("mcs_clipped_to_profile") is True,
                  "MCP 表 3 显式返回256QAM CQI离散表与MCS28曲线缺口")
            mcs_default = _payload(await session.call_tool("sr_mcs_info", {}))
            check(mcs_default.get("table") == 3
                  and mcs_default.get("source") == mcs3.get("source"),
                  "sr_mcs_info默认查预置256QAM表3")

            print("\n" + "=" * 68 + "\n2  sr_capabilities\n" + "=" * 68)
            caps = _payload(await session.call_tool("sr_capabilities", {}))
            engines = {e["name"]: e for e in caps.get("engines", [])}
            for e in engines.values():
                print(f"  {e['name']:<16} {'可用' if e['available'] else '不可用'}")
            check(engines.get("internal_sim", {}).get("available") is True, "internal_sim 报告可用")
            # sionna-rt 是可选依赖，没装很正常。这里只要求"报告与事实一致"——
            # 装了就说可用，没装就必须说出缺什么。硬断言"可用"会让没装射线追踪的
            # 新用户按安装文档跑完看到 FAILED，误以为装坏了。
            rt_e = engines.get("sionna_rt", {})
            check(
                rt_e.get("available") is True
                or (rt_e.get("available") is False and bool(rt_e.get("missing"))),
                "sionna_rt 可用性报告与事实一致"
                + ("（已装）" if rt_e.get("available") else "（未装，已列出缺失项）"),
            )
            # 引擎不可用时必须如实说明缺什么，不能假装能跑。
            # 对**每个**引擎都查，而不是挑一个恒不可用的当样板——
            # QuaDRiGa 路线已删除，样板引擎不复存在。
            check(
                all(
                    e.get("available") is True
                    or (e.get("available") is False and bool(e.get("missing")))
                    for e in engines.values()
                ),
                "不可用引擎都列出了缺失项",
            )
            check(all(e.get("detail") for e in engines.values()), "每个引擎都有可读说明")

            scenes = _payload(await session.call_tool("sr_list_scenes", {}))
            print(f"\n  射线追踪场景 {len(scenes['scenes'])} 个 "
                  f"(内置 {sum(1 for s in scenes['scenes'] if s['builtin'])} / "
                  f"真实OSM {sum(1 for s in scenes['scenes'] if not s['builtin'])})")
            # 场景清单与射线追踪能不能跑是两件事：清单来自 configs/scenes 的静态
            # 元数据，没装 sionna-rt 也该列得出来，只是 ray_tracing_available 为 false。
            check(
                scenes["ray_tracing_available"] == rt_e.get("available"),
                "场景清单里的射线追踪可用性与引擎探测一致",
            )
            check(len(scenes["scenes"]) >= 4,
                  "四个内置场景始终可发现；本地资产不从外部源码树静默借用")

            print("\n" + "=" * 68 + "\n3  sr_plan —— 交互提案\n" + "=" * 68)
            prop = _payload(
                await session.call_tool(
                    "sr_plan",
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

            print("\n" + "=" * 68 + "\n4  sr_revise —— 用户表态\n" + "=" * 68)
            rev = _payload(
                await session.call_tool(
                    "sr_revise",
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
            print("  改动：")
            for c in rev["changes"]:
                print(f"    {c}")
            check(len(rev["changes"]) >= 4, "差分修正生效")

            print("\n" + "=" * 68 + "\n5  sr_generate\n" + "=" * 68)
            gen = _payload(await session.call_tool("sr_generate", {"draft_id": draft_id}))
            check(gen.get("status") == "ok", "生成成功")
            s = gen["summary"]
            print(f"  dataset_id {gen['dataset_id']}")
            print(f"  形状       {s['shape']}")
            print(f"  耗时       {s['elapsed_s']}s")
            print(f"  SINR       中位数 {s['sinr_dB']['median']} dB")
            print("\n  替用户做的决定（会转述给用户）：")
            for a in gen["auto_decided"][:6]:
                print(f"    · {a}")
            check(bool(gen["auto_decided"]), "列出了自动决定的项")
            check(s["shape"]["BS_ant"] == 4, "用户指定的 4T4R 生效")

            ds_id = gen["dataset_id"]

            print("\n" + "=" * 68 + "\n5.5  sr_tdd_mcs —— TDD CQI/BF Gain/OLLA\n" + "=" * 68)
            tdd = _payload(await session.call_tool(
                "sr_tdd_mcs",
                {
                    "dataset_id": ds_id,
                    "cqi": 9,
                    "olla_mcs_offset": -0.2,
                    "feedback_ack": False,
                },
            ))
            check(tdd.get("scheduled") is True and tdd.get("rank", 0) >= 1,
                  "真实数据上完成 TDD MCS 决策并保留 rank")
            check(tdd.get("cqi_initial_mcs") == 18 and
                  tdd.get("reported_cqi_codepoint") == 10 and
                  abs(tdd.get("cqi_mcs_sinr_db", 0.0) - 16.8323) < 1e-3,
                  "MCP 将256QAM表行9映射到MCS18并返回上报CQI10")
            check(len(tdd.get("pmi_stream_sinr_db", [])) == tdd.get("rank") and
                  len(tdd.get("svd_stream_sinr_db", [])) == tdd.get("rank") and
                  len(tdd.get("bf_gain_per_stream_db", [])) == tdd.get("rank"),
                  "MCP 返回逐流 PMI/SVD SINR 与 BF Gain 审计量")
            check(tdd.get("bf_gain_csi_view") == "gnb_precoding_csi"
                  and tdd.get("bf_gain_enters_mcs") is True
                  and tdd.get("true_channel_bf_audit_enters_mcs") is False,
                  "MCP 明确 MCS 只消费 gNB CSI 上的 BF Gain，h_true 差值仅作审计")
            check(tdd.get("power_constraint") == "nebf"
                  and tdd.get("physical_tx_sinr_label") == "SINR_NEBF",
                  "TDD 前门默认使用 NEBF，并把物理 TX SINR 明确命名")
            check(abs(tdd.get("sinr_svd_gnb_db", 0.0)
                      - tdd.get("sinr_pmi_gnb_db", 0.0)
                      - tdd.get("bf_gain_user_db", 0.0)) < 2e-4,
                  "SINR_NEBF/SVD 与 SINR_PMI 的 gNB 视角差闭合到 BF Gain")
            check(abs(tdd.get("user_sinr_db", 0.0) -
                      (tdd.get("cqi_mcs_sinr_db", 0.0) + tdd.get("bf_gain_user_db", 0.0))) < 2e-4,
                  "历史 user_sinr 字段等于 SINR_AMC_PRED，不冒充接收真值")
            check(tdd.get("final_mcs") == max(
                      0, min(27, int((tdd.get("mcs_after_bf", 0) - 0.2) // 1))),
                  "MCP 最终 MCS 遵循加 OLLA、floor、钳位顺序")
            check(tdd.get("receiver") == "classic MMSE" and
                  "only precoding weight changes" in tdd.get("fairness_contract", ""),
                  "MCP 结果钉住 MMSE 与同工况预编码对照")
            check(abs(tdd.get("olla_next_offset_mcs", 0.0) + 0.29) < 1e-12,
                  "NACK 只更新下一时刻 OLLA 状态")
            check(tdd.get("actual_bler_available") is True
                  and isinstance(tdd.get("actual_receive_sinr_db"), (int, float))
                  and 0.0 <= tdd.get("final_mcs_newtx_bler", -1.0) <= 1.0
                  and "h_true" in tdd.get("bler_sinr_source", ""),
                  "最终 BLER 只使用同一物理 NEBF 权在 h_true 上的接收 SINR")
            check("predicted_final_mcs_newtx_bler" not in tdd
                  and tdd.get("sinr_views", {}).get("amc_prediction", {}).get("name")
                  == "SINR_AMC_PRED",
                  "MCP 不返回伪精确预测 BLER，并把 AMC 预测与真实判错分栏")

            cqi0 = _payload(await session.call_tool(
                "sr_tdd_mcs", {"dataset_id": ds_id, "cqi": 0},
            ))
            check(cqi0.get("scheduled") is True and cqi0.get("cqi_initial_mcs") == 0
                  and cqi0.get("reported_cqi_codepoint") == 1,
                  "MCP 历史表行0从MCS0开始，并明确对应上报CQI1")
            reported0 = _payload(await session.call_tool(
                "sr_tdd_mcs", {
                    "dataset_id": ds_id, "cqi": 0,
                    "cqi_numbering": "reported_4bit",
                },
            ))
            check(reported0.get("scheduled") is False
                  and reported0.get("reason") == "reported_cqi_out_of_range",
                  "MCP 上报4-bit CQI0直接返回out-of-range且不调度")

            print("\n" + "=" * 68 + "\n6  sr_deliver —— 取货代码\n" + "=" * 68)
            d1 = _payload(await session.call_tool("sr_deliver", {"dataset_id": ds_id, "want": "信道"}))
            print(f"  第一次点单：{d1['measurements']}")
            check(d1["measurements"] == ["channel"], "只要信道时只给信道")

            d2 = _payload(
                await session.call_tool(
                    "sr_deliver", {"dataset_id": ds_id, "want": "我还想看 PMI 和 SRS RSRP"}
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
                    "sr_generate",
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

            print("\n" + "=" * 68 + "\n8  sr_describe_dataset / sr_list_datasets\n" + "=" * 68)
            desc = _payload(await session.call_tool("sr_describe_dataset", {"dataset_id": ds_id}))
            print(f"  形状 {desc['shape']}  含角度 {desc['has_angles']}")
            print(f"  可用测量量 {desc['available_measurements']}")
            check(bool(desc.get("available_measurements")), "描述包含可用测量量清单")

            lst = _payload(await session.call_tool("sr_list_datasets", {}))
            print(f"  本机已有 {len(lst['datasets'])} 个数据集")
            check(len(lst["datasets"]) >= 1, "数据集列表可用")

            print("\n" + "=" * 68 + "\n9  说明书回传：没人点也要干净返回\n" + "=" * 68)
            # `got=0` 是**正常路径**，不是错误：用户可能还在看，也可能改用对话说了。
            # 早先这里若抛异常或长时间挂住，agent 就会以为工具坏了并放弃这条交互。
            _t0 = time.time()
            wait = _payload(await session.call_tool("sr_await_config", {"timeout_s": 2}))
            _el = time.time() - _t0
            print(f"  等了 {_el:.1f} 秒，got={wait.get('got')}")
            check(wait.get("got") == 0 and "error" not in wait, "没人点时干净返回 got=0")
            check("note" in wait and "不是错误" in wait["note"], "明说超时不是错误")
            check(2.0 <= _el < 8.0, f"真的按 timeout_s 等（实测 {_el:.1f} 秒）")
            check(wait["bridge"]["enabled"] is True, "回传桥状态一并返回，便于排查")

    print("\n" + "=" * 68 + "\n10  TDD 系统载波固定为 272 RB / 17×16\n" + "=" * 68)
    # 链路级仍可生成其他带宽，但当前 sr_system_sim 是预置 100 MHz
    # TDD 产品 profile：张量必须真的是 272 RB，标签也必须是
    # 100 MHz / 30 kHz。错配时硬失败，不猜一个新 RBG 口径。
    from superran import server as _sv  # noqa: PLC0415

    _c = _sv._carrier_grid(
        {"bandwidth_hz": 100_000_000.0, "subcarrier_spacing": 30_000},
        num_rb=272,
    )
    check(_c["num_rbg"] == 17 and _c["rbg_prb_sizes"] == [16] * 17,
          "100 MHz TDD 固定为 17 RBG × 16 RB")
    check(_c["user_configurable"] is False,
          "TDD 载波 profile 不对用户开放修改")
    check(_c["standard_num_rb"] == 273
          and _c["standard_tail_rb_omitted_before_generation"] == 1,
          "明确记录标准 273 RB → 项目简化 272 RB")
    for _cfg, _nrb in (
        ({"bandwidth_hz": 20_000_000.0, "subcarrier_spacing": 30_000}, 272),
        ({"bandwidth_hz": 100_000_000.0, "subcarrier_spacing": 15_000}, 272),
        ({"bandwidth_hz": 100_000_000.0, "subcarrier_spacing": 30_000}, 51),
        ({"bandwidth_hz": 100_000_000.0, "subcarrier_spacing": "bad"}, 272),
    ):
        try:
            _sv._carrier_grid(_cfg, num_rb=_nrb)
            check(False, f"非固定 TDD 格栅应当被拒绝：{_cfg}, {_nrb} RB")
        except ValueError:
            check(True, f"非固定 TDD 格栅硬失败：{_cfg}, {_nrb} RB")

    print("\n" + "=" * 68 + "\n11  skill 的工具地图必须和代码对得上\n" + "=" * 68)
    # SKILL.md 写着"35 个 sr_* 工具全在这张表里"。这句话曾经**同时**漏了
    # sr_system_scene 又把明令不存在的 sr_compare_system_arms 算了进去，
    # 两个错误互相抵消，总数正好仍可能对上 —— 只数总数看不出来，必须逐个比名字。
    # 后果是实打实的：sr_system_scene 是用来免掉"每次手拍八九个系统级参数"的，
    # 它不在表里，用 skill 的 agent 就永远发现不了。
    import re as _re  # noqa: PLC0415
    from pathlib import Path as _Path  # noqa: PLC0415

    from superran import server as _srv  # noqa: PLC0415

    _skill = _Path(__file__).resolve().parents[1] / "skills" / "channel-sim" / "SKILL.md"
    if not _skill.exists():
        check(False, f"找不到 SKILL.md：{_skill}")
    else:
        _text = _skill.read_text(encoding="utf-8")
        _code_tools = {n for n in dir(_srv) if n.startswith("sr_")}
        _map = _text.split("## 工具地图与参考文件")[-1]
        _listed = set(_re.findall(r"`(sr_[a-z_0-9]+)`", _map))
        check(not (_code_tools - _listed),
              f"每个 sr_* 工具都在工具地图里（缺 {sorted(_code_tools - _listed)}）")
        check(not (_listed - _code_tools),
              f"工具地图里没有不存在的工具（多 {sorted(_listed - _code_tools)}）")
        _claims = {int(m) for m in _re.findall(r"(\d+)\s*个\s*`?sr_\*", _text)}
        check(_claims == {len(_code_tools)},
              f"正文声称的工具数 {sorted(_claims)} 等于实际 {len(_code_tools)}")


def test_main_script():
    """pytest 入口：MCP 全链路（失败时 sys.exit(1)）。

    只跑脚本不跑 pytest（或反过来）都会漏掉另一半——两种执行模型必须看到
    同一个真理，这条薄壳就是为此存在的。
    """
    FAILED.clear()
    asyncio.run(main())
    assert not FAILED, "MCP 全链路失败：" + "；".join(FAILED)


if __name__ == "__main__":
    asyncio.run(main())
    print("\n" + "=" * 68)
    if FAILED:
        print(f"FAILED {len(FAILED)} 项：")
        for f in FAILED:
            print("  - " + f)
        sys.exit(1)
    print("MCP 全链路通过。")
