"""指标影响因子表：这个量由哪些因素决定、平台建模了哪些、该怎么问用户。

**为什么需要它。** 提问原来按任务关键词套模板：用户说"干扰"就走"干扰协调"
那套问题，先问码本基线，却不问发射功率、室内比例——而后者才决定下行 IoT
的绝对值。仿真器自己知道 IoT 的计算路径：

    I/N = Σ_邻区 P_tx·G·PL⁻¹ / (kT·B_RB·NF)，   SIR = P_rx,serving / Σ_邻区 P_rx

每一项对应一个用户可能心里有数、平台却在替他拍的假设。这张表把它们按对
结论的影响排序写下来，并**区分三种状态**：

* ``modeled``——平台算了，问清用户的取值即可；
* ``not_modeled``——会明显改变结果但平台没有这个机制。**必须主动告诉用户**，
  否则"模型里没有"会被读成"这个因素不重要"；
* ``partial``——算了一部分，边界要讲清。

**表里的说法必须和仿真器一致。** 带 ``verify`` 的条目由
``tests/test_interference.py`` 的"影响因子表对账"一节逐样本核对：例如声称
"发射功率每降 6 dB，I/N 逐样本降 6 dB、SIR 不变"，测试就真改功率跑一遍；
声称"邻区负载不进入信道层"，测试就改负载确认逐位不变。哪天有人给信道层
加了负载模型，那条测试会变红，逼着同时改这张表——知识不会悄悄过期。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MODELED = "modeled"
NOT_MODELED = "not_modeled"
PARTIAL = "partial"


@dataclass(frozen=True)
class Factor:
    """一个影响因素。"""

    key: str
    label: str
    status: str                      # MODELED / NOT_MODELED / PARTIAL
    impact: int                      # 1 = 能改变结论量级，数字越大越次要
    effect_iot: str                  # 对下行 IoT（干扰相对热噪声）的作用
    effect_sir: str                  # 对下行 SIR 的作用
    magnitude: str                   # 量级与依据（实测 / 标准估算 / 未测）
    ask: str                         # 该怎么问用户（完整人话）
    platform_default: str            # 用户不答时平台实际用什么
    config_key: str | None = None    # 可配置时对应的配置键
    verify: dict[str, Any] | None = field(default=None, compare=False)

    def as_dict(self, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        out = {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "impact": self.impact,
            "effect_iot": self.effect_iot,
            "effect_sir": self.effect_sir,
            "magnitude": self.magnitude,
            "ask": self.ask,
            "platform_default": self.platform_default,
            "config_key": self.config_key,
        }
        if cfg is not None and self.config_key and self.config_key in cfg:
            out["current"] = cfg[self.config_key]
        return out


# ---------------------------------------------------------------------------
# 下行干扰：IoT / SIR / SINR
# ---------------------------------------------------------------------------

DL_INTERFERENCE: tuple[Factor, ...] = (
    Factor(
        key="tx_power_dbm",
        label="基站发射功率（功率谱密度）",
        status=MODELED,
        impact=1,
        effect_iot="信号与干扰同比例变化，热噪声不变：I/N 与功率 dB 对 dB 变化",
        effect_sir="逐样本不变",
        magnitude="精确 1:1（测试逐样本核对）。同一总功率下带宽越宽、每 RB 功率越低，效果相同",
        ask=(
            "基站发射功率按多少？平台默认 46 dBm（宏站满功率）。站距 200~300 m "
            "的现网多是微站或降功率宏站；IoT 会随功率 dB 对 dB 平移，SIR 不受影响。"
        ),
        platform_default="46 dBm 总载波功率，100 MHz 均分到 272 RB",
        config_key="tx_power_dbm",
        verify={"set": {"tx_power_dbm": 40.0}, "base": {"tx_power_dbm": 46.0},
                "in_shift_db": -6.0, "sir_shift_db": 0.0},
    ),
    Factor(
        key="indoor_users",
        label="室内用户比例与穿透损耗（O2I）",
        status=NOT_MODELED,
        impact=1,
        effect_iot="室内用户的信号与干扰同时衰减，I/N 下降约一个穿透损耗",
        effect_sir="近似不变（服务与邻区经过同一面墙）",
        magnitude=(
            "未在本平台实测。按 38.901 低损耗 O2I 模型在 2.6 GHz 估算：墙损约 12 dB，"
            "室内距离损耗均值约 4 dB，合计均值约 16 dB（另有 4.4 dB 标准差）；"
            "38.901 UMa/UMi 评估假设 80% 用户在室内"
        ),
        ask=(
            "你关心的用户主要在室内还是室外？平台目前只有室外、1.5 m 高的用户。"
            "如果对标的是 80% 室内的部署，IoT 绝对值会偏高十几 dB，只能看趋势和 SIR。"
        ),
        platform_default="全部室外，1.5 m",
    ),
    Factor(
        key="neighbor_load",
        label="邻区负载（邻区有多少资源在发）",
        status=NOT_MODELED,
        impact=1,
        effect_iot="若按激活概率 η 缩放，干扰降为 η 倍：η=50% 时 I/N 约 -3 dB",
        effect_sir="若建模则 +10·log10(1/η) dB；信道层当前逐位不变",
        magnitude=(
            "信道层逐位无效（测试核对 pdsch_load / prb_utilization 改动后 SIR、SINR "
            "完全不变）；系统级 neighbor_prb_util 才生效"
        ),
        ask=(
            "邻区负载按多少理解？信道层默认所有邻区满功率发射，是最坏干扰。"
            "想看 30%~50% 负载下的干扰和体验，要走系统级仿真；只看信道层就要接受"
            "这是满载上界。"
        ),
        platform_default="信道层邻区恒满发",
        config_key="prb_utilization",
        verify={"set": {"prb_utilization": 0.3, "pdsch_load": 0.3},
                "base": {"prb_utilization": 1.0, "pdsch_load": 1.0},
                "in_shift_db": 0.0, "sir_shift_db": 0.0},
    ),
    Factor(
        key="isd_m",
        label="站间距",
        status=MODELED,
        impact=1,
        effect_iot="站距越小，服务与邻区都越近，I/N 越大（变化主要来自路损绝对值）",
        effect_sir=(
            "单斜率路损下近似不变；38.901 的视距概率随距离变化、断点距离与固定下倾"
            "会让它随站距有几 dB 变化"
        ),
        magnitude=(
            "一次探索性扫描（UMa、46 dBm、19 站、全室外满载）中 200 m 与 1000 m 的"
            " IoT 中位相差 30 dB 以上；换条件后以本次探测为准"
        ),
        ask="要比较哪些站距？它通常就是这次要扫的变量。",
        platform_default="预设值（company_64t4r_multicell 为 500 m）",
        config_key="isd_m",
    ),
    Factor(
        key="stat_scope",
        label="统计对象（全网 / 中心站）",
        status=PARTIAL,
        impact=2,
        effect_iot="外圈小区的邻区不完整，把它们算进来会拉低 IoT",
        effect_sir="同理偏高",
        magnitude=(
            "没有 wrap-around；sys_multicell_center_cell 与 _edge_cell 两个场景的"
            "服务小区 IoT 中位实测相差约 6.5 dB"
        ),
        ask=(
            "统计看全部用户还是只看中心站？平台没有 wrap-around，全网统计含外圈"
            "邻区不全的用户；19 站比 7 站更接近中心站口径，但耗时约三倍。"
        ),
        platform_default="全部小区的用户一起统计",
        config_key="num_sites",
    ),
    Factor(
        key="ue_distribution",
        label="用户撒点（均匀 / 热点 / 成簇）",
        status=NOT_MODELED,
        impact=2,
        effect_iot="热点靠近边缘时 IoT 尾部变重、靠近站点时变轻；决定统计采到哪些位置",
        effect_sir="同上，改变分布形状与分位数",
        magnitude=(
            "当前内核只有均匀撒点：ue_distribution / num_hotspots 不被读取，"
            "设成 hotspot 与 uniform 逐位相同（测试核对）"
        ),
        ask=(
            "平台目前只能均匀撒点。如果你关心热点区域或室内聚集用户，"
            "结果只代表均匀分布下的统计，这一点要写进结论边界。"
        ),
        platform_default="uniform（唯一实现）",
        config_key="ue_distribution",
        verify={"set": {"ue_distribution": "hotspot", "num_hotspots": 3},
                "base": {"ue_distribution": "uniform"},
                "in_shift_db": 0.0, "sir_shift_db": 0.0},
    ),
    Factor(
        key="scenario",
        label="传播场景（UMa / UMi）与站高、载频",
        status=MODELED,
        impact=2,
        effect_iot="改变路损斜率、视距概率，从而同时改 I/N",
        effect_sir="改变服务与邻区路损之差",
        magnitude="UMa 25 m 站高与 UMi 10 m 站高的路损公式不同；以本次探测为准",
        ask="宏站（UMa）还是街道微站（UMi）？载频按 2.6 GHz 吗？",
        platform_default="UMa（38.901 视距概率抽样），2.6 GHz",
        config_key="scenario",
    ),
    Factor(
        key="noise_figure_db",
        label="终端噪声系数",
        status=MODELED,
        impact=3,
        effect_iot="噪声底抬高多少，I/N 就降多少",
        effect_sir="逐样本不变",
        magnitude="精确 1:1（测试逐样本核对）",
        ask="终端噪声系数用默认 7 dB 可以吗？一般不用改。",
        platform_default="7 dB",
        config_key="noise_figure_db",
        verify={"set": {"noise_figure_db": 9.0}, "base": {"noise_figure_db": 7.0},
                "in_shift_db": -2.0, "sir_shift_db": 0.0},
    ),
)

# 配置里能写、提问层也会问，但仿真器（信道生成路径）从不读取的键。用户设了
# 它们，数据不会有任何变化——必须当场说清。tests/test_interference.py 用源码
# 静态检查确认它们确实没被读取；哪天实现了，测试变红，逼着把它从这里拿掉。
INERT_CONFIG_KEYS: dict[str, str] = {
    "ue_distribution": "当前内核只有均匀撒点，hotspot / clustered 不生效",
    "num_hotspots": "热点撒点未实现",
    "prb_utilization": "信道层邻区恒满发；负载只在系统级 neighbor_prb_util 生效",
    "pdsch_load": "信道层邻区恒满发；负载只在系统级 neighbor_prb_util 生效",
    "train_penetration_loss_db": "车体穿透损耗未实现，高铁预设里的 20 dB 不生效",
    "hypercell_size": "超级小区合并未实现，设多少都按独立小区算",
    "joint_trp_count": "多 TRP 联合发送未实现，仍按单 TRP 服务",
    "train_length_m": "车厢内撒点未实现，UE 按普通方式撒点",
    "train_width_m": "车厢内撒点未实现，UE 按普通方式撒点",
}


def inert_keys_in(cfg: dict[str, Any] | None) -> list[str]:
    """配置里设了、但仿真器不读的键，逐条说明。用于所有机器返回（预设、探测、报告）。

    “取默认值”不算设置：均匀撒点、超级小区规模 1、负载 1.0 本来就是仿真器的行为。
    """
    out = []
    for key, why in INERT_CONFIG_KEYS.items():
        if not cfg or key not in cfg or cfg[key] is None:
            continue
        val = cfg[key]
        if key == "ue_distribution" and str(val) == "uniform":
            continue
        if key in {"hypercell_size", "joint_trp_count"} and int(val or 1) <= 1:
            continue
        if key in {"prb_utilization", "pdsch_load"} and float(val) >= 1.0:
            continue
        out.append(f"{key}={val} 不生效：{why}")
    return out

# 在看到任何结果之前让用户写下预期。探测结果与它对照，差距大就先停下查假设。
EXPECTATION_QUESTION: dict[str, Any] = {
    "key": "expectation",
    "question": (
        "跑之前先写下你的预期：这个场景的下行干扰大概在什么量级"
        "（例如 500 m 站距 IoT 中位约多少 dB、SINR 中位约多少 dB）？参考来源是"
        "现网统计、论文还是经验？"
    ),
    "why": (
        "探测只要几十秒。先有预期，探测出来就能逐项对照；差距超过约 5 dB 时先停下"
        "查假设（发射功率、室内比例、邻区负载、统计对象），而不是跑完几十分钟的"
        "正式数据才发现口径不同。"
    ),
}

METRIC_FACTORS: dict[str, tuple[Factor, ...]] = {
    "dl_interference": DL_INTERFERENCE,
}


def factors_for(metric: str = "dl_interference") -> tuple[Factor, ...]:
    return METRIC_FACTORS[metric]


def checklist(metric: str = "dl_interference",
              cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """给 Agent 的影响因子清单：按影响排序，未建模项单独点名。"""
    items = sorted(factors_for(metric), key=lambda f: (f.impact, f.status != NOT_MODELED))
    return {
        "metric": metric,
        "factors": [f.as_dict(cfg) for f in items],
        "must_disclose": [
            f.label for f in items if f.status != MODELED and f.impact <= 2
        ],
        "expectation_question": dict(EXPECTATION_QUESTION),
        "how_to_use": (
            "向用户确认 impact=1 的各项（已建模的问取值，未建模的说清会偏向哪边）；"
            "先写下预期，再跑 sr_probe_scenario 对照；偏差大先查这张表里的假设，"
            "对齐后再正式生成。"
        ),
    }
