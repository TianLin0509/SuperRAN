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

from dataclasses import dataclass, field, replace
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
    magnitude: str                   # 量级与依据（实测 / 标准估算 / 未测）
    ask: str                         # 该怎么问用户（完整人话）
    platform_default: str            # 用户不答时平台实际用什么
    effect_iot: str = ""             # 对下行 IoT（干扰相对热噪声）的作用
    effect_sir: str = ""             # 对下行 SIR 的作用
    effect: str = ""                 # 对其他指标（速率、时延）的作用
    config_key: str | None = None    # 可配置时对应的配置键
    # 这个键在哪一层生效：generation（换它就是换一批信道数据）或 system
    # （同一批数据上换参数即可，能做同数据、同随机流的配对比较）。
    layer: str = "generation"
    # 提问时给用户的选项：(取值, 标签, 取舍说明)，第一个是推荐项。
    options: tuple[tuple[Any, str, str], ...] = ()
    # 只在扫这个变量时才要问（例如只有比较 SRS 周期时才必须问自适应周期）。
    only_for_sweep: str | None = None
    # 对哪类比较是决定性的：扫描变量名，或 "scheduler"（比较调度器）。影响等级衡量的是
    # 目标量绝对值；比较/扫描要的是差值，决定差值的因素要提前问。
    decisive_for: tuple[str, ...] = ()
    # 预期与探测对不上时，这个假设能解释多大的偏差：
    # {"metric": 指标, "if": 另一种取值的说法, "delta_db": 估计变化, "basis": 依据}
    gap_hint: dict[str, Any] | None = field(default=None, compare=False)
    verify: dict[str, Any] | None = field(default=None, compare=False)

    def as_dict(self, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        out = {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "impact": self.impact,
            "effect_iot": self.effect_iot,
            "effect_sir": self.effect_sir,
            "effect": self.effect,
            "layer": self.layer,
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
        label="基站总发射功率（均分到各 RB）",
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
        options=(
            (46.0, "46 dBm · 宏站满功率", "平台默认；站距 500 m 左右的宏站"),
            (40.0, "40 dBm · 降功率宏站", "站距 200~300 m 的宏站常见"),
            (33.0, "33 dBm · 微站", "站距 200 m 以内的街道微站"),
        ),
        gap_hint={"metric": "iot_dl_db", "if": "按 33 dBm 微站功率", "delta_db": -13.0,
                  "basis": "I/N 与功率 dB 对 dB（测试逐样本核对）",
                  "only_if_above": ("tx_power_dbm", 33.0), "per_db_of": "tx_power_dbm"},
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
        options=(
            ("accept_outdoor", "按全室外解读", "IoT 当上界看，重点看趋势与 SIR"),
            ("indoor_major", "用户以室内为主", "只比较 SIR 与相对趋势，IoT 绝对值不作结论"),
            ("need_o2i", "必须有室内穿透损耗", "当前做不了，需要先补 O2I 建模"),
        ),
        gap_hint={"metric": "iot_dl_db", "if": "80% 室内（38.901 部署假设）", "delta_db": -16.0,
                  "basis": "38.901 低损耗 O2I 模型 2.6 GHz 手算，未在本平台实测"},
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
        options=(
            ("full_load", "按满载上界", "信道层现状；干扰最坏"),
            ("system_level", "按实际负载（改走系统级）", "neighbor_prb_util 设成现网负载"),
            ("both", "两者都要", "信道层给上界，系统级给典型"),
        ),
        gap_hint={"metric": "iot_dl_db", "if": "邻区负载 50%", "delta_db": -3.0,
                  "basis": "干扰按激活概率缩放的估算；信道层未建模"},
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
        label="统计范围（站数；无 wrap-around）",
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
        options=(
            (19, "19 站 57 小区", "更接近中心站口径，耗时约 3 倍"),
            (7, "7 站 21 小区", "快；外圈邻区不全，IoT 偏低"),
        ),
        gap_hint={"metric": "iot_dl_db", "if": "只看中心站", "delta_db": 6.5,
                  "basis": "中心站与边缘站小区场景实测相差约 6.5 dB"},
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
        options=(
            ("UMa_NLOS", "宏站（UMa，25 m 站高）", "38.901 城区宏站"),
            ("UMi_NLOS", "街道微站（UMi，10 m 站高）", "小站距、低功率部署"),
        ),
    ),
    Factor(
        key="carrier_bandwidth",
        label="载频与带宽",
        status=MODELED,
        impact=2,
        effect_iot="带宽决定每 RB 的功率与噪声底：同一总功率下带宽越宽，每 RB 功率越低，IoT 越低",
        effect_sir="载频改变路损与视距概率，SIR 随之小幅变化",
        magnitude="100→20 MHz 同功率时 IoT +7 dB（历史实测，UMi 33 dBm 条件）",
        ask="载频、带宽按 2.6 GHz / 100 MHz 吗？",
        platform_default="2.6 GHz、100 MHz（272 RB）",
        config_key="bandwidth_hz",
    ),
    Factor(
        key="downtilt",
        label="天线下倾与方向图",
        status=PARTIAL,
        impact=2,
        effect_iot="下倾决定邻区主瓣打到本小区的程度，站距越小越敏感",
        effect_sir="直接改变 SIR 分布",
        magnitude="默认 64T 子阵固定下倾 6°，阵元方向图是 3GPP 式参数化模型（非实测）",
        ask="下倾按默认 6° 可以吗？站距很小（<200 m）时它对干扰影响很大。",
        platform_default="固定 6° 下倾，参数化阵元方向图",
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

# ---------------------------------------------------------------------------
# 下行体验：体验速率 / 边缘速率 / 完成时延（系统级 sr_system_sim）
# ---------------------------------------------------------------------------
# 默认值取自 sr_system_sim 的签名；改签名时同步改这里（对账测试会核对）。

DL_EXPERIENCE: tuple[Factor, ...] = (
    Factor(
        key="traffic_model",
        label="话务模型",
        status=MODELED,
        impact=1,
        effect=(
            "决定哪些 KPI 有意义：满缓冲下标准 DRB 忙期吞吐为 None，边缘看窗口发送速率"
            "的 5% 分位；体验速率与小包完成时延需要有限到达话务"
        ),
        magnitude="口径切换，不是数值偏移：换话务模型等于换了要回答的问题",
        ask=(
            "速率/时延按哪种话务口径？满缓冲看的是小区容量下每个用户能分到多少；"
            "有限到达（文件下载、小包）看的是用户实际体验与完成时延，两者数值与含义都不同。"
        ),
        platform_default="ftp3（500 kB 文件、每 UE 2 次/秒）",
        config_key="traffic_model",
        layer="system",
        options=(
            ("ftp3", "有限到达（FTP3 文件下载）", "体验速率与完成时延有意义；平台默认"),
            ("full_buffer", "满缓冲", "看容量与边缘用户分到的发送速率"),
            ("mixed", "大小包混合", "默认一半 UE 小包 1500 B、20 次/秒，一半大包 500 kB、2 次/秒；"
             "同时看小包时延与大包速率"),
        ),
    ),
    Factor(
        key="neighbor_prb_util",
        label="系统级邻区负载",
        status=MODELED,
        impact=1,
        effect="邻区按这个占用率抽样发射：负载越高，本小区用户 SINR 越低、MCS 越低",
        magnitude=(
            "系统级默认 30%；注意信道数据的 IoT/SINR 是按邻区满载算的，两层口径不同，"
            "引用干扰数和引用速率时负载假设不一致"
        ),
        ask="邻区负载按多少？系统级默认 30%，现网忙时常见 50%~70%，满载是最坏情况。",
        platform_default="0.3（± 0.05 抖动）",
        config_key="neighbor_prb_util",
        layer="system",
        options=(
            (0.3, "30% · 平台默认", "轻中载"),
            (0.6, "60% · 忙时典型", ""),
            (1.0, "100% · 满载最坏", "与信道层 IoT 口径一致"),
        ),
    ),
    Factor(
        key="srs_period_adaptive",
        label="SRS 周期自适应",
        status=MODELED,
        impact=1,
        effect="开着时实际 SRS 周期由资源分配决定，名义上的 10/20 ms 可能被改成同一个值",
        magnitude="比较 SRS 周期时是决定性的：不关掉，两组可能跑成同一个周期",
        ask=(
            "比较 SRS 周期时，要不要关掉自适应、强制按 10 ms / 20 ms 跑？"
            "不关的话平台会按资源情况自选周期，两组可能变成同一个周期。"
        ),
        platform_default="开启（srs_period_adaptive=True）",
        config_key="srs_period_adaptive",
        layer="system",
        only_for_sweep="srs_period_ms",
        options=(
            (False, "关掉，强制名义周期", "比较周期时必须这样"),
            (True, "保留自适应", "看现网策略下的效果，但两组实际周期要事后核对"),
        ),
    ),
    Factor(
        key="ue_speed_kmh",
        label="用户速度与时间轴",
        status=MODELED,
        impact=2,
        effect=("速度决定信道老化快慢；要体现老化，每 UE 需要 ≥8 个时间相关快照，独立撒点不行。"
                "时间轴也是 PF 多用户分集的前提：单快照下信道不随时间起伏，比较调度器会系统性低估 PF"),
        magnitude="3 km/h 下几乎不老化，60 km/h 以上 SRS/CSI 时延的影响才明显",
        ask="用户速度按多少？这决定 CSI 老化的程度，平台会按它生成连续轨迹。",
        platform_default="预设值（多为 3 km/h），单快照",
        config_key="ue_speed_kmh",
        decisive_for=("srs_period_ms", "scheduler"),
        options=(
            (3.0, "3 km/h · 步行", "几乎不老化"),
            (30.0, "30 km/h · 城区车速", ""),
            (120.0, "120 km/h · 高速", "老化明显"),
        ),
    ),
    Factor(
        key="cell_load",
        label="本小区负载（资源有多紧）",
        status=MODELED,
        impact=1,
        effect="调度器之间的差别主要出现在资源紧张时；轻载下 PF、EDF 等几乎没有差别",
        magnitude="比较调度器时是决定性的：负载轻，任何调度器的时延都差不多",
        ask=("本小区负载按多紧来比？推荐把 PRB 利用率标定到现网忙时水平（例如 50%~70%），"
             "否则由每 UE 到达率自然形成。"),
        platform_default="不标定（target_prb_utilization=None）：按每 UE 到达率自然形成",
        config_key="target_prb_utilization",
        layer="system",
        decisive_for=("scheduler",),
        options=(
            (0.6, "PRB 利用率标定到 60%", "现网忙时典型，调度器差异可见"),
            (0.3, "标定到 30%", "轻中载"),
            (0.9, "标定到 90%", "接近拥塞，差异最大"),
        ),
    ),
    Factor(
        key="num_ues",
        label="每小区用户数",
        status=MODELED,
        impact=2,
        effect="决定调度竞争与 PF 多用户分集；不标定负载时，负载也由它和到达率自然形成",
        magnitude="每小区几个用户与十几个用户，调度器差异可以完全不同",
        ask="每小区大概多少活跃用户？",
        platform_default="预设值（company_64t4r_multicell 为 21 UE / 21 小区，即每小区约 1 个）",
        config_key="num_ues",
        decisive_for=("scheduler",),
        options=(
            (210, "每小区约 10 个（210 UE / 21 小区）", "调度竞争可见"),
            (105, "每小区约 5 个", ""),
            (21, "每小区约 1 个（预设）", "几乎没有调度竞争"),
        ),
    ),
    Factor(
        key="mu_enabled",
        label="MU-MIMO",
        status=PARTIAL,
        impact=2,
        effect="开 MU 提升小区容量，边缘用户收益小得多；只支持两用户配对、每 UE rank 1~2",
        magnitude="单小区满缓冲实测小区吞吐 +64%，5% 边缘仅 +7%",
        ask="要不要开 MU？默认只做 SU。",
        platform_default="关闭（SU，mu_enabled=False）",
        config_key="mu_enabled",
        layer="system",
        decisive_for=("srs_period_ms",),
        options=(
            (False, "SU（默认）", "口径简单"),
            (True, "开 MU（两用户配对）", "容量上界更高，边缘收益小"),
        ),
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
    # 速率落在 SINR 上，所以干扰侧影响最大的几项也在清单里（降一级）。
    "dl_experience": DL_EXPERIENCE + tuple(
        replace(f, impact=f.impact + 1)
        for f in DL_INTERFERENCE if f.key in {"tx_power_dbm", "indoor_users", "stat_scope"}
    ),
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


def explain_gap(metric: str, expected: float, measured: float,
                *, cfg: dict[str, Any] | None = None,
                threshold_db: float = 3.0) -> dict[str, Any]:
    """用户预期与探测对不上时，哪些假设能解释这个差距（方向对、量级够）。

    只列出因子表里有 ``gap_hint`` 的条目，按“换成另一种取值后能补上多少差距”排序。
    这是**候选解释**，不是结论：量级来自对账测试或标准估算，依据随条目给出。
    """
    gap = float(expected) - float(measured)
    out: dict[str, Any] = {
        "metric": metric, "expected": expected, "measured": round(float(measured), 2),
        "gap_db": round(gap, 2),
    }
    if abs(gap) < threshold_db:
        out["verdict"] = f"与预期相差 {gap:+.1f} dB，在 ±{threshold_db:g} dB 内，视为一致"
        out["candidates"] = []
        return out
    seen: set[str] = set()
    cands = []
    for table in METRIC_FACTORS.values():
        for f in table:
            h = f.gap_hint
            if not h or h.get("metric") != metric or f.key in seen:
                continue
            seen.add(f.key)
            delta = float(h["delta_db"])
            cond = h.get("only_if_above")
            if cond and cfg is not None:
                cur = cfg.get(cond[0])
                if cur is not None and float(cur) <= float(cond[1]):
                    continue  # 当前已经是那种取值，解释不了
                if cur is not None and h.get("per_db_of") == cond[0]:
                    delta = -(float(cur) - float(cond[1]))  # 1:1 斜率，按当前值算
            if delta * gap <= 0:
                continue  # 方向不对，解释不了
            cands.append({
                "factor": f.label, "status": f.status, "if": h["if"],
                "would_change_db": delta, "covers_share": round(min(abs(delta) / abs(gap), 1.0), 2),
                "basis": h["basis"],
            })
    cands.sort(key=lambda c: -abs(c["would_change_db"]))
    out["verdict"] = (
        f"探测比预期{'高' if gap < 0 else '低'} {abs(gap):.1f} dB。先核对下列假设，"
        "对齐后再正式生成；它们加起来能否补上差距要重新探测确认。"
    )
    out["candidates"] = cands
    return out
