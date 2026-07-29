"""决策点知识表：这个任务该和用户对齐哪些事。

这是 superwireless 唯一不可替代的部分。参数转发谁都能写，但"哪几件事
一改结论就翻盘、为什么、选错会怎样"是无线领域知识。

分两层，顺序不能颠倒：

* **实验设计层**（design）——跟什么基线比？用什么指标？结论要推广到哪？
  参数配错了重跑就行，实验设计错了整个结论作废。所以先问这层。
* **仿真参数层**（decisions）——信道模型、天线、带宽……

另外两样东西同样重要：
* **对比组建议**（sweeps）——很多结论必须靠 A/B 才立得住，MCP 主动提。
* **常见陷阱**（pitfalls）——这类课题上大家反复踩的坑。

收敛式，不是发散式：参数空间有限且已知，不让模型自由发挥。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class Option:
    value: Any
    label: str
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "label": self.label, "note": self.note}


@dataclass
class Decision:
    """一个值得问用户的仿真参数。"""

    key: str
    question: str
    default: Any
    why: str  # 为什么这个选择会改变结论 —— 最关键的一栏
    options: list[Option] = field(default_factory=list)
    priority: int = 5  # 越小越该先问

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "question": self.question,
            "default": self.default,
            "why": self.why,
            "options": [o.as_dict() for o in self.options],
            "priority": self.priority,
        }


@dataclass
class DesignQuestion:
    """实验设计层的问题。没有默认值——这些必须用户自己想清楚。

    它不影响任何仿真参数，但决定了这批数据能不能支撑用户想要的结论。
    """

    key: str
    question: str
    why: str
    examples: list[str] = field(default_factory=list)
    optional: bool = False  # True 表示用户不答也能走

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "question": self.question,
            "why": self.why,
            "examples": self.examples,
            "optional": self.optional,
        }


@dataclass
class Sweep:
    """建议的对比维度：一次生成多组数据，结论才站得住。"""

    key: str
    values: list[Any]
    label: str
    why: str

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "values": self.values, "label": self.label, "why": self.why}


@dataclass
class TaskProfile:
    """一类仿真任务的完整画像。"""

    task: str
    label: str
    keywords: tuple[str, ...]
    decision_keys: tuple[str, ...]
    design_keys: tuple[str, ...] = ()
    sweeps: tuple[Sweep, ...] = ()
    pitfalls: tuple[str, ...] = ()
    config_hints: dict[str, Any] = field(default_factory=dict)
    guards: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 实验设计层问题
# ---------------------------------------------------------------------------

_DESIGN: dict[str, DesignQuestion] = {
    q.key: q
    for q in (
        DesignQuestion(
            key="baseline",
            question="你的方法要跟什么比？",
            why=(
                "没有基线的仿真结果没法解读——"
                "「NMSE 达到 -18 dB」本身不说明任何问题，"
                "关键是它比现有方案好还是差。基线定下来，"
                "才知道要不要同时生成对照数据。"
            ),
            examples=[
                "3GPP Type I / Type II 码本",
                "理想信道下的 SVD 上界",
                "已发表的某个方法",
                "不压缩的全反馈",
                "传统 LS / MMSE 估计",
            ],
        ),
        DesignQuestion(
            key="metric",
            question="用什么指标判断好坏？",
            why=(
                "指标决定了要留哪些量。比如只看 NMSE 就不需要 PMI，"
                "但要算频谱效率损失就得同时留预编码矩阵和 SINR。"
                "事后补指标往往要重跑。"
            ),
            examples=[
                "NMSE / 余弦相似度",
                "频谱效率或吞吐损失",
                "误块率 BLER",
                "波束选对的概率",
                "定位误差 CDF",
            ],
        ),
        DesignQuestion(
            key="scope",
            question="这个结论想推广到什么范围？",
            why=(
                "只在一种场景下验证，结论就只在那种场景成立。"
                "想说「在城区普遍有效」，至少要覆盖视距和非视距两类；"
                "想说「对高速用户也管用」，就得扫速度。"
                "范围定下来，需要几组对照就清楚了。"
            ),
            examples=[
                "就这一个场景，先看可行性",
                "城区宏站普遍适用（视距+非视距）",
                "覆盖不同移动速度",
                "覆盖不同天线规模",
            ],
            optional=True,
        ),
        DesignQuestion(
            key="hypothesis",
            question="你预期会看到什么？如果结果相反说明什么？",
            why=(
                "先说出预期，能暴露隐含假设。"
                "而且如果结果和预期一致但原因不对（比如信道恰好低秩导致谁都表现好），"
                "事先想过就容易发现。"
            ),
            examples=[
                "预期比 Type I 好 3 dB 以上",
                "预期在低信噪比下优势更明显",
                "预期高速场景下性能会掉但仍优于基线",
            ],
            optional=True,
        ),
    )
}


# ---------------------------------------------------------------------------
# 仿真参数决策点
# ---------------------------------------------------------------------------

_CHANNEL_MODEL = Decision(
    key="channel_model",
    question="信道模型用哪个？",
    default="CDL-C",
    why=(
        "CDL-C 是密集散射的非视距基准，信道矩阵接近满秩，难度贴近真实城区；"
        "换成 CDL-D/E（视距，K 因子 13.3/22 dB）信道近似低秩，压缩率、"
        "波束增益这类指标会明显偏乐观。TDL 系列没有每条径的角度信息，"
        "凡是依赖角度的算法都不能用它。"
    ),
    options=[
        Option("CDL-A", "非视距 · 强散射", "时延与角度扩展都大"),
        Option("CDL-B", "非视距 · 中等散射", ""),
        Option("CDL-C", "非视距 · 城区基准（默认）", "最常用的对比基线"),
        Option("CDL-D", "视距 · K=13.3 dB", "信道低秩，指标偏乐观"),
        Option("CDL-E", "视距 · K=22 dB", "强视距，几乎单径主导"),
        Option("TDL-C", "非视距 · 仅抽头", "无角度信息，不能做波束/定位"),
    ],
    priority=1,
)

_SNR = Decision(
    key="snr_range_dB",
    question="要不要限定信噪比范围？",
    default=None,
    why=(
        "信噪比不是可以直接设定的参数——它由路损、发射功率和噪声共同决定，"
        "取决于用户撒在哪里。默认不限定，让它自然分布（生成后会报告实际分布）。"
        "确实需要特定区间时会用拒绝采样实现，代价是变慢，"
        "区间偏离实际分布太远时可能一个都取不到。"
        "想整体抬高或压低信噪比，调发射功率或站间距比筛选更有效。"
    ),
    options=[
        Option(None, "不限定（默认）", "自然分布，最快"),
        Option([0.0, 25.0], "0~25 dB", "覆盖边缘到近点，可能拒绝率较高"),
        Option([-5.0, 5.0], "-5~5 dB", "只看小区边缘"),
        Option([25.0, 45.0], "25~45 dB", "只看近点"),
    ],
    priority=2,
)

_BANDWIDTH = Decision(
    key="bandwidth_hz",
    question="带宽多少？",
    default=100e6,
    why=(
        "带宽决定频域维度（100 MHz@30kHz 对应 273 个 RB），"
        "既是压缩率的分母，也决定时延分辨率——做定位/时延估计时，"
        "分辨率约等于光速除以带宽，100 MHz 对应 3 米。"
    ),
    options=[
        Option(100e6, "100 MHz（默认）", "273 RB，n78 典型"),
        Option(50e6, "50 MHz", "133 RB"),
        Option(20e6, "20 MHz", "51 RB，跑得快"),
        Option(200e6, "200 MHz", "毫米波常用，时延分辨率更高"),
    ],
    priority=3,
)

_SPEED = Decision(
    key="ue_speed_kmh",
    question="终端移动速度？",
    default=3.0,
    why=(
        "速度通过多普勒决定信道老化速度。静止/步行（3 km/h）时上下行互易性"
        "几乎不衰减；60 km/h 以上时 SRS 测量到实际使用之间信道已经变了，"
        "依赖互易性的方案会明显掉性能。"
    ),
    options=[
        Option(3.0, "3 km/h · 步行（默认）", "信道近似静止"),
        Option(30.0, "30 km/h · 市区车速", ""),
        Option(60.0, "60 km/h · 快速路", "信道老化开始显著"),
        Option(120.0, "120 km/h · 高速", "互易性明显退化"),
    ],
    priority=4,
)

_ANTENNA = Decision(
    key="bs_antenna",
    question="基站天线配置？",
    default="64T4R",
    why=(
        "端口数直接决定 CSI 的维度和码本大小。64 口按 8H4V 双极化解读，"
        "这是 3.5 GHz 宏站的主流配置；32 口及以下码本更小、搜索更快。"
    ),
    options=[
        Option("64T4R", "64 发 4 收（默认）", "8H4V 双极化，宏站主流"),
        Option("32T4R", "32 发 4 收", "8H2V 双极化"),
        Option("16T2R", "16 发 2 收", "小站/快速验证"),
        Option("4T4R", "4 发 4 收", "最小配置，秒级出数据"),
    ],
    priority=3,
)

_EST_MODE = Decision(
    key="channel_est_mode",
    question="信道估计方式？",
    default="ls_linear",
    why=(
        "ideal 给理想信道，适合先验证算法上界；ls_linear 是带导频和噪声的"
        "实际估计。两者的差距就是估计误差对你算法的影响。"
        "注意理想信道会一并给出，所以选实际估计不会损失真值参照。"
    ),
    options=[
        Option("ideal", "理想信道", "无估计误差，算上界用"),
        Option("ls_linear", "LS + 线性插值（默认）", "贴近实际实现"),
        Option("ls_mmse", "LS + MMSE", "更好的估计器"),
    ],
    priority=2,
)

_PILOT = Decision(
    key="pilot_type",
    question="导频类型？",
    default="csi_rs_gold",
    why=(
        "下行用 CSI-RS（Gold 序列），上行用 SRS（ZC 序列）。做上行互易性、"
        "SRS 跳频相关的课题必须走 srs_zc，否则拿不到 SRS 侧的量。"
    ),
    options=[
        Option("csi_rs_gold", "CSI-RS · Gold 序列（默认）", "下行"),
        Option("srs_zc", "SRS · ZC 序列", "上行，互易性相关课题用"),
    ],
    priority=4,
)

_LINK = Decision(
    key="link",
    question="上行还是下行？",
    default="DL",
    why="下行看 CSI 反馈与预编码，上行看 SRS 与互易性。做互易性对比需要成对的上下行。",
    options=[
        Option("DL", "下行（默认）", ""),
        Option("UL", "上行", ""),
        Option("both", "上下行成对", "互易性课题必选，数据量翻倍"),
    ],
    priority=4,
)

_NUM_SAMPLES = Decision(
    key="num_samples",
    question="生成多少个样本？",
    default=200,
    why=(
        "统计类结论（累积分布、平均性能）通常需要几百个样本；先跑 20 个"
        "验证流程通不通，再放大到几百个正式出图，能省不少时间。"
        "要做分布尾部分析（如 5% 边缘用户）则需要上千。"
    ),
    options=[
        Option(20, "20 · 冒烟测试", "验证流程"),
        Option(200, "200（默认）", "够画分布图"),
        Option(1000, "1000 · 正式实验", "尾部统计可信"),
    ],
    priority=6,
)

_SCENARIO = Decision(
    key="scenario",
    question="传播场景？",
    default="UMa_NLOS",
    why=(
        "城区宏站（UMa）站高 25 m、站间距 500 m；城区微站（UMi）站高 10 m、"
        "站距更小；室内工厂（InF）时延扩展与角度分布都明显不同。"
    ),
    options=[
        Option("UMa_NLOS", "城区宏站 · 非视距（默认）", "38.901 基准"),
        Option("UMi_NLOS", "城区微站 · 非视距", "密集城区"),
        Option("InF", "室内工厂", "38.901 §7.2"),
    ],
    priority=3,
)

_NUM_SITES = Decision(
    key="num_sites",
    question="几个站点？",
    default=7,
    why=(
        "单站看不到小区间干扰。7 站 21 小区是密集城区常用规模；"
        "19 站 57 小区是 38.901 Case 1 基准，中心小区的干扰环境最完整，"
        "但耗时约为 7 站的三倍。"
    ),
    options=[
        Option(1, "1 站 · 单小区", "无小区间干扰"),
        Option(7, "7 站 21 小区（默认）", "一圈邻区"),
        Option(19, "19 站 57 小区", "两圈邻区，38.901 基准"),
    ],
    priority=1,
)

_UE_DIST = Decision(
    key="ue_distribution",
    question="用户怎么撒点？",
    default="uniform",
    why=(
        "均匀撒点是标准做法；热点分布会把用户往少数几处集中，边缘用户比例"
        "和干扰强度都上升，更能拉开干扰协调类算法的差距。撒点方式一换，"
        "SINR 分布整体平移，跨实验对比会失效。"
    ),
    options=[
        Option("uniform", "均匀分布（默认）", "标准做法"),
        Option("clustered", "成簇分布", "若干簇中心"),
        Option("hotspot", "热点分布", "边缘用户比例高，干扰更重"),
    ],
    priority=2,
)

_PRB_UTIL = Decision(
    key="prb_utilization",
    question="邻区负载率？",
    default=1.0,
    why=(
        "负载率决定邻区有多大比例的时频资源在发射。满载（1.0）是干扰最坏情况；"
        "轻载下干扰问题会被掩盖，容易得出「算法没用」的错误结论。"
    ),
    options=[
        Option(1.0, "满载 1.0（默认）", "干扰最坏情况"),
        Option(0.7, "0.7 · 忙时典型", ""),
        Option(0.3, "0.3 · 轻载", "干扰弱"),
    ],
    priority=3,
)

_TDD = Decision(
    key="tdd_pattern",
    question="TDD 时隙配比？",
    default="DDDSU",
    why=(
        "配比决定上下行时隙比例，也决定 SRS 发送机会的间隔——"
        "间隔越长，用 SRS 推下行时信道老化越严重。"
    ),
    options=[
        Option("DDDSU", "DDDSU（默认）", "国内主流 2.5 ms 单周期"),
        Option("DDSUU", "DDSUU", "上行更多，SRS 更密"),
        Option("DDDDDDDSUU", "DDDDDDDSUU", "下行为主，SRS 间隔长"),
    ],
    priority=5,
)

_MOBILITY = Decision(
    key="mobility_mode",
    question="移动模型？",
    default="static",
    why=(
        "静止时每个样本独立；直线/随机游走会产生有时间相关性的轨迹，"
        "做切换、跟踪、信道预测类课题必须用它，否则样本间没有时序关系。"
    ),
    options=[
        Option("static", "静止（默认）", "样本间独立"),
        Option("linear", "直线移动", "轨迹连续"),
        Option("random_walk", "随机游走", ""),
        Option("random_waypoint", "随机路点", ""),
    ],
    priority=5,
)

_NUM_SLOTS = Decision(
    key="num_slots_per_sample",
    question="每个样本取几个连续时隙？",
    default=1,
    why=(
        "默认 1 个时隙，样本之间没有时序关系。做信道预测、跟踪、老化分析"
        "必须取多个连续时隙——后续时隙由 Jakes 模型按多普勒演进，"
        "才有真实的时间相关性。数据量按时隙数线性增长。"
    ),
    options=[
        Option(1, "1 个（默认）", "单快照"),
        Option(5, "5 个", "短时序"),
        Option(14, "14 个", "覆盖一个 TDD 周期以上"),
    ],
    priority=2,
)

ALL_DECISIONS: dict[str, Decision] = {
    d.key: d
    for d in (
        _CHANNEL_MODEL, _SNR, _BANDWIDTH, _SPEED, _ANTENNA, _EST_MODE,
        _PILOT, _LINK, _NUM_SAMPLES, _SCENARIO, _NUM_SITES, _UE_DIST,
        _PRB_UTIL, _TDD, _MOBILITY, _NUM_SLOTS,
    )
}


# ---------------------------------------------------------------------------
# 任务画像
# ---------------------------------------------------------------------------

_LOS_NLOS_SWEEP = Sweep(
    key="channel_model",
    values=["CDL-C", "CDL-D"],
    label="非视距 vs 视距",
    why="视距下信道近似低秩，很多方法会显得特别好。两者都跑才知道结论是否普遍成立。",
)

_SPEED_SWEEP = Sweep(
    key="ue_speed_kmh",
    values=[3.0, 60.0, 120.0],
    label="低速 / 中速 / 高速",
    why="信道老化随速度急剧变化，单一速度下的结论无法外推。",
)

_ANTENNA_SWEEP = Sweep(
    key="bs_antenna",
    values=["32T4R", "64T4R"],
    label="不同天线规模",
    why="维度变化会改变压缩率与码本搜索开销，只测一种规模难说方法可扩展。",
)

_LOAD_SWEEP = Sweep(
    key="prb_utilization",
    values=[0.3, 1.0],
    label="轻载 vs 满载",
    why="轻载下干扰问题被掩盖，容易低估干扰协调算法的价值。",
)

TASK_PROFILES: tuple[TaskProfile, ...] = (
    TaskProfile(
        task="csi_compression",
        label="CSI 压缩 / 反馈",
        keywords=("csi", "压缩", "反馈", "feedback", "compress", "码本量化", "自编码", "csinet"),
        design_keys=("baseline", "metric", "scope"),
        decision_keys=("channel_model", "bs_antenna", "snr_range_dB", "bandwidth_hz", "num_samples"),
        sweeps=(_LOS_NLOS_SWEEP, _ANTENNA_SWEEP),
        pitfalls=(
            "只在视距场景验证会高估压缩率——视距信道本身就低秩，压什么都好压。",
            "压缩率要跟反馈开销对齐比较：比 Type II 码本省多少比特，而不是只看重建误差。",
            "只测高信噪比容易掩盖量化噪声与信道噪声的相互作用。",
        ),
        config_hints={"link": "DL", "channel_est_mode": "ls_linear"},
    ),
    TaskProfile(
        task="beam_management",
        label="波束管理 / 波束搜索",
        keywords=("波束", "beam", "赋形", "beamforming", "波束搜索", "扫描", "码本搜索", "波束跟踪"),
        design_keys=("baseline", "metric"),
        decision_keys=("channel_model", "bs_antenna", "ue_speed_kmh", "scenario", "num_samples"),
        sweeps=(_LOS_NLOS_SWEEP, _SPEED_SWEEP),
        pitfalls=(
            "TDL 模型没有每条径的角度，波束类算法在它上面跑出的结果没有物理意义。",
            "只用视距信道验证波束搜索，会严重高估命中率——非视距下最强径未必对着用户。",
            "搜索开销要跟穷举比较，只报命中率不报搜索次数说明不了问题。",
        ),
        config_hints={"link": "DL"},
        guards=("require_cdl",),
    ),
    TaskProfile(
        task="channel_estimation",
        label="信道估计 / 插值 / 降噪",
        keywords=("信道估计", "估计", "插值", "estimation", "interpolat", "降噪", "去噪", "denois"),
        design_keys=("baseline", "metric"),
        decision_keys=("channel_est_mode", "pilot_type", "snr_range_dB", "ue_speed_kmh", "channel_model"),
        sweeps=(_SPEED_SWEEP,),
        pitfalls=(
            "选 ideal 模式就没有估计误差可分析了——理想信道会作为真值一并给出，选实际估计不损失参照。",
            "低信噪比区间才是估计算法拉开差距的地方，只测高信噪比看不出优劣。",
            "导频密度和插值方法要一起说，否则 NMSE 不可比。",
        ),
        config_hints={},
    ),
    TaskProfile(
        task="positioning",
        label="定位 / 时延估计",
        keywords=("定位", "position", "toa", "时延估计", "测距", "localization", "tdoa", "aoa估计"),
        design_keys=("baseline", "metric", "scope"),
        decision_keys=("bandwidth_hz", "channel_model", "scenario", "num_sites", "num_samples"),
        sweeps=(_LOS_NLOS_SWEEP,),
        pitfalls=(
            "时延分辨率约等于光速除以带宽——100 MHz 只有 3 米，定位精度先被带宽卡死。",
            "非视距下首径未必是直达径，视距比例直接决定定位误差分布，必须报告。",
            "单站只能测角，多站才能交汇定位。",
        ),
        config_hints={},
        guards=("require_cdl",),
    ),
    TaskProfile(
        task="precoding",
        label="预编码 / 码本设计",
        keywords=("预编码", "precod", "码本", "codebook", "pmi", "svd", "波束成形权", "mu-mimo配对"),
        design_keys=("baseline", "metric"),
        decision_keys=("channel_model", "bs_antenna", "snr_range_dB", "channel_est_mode", "num_samples"),
        sweeps=(_LOS_NLOS_SWEEP, _ANTENNA_SWEEP),
        pitfalls=(
            "拿理想信道算预编码增益是上界，实际系统用的是估计信道，两者差距可能很大。",
            "码本方案要跟 SVD 理想预编码比，才知道量化损失有多少。",
        ),
        config_hints={"link": "DL"},
    ),
    TaskProfile(
        task="interference",
        label="干扰协调 / 调度",
        keywords=("干扰", "interference", "协调", "调度", "schedul", "comp", "协作", "icic", "功控"),
        design_keys=("baseline", "metric", "scope"),
        decision_keys=("num_sites", "ue_distribution", "prb_utilization", "tdd_pattern", "num_samples"),
        sweeps=(_LOAD_SWEEP,),
        pitfalls=(
            "轻载场景下干扰问题被掩盖，容易得出「算法没用」的结论。",
            "边缘用户（5% 分位）才是干扰协调的目标，只看平均吞吐看不出效果。",
            "单小区场景没有干扰源，这类课题必须多站。",
        ),
        # ChannelHub 默认不保存干扰小区的信道矩阵（只把干扰体现在 SINR 里），
        # 但干扰协调算法必须拿到干扰信道本身，所以这里显式打开。
        # 代价：数据量按干扰小区数翻倍。
        config_hints={"link": "DL", "measurements": {"interferer_channels": True}},
        guards=("require_multicell",),
    ),
    TaskProfile(
        task="mobility",
        label="移动性 / 切换",
        keywords=("切换", "handover", "移动性", "mobility", "轨迹", "重选", "乒乓"),
        design_keys=("baseline", "metric"),
        decision_keys=("ue_speed_kmh", "mobility_mode", "num_sites", "ue_distribution", "num_samples"),
        sweeps=(_SPEED_SWEEP,),
        pitfalls=(
            "静止模型下样本之间没有时序关系，切换判决无从谈起——必须选移动模型。",
            "乒乓切换要看连续轨迹，单快照数据分析不出来。",
        ),
        config_hints={"mobility_mode": "linear"},
        guards=("require_multicell", "require_trajectory"),
    ),
    TaskProfile(
        task="reciprocity",
        label="上下行互易性 / SRS 老化",
        keywords=("互易", "reciprocity", "srs", "老化", "aging", "上下行", "srs跳频"),
        design_keys=("baseline", "metric"),
        decision_keys=("ue_speed_kmh", "tdd_pattern", "channel_est_mode", "channel_model", "num_samples"),
        sweeps=(_SPEED_SWEEP,),
        pitfalls=(
            "速度低于 10 km/h 时信道几乎不老化，看不出任何差异。",
            "TDD 配比决定 SRS 间隔，是老化程度的另一个关键变量，别只扫速度。",
        ),
        config_hints={"link": "both", "pilot_type": "srs_zc"},
    ),
    TaskProfile(
        task="channel_prediction",
        label="信道预测 / 时序建模",
        keywords=("预测", "predict", "时序", "外推", "extrapolat", "信道跟踪", "lstm", "时间相关"),
        design_keys=("baseline", "metric", "scope"),
        decision_keys=("num_slots_per_sample", "ue_speed_kmh", "channel_model", "bs_antenna", "num_samples"),
        sweeps=(_SPEED_SWEEP,),
        pitfalls=(
            "默认每样本只有 1 个时隙，样本之间独立，根本没有时序可预测——必须调大连续时隙数。",
            "预测增益要跟「直接用上一时刻的信道」这个平凡基线比，否则容易自我感觉良好。",
            "多普勒决定可预测的时间跨度，速度不同结论完全不同。",
        ),
        config_hints={"num_slots_per_sample": 14, "mobility_mode": "linear"},
        guards=("require_multislot",),
    ),
    TaskProfile(
        task="link_adaptation",
        label="链路自适应 / CQI / MCS 选择",
        keywords=("链路自适应", "cqi", "mcs", "调制编码", "自适应", "link adaptation", "bler", "外环"),
        design_keys=("baseline", "metric"),
        decision_keys=("snr_range_dB", "channel_est_mode", "ue_speed_kmh", "prb_utilization", "num_samples"),
        sweeps=(_SPEED_SWEEP, _LOAD_SWEEP),
        pitfalls=(
            "CQI 上报到实际调度之间有延迟，静止场景下这个延迟无害，高速下才是主要误差源。",
            "只看平均吞吐会掩盖误块率的波动，两个指标要一起报。",
        ),
        config_hints={"link": "DL"},
    ),
    TaskProfile(
        task="channel_charting",
        label="信道表征 / 嵌入学习",
        keywords=("表征", "embedding", "charting", "嵌入", "自监督", "对比学习", "mae", "预训练"),
        design_keys=("baseline", "metric", "scope"),
        decision_keys=("channel_model", "bs_antenna", "scenario", "ue_distribution", "num_samples"),
        sweeps=(_LOS_NLOS_SWEEP,),
        pitfalls=(
            "表征学习需要的样本量比常规仿真大一个量级，先估算好数据体积。",
            "如果要跟位置对齐评估（如信道图），必须保留 UE 坐标——geometry 里有。",
            "ChannelHub 本身有一套 16-token 特征是为 MAE 服务的，"
            "本项目给的是物理量，两者不要混用。",
        ),
        config_hints={},
    ),
    TaskProfile(
        task="generic",
        label="通用信道仿真",
        keywords=(),
        design_keys=("metric",),
        decision_keys=("scenario", "channel_model", "bs_antenna", "snr_range_dB", "num_samples"),
        sweeps=(),
        pitfalls=(),
        config_hints={},
    ),
)


def classify_intent(intent: str) -> TaskProfile:
    """从自然语言意图识别任务类型。命中最多关键词者胜，都不命中走通用。"""
    text = (intent or "").lower()
    best: TaskProfile | None = None
    best_hits = 0
    for prof in TASK_PROFILES:
        hits = sum(1 for kw in prof.keywords if kw in text)
        if hits > best_hits:
            best, best_hits = prof, hits
    return best or TASK_PROFILES[-1]


def decisions_for(profile: TaskProfile, *, limit: int = 6) -> list[Decision]:
    """取该任务应当询问的仿真参数，按优先级排序，最多 limit 个。

    上限默认 6：一次抛八个问题，用户会直接说「你看着办」。
    """
    picked = [ALL_DECISIONS[k] for k in profile.decision_keys if k in ALL_DECISIONS]
    picked.sort(key=lambda d: d.priority)
    return picked[:limit]


def design_questions_for(profile: TaskProfile, *, limit: int = 3) -> list[DesignQuestion]:
    """取实验设计层的问题。必答的排在前面。"""
    picked = [_DESIGN[k] for k in profile.design_keys if k in _DESIGN]
    picked.sort(key=lambda q: (q.optional,))
    return picked[:limit]


def also_configurable(profile: TaskProfile) -> list[str]:
    """没被问到、但用户可以主动提的参数——只给名字，不展开。

    这解决的是「你不知道你能要什么」：不列出来用户想不到还能调移动速度，
    全展开又会淹没那几个真正关键的。
    """
    asked = set(profile.decision_keys)
    labels = {
        "scenario": "传播场景", "channel_model": "信道模型", "bs_antenna": "天线配置",
        "snr_range_dB": "信噪比范围", "bandwidth_hz": "带宽", "ue_speed_kmh": "移动速度",
        "channel_est_mode": "信道估计方式", "pilot_type": "导频类型", "link": "上下行",
        "tdd_pattern": "TDD配比", "num_sites": "站点数", "ue_distribution": "撒点方式",
        "prb_utilization": "邻区负载", "mobility_mode": "移动模型", "num_samples": "样本数",
        "num_slots_per_sample": "连续时隙数",
    }
    extra = [
        "载波频率", "终端天线数", "用户数", "站间距", "发射功率",
        "噪声系数", "SRS跳频", "干扰用户数", "随机种子",
    ]
    return [v for k, v in labels.items() if k not in asked] + extra


def sweep_suggestions(profile: TaskProfile) -> list[dict[str, Any]]:
    """建议的对比维度。用户想让结论站得住时，一次生成多组比事后补跑省事。"""
    return [s.as_dict() for s in profile.sweeps]


# ---------------------------------------------------------------------------
# 体检拦截
# ---------------------------------------------------------------------------


def check_guards(profile: TaskProfile, cfg: dict[str, Any]) -> list[dict[str, str]]:
    """检查参数组合在物理上是否讲得通。

    返回问题清单；空列表表示没问题。这里拦的是「跑得出结果但结果没意义」
    的组合——比报错更危险，因为数值看起来完全正常。
    """
    issues: list[dict[str, str]] = []

    model = str(cfg.get("channel_model", "")).upper()
    if "require_cdl" in profile.guards and model.startswith("TDL"):
        issues.append(
            {
                "severity": "block",
                "key": "channel_model",
                "message": (
                    f"{profile.label} 依赖每条径的角度信息，但 TDL 模型不含角度"
                    "（ChannelHub 内部即以此区分两类模型）。算法会跑出结果，"
                    "但那些结果没有物理意义，而且不会报错。"
                ),
                "suggestion": "改用 CDL-A~E 中的任一个（默认 CDL-C）",
            }
        )

    n_sites = int(cfg.get("num_sites", 1) or 1)
    if "require_multicell" in profile.guards and n_sites <= 1:
        issues.append(
            {
                "severity": "block",
                "key": "num_sites",
                "message": f"{profile.label} 需要小区间干扰，单站场景下没有干扰源。",
                "suggestion": "站点数设为 7（21 小区）或 19（57 小区）",
            }
        )

    n_slots = int(cfg.get("num_slots_per_sample", 1) or 1)
    if "require_multislot" in profile.guards and n_slots <= 1:
        issues.append(
            {
                "severity": "block",
                "key": "num_slots_per_sample",
                "message": (
                    f"{profile.label} 需要样本内部有时间相关性，"
                    "但每样本只取 1 个时隙时各样本相互独立，没有可预测的时序结构。"
                ),
                "suggestion": "连续时隙数设为 14（覆盖一个 TDD 周期以上）",
            }
        )

    mob = str(cfg.get("mobility_mode", "static"))
    if "require_trajectory" in profile.guards and mob == "static":
        issues.append(
            {
                "severity": "warn",
                "key": "mobility_mode",
                "message": "静止模型下用户不移动，切换/轨迹类分析没有对象。",
                "suggestion": "改用 linear 或 random_waypoint",
            }
        )

    speed = float(cfg.get("ue_speed_kmh", 3.0) or 3.0)
    if profile.task in ("reciprocity", "channel_prediction") and speed < 10.0:
        issues.append(
            {
                "severity": "warn",
                "key": "ue_speed_kmh",
                "message": f"速度 {speed:g} km/h 下信道几乎不老化，{profile.label} 看不出差异。",
                "suggestion": "建议 60 km/h 以上，或扫一组速度做对比",
            }
        )

    bw = float(cfg.get("bandwidth_hz", 100e6) or 100e6)
    if profile.task == "positioning" and bw < 50e6:
        issues.append(
            {
                "severity": "warn",
                "key": "bandwidth_hz",
                "message": (
                    f"带宽 {bw/1e6:.0f} MHz 对应时延分辨率约 "
                    f"{3e8/bw:.1f} 米，定位精度会被带宽卡住。"
                ),
                "suggestion": "定位类课题建议 100 MHz 以上",
            }
        )

    est = str(cfg.get("channel_est_mode", ""))
    if profile.task == "channel_estimation" and est == "ideal":
        issues.append(
            {
                "severity": "warn",
                "key": "channel_est_mode",
                "message": "理想信道没有估计误差，信道估计算法无从对比。",
                "suggestion": "改用 ls_linear 或 ls_mmse；理想信道会作为真值一并给出",
            }
        )

    if profile.task == "reciprocity" and str(cfg.get("link", "DL")) != "both":
        issues.append(
            {
                "severity": "warn",
                "key": "link",
                "message": "互易性课题需要成对的上下行信道，只取单向无法对比。",
                "suggestion": 'link 设为 "both"',
            }
        )

    return issues
