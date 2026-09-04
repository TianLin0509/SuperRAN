"""Curated detailed-reading layers for the SuperRAN developer guide.

The compact chapter remains the source of truth for the quick reading path.
Each entry below adds a second teaching layer: mental model, implementation
trace, worked example, verification contract and common mistakes.  Keeping
this material structured makes the UI and coverage tests uniform without
turning the detailed edition into repeated filler.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class DetailSpec:
    """One chapter's deeper, implementation-oriented reading material."""

    promise: str
    principles: tuple[str, ...]
    implementation: tuple[tuple[str, str], ...]
    example_title: str
    example: str
    checks: tuple[tuple[str, str], ...]
    pitfalls: tuple[str, ...]
    source_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class FormulaSpec:
    """Human explanation placed next to one rendered mathematical formula."""

    title: str
    meaning: str
    symbols: tuple[tuple[str, str], ...]


def render_formula(name: str, formula_html: str) -> str:
    """Wrap a KaTeX/MathML expression with detailed-edition semantics."""

    try:
        spec = FORMULA_SPECS[name]
    except KeyError as exc:
        raise KeyError(f"formula {name!r} has no symbol explanation") from exc
    symbols = "".join(
        f"<div><dt>{symbol}</dt><dd>{meaning}</dd></div>"
        for symbol, meaning in spec.symbols
    )
    return (
        f'<figure class="formula-card" data-formula="{escape(name)}">'
        f'<div class="formula-expression">{formula_html}</div>'
        '<figcaption class="formula-explain">'
        f'<strong>{spec.title}</strong><p>{spec.meaning}</p>'
        f'<dl class="symbol-list">{symbols}</dl>'
        "</figcaption></figure>"
    )


def _paragraphs(items: tuple[str, ...]) -> str:
    return "".join(f"<p>{item}</p>" for item in items)


def render_detail(key: str, title: str) -> str:
    """Render one detailed layer; fail loudly when a chapter is uncovered."""

    try:
        spec = DETAIL_SPECS[key]
    except KeyError as exc:  # documentation coverage is a build contract
        raise KeyError(f"chapter {key!r} has no detailed-reading content") from exc
    trace = "".join(
        f'<li><span class="step-no">{index}</span><div><strong>{name}</strong><p>{body}</p></div></li>'
        for index, (name, body) in enumerate(spec.implementation, 1)
    )
    checks = "".join(
        f'<li><span>✓</span><div><strong>{name}</strong><p>{body}</p></div></li>'
        for name, body in spec.checks
    )
    pitfalls = "".join(f"<li>{item}</li>" for item in spec.pitfalls)
    sources = "".join(f"<code>{escape(path)}</code>" for path in spec.source_paths)
    source_html = (
        '<div class="detail-sources"><strong>建议沿源码继续读</strong>' + sources + "</div>"
        if sources else ""
    )
    return (
        f'<section class="detail-content" data-detail-for="{escape(key)}" '
        f'aria-label="{escape(title)}详细版">'
        '<div class="detail-opening"><span>DETAILED EDITION</span>'
        f'<h2>深入理解：{escape(title)}</h2><p>{spec.promise}</p></div>'
        '<h2>从直觉到工程模型</h2>' + _paragraphs(spec.principles)
        + '<h2>沿真实实现走一遍</h2><ol class="steps detail-trace">' + trace + "</ol>"
        + f'<aside class="worked-example"><span>WORKED EXAMPLE</span><h2>{spec.example_title}</h2>'
        f'<div>{spec.example}</div></aside>'
        + '<h2>怎样证明这一章的实现没有走偏</h2><ul class="detail-checks">' + checks + "</ul>"
        + '<aside class="callout warn detail-pitfalls"><span class="callout-icon">!</span>'
        '<div><strong>常见误区与边界</strong><ul>' + pitfalls + "</ul></div></aside>"
        + source_html + "</section>"
    )


DETAIL_SPECS: dict[str, DetailSpec] = {
    "overview": DetailSpec(
        promise="把 SuperRAN 看成一条把问题编译成可复核证据的流水线，而不是一组彼此无关的无线算法。读完后应能判断一个结果究竟来自物理信道、系统调度，还是统计与呈现层。",
        principles=(
            "平台的窄腰是<strong>数据合同与证据合同</strong>。上游默认是本仓 first-party source，可选接 direct Sionna RT；下游可以换预编码、接收机、调度器与 KPI，但中间必须始终说清 <code>h_true</code>、<code>h_est</code>、功率参考面、随机种子、样本单位和统计窗口。只要这些角色没有混在一起，同一实验才有可能复现；一旦把估计信道偷换成真值，后续再漂亮的曲线也失去解释力。",
            "一次可信实验同时包含三条链。<strong>物理链</strong>回答信号如何经过阵列、传播和干扰；<strong>决策链</strong>回答 gNB 在当时信息下如何选 rank、MCS、SU/MU 与资源；<strong>证据链</strong>回答样本是否独立、比较是否配对、统计是否跨过预热窗口。三条链最终在 manifest、逐样本结果和 Gate 报告中会合，结论才不仅是一次脚本输出。",
            "容量模式与体验模式分别回答“持续有数据时空口能做多快”和“有限业务到达后用户实际等多久、拿到多少有效字节”。前者可以全带调度并关注谱效，后者必须模拟 FIFO、空闲 TTI、按需 RBG、首包等待和尾料。两种模式共用物理输入，却不能共享一套含糊的 KPI 口径。",
            "交互配置 Mock 和 KPI 工作台是这三条链面向用户的两个检查点。前者在运行前把 resolved config 画成拓扑、阵列、频时资源、PDP 与算法选择，并把用户修改作为受限 delta 回到 Draft；后者在运行后只读取 Result contract，把小区、用户、CDF、资源和告警按本轮意图排序。一个负责防止“看见的配置和执行的配置不同”，另一个负责防止“平均值遮住边缘用户和口径限制”。它们不是宣传截图，而是实验合同的可视化接口。",
        ),
        implementation=(
            ("发现能力", "<code>sr_capabilities</code> 先报告可用数据源、可选物理后端和依赖状态。计划阶段据此决定能做什么，不能把缺失后端静默替换成低保真模型。"),
            ("冻结实验", "<code>sr_plan</code> 与场景/算法配置把问题转成显式合同：基线、主指标、控制变量、样本单位和停止条件都在生成之前确定。"),
            ("用说明书做运行前复核", "<code>sr_spec_sheet</code> 从同一 Draft 生成交互 HTML；用户指定值、系统补全值和实际拓扑均可见。页面只开放白名单字段，回传 delta 仍需 <code>sr_revise</code> 形成新的 resolved config。"),
            ("生成并体检", "生成器写出真值、估计值、元数据和 lineage；Gate 1 检查形状、单位、有限性、路径损耗、干扰与样本合同。失败时停在数据层，而不是继续跑算法。"),
            ("比较并发布", "算法在相同样本上运行，Gate 2/3 检查配对统计、置信区间、效应量、覆盖范围与限制。交付物带着配置摘要和证据路径，而不只是一个百分比。"),
            ("用工作台做运行后解释", "体验模式把既有 Result 写成 KPI HTML：Agent 可前置本轮相关指标，但所有小区/用户 KPI、置信区间、CDF、资源对账、告警和排序理由仍保留，页面不重新计算一套更有利的数。"),
        ),
        example_title="一个 SRS 权对比 PMI 权实验为什么需要两张界面",
        example=(
            "<p>运行前，说明书把 company_64t4r 单小区入门场景的 64T4R 阵列、pol-h-v 端口顺序、ls_mmse、SRS 周期和逐 RB 比较口径放在一页。用户若把估计模式改成 ideal，页面只回传这一个 delta；Agent 必须复述并重新生成说明书。否则一臂使用真值、一臂使用估计值的比较虽然能跑，却不再回答“SRS 权相较于 PMI 权”的问题。21 小区 company_64t4r_multicell 是单独预注册、单独过门的进阶压力实验，不能冒充 Hello World。</p>"
            "<p>运行后，配对结果先给 Gate 2/3 与逐样本谱效；若继续跑 mixed 体验仿真，KPI 工作台再给含头/掐头速率、首包覆盖率、0..17 RBG 分布和用户 CDF。页面上任何“收益”都必须能追到 comparison statement 或带 CI 的 Result；工作台不能从图形差异自行创造第二个结论。</p>"
        ),
        checks=(
            ("角色可追溯", "每个结果能反查设计 CSI、评估真值、场景、配置哈希与随机流。"),
            ("比较只改一件事", "A/B 使用相同 drop、traffic、BLER 和 scheduler 随机流，差异项在 manifest 中可见。"),
            ("模式口径明确", "报告显式标注 capacity/legacy_v1 或 experience/experience_v2，KPI 不跨模式偷换。"),
            ("结论受 Gate 约束", "任何提升数字都能对应通过的 Gate 2/3 记录、样本量与适用边界。"),
            ("界面身份一致", "说明书关键字段与 manifest 一致；KPI 工作台数值与 Result JSON 一致，排序前后只改变位置。"),
        ),
        pitfalls=(
            "把 MCP 工具调用成功当成物理正确；工具可运行只说明接口通，不说明数据合同和统计口径成立。",
            "用一张平均曲线遮住用户级离群点、失败样本或覆盖率不足。",
            "在看到结果后再改主指标、删异常点或选择最有利的随机种子。",
            "把交互 Mock 当成静态宣传页，或让 KPI 页面在 Result 之外临时重算一个更好看的口径。",
        ),
        source_paths=("src/superran/server.py", "src/superran/spec.py", "src/superran/bridge.py", "src/superran/generate.py", "src/superran/gates.py", "src/superran/results.py", "src/superran/kpi_view.py"),
    ),
    "quickstart": DetailSpec(
        promise="解释从一个干净 Python 环境到第一份可发布实验之间每一步真正建立了什么合同，并给出出现环境、数据或统计问题时应从哪里断点检查。",
        principles=(
            "安装并不等于把所有可选后端都装上。基础包自带统计信道、MCP 与分析能力；射线追踪 direct adapter 是独立可探测能力。正确流程是先运行能力发现，而不是在导入失败后偷偷接另一个源码树。",
            "MCP 的 stdio 进程只是协议边界。Agent 发送结构化参数，服务端返回数据集或结果标识、摘要和产物路径；大型数组留在磁盘合同里。绝对 Python 路径与项目根决定进程加载哪份 SuperRAN，历史外部 source-root 环境变量不能改变实现。",
            "第一次实验的目标不是追求规模，而是闭合一条最小证据链：能力可见、配置可解析、数据可生成、Gate 1 可解释、一个算法可运行、结果可回指输入。这个闭环成功后再扩大 UE、drop、snapshot 和 replication；否则大规模运行只会把同一个配置错误复制更多遍。新环境还应先用不存在的外部根做反向检查，证明实现选择没有暗门。",
            "重命名兼容必须是单向、临时且可观察的。<code>_compat.py</code> 只在对应 <code>SUPERRAN_*</code> 新键缺失时，把仍有效的七个数据/UI后缀复制到当前进程；外部 source-root 后缀明确排除。它不覆盖新值、不修改宿主配置文件，也不兼容旧 Python 包名。",
            "SRS 权与 PMI 权的 Hello World 必须拆成两张表。<strong>机制诊断</strong>让两臂共享同一 <code>dataset_id</code>、同一 UL-SRS <code>h_est</code>、同一 <code>h_true</code>、同一接收机与逐样本工作点，只把 <code>method</code> 从 <code>svd</code> 改为 <code>type1</code>，隔离连续方向与有限码本的构造损失；<strong>主实验</strong>按真实信息链比较 UL-SRS/SVD 与 DL-CSI-RS/PMI，并把 <code>varies=[csi,method]</code> 写进合同。两者不能混称为“只改权”，当前 PMI 也只是 Type-I-style 列码本近似，不代表已经覆盖 38.214 Type-I 的全部 RI/PMI/子带限制。",
            "主实验里的两个 CSI 名称对应两条不同的可实现链。SRS 臂从上行估计出发，first-party v2 的 canonical UL 张量按 transpose-only 互易合同直接映射回下行约定，再由 gNB 计算逐 RBG 协方差/SVD 权；历史 v1 数据才显式共轭。PMI 臂由 UE 在 DL CSI-RS 估计上搜索 Type-I-style 码本并反馈索引。两臂都在同一 <code>h_true</code> 与 MMSE 接收机上评价。",
            "Hello World 的价值还在于演示失败如何被保存。当前 80 条观测来自 10 个 UE 位置、每个位置 8 个快照；推断必须先按位置取簇均值，不能把 80 行当作 80 个独立用户。历史上静态位置生成 bug 曾让所有样本落在同一点，把重复快照误报成大样本并产生漂亮的点估计；修复后 Gate 1 会检查位置覆盖，Gate 2 会显式写出 80→10 的推断单位，Gate 3 再根据 CI 与 Wilcoxon 判决。脚本退出码 0 表示主实验三道门均通过，2 表示数据门阻塞，3 表示数据和公平性可用但方向性结论被拦截；三种状态都会先写证据文件，因此退出码 3 不是“运行失败后什么也没留下”。",
        ),
        implementation=(
            ("建立隔离环境", "使用项目自己的虚拟环境安装 editable 包，使源码改动立即生效，同时避免系统 Python 中的 NumPy、MCP 或 Sionna 版本污染结果。"),
            ("审计命名迁移", "启动时调用 <code>migrate_legacy_environment()</code>；仅复制缺失的新键，<code>legacy_environment_audit()</code> 返回本进程实际迁移项。宿主配置应随后改成 SUPERRAN_*。"),
            ("核对真实启动链", "直接运行 <code>python -m superran.server</code>，再核对 Agent 配置里的 command、args 和环境变量。命令行可用但宿主不可用时，优先比较两条启动链。"),
            ("生成运行前说明书", "用 Draft 调 <code>sr_spec_sheet</code>，把 url 交给用户核对。若页面返回 delta，必须先复述、<code>sr_revise</code> 并重新出说明书，不能拿旧 draft 继续生成。"),
            ("先跑小样本", "用少量 sample 验证数据字段、h_true/h_est 角色、metadata 和 Gate 1；保存 dataset_id 后再把同一数据交给不同算法。"),
            ("执行两层配对 Hello World", "先用 <code>csi=srs</code> 的 SVD/type1 机制诊断隔离码本损失；再用 <code>csi=srs,method=svd</code> 对 <code>csi=csirs,method=type1</code> 做预注册主实验，并显式声明 <code>varies=[csi,method]</code>。两张表都读取 <code>passed</code>、paired、gate_comparison、gate_conclusion 与 statement，而不是只截均值。"),
            ("按需进入体验工作台", "同一 dataset 可交给 experience_v2；设置 warmup 后生成 KPI 页面。<code>kpi_view.url</code> 不可用时使用 UTF-8 <code>html_path</code>，并保留 serve_error。"),
            ("分层放大", "先增加 snapshot 与 UE，再增加 drop/replication，最后才打开重型后端和长系统仿真。每次只扩大一个维度，便于定位复杂度与内存增长。"),
        ),
        example_title="从一条意图到一次被 Gate 正确拦住的 SRS/PMI 实验",
        example=(
            "<p><code>sr_plan</code> 用 company_64t4r 建 Draft，说明书首屏应明确 ls_mmse、64T4R、pol-h-v 和本次 SRS/PMI 关注项。随后 <code>sr_generate(draft_id=..., num_samples=80)</code> 生成 10 UE×8 snapshots 的一次数据并通过 Gate 1；旧示例若丢掉 draft_id、只传 preset，就会绕过用户在说明书中确认的差分，这种调用虽然语法可用，却破坏闭环。多小区实验必须另建 Draft 和预注册。</p>"
            "<p>正式样例 <code>ds_312bd664</code> 有 80 条快照，但按 UE 位置聚类后只有 10 个独立配对。主实验中 UL-SRS/SVD 为 34.005、DL-CSI-RS/PMI 为 33.765 bit/s/Hz，点估计 +0.7%，但 95% CI 为 [−5.622,+6.103]、Wilcoxon p=0.846，因此 Gate 3 判定<strong>结论不成立</strong>。同 SRS 机制诊断的点估计虽为 +15.9%，CI 仍跨零，也不能升级成收益结论。这个结果展示的是平台能阻止伪阳性；之后可选的 mixed 体验仿真与 KPI 页面不能反过来替代链路级配对判决。</p>"
        ),
        checks=(
            ("解释器唯一", "终端和 MCP 宿主报告同一 Python 可执行文件与同一 editable 安装。"),
            ("能力不降级", "缺失可选后端时 capability 明示 unavailable；计划中也能看到替代方案或阻塞原因。"),
            ("兼容不覆盖", "同时设置旧键和新键时新键逐值胜出；重复迁移不重复记录，审计只包含确实复制的数据/UI白名单键且不含 source root。"),
            ("最小闭环", "小数据集可加载、Gate 1 通过、算法结果包含 dataset/config lineage。"),
            ("Hello World 身份不混淆", "机制诊断仅 method 不同且标为 exploratory；主实验共享 dataset/h_true/receiver/工作点，显式声明 csi+method 两项差异并绑定预注册主指标。"),
            ("两张页面可交付", "spec 与 KPI HTML 均为 UTF-8 自包含文件；url 降级、回传模式、排序证据和源 Result 可追溯。"),
            ("放大可估算", "正式运行前能从样本 shape 估算磁盘、内存和运行时间，不以盲跑代替规划。"),
        ),
        pitfalls=(
            "照抄另一台机器的虚拟环境绝对路径，导致 Agent 实际运行旧包。",
            "第一次就跑数千 UE/drop，直到数小时后才发现配置字段或单位错误。",
            "把可选物理后端缺失当成普通 warning，随后仍用高保真措辞描述轻量数据。",
            "依赖旧环境变量能继续运行，就误以为旧包名、CLI、MCP 名称或数据 schema 也受长期兼容。",
            "一臂用 ideal CSI、一臂用 estimated CSI，或为两臂分别重新生成信道，再把差值解释成 PMI 量化损失。",
        ),
        source_paths=("pyproject.toml", "src/superran/_compat.py", "src/superran/channelhub.py", "src/superran/server.py", "src/superran/spec.py", "src/superran/linklevel.py", "src/superran/gates.py", "src/superran/kpi_view.py", "src/superran/paths.py"),
    ),
    "architecture": DetailSpec(
        promise="从层间不变量而不是文件列表理解架构：什么信息可以跨层流动、什么必须隔离，以及为何 h_true/h_est 和 symbol/snapshot/TTI 是最容易造成可信度事故的两个边界。",
        principles=(
            "五层架构的价值在于让错误尽早暴露。编排层可以表达“比较两种预编码”，但不能决定缺失估计信道时用真值顶上；算法层可以计算权和 SINR，但不能修改样本抽样规则；呈现层可以优先展示 KPI，却不能重新计算一套更有利的口径。每层只拥有自己的决策权，跨层数据通过 dataclass、JSON/YAML 和落盘数组的显式合同传递。",
            "<code>h_true</code> 与 <code>h_est</code> 是一次实验中的两个观察者。前者代表自然界实际发生的下行传播，用来评估接收信号；后者代表基站在 SRS、噪声、插值和处理时延之后知道的东西，用来设计预编码与调度。即使两者 shape 完全相同，也不能互换。理想 CSI 基线必须显式命名为 ideal，而不能靠数组相等暗中实现。",
            "时间也有三层语义。14 个 OFDM symbol 可以用于导频映射与信道估计，一个 channel snapshot 表示某个时刻可复用的物理状态，一个 TTI 则驱动队列、调度、BLER、OLLA 和 KPI。新数据把 <code>sample_interval_s</code> 独立落盘，不能从slot、SRS双腿或报告周期反推；系统仿真在每个 TTI 引用一个 snapshot 并不会抹掉 symbol 级估计。",
        ),
        implementation=(
            ("编排形成 Spec", "自然语言意图被解析为场景、数据源、算法、KPI、随机策略和 Gate 约束；未决项留在 decisions，而不是被默认值悄悄覆盖。"),
            ("生成形成 Dataset", "first-party source 生成并统一轴顺序、dtype、单位与角色，写入真值、估计值及 metadata；可选 direct adapter 必须经过同一合同。"),
            ("算法形成 Result", "预编码只读取设计 CSI，接收评估读取真值；系统层消费链路表并产生逐 TTI 分配、用户和小区统计。"),
            ("证据形成 Conclusion", "验证模块检查数据，分析模块进行配对统计，结果合同把配置、样本、算法版本、限制和图表绑定到一起。"),
        ),
        example_title="估计源缺失时为何必须硬失败",
        example=(
            "<p>一个 TDD 实验要求用 UL SRS 估计设计 DL 权。若输入样本只有 <code>h_dl_true</code>，最方便的做法是复制一份命名为 <code>h_est</code>；这样 NMSE 变为零、波束看似完美，性能会系统性偏高，而且结果表面仍“字段齐全”。</p>"
            "<p>当前合同要求适配层辨认估计来源并在缺失时失败。要做理想上界，应把方法明确配置为 <code>ideal</code>，在 metadata 中记录 oracle 假设。两种结果可以同时存在，但读者能知道一个是可实现链路，一个是上界。</p>"
        ),
        checks=(
            ("轴与单位", "数组每一维、复数 dtype、功率单位、时间和频率采样间隔都有 manifest 描述。"),
            ("信息隔离", "预编码入口只接收 h_est，评价入口接收 h_true；测试能通过故意交换两者触发失败。"),
            ("时间映射", "symbol 到 snapshot 的选择、snapshot 更新周期和 TTI 引用规则分别记录，名称不混用。"),
            ("可替换后端", "更换数据源后仍输出相同窄腰合同；不能输出的能力显式 unavailable。"),
        ),
        pitfalls=(
            "把层次架构理解成目录美化，随后在呈现层重新计算 KPI 或在 server 中塞物理公式。",
            "看到 h_true/h_est 同形就认为可互换，忽略它们代表不同信息集。",
            "把 SRS 周期、CSI 处理时延、snapshot 周期和 TTI 时长统称为“信道年龄”。",
        ),
        source_paths=("src/superran/spec.py", "src/superran/generate.py", "src/superran/channelhub.py", "src/superran/results.py"),
    ),
    "hardware": DetailSpec(
        promise="把 64T/256T、物理阵元、RF 端口、展平索引、载波与电下倾放进同一张硬件合同中，理解一个配置值如何一路改变 F 矩阵、端口信道和最终链路结果。",
        principles=(
            "“64T”或“256T”首先是 RF 端口数，不等于物理辐射阵元数。预置 64T 面板按两种极化、水平和垂直端口组织，每个 RF 端口再驱动 3 个垂直阵元，因此物理阵元侧是 192；256T 场景每端口驱动 6 个垂直阵元，物理阵元侧是 1536。<code>F</code> 就是把这些物理阵元电压映射到 RF 端口激励的稀疏馈电矩阵。",
            "统一展平顺序是 <code>r(p,h,v)=p·(N_hN_v)+h·N_v+v</code>：先分极化块，再水平，再垂直，垂直索引从面板顶部到下部。它是内存和接口合同，不改变阵元的真实坐标。Type-I 码本有自己的行序，边界函数显式生成 permutation；绝不能通过翻转几何坐标来“修复”编号差异，否则电下倾方向也会跟着被翻转。",
            "载波默认值先经过标准表和硬件默认解析，再进入场景。100 MHz、30 kHz SCS 对应 273 RB 才是标准栅格；项目中的 272 是系统级按 17 个 RBG、每组 16 RB 对齐后的可调度部分。剩余标准 RB 没有消失，而是被明确排除在当前调度网格之外。这个区别会影响噪声带宽、总功率到每 RB 的换算和资源利用率分母。",
            "6° 电下倾不是自然常数，而是预置面板当前默认馈电校准。用户可配置其他角度；角度进入每个垂直子阵的复馈电权，因而不同下倾必须产生不同 <code>calibration_id</code>。正值表示把主瓣压向水平面以下，top-to-bottom 只决定编号，不得参与符号翻转。",
        ),
        implementation=(
            ("解析硬件 profile", "<code>company_antenna_block</code> 根据 64T/256T 选择端口布局、每端口阵元数、间距、极化、方向图和默认下倾，并把临时参数化方向图标成非实测。"),
            ("建立索引合同", "<code>port_flat_index</code> 统一新模块的 pol-h-v 顺序；历史 h-v-pol 只留在显式兼容边界，<code>type1_to_port_permutation</code> 负责码本重排。"),
            ("构造物理耦合", "SuperRAN native.EffectiveArray 在物理阵元坐标上计算阵列响应，再用每端口垂直子阵馈电权填充稀疏 F。64T 为 192×64，256T 为 1536×256。"),
            ("压到系统入口", "<code>H_port = H_AE F</code> 把阵元级传播压成端口级 MIMO 信道；后续预编码、SRS 与码本只看到稳定端口顺序。"),
        ),
        example_title="从 (p,h,v) 找到 256T 的端口和六个物理阵元",
        example=(
            "<p>以 <code>N_h=16</code>、<code>N_v=8</code>、双极化为例，<code>(p=1,h=3,v=5)</code> 的端口索引是 <code>1·128+3·8+5=157</code>。这个端口驱动同一水平位置、同一极化下的六个连续垂直阵元；F 的第 157 列只有这六行非零，非零值是包含幅相与电下倾的馈电权。</p>"
            "<p>若六个权已做二范数归一，则该列范数为 1；所有端口子阵不重叠时 <code>FᴴF=I₂₅₆</code>。把一个端口激励乘以 F 后，总能量不变，但在垂直方向形成子阵因子。随后再叠加单元方向图，得到端口级方向响应。</p>"
        ),
        checks=(
            ("索引双射", "所有合法 (p,h,v) 恰好映射到 0..N−1，无重复、无缺口，Type-I permutation 也是完整置换。"),
            ("F 结构", "shape、每列非零数、非零行位置和列范数符合 1驱3/1驱6 合同，且 FᴴF 在数值容差内为单位阵。"),
            ("物理方向", "正下倾使主瓣向负仰角移动；改变索引顺序不改变坐标和方向图。"),
            ("配置可追溯", "profile、端口顺序、下倾、参考频率、方向图来源和 calibration_id 全部落盘。"),
        ),
        pitfalls=(
            "把 64T 当作 64 个独立理想阵元，遗漏 1驱3 子阵和方向图。",
            "为统一数组顺序而翻转物理垂直坐标，导致电下倾和到达角含义一起颠倒。",
            "把 272 RB 描述成标准表值，或用 273 作为 17×16 RBG 的利用率分母。",
        ),
        source_paths=("src/superran/hardware.py", "src/superran/physical.py", "presets/presets.yaml"),
    ),
    "channel": DetailSpec(
        promise="把一条 MIMO 信道拆成路径功率、时延、多普勒、空间阵列响应和极化耦合，进一步解释同站共享传播状态、跨站独立和系统层 slot 快照分别解决什么问题。",
        principles=(
            "宽带双极化 MIMO 信道可以看成很多条射线外积的叠加。每条射线带有功率 <code>Pℓ</code>、时延 <code>τℓ</code>、多普勒 <code>νℓ</code>、发收阵列响应以及 2×2 极化耦合矩阵 <code>Jℓ</code>。频率选择性来自时延相位，时间变化来自多普勒相位，空间选择性来自不同角度下的阵列响应，交叉极化则由 Jones 向量与 J 的乘积决定。H 的一个元素因此不是独立抽出的复高斯数，而是同一组物理路径在某一发射端口和接收天线上的投影。",
            "CDL-A～E 明确给出 cluster/ray 的统计结构，适合 MIMO、波束和空间相关性研究；TDL 主要保留时延-功率轮廓，不能凭空恢复完整角度与极化结构。Sionna RT 则从场景几何和材料追踪路径。三者可以输出相似形状的 H，但背后的条件不同，报告必须标注数据源，不能把统计 CDL 和确定性场景射线混称为同一真值。",
            "同一站点的多个扇区看到同一片建筑、街道和遮挡，因此应共享站点级传播状态，再按扇区方位、端口和 UE 链路投影；这不是把一个扇区 H 复制给另一个扇区。不同站点没有同一物理中心和局部散射环境，直接复制状态会制造不真实的跨站相关。当前合同用 site/cluster seed 表达共享层级，并验证共享的是潜在环境而不是最终矩阵。",
            "物理后端可以在一个 slot 内生成 14 个 symbol 以完成 SRS/导频、插值和 Doppler 演化；系统层只保留代表该 slot 的一个 snapshot。典型系统级仿真会预先生成或按较慢周期更新链路状态，再在更细的 TTI 队列循环中复用。若研究 symbol 内高速变化或特殊导频，就保留更细时间轴；一般体验速率无需把 14 份完整 H 带入每个 TTI。",
            "源代码树能import只证明Python依赖存在，不证明物理API兼容。适配器在能力发现时握手paired角色、source registry、LMMSE出口、SRS带宽选择器与端口顺序控制；不兼容树可继续作为算法参考，但整仓engine必须标unavailable。",
        ),
        implementation=(
            ("建立站点状态", "内部源按 site 生成共享大尺度和 cluster 状态，用独立站点随机流隔离不同站；sector 只在该状态上施加方向、阵列和链路投影。"),
            ("展开路径", "CDL/RT 后端生成每条路径的功率、时延、角度、相位、多普勒和极化耦合，并在真实阵元坐标上求发收响应。"),
            ("形成端口 H", "单元方向图和 Jones 极化先作用于射线，物理阵元 H 再乘馈电 F 压到 RF 端口，最终形成 DL 4×64、UL 64×4 等矩阵。"),
            ("形成时间快照", "symbol级数据用于估计链；新生成显式冻结<code>sample_interval_s</code>，系统层先读该字段再映射TTI，旧数据才走周期推断兼容路径。"),
            ("握手源合同", "能力发现验证五项适配API；缺任一项即fail closed，避免新版局部增强掩盖64T/256T、4R、SRS或LMMSE合同退化。"),
        ),
        example_title="同站三扇区为什么相关但绝不相同",
        example=(
            "<p>站点 A 的三个扇区共享“某栋楼造成主遮挡、某组 cluster 位于街谷”的潜在状态。扇区 0 朝北、扇区 1 朝东南、扇区 2 朝西南；同一 cluster 进入各扇区天线坐标后会有不同方位角、方向图增益和端口相位，因此三个 H 不相等。若 UE 移动，路径时延和角度的演化仍具有共同几何原因。</p>"
            "<p>站点 B 使用独立 site seed。即便两个站的天线配置相同，也不应复用 A 的 cluster realization。验证时既要看到同站共享标识一致，也要检查扇区矩阵并非逐元素相等；跨站共享标识必须不同。</p>"
        ),
        checks=(
            ("功率与 PDP", "路径功率和归一、时延顺序、RMS delay spread 与所选 profile/场景相符。"),
            ("空间与极化", "改变角度或极化会系统性改变阵列响应/交叉极化功率，而不是只改变矩阵标签。"),
            ("共享层级", "同站 site_state_id 相同但最终 H 不同；异站 site_state_id 和随机流独立。"),
            ("时间一致", "Doppler、snapshot 周期、SRS 周期和处理时延分别可见，系统层映射无越界或未来信息。"),
            ("源兼容", "仅能import但缺端口顺序/SRS选择器/LMMSE出口的伪兼容checkout必须在sr_capabilities阶段被拦。"),
        ),
        pitfalls=(
            "把同站共享实现为复制最终 H，三个扇区因而拥有不可能的完全相关信道。",
            "用 TDL 输出支撑需要角度、极化或 MU 用户相关性的结论。",
            "认为系统层只留一个 snapshot 就等于物理层从未生成/使用 14 个 symbol。",
        ),
        source_paths=("src/superran/native.py", "src/superran/channelhub.py", "src/superran/scenes.py", "src/superran/scene_assets.py", "src/superran/spec38901.py", "src/superran/generate.py"),
    ),
    "antenna": DetailSpec(
        promise="从单个阵元接收一条 +45/−45° 极化射线开始，逐层叠加阵元方向图、空间相位和子阵馈电，直到得到可供预编码使用的端口级 H，并用结构不变量证明 F 矩阵没有排错。",
        principles=(
            "天线响应至少有三层。第一层是<strong>单元方向图</strong>：水平约 110°、垂直约 65° 的参数化包络把入射方向变成幅度增益；这不是实测图，metadata 必须保留 <code>parametric_temporary</code>。第二层是<strong>极化 Jones 向量</strong>：预置交叉极化为 +45°/−45°，路径的 2×2 极化矩阵决定同极化和交叉极化耦合。第三层是<strong>阵列与子阵因子</strong>：真实坐标带来角度相关相位，F 的复馈电权再把多个物理阵元合成一个 RF 端口。",
            "方向图以 dB 衰减相加，但进入复信道前必须转换成电压幅度 <code>10^(G/20)</code>，而不是功率比例 <code>10^(G/10)</code>。一条射线对某个收发极化对的复系数，是发射 Jones 向量、路径极化矩阵、接收 Jones 向量、两端方向图幅度与空间相位的共同结果。最后对所有路径、时延和多普勒相干叠加，才能得到 H。",
            "F 是硬件馈电的线性算子。对 64T，192 个物理阵元按端口分成 64 组，每组三个垂直阵元；对 256T，1536 个阵元分成 256 组六阵元。每列表示“激励这个 RF 端口时哪些阵元以什么复权发射”，每行表示“这个物理阵元属于哪个端口”。子阵不重叠且列归一时，<code>FᴴF=I</code>；这既给出能量守恒，也给出排布正确性的强检查。",
            "单元方向图与端口方向图不能混为一张图。端口方向图等于单元方向图乘以垂直子阵因子；改变电下倾只改变子阵因子的相位斜坡，改变 110° HPBW 则改变每个阵元的水平包络。最终链路还要乘传播路径和对端阵列，不能从某张方向图直接读取用户 SINR。",
        ),
        implementation=(
            ("计算单元幅度", "用相对面板波束指向的方位/仰角求水平、垂直衰减并封顶，转换成复电压乘数；同时记录方向图参数与是否实测。"),
            ("计算极化耦合", "为 +45°/−45° 构造 Jones 向量，与每条路径的极化矩阵相乘，得到发射极化到接收极化的复耦合。"),
            ("构造阵元响应", "利用水平 0.5λ、垂直 0.67λ 等真实坐标计算空间相位；坐标和 top-to-bottom 索引保持分离。"),
            ("应用馈电矩阵", "生成带下倾相位的稀疏 F，先验证结构，再用 <code>H_port=H_AE F</code> 得到端口级信道。"),
        ),
        example_title="一条离轴射线如何影响端口信号",
        example=(
            "<p>设射线从水平主瓣右侧 55° 到达。若水平 3 dB 波宽为 110°，参数化包络在该方向约产生 3 dB 单元功率衰减，即复幅度约乘 0.708。它同时在相邻水平阵元上形成由 0.5λ 间距决定的相位差。</p>"
            "<p>再看一个 1驱3 垂直端口：三个阵元相距 0.67λ，馈电权含 6° 下倾相位斜坡。对接近 −6° 的射线，三个复数更接近同相叠加；对远离主瓣的仰角则可能部分抵消。射线的 +45/−45° 耦合还要乘路径 J 矩阵。最终端口系数是这些复量的相干和，不是“方向图损失 + 阵列增益”的一个固定 dB 常数。</p>"
        ),
        checks=(
            ("极化基准", "+45°/−45° 顺序、Jones 基底和交叉极化功率在发射/接收两端一致。"),
            ("方向图锚点", "波束中心为峰值，±HPBW/2 约为 −3 dB，封顶衰减不越界；电压/功率换算正确。"),
            ("F 不变量", "每列非零阵元数、位置、列范数、FᴴF、下倾方向和 64T/256T shape 全部通过。"),
            ("端到端敏感性", "关闭方向图、改变极化或下倾时 H 与链路 KPI 按物理预期变化，且默认配置可逐位/容差回归。"),
        ),
        pitfalls=(
            "把双极化仅实现成端口数乘二，却没有 Jones 向量和路径极化耦合。",
            "把 dB 功率增益用 10^(G/10) 直接乘复信道，导致衰减被平方。",
            "只验证 F 的 shape，不验证非零位置、能量和下倾方向；排错一列仍可能悄悄通过。",
        ),
        source_paths=("src/superran/hardware.py", "src/superran/generate.py", "tests/test_company_256t.py", "tests/test_physics_invariants.py"),
    ),
    "srs": DetailSpec(
        promise="详细推导 64×4 SRS 接收矩阵的维度与估计流程，区分序列、估计算法、周期和处理时延，并解释 LS 不会自动抹掉干扰方向性、LMMSE 又真正多用了什么信息。",
        principles=(
            "上行 SRS 时，UE 的 4 个发射端口在已知资源元素上发送参考序列，基站 64 个 RF 端口分别接收。因此每个频点的上行信道是 <code>H_UL∈C^(64×4)</code>：行对应基站接收端口，列对应 UE 发射端口。若把所有端口的已知导频写成 X，接收矩阵为 <code>Y=H_UL X+I+N</code>；用导频伪逆去扩频后得到 64×4 的估计。TDD 互易用于把这个上行信息映射为下行预编码 CSI，但形状转置、RF 校准和处理时延必须显式处理。",
            "NR SRS 的基序列来自低 PAPR 序列族，长度条件满足时使用 Zadoff–Chu 构造，并叠加循环移位、comb、跳频和端口映射；因此“当前是不是 ZC”不能只看一个根序列函数。<code>srs_sequence</code> 保留原有基序列入口，<code>srs_waveform</code> 再按assignment生成每端口序列、绝对comb RE和完整接收观测；100 MHz 的 C_SRS=63/B_SRS=1 资源与 17-hop 顺序由本地合同承载。配置输出同时记录profile、comb、周期、实际RB、slot和n_SRS_ID。",
            "LS 在导频点只做 <code>Y X†</code>，不知道信道的频域/时域协方差；它不会把干扰变成无方向。干扰原本经过 64 根接收端口形成的空间向量仍在 Y 中，LS 只是把与目标序列相关的那部分投影进估计。若其他用户序列不正交，污染也保留其方向性。LMMSE 在 LS 之上再用 <code>R_tp(R_pp+R_v)^−1</code> 融合先验相关与噪声/干扰协方差，从而在统计匹配时降低均方误差。",
            "SRS 周期描述 UE 多久发一次导频；处理时延描述收到导频后多久可供调度使用；channel snapshot 周期描述物理信道多久更新。所谓 CSI lag 是这三者和当前 TTI 的组合量，不应把它命名成“SRS 年龄”。报告应写清 SRS 周期、最后一次可用报告时刻与等效 CSI 时延。",
            "PreSINR 是估计质量而不是业务接收SINR：分子为UL真信道功率，分母为UL估计误差功率；功率先求和再作比，时间IIR在线性比值域执行。底噪还必须注明per active RE、per RB或全分配带宽，30 kHz下per-RB与per-RE相差10log10(12)。",
        ),
        implementation=(
            ("生成资源与序列", "<code>srs_config</code> 对 hopping 只接受 272 RB/C_SRS=63/B_SRS=1/b_hop=0/n_RRC=0，并返回本地版本化17-hop日历；每个assignment occurrence展开为16 RB×6 comb-2 RE=96 RE，两腿同RB、完成后才hop。"),
            ("形成接收观测", "每个UE先把两端口序列乘自己的UE→受害gNB UL信道，再在同一4096点OFDM栅格上施加TA/CFO并相干叠加；Y_signal、Y_interference、Y_noise和Y_total四份都保留。"),
            ("估计与证据", "LS逐端口解扩后在时延域保留因果窗，再折回RB；同时输出raw SIR、post-despread SIR、NMSE和逐slot×RB的UL IoT充分统计，证据附SHA-256。"),
            ("复算测量口径", "<code>srs_metrics</code>按实际comb RE数计算开环功控、接收功率与per-RE/RB底噪；<code>h_ul_true</code>与互易映射回UL的估计复算PreSINR，IoT sidecar可独立加载验hash。"),
            ("应用时序", "系统层按 SRS 周期选最后一份已经完成处理的估计，禁止读未来 snapshot；估计来源和 lag 进入链路表元数据。"),
        ),
        example_title="4 端口正交 SRS 上的一次 LS 与 LMMSE",
        example=(
            "<p>若四个端口在同一导频块使用正交序列，X 满列秩，64 根接收端口的 Y 右乘 X† 后，每一行都得到对应的四个复信道系数。某邻区 SRS 与目标序列相关时，它不会均匀加到所有元素，而会按邻区到 64 端口的空间通道投影成有方向的污染。</p>"
            "<p>当前固定profile的一次16-RB、comb-2发送有96个活动RE。平坦信道toy中，两个UE即使raw RE功率相同，只要使用CS0/1与CS2/3且时频对齐，时延窗后可分离；同CS时污染落在目标因果窗而无法消除。加入500 Hz CFO或2 us时偏后，异CS残余重新出现。这组数字只验证接收机因果，不代表现场干扰功率。</p>"
            "<p>导频稀疏时，LS在导频点无偏但中间RB依赖插值。LMMSE若知道接近真实的RMS delay spread，可用频域相关联合加权；先验错得很大时，单条realization不保证优于LS。因此验证看多样本NMSE分布和先验敏感性，而不是挑一个样本断言LMMSE必胜。</p>"
        ),
        checks=(
            ("维度闭合", "X、Y、H 的端口和资源轴相乘合法，DL/UL 转置与校准位置明确。"),
            ("序列属性", "目标长度、循环移位与端口组合的自相关/互相关达到配置预期，跳频 RB 索引合法。"),
            ("接收分层", "同一输入分别核对raw RE干扰、解扩后残余与最终H-hat；UL IoT可从I矩阵和噪声逐字重算，axis与hash一致。"),
            ("功率参考", "30-kHz per-RE与360-kHz per-RB底噪相差10.79 dB；PreSINR同时缩放H真值/估计后不变，IIR结果与线性域解析值一致。"),
            ("估计公平", "LS/LMMSE 使用同一观测和同一导频，LMMSE 先验、SNR 与 tau_rms 落盘；ideal 单独标为上界。"),
            ("因果时序", "任一 TTI 使用的报告时间不晚于当前时间减处理时延，周期与 snapshot 映射可复算。"),
        ),
        pitfalls=(
            "把 64×4 解释成 64 个下行发射端口乘 4 个 UE 接收端口，却忘了 SRS 本身是上行观测。",
            "认为 LS 是逐元素白化，进而断言干扰方向性丢失；真正问题是序列污染和插值误差。",
            "把邻区BS到本UE的下行干扰信道，当成邻区UE到受害gNB的上行SRS cross-link；两者不是同一传播链路。",
            "把 LMMSE 写成无条件逐样本优于 LS，忽略协方差与时延扩展先验失配。",
        ),
        source_paths=("src/superran/physical.py", "src/superran/srs_resource.py", "src/superran/srs_waveform.py", "src/superran/srs_metrics.py", "src/superran/loader.py", "src/superran/csi_aging.py", "tests/test_srs_waveform.py", "tests/test_physics_contract_extensions.py"),
    ),
    "measurements": DetailSpec(
        promise="说明同一份复信道如何产生 PDP、RSRP、SINR、PMI、协方差和几何观测量，并建立每个观测量的输入角色、聚合轴、单位和可复算证据。",
        principles=(
            "数据集不是“一个 H 文件”，而是带角色和坐标的张量合同。样本、时间、RB、基站端口和 UE 端口轴决定每一次平均的物理含义；在 RB 上平均功率、先平均复数再取模、或先做波束再平均会得到不同量。观测函数必须显式选择轴，不能依赖 NumPy 默认行为让 shape 恰好可广播。",
            "几何量与信道量回答不同问题。距离、LoS、角度和路径损耗来自场景/路径元数据；PDP、协方差、RSRP、SINR 和 PMI 从复信道及功率参考面派生。估计质量还需要同时读取 h_true 与 h_est。一个图若混用了几何真值和估计侧可见量，标题与报告必须写清，否则会给算法超出实际系统的信息。",
            "PMI 不是固定“每 5 ms”更新的自然属性。码本、CSI-RS 资源、报告配置、处理与反馈时延共同决定可用 PMI 周期。离线函数可以对每个 snapshot 计算最佳码字，这是算法上界/候选表；系统仿真只有在报告到达时更新 UE 可用 PMI，并在中间 TTI 复用旧值。",
        ),
        implementation=(
            ("加载合同", "loader 校验数组 shape、dtype、NaN/Inf、metadata 和 h_true/h_est 角色，先把文件变成稳定 Dataset 对象。"),
            ("选择参考面", "观测函数明确使用阵元级还是端口级 H、真值还是估计值、总载波还是每 RB 功率，并记录选择。"),
            ("执行聚合", "按观测量定义做时延、频率、端口、用户或样本聚合；需要波束时先通过码本/权矩阵再计算接收功率。"),
            ("输出可复算摘要", "数值结果与单位、shape、聚合方式、源字段和配置一起返回，图表只消费这个结果合同。"),
        ),
        example_title="同一 UE 的 RSRP、PMI 与 post-BF SINR 为什么不同",
        example=(
            "<p>RSRP 可以从参考信号 RE 上的接收功率得到，重点是覆盖；PMI 在候选码本中寻找某个准则最优的预编码索引，重点是可反馈方向；post-BF SINR 还要加入流间、用户间和邻区干扰以及接收机。一个 UE 可以有不错的 RSRP，却因强干扰得到低 SINR，也可能因估计过期选到不再最优的 PMI。</p>"
            "<p>正确的 toy 检查会固定一个样本，打印选用的 snapshot/RB/端口、总功率到每 RB 的换算、最佳码字功率和最终干扰分解。这样读者能从原始 H 逐步复算，而不是只接受一个聚合后的 dB 数。</p>"
        ),
        checks=(
            ("聚合顺序", "每个 KPI 的取模、平方、求和、平均和 dB 转换顺序有公式与单元测试。"),
            ("角色正确", "设计类观测读取 h_est，评价类观测读取 h_true；oracle 量显式标注。"),
            ("周期正确", "离线逐 snapshot 候选与系统可用报告分开，PMI/CQI/SRS 更新周期不混用。"),
            ("toy 可复算", "至少一个小矩阵样本能手工/NumPy 复算到同一数值和单位。"),
        ),
        pitfalls=(
            "在复信道上先平均再平方，错误抵消不同 RB/路径的相位。",
            "把离线每 snapshot 最佳 PMI 当成系统每 TTI 都能即时知道的反馈。",
            "只给观测值名称，不记录使用的功率参考面、聚合轴和真值/估计角色。",
        ),
        source_paths=("src/superran/loader.py", "src/superran/measure.py", "src/superran/srs_metrics.py", "src/superran/physical.py"),
    ),
    "beamforming": DetailSpec(
        promise="从矩阵约定出发严格区分 EBF、PEBF、NEBF，解释每天线约束究竟归一哪一个轴、为什么 NEBF 会改变 MU 零陷，并给出功率与正交性的双重验证方法。",
        principles=(
            "项目统一使用 <code>Q[frequency, antenna, stream]</code>，因此某根物理发射天线的功率是 Q 对 stream 轴的行范数平方；若外部文档把矩阵写成 <code>[stream,antenna]</code>，同一个物理操作会被称为列归一。讨论“列范数”之前必须先写矩阵方向，否则两段都正确的代码也可能归一了相反的对象。",
            "EBF 从 SVD/其他方向矩阵出发，让各流等分总功率 P，约束是 <code>tr(QQᴴ)≤P</code>。PEBF 找到当前功率最大的天线，用一个全局系数把它压到 P/M；所有列之间的几何关系保持不变，但其他天线通常低于上限，因而总功率利用率小于 1。NEBF 则分别缩放每根非零天线，使其恰好使用 P/M，总功率可用满，但每行缩放不同，会改变 Q 的列 Gram 矩阵。",
            "在单用户 SVD 中，阵列行功率往往较均匀，NEBF 的行缩放接近一个全局常数，所以与 EBF 接近，PEBF 可能因受最强天线限制损失功率。在 MU ZF/RZF 中，零干扰依赖完整矩阵的列方向；NEBF 的非均匀行缩放等价于在天线维施加对角矩阵，原来为零的用户间内积可能重新出现。此时即使总功率更高，残留干扰也可能让 NEBF 低于 PEBF。",
            "功率约束必须作用于真正送进信道的物理 Q，而不是只修改一个诊断副本。兼容旧链路公式时可以返回 <code>W_model</code> 与流功率，但必须保证重构出的 Q 与诊断对象一致；重复归一会让 PEBF/NEBF 功率被缩两次。",
        ),
        implementation=(
            ("建立 EBF 物理矩阵", "方向列先单位化并乘 <code>sqrt(P/L)</code>，得到总功率受限的 Q；EBF 路径保持历史默认逐位行为。"),
            ("施加每天线约束", "PEBF 对整个频点使用单一 scale；NEBF 计算每个 antenna row norm 并逐行缩放，零行直接报错而不是凭空补功率。"),
            ("输出兼容模型", "<code>equal_power_weights</code> 返回物理 Q、兼容旧 SINR 公式的 W_model 和同一 Q 的 PowerDiagnostics。"),
            ("进入 SU/MU 真实信道", "非 EBF 模式不能继续套奇异值闭式；必须把归一后的 Q 打回 H，重新计算 Gram、接收机、流间和用户间干扰。"),
        ),
        example_title="两天线两流：为什么用满功率仍可能更差",
        example=(
            "<p>设某个 MU ZF 权在两根天线上的行功率分别是 0.45P 和 0.05P，而每天线上限是 0.5P。PEBF 的最大行尚未越界，基本不缩放；NEBF 会把第二行放大到 0.5P，第一行略放大。总功率从 0.5P 提到 P，看似多了一倍。</p>"
            "<p>但第二行被放大十倍后，两个用户权向量的相对方向改变，原本为零的交叉项不再为零。若新增干扰超过信号功率增益，post-MMSE SINR 下降，于是出现 NEBF&lt;PEBF。SU 只有同一用户的流，且 SVD 行功率较均匀时，这种方向破坏较小，因此 NEBF≈EBF 是更常见的 sanity check，而不是数学恒等式。</p>"
        ),
        checks=(
            ("功率上限", "每频点总功率不超过 P；PEBF/NEBF 每根天线不超过 P/M，NEBF 非零行接近恰好 P/M。"),
            ("功率利用率", "EBF/NEBF 典型利用率接近 1，PEBF 小于等于 1；零行和数值容差有显式报告。"),
            ("几何变化", "诊断 QᴴQ 的非对角能量：PEBF 与 EBF 的归一化几何保持，NEBF 允许变化且 MU 链路必须重算。"),
            ("预期排序", "受控 SU 样本验证 NEBF≈EBF≫PEBF 的可能区间；受控 MU 样本至少能构造 NEBF&lt;PEBF，而不是硬编码排序。"),
        ),
        pitfalls=(
            "只看总功率等于 P 就宣布满足每天线约束，遗漏某根天线超 P/M。",
            "按“列归一”字面操作，未确认本地 Q 的 antenna 轴究竟是行还是列。",
            "归一了用于报告的 Q，却仍用原始 SVD 奇异值计算 SINR，使性能和诊断来自两套权。",
        ),
        source_paths=("src/superran/beamforming.py", "src/superran/linklevel.py", "src/superran/mumimo.py"),
    ),
}

DETAIL_SPECS.update({
    "sinr": DetailSpec(
        promise="从端口级 H、物理预编码 Q 和接收机 G 一直算到逐流 post-MMSE SINR，再说明全带谱效如何聚合、为什么总载波功率与每 RB 功率不能在不同参考面上混用。",
        principles=(
            "链路计算必须先固定矩阵和功率参考面。端口级信道 H 只描述传播增益；物理预编码 Q 同时包含方向与发射幅度；两者相乘得到有效流信道。接收端再把目标流、同用户其他流、MU 用户、邻区和噪声投影到同一个 G 上，得到 post-MMSE SINR。若 Q 已经包含 <code>sqrt(P/L)</code>，后续公式不能再乘一次 P/L。",
            "总载波功率、每 RB 功率和数字波束功率是三层预算。总功率均匀摊到调度 RB 后得到每 RB 输入；同一 RB 内再由 rank/MU 流分功率；EBF/PEBF/NEBF 则约束这些流在物理天线上的合成。任何一层被重复扣除都会让 SINR 偏低，被遗漏则偏高。噪声也必须按 12 个子载波的 RB 带宽和 NF 计算，不能拿整带噪声与单 RB 信号相除。",
            "所谓“全带谱效”是对整带各 RB/流的可传输效率进行定义明确的聚合，而不是把平均 SINR直接代入 Shannon。频率选择信道上，先把每 RB SINR 映射到 MCS/谱效再加权，与先平均线性 SINR或 dB SINR后映射并不等价。容量模式可以按满业务可用效率聚合；体验模式还必须用队列封顶后的 useful bytes，超出包长的 padding 不算真实谱效。",
            "Shannon <code>log2(1+SINR)</code> 只适合作为连续理想上界或诊断。实际调度使用 CQI/MCS、BLER 曲线、TBS 量化、DMRS/控制开销和 rank；若用 Shannon 生成链路表，再用离散 TBS 评价，会把两套口径混在一起。",
        ),
        implementation=(
            ("形成有效信道", "从 h_est 设计 Q，但把同一 Q 乘到 h_true 上形成评价侧 H_eff；PEBF/NEBF 必须使用真实归一后的 Q。"),
            ("构造干扰协方差", "把邻区每个 RBG 的占用、功率、信道和权投影到接收端，累加成 R_uu；热噪声以 N0I 单独加入。"),
            ("计算接收与逐流 SINR", "求线性 MMSE G，再对每流显式计算目标功率、流间泄漏、外部干扰和噪声，保留分解诊断。"),
            ("聚合到链路表", "逐 rank 保存真值 SINR、发送侧预测 SINR、MCS、BLER 与谱效；全带或 RBG 聚合规则写进 metadata。"),
        ),
        example_title="同样 20 dBm，为什么少一个 10log10 会差一整档 MCS",
        example=(
            "<p>若 20 dBm 是整载波总功率，272 个调度 RB 均分后每 RB 约为 −4.35 dBm，而不是 20 dBm。rank 2 时每流再少 3 dB。接收侧噪声应按一个 RB 的 360 kHz 带宽计算；若误用整带噪声，分母又被放大约 24 dB。</p>"
            "<p>验证时从 Q 的总功率和每天线功率开始，逐层打印每 RB、每流信号功率、干扰和噪声。将 H 设为单位阵、无邻区时，数值应退化成容易复算的对角案例；打开第二流或邻区后，新增项只出现在对应分母中。</p>"
        ),
        checks=(
            ("参考面闭合", "信号、干扰和噪声在同一 RB、同一接收后参考面与同一线性单位相加。"),
            ("Q 唯一", "功率诊断所检查的物理 Q 与实际进入 H_eff 的 Q 是同一个数组/数值。"),
            ("退化案例", "单位信道、零干扰、rank 1 可解析复算；增加一个正交/同向干扰流时变化符合公式。"),
            ("聚合口径", "逐 RB→全带的顺序、权重、空 RB 和 S slot 处理均在结果元数据中可见。"),
        ),
        pitfalls=(
            "Q 已含流功率后，又在 SINR 分子乘一次 P/L。",
            "把 dBm/dB 直接相加到线性协方差矩阵，或用整带噪声除单 RB 信号。",
            "用平均 dB SINR 代替逐 RB MCS/TBS 聚合，并把结果称为真实全带谱效。",
        ),
        source_paths=("src/superran/linklevel.py", "src/superran/mumimo.py", "src/superran/interference.py"),
    ),
    "bfgain": DetailSpec(
        promise="把 BF Gain 从一个“SVD 比 PMI 高几 dB”的标量拆成 CSI 视角、两套权、功率约束、post-MMSE 接收、RB/RBG/流聚合和真值审计，让读者能按公式独立复现。",
        principles=(
            "BF Gain 是基站决策时的可知量，不是事后用 <code>h_true</code> 算出的实际增益。两套权都在同一份 <code>h_prec</code> 上设计和评估：一条是实际发送方向（默认 SVD），另一条是 Type-I-style 宽带 PMI 参照方向。如果 SRS 估计陈旧，两边都只能看陈旧 CSI。",
            "“方向”和“空间功率约束”是两个轴：默认物理 TX 是 SVD+NEBF，称 <code>SINR_NEBF</code>；显式选 PEBF/EBF 时称 <code>SINR_PEBF/SINR_EBF</code>。PMI 参照也施加完全相同的约束，只保留 <code>SINR_PMI</code> 这个业务名字。公平对照要求同 rank、同总功率、同约束、同损伤和同经典 MMSE，唯一改变方向。",
            "聚合顺序不能换。每条流先得到逐 RB 线性 post-MMSE SINR；RBG 内在线性域平均 RB，再转 dB；RBG 内各流和全带各 RBG 最后在 dB 域算术平均。这是当前单码字宽带工程口径，不冒充已标定的 EESM/MIESM。",
            "必须分开三个量：<code>SINR_NEBF,gNB−SINR_PMI,gNB</code> 形成 BF Gain；<code>Γ(MCS(CQI))+G_BF</code> 只是 <code>SINR_AMC_PRED</code>，负责无 OLLA MCS 反折；同一个 Q 打到 <code>h_true</code> 才得到 <code>SINR_NEBF,RX</code>，只有它能和最终 MCS 一起查 BLER。",
        ),
        implementation=(
            ("构造 SVD 方向", "每个 RB/RBG 计算 <code>Rtx=E[Hcode·Hcodeᴴ]</code> 的主特征向量；单快照时等价于数学下行矩阵 <code>Hcodeᴴ</code> 的右奇异向量。"),
            ("构造 PMI 方向", "生成 O1=O2=4 的水平/垂直 DFT Kronecker 列与四个双极化共相位候选，按宽带残余协方差逐层贪心选列，并按 metadata 重排端口。"),
            ("形成物理 Q", "两方向强制相同 rank，先每流 P/r；再对两边施加同一个 C。默认 NEBF 对 Q 的每根天线行缩放到 P/M；若用 [stream,antenna] 记法就是列范数归一。"),
            ("算 gNB 逐 RB/流 SINR", "把两个物理 Q 分别打到同一 h_prec，用同一损伤协方差和经典 MMSE 闭式得到 <code>SINR_NEBF/PEBF/EBF</code> 与 <code>SINR_PMI</code>。"),
            ("聚合并作差", "<code>rbg_sinr_db</code> 完成 RB 线性平均与dB/流聚合；逐 RBG 差保存为 <code>bf_gain_rbg</code>，全带差保存为 <code>bf_gain_db</code>。"),
            ("分离决策与判错", "只把 gNB CSI 增益加到 CQI 门限形成 <code>SINR_AMC_PRED</code>；同一实际 Q 在 h_true 上得到 <code>actual_receive_sinr_db</code>，最终 BLER 只查后者。"),
        ),
        example_title="同一 h_est，换 h_true 为什么不能改当次 BF Gain",
        example=(
            "<p>固定一份 4T2R、8 RB 的 <code>h_est</code>，分别配两份完全不同的 <code>h_true</code>。修正后两臂进入 MCS 的 <code>bf_gain_user_db</code> 逐位相同，因为基站在发送前看不到哪份真值会发生。</p>"
            "<p>但事后真值审计和真实 BLER 会不同。一个固定回归样本中，gNB 预测 BF Gain 相同；完全匹配真值与另一份真值产生不同的 <code>actual_receive_sinr_db</code>。这个误差只能通过本次 ACK/NACK 和后续 OLLA 学到，不能把真值提前喂回当前 MCS。</p>"
        ),
        checks=(
            ("h_true 隔离", "固定 h_est 只改 h_true，进入 MCS 的 BF Gain 不变，真值审计允许变。"),
            ("零时延闭合", "h_prec=h_true 时 predicted/true BF Gain 一致，prediction error 为 0。"),
            ("Type-I 退化", "precoder=type1 时 TX 与 PMI 权相同，BF Gain 逐值为 0。"),
            ("粒度和功率", "逐 RBG 差的宽带平均等于用户级差；两分支 power_constraint/rank/noise 完全相同。"),
            ("BLER 因果性", "不提供 h_true 接收 SINR时 BLER 为 unknown；提供数据集后，曲线查询输入必须逐值等于 final MCS + actual_receive_sinr_db。"),
        ),
        pitfalls=(
            "用 h_true 评估 SVD/PMI 后作差，再把这个事后真值加入当次 MCS。",
            "SVD 用 rank2、PMI 用 rank1，把层数和功率差伪装成 BF Gain。",
            "一边用 EBF、一边用 PEBF/NEBF，或 RBG 内直接平均 dB，导致对照参考面不一致。",
        ),
        source_paths=("src/superran/system.py", "src/superran/csi_aging.py", "src/superran/beamforming.py", "src/superran/mumimo.py", "src/superran/loader.py"),
    ),
    "linkadapt": DetailSpec(
        promise="把 CQI、BF、SU/MU OLLA、MCS、BLER 和离散 TBS 串成因果闭环，明确发送侧预测与接收侧真值各自在哪一步使用，以及为何按需 RBG 必须查表反推。",
        principles=(
            "CQI 是接收侧基于过去测量形成的量化反馈，本 profile 使用内部 0..14 离散表映射 MCS。BF gain 是基站基于当前可见 CSI 预测的波束增益。系统先用不含 OLLA 的基准 SINR查预置表并记录 mcs_without_olla，再把 ACK/NACK 学到的连续 MCS-index OLLA 加到该基准 MCS，floor 并钳位。传输之后，真实 H、真实干扰和实际 Q 给出接收侧 SINR，BLER 曲线把它变成错误概率；抽样 ACK/NACK 再更新 OLLA。",
            "MU 不是在 SU MCS 上只减一个固定余量。它至少增加残留相关性损失、同 RBG 总功率在并发 rank 间平分的损失以及用户级 MU OLLA。MU OLLA 对每个用户维护，但不按配对对象再分状态：A 与 B 配对失败、A 与 C 配对失败，都会更新 A 的同一份 MU 偏置。SU 与 MU 状态分开，避免一种传输的误差污染另一种。",
            "TBS 经过 38.214 离散量化和码块对齐，只近似随 RBG 线性。即使 17 个 RBG 的 TBS 比单 RBG×17 高 1%，用除法反推也可能少给一个 RBG，使当前包无法完成。正确实现为每个 slot/MCS/rank 预生成各 RBG 前缀查表，验证单调不减，并用 <code>searchsorted(side='left')</code> 找第一个够用值。量化平台合法，资源增加却令 TBS 下降才是硬错误。",
            "BLER 与 HARQ 的边界要写清。当前体验仿真在 NACK 后冻结 MCS、RBG 数、rank 与 TBS，并只给一次 IR/CC 重传机会；IR/CC 是基于 NewTx 曲线的系统级 BLER 抽象。它仍不等同于完整 NR HARQ 进程：RV、LLR、并行 process 与标准时序没有展开。",
        ),
        implementation=(
            ("形成发送侧预测", "链路表同时保存历史CQI表行和上报4-bit codepoint、基础门限与BF gain；先由SINR反折无OLLA基准MCS，再加用户级MCS-domain OLLA。MU先在SINR域加CorrLoss/powerLoss反折基准MCS，再加SU/MU OLLA。"),
            ("查询真实 TBS", "TbsLookup 对 slot 类型、MCS table 3、rank 与 RBG 前缀建表，构建时验证每一行单调不减。"),
            ("执行真实传输", "按实际分配 RBG 查 TB bytes，用码字级有效 SINR+最终发送 MCS 查预置 BLER 曲线并从独立 BLER 随机流抽 ACK/NACK；NACK 后只允许一次 IR/CC 重传。"),
            ("闭环更新", "ACK/NACK 只更新对应用户、对应 SU/MU 状态；PF credit 按配置使用 scheduled_tbs 或 acked_goodput，绝不回到全带估计。"),
        ),
        example_title="17 RBG 的 29,722 B 为什么不能除以 17",
        example=(
            "<p>在一个已核实的 MCS12/rank2 条件下，单 RBG TBS 为 1,729 B，线性外推 17 倍是 29,393 B；真实 17 RBG TBS 为 29,722 B，多 1.1%。若队列剩余 29,500 B，按总量比例或单 RBG 除法可能给 16 个 RBG，但真实 16 RBG 表项未必够。</p>"
            "<p><code>required_rbg</code> 直接在这行 17 项表中找第一个 ≥29,500 的值。大包找不到时钳到 17，排第一便自然吃完整 band；小包则只拿恰够资源。测试除检查单调，还应把除法算法故意换回去，证明边界包会出现未完成或多一次等待。</p>"
        ),
        checks=(
            ("因果信息", "MCS 只读过去/当前可用 CQI、BF 预测和 OLLA，实际 ACK 前不接触真值结果。"),
            ("双 OLLA", "SU/MU 各有用户级状态、更新事件和 BLER 收敛统计；关闭 MU 时 SU 基线不漂移。"),
            ("TBS 单调", "所有支持的 slot×MCS×rank 表行单调不减，平台取第一个够用前缀，下降则硬失败；边界有穷举测试。"),
            ("字节处理", "ACK/NACK 后 queue、inflight、acked、padding 守恒；当前 HARQ 近似在报告中显式标注。"),
        ),
        pitfalls=(
            "把 CQI+BF+OLLA 当作实际接收 SINR，BLER 因而只剩固定随机噪声。",
            "MU 只减 3 dB，遗漏用户相关性和独立 MU OLLA。",
            "用 TBS(17)×n/17 或队列/TBS(1) 的除法决定 RBG 数。",
        ),
        source_paths=("src/superran/linkadapt.py", "src/superran/bler_curves.py", "src/superran/experience.py"),
    ),
    "dlamc": DetailSpec(
        promise="把下行 AMC 的四条信息面拆开：终端在真实信道上测什么、基站用自己"
                "那份可能陈旧的 CSI 预测什么、闭环用 ACK/NACK 纠正什么、以及最后"
                "由谁来决定这次 TB 到底解不解得出来。每一步都给出可复算的数字。",
        principles=(
            "这条链最容易出的错不是公式写错，而是<strong>把两条信息面接在一起</strong>。"
            "终端只能在真实信道上测量，基站只能用自己收到的 SRS 估计做预测，"
            "两者之间的差就是 CSI 老化与 BF 失配的全部代价，也正是 OLLA 存在的理由。"
            "一旦让发送决策看到真实接收 SINR，首传 BLER 会被构造在目标值上，"
            "所有老化相关的结论同时失效，而结果表面上完全正常。",
            "OLLA 是 MCS 域的连续状态，不是 dB 域的余量。它加在<strong>已经选好的基准"
            "档</strong>上再取整，所以一个很小的负偏置也能把整数档压下去一档；"
            "稳态 BLER 只由上下步长之比决定，与绝对值无关。关掉 OLLA 只应该去掉"
            "这一步叠加，不应该顺带换掉决策坐标。",
            "rank 是个慢变量。它一变，每流功率、TBS、OLLA 的收敛点全跟着变，"
            "高频切换会让链路自适应根本收敛不了。所以默认固定；自适应模式是一个完整的"
            "判决状态机，而不是“发现谱效更高就切”。链路表里的逐快照 best_rank 只是个"
            "诊断量，不是发送 rank。",
            "自适应的判决分成三层，三层的时间尺度完全不同。<b>每 TTI</b> 只做两件事："
            "累积一个谱效滤波样本、以及跑一次快速回退监测。<b>每个判决周期</b>（现场"
            "默认 1000 个 TTI，30 kHz 下 500 ms）才真正判一次该不该换 rank，而且还要先"
            "攒够最少样本数（现场 3 个）。<b>回退是实时的</b>：升 rank 之后进入监测窗，"
            "新增 NACK 超过硬门限当场就退，不等窗口结束更不等下一个判决周期。把这三层"
            "压成一层——比如每 TTI 都判一次谱效——就回到了链路表逐快照跟随的行为，"
            "所有防乒乓设计同时失效。",
            "谱效估计不是“rank × MCS 谱效”这么简单，前面还有两道修正，而且两道都会"
            "改变判决结果。<b>最小 MCS 闸门</b>：某个 rank 的预估 MCS 低于门限（现场 9）"
            "时，它的谱效直接置 0——那一层基本传不动，让它参与 argmax 只会把用户推到一个"
            "必然回退的高 rank 上。<b>资源消耗加权</b>：每个 rank 的谱效再乘一个系数"
            "（现场 [1.0, 0.97, 0.95, 0.93]），体现高 rank 需要更多 DMRS 端口、可用 RE 更少。"
            "少了闸门，高 rank 会靠一个发不出去的档位赢下 argmax；少了加权，rank4 相对 "
            "rank1 会被系统性高估约 7%。",
            "升和降是<b>不对称</b>的，这是有意的。升 rank 要求最优 rank 的滤波谱效严格"
            "超过当前的 1.1 倍——两个几乎并列的候选不该每个周期互相顶替一次。降 rank 在"
            "现场默认值下<b>没有迟滞、立即生效</b>：最优 rank 由 argmax 选出，"
            "SE̅(r★) ≥ SE̅(r_cur) 必然成立，所以“当前仍明显更优”这个条件恒为假。"
            "物理上说得通——高 rank 的弱流会把整个码字拖垮，谱效自己就掉下来了，"
            "再加一道门槛只是让用户在坏工作点上多待 500 ms。需要真正的降迟滞时把系数"
            "设成小于 1（0.9 表示当前 rank 还有最优的 90% 以上就不降）。",
            "<b>快速回退必须连 OLLA 一起退。</b>升 rank 之后 OLLA 会在新工作点上重新"
            "收敛：新 rank 的每流功率更低、预测偏差也不一样，偏置很快就漂到另一个值。"
            "这时只把 rank 退回去，旧 rank 就带着一个属于别人的偏置继续跑，接下来几百个 "
            "TTI 的 MCS 全是错的，而且 KPI 上看不出来——它表现为“降回 rank 之后吞吐"
            "还是上不来”。所以抬升时要把当时的 OLLA 存成回退点，回退时一并恢复。",
            "反复失败的抬升要付代价，否则就是另一种乒乓：估计谱效说该升、实测误码说该降，"
            "两个判据每个周期互相推翻一次。现场的做法是<b>判决周期指数退避</b>——每回退"
            "一次周期翻倍，最多翻 4 次（1000 → 16000 TTI）；升 rank 成功或正常降 rank 时"
            "计数清零。这比“封锁某一档若干周期”更温和：它不禁止重试，只是让重试越来越稀。",
            "反馈有时延，解码有位置。ACK/NACK 要搭上行时隙回来，所以 OLLA 与重传"
            "都比发送晚若干个 TTI；误块抽签只能在<strong>实际授予的那几个 RBG</strong> "
            "上算 SINR，用全带均值判小包会在两个方向上都错。",
        ),
        implementation=(
            ("测量与滤波", "上报时刻在真实信道上用当期 Type-I 参照权测 PMI-SINR，"
                          "量化成 4-bit codepoint，再按 cqi_filter_domain 选定的域做"
                          "一阶 IIR；两次上报之间保持不变。"),
            ("预测坐标", "CQI 经离散表得初始 MCS，取该档目标 BLER 的 NewTx 门限 Γ，"
                        "加上在 h_prec 上算出的 BF Gain，逐 rank 各存一份宽带值与"
                        "逐 RBG 值。"),
            ("选档与闭环", "在预测坐标上反折 mcs_without_olla，叠加用户级 OLLA 偏置，"
                          "floor 并钳到 profile 范围；MU 先在 SINR 域加 CorrLoss 与"
                          "PowerLoss 再反折。"),
            ("Rank 与资源", "RankController 给出本 TTI 的 rank；TBS 按 slot/MCS/rank/RBG "
                           "前缀表反查最小够用 RBG 数，频选模式再挑质量最好的子集。"),
            ("Rank 谱效采样", "每个新的 AMC 坐标喂一次 observe_link：逐 rank 反折出真会发的 "
                            "MCS，过最小 MCS 闸门、乘资源消耗系数，再做 β 一阶 IIR。"
                            "两条评估路径共用同一个控制器，修正只有一份实现。"),
            ("Rank 判决与回退", "step() 每 TTI 先跑回退监测（可立即回退并返回要恢复的 OLLA "
                              "偏置），再看是否到了退避后的判决周期；主循环把返回的 OLLA "
                              "写回自己的状态。"),
            ("解码与反馈", "同一发射权作用到 h_true，取被授 RBG 聚合成单码字 SINR，"
                          "用最终 MCS 查 NewTx 曲线抽 ACK/NACK；增量在发送时刻定下，"
                          "在反馈生效的 TTI 才落到 OLLA 状态上。"),
        ),
        example_title="12.00 dB 的 PMI-SINR 最后发成了 MCS15",
        example=(
            "<p>目标 BLER 10%、BF Gain 4.00 dB、OLLA 偏置 −0.30 档、rank 1。"
            "12.00 dB 落在上报 codepoint 7（内部行 6），映射初始 MCS12，"
            "其 NewTx 10% 门限 Γ = 11.1016 dB。预测坐标 = 11.1016 + 4.00 = 15.1016 dB，"
            "落在 MCS16 门限 14.8955 与 MCS17 门限 15.8460 之间，基准档 = MCS16。"
            "加 OLLA 得 15.70，floor 得 15，钳位后<strong>最终发送 MCS15</strong>。</p>"
            "<p>注意最终档的 10% 门限（13.9 dB 量级）低于预测坐标，这只说明 OLLA 把"
            "工作点往回压了一档，<strong>不说明这次一定误块</strong>。真正判错要看被授 "
            "RBG 上的接收 SINR：15.1 dB 时 BLER = 0.0006，13.2 dB 时 BLER = 0.997。"
            "不到 2 dB 的差别跨越了整条瀑布——这就是为什么解码 SINR 必须取实际授予的"
            "那几个 RBG，而不是全带均值。</p>"
        ),
        checks=(
            ("决策坐标不含真值", "开/关 OLLA 两条轨迹的 base_tx_sinr_db 相同，"
                               "且都等于链路表的 sinr_tx_db；关掉 OLLA 不会选出用真实"
                               "SINR 反折的那一档。"),
            ("解码位置正确", "部分授权的 sinr_db 逐条等于被授 RBG 上真值的 dB 域均值，"
                           "且明显不等于全带均值。"),
            ("rank 稳定", "固定模式下每一次 grant 的 rank 都等于配置值，小区平均 rank "
                        "精确为整数；自适应模式在周期未到或比值未跨门限时不动。"),
            ("升 rank 门限是严格大于", "谱效比 1.05 / 1.10 / 1.11 分别给出 rank1 / rank1 / "
                                  "rank2；等于门限时不切。"),
            ("闸门与加权都生效", "预估 MCS 低于闸门的 rank 滤波谱效恒为 0；输入全 1.0 时"
                             "滤波值逐 rank 等于资源消耗系数本身。"),
            ("回退恢复 OLLA", "抬升前 OLLA = −1.5、新 rank 上漂到 −4.0，回退后精确恢复"
                           "成 −1.5，而不是留下 −4.0。"),
            ("退避会封顶", "反复失败的抬升让判决周期走 100→200→400→800→1600 后停住，"
                        "对应 max_backoff_times = 4。"),
            ("反馈时序", "DDDSU 逐相位偏移为 5/4/3/2 个 TTI；纯下行图案退化成零时延"
                       "并在 notes 里显式说明。"),
        ),
        pitfalls=(
            "拿真实接收 SINR 反折 MCS，首传 BLER 被构造在目标值上，老化代价消失。",
            "把 OLLA 折成 dB 加进 SINR 坐标，偏置的物理含义随工作点漂移。",
            "让 rank 逐快照跟随 best_rank，OLLA 与 PF 都在追一个每 5 ms 就变的目标。",
            "回退只退 rank 不退 OLLA，旧 rank 带着新 rank 收敛出来的偏置继续跑。",
            "把最小 MCS 闸门省掉，让一个根本发不出去的高 rank 靠估计谱效赢下 argmax。",
            "把 spec_asymmetric 的“降 rank 无迟滞”误当当前默认；负责人已裁决默认统一为升降都需 10% 余量。",
            "小包用全带均值判误块；授到好子带时高估误块，授到差子带时低估。",
            "把 avg_mcs 当成链路自适应视角——它的分母含重传，重传重放的是冻结的旧档。",
        ),
        source_paths=("src/superran/amc_policy.py", "src/superran/system.py",
                      "src/superran/experience.py", "src/superran/csi_aging.py"),
    ),
    "mu": DetailSpec(
        promise="按当前已确认的 Phase B 规则解释一次 SU/MU 自适应：只做一次 PF 排序，分别构造数据受限的全 SU 与 MU 方案，用真实 useful bytes 比较，并在 SU 能清空全队列时强制选择 SU。",
        principles=(
            "调度与传输模式选择是两步。PF 先根据每个有数据用户的潜在满带 TBS 与历史平均量给出唯一优先级顺序；SU/MU 两个候选方案都必须遵守这份顺序，不能各自重新挑一组有利用户。随后在同一剩余 RBG、同一队列和同一 CSI 上分别模拟“全部 SU”和“允许真实 MU 配对”的可发送结果。",
            "比较指标是队列封顶后的真实有效 payload，而不是满业务谱效或 grant TBS。若某个小包只剩 500 B，给它 5,000 B 的 TB 仍只贡献 500 useful bytes。这样，MU 只有在同一物理 RBG 上真正多送业务时才胜出，不会因为 padding 伪造收益。若 SU 方案已经能传完所有可服务用户队列，则默认 SU：此时 MU 没有额外用户体验收益，却增加相关性、功率损失和实现复杂度。",
            "Phase A 需要预先产生真实用户对信息：候选 pair、rank、预编码器、残留相关性/干扰损失及可支持状态。Phase B 不能凭一个平均 MU 增益临时假造配对。当前可以先使用 ZF/RZF 和明确的候选规则，但 pair table 必须来自真实 h_est 设计并能在 h_true 上复评。",
            "MU 资源统计按物理 PRB 计一次。两个用户在同一 RBG 配对，已用 PRB 仍是一份；用户级 attribution 可以各记 grant 或按用户数分摊，但小区 <code>MU PRB/已用 PRB</code> 的分子分母不能把同一 RBG 双计。",
            "配对的代价有两半，任何评估路径都必须同时记账，否则结果只会朝一个方向偏。第一半是<b>发送侧变保守</b>：配对后每流只分到 P/(K·rank) 的功率，还要吃零陷残余，AMC 坐标应当整体下移，选出的 MCS 因此更低。第二半是<b>接收侧更容易错</b>：即使 MCS 已经降过，同一档在配对状态下的误块概率仍高于单用户——因为真实接收 SINR 是把 ZF 权（按基站可能已老化的 CSI 算出）打到双方 h_true 上、再把对方的流放进干扰协方差得到的，它不等于任何 dB 项的简单相加。只记第一半会低估吞吐、只记第二半会高估 MCS，而历史 capacity 两半都没记：它按 SU 坐标选 MCS、按 SU 真值抽签，只把 TB 大小乘一个建表阶段测出的标量比值，等价于宣称「配对让包变小但一点也不更容易错」。",
            "因此 capacity 与 experience 现在读同一张 pair 表（<code>mu_accounting=\"pair_table\"</code>，默认）。矩阵运算全部留在建表阶段，主循环只查表：实测约 3.8 ms/pair/快照，12 UE × 40 快照约 10 s，与主循环十万 TTI 的开销相比可以忽略。历史的标量口径降级为 <code>se_ratio_legacy</code>，仅用于复现旧结果，选用时会写进结果 notes，并明说结果系统性乐观。两个口径不可拼在同一张趋势图里。",
            "<code>PowerLoss = −10log10(2) = −3.0103 dB</code> 是<b>记账标签而不是近似</b>。pair 表按 <code>CorrLoss ≜ pred_MU − pred_SU − PowerLoss</code> 定义，所以决策里真正生效的平移量 <code>CorrLoss + PowerLoss</code> 恒等于 <code>pred_MU − pred_SU</code>，那个常数在代数上精确抵消；单列它只是为了让诊断能分开回答「功率分摊占多少、相关性损失占多少」。这条恒等式只在当前支持的 2 用户 × 每用户 rank2 下成立：扩到 3/4 用户或不等流数时，等功率分流的常数本身要按实际流数重新定义，届时必须同时更新标签与它在诊断里的解读，不能只改数值。",
            "capacity 的 SU/MU 判决是逐 TTI 做的，不再依赖一个全程标量。锚点固定为 PF 第一名，先按准入判据筛伙伴（与 experience 相同：两侧的<b>预测</b> BLER 都不得超过 0.5），再在通过准入的伙伴里取聚合谱效最高的那个，最后还要赢过锚点单发的 SU 方案才真配对。capacity 是全带调度，同一 TTI 的 RE 数对 SU/MU 完全相同，所以比较 <code>Σ rank × MCS 谱效</code> 与 experience 比较 useful bytes 是同一件事。两种拒配对的原因分别计入 <code>mu_pair_rejects</code>（一个通过准入的伙伴都没有）与 <code>mu_su_wins</code>（有可配的对但单发更划算），不静默退回。",
            "capacity 的重传恒按 SU 重发。HARQ 的合同是冻结发送身份（MCS/RBG 数/rank/TBS），而配对会同时改变真实 SINR 与 TBS，两者直接冲突。把重传也做成 MU 需要先定义「配对状态属不属于冻结身份的一部分」，这是尚未确认的现场口径，因此当前显式选择更保守的一侧，并在文档与 notes 里写清楚，而不是让它成为一个没人知道的隐含假设。",
        ),
        implementation=(
            ("一次 PF 排序", "对所有有数据且非 outage 用户计算 metric，稳定 tie-break 后得到 ordered_users；该数组同时喂给 SU 与 MU planner。"),
            ("构造 SU 方案", "按顺序为用户查 required_rbg、分配不重叠 RBG，计算队列封顶 useful bytes，并判断是否清空全部可服务队列。"),
            ("构造 MU 方案", "从同一顺序读取 Phase A pair，应用 CorrLoss、powerLoss 与 MU OLLA 重新选 MCS，在相同物理 RBG 上形成 pair grant。"),
            ("选择与落账", "SU 清空全部可服务队列则选 SU；outage或错slot HARQ backlog只作诊断；否则 MU useful≥SU useful 才选 MU。执行后按真实模式更新队列、PF、OLLA、PRB 与配对 KPI。"),
            ("capacity 共用同一张表", "capacity 主循环把「这一格实际会发哪一档 MCS」抽成单一函数：AMC 坐标（MU 时加 CorrLoss+PowerLoss）叠 OLLA（MU 时叠 SU+MU 两条）后 floor 钳位。配对判决与真正发送调用同一个函数，避免「按什么比的」和「发了什么」悄悄漂开。"),
            ("误块抽签换坐标", "capacity 首传的 BLER 查表输入在 MU 时改读 <code>MuPairLink.true_sinr_db[snap, side]</code>，SU 时仍读 <code>UeLinkTable.sinr_db[snap, rank-1]</code>；重传恒为 SU，因此仍查 SU 真值。"),
            ("缺表硬失败", "开了 MU 又选 <code>pair_table</code> 却没有 pair 数据时直接抛错并给出两条明确出路（补建 pair 表，或显式改用历史口径），不静默按 1.0 降级。"),
        ),
        example_title="两个 rank-2 用户并不意味着 MU 一定更好",
        example=(
            "<p>用户 A、B 都是 rank 2。SU 时 A 在前 9 个 RBG 可清空 8 kB，B 用剩余 8 个发 7 kB，共 15 kB useful。MU 若在 9 个 RBG 配对，两人功率各少 3 dB，并有不同 CorrLoss；即使四条流的理论谱效高，真实 MCS 下降后也可能只发 13 kB，此时选择 SU。</p>"
            "<p>反之，A 是 1 kB 小包、B 是大包。全 SU 若 A 独占若干资源后 B 只能发送一部分，MU 可在 A 所需的同一 RBG 上同时服务 B，useful bytes 可能更高。若 SU 本来就能在 17 RBG 内清空 A、B，则直接 SU，不为“出现 MU”而强配。</p>"
        ),
        checks=(
            ("排序一致", "逐 TTI trace 证明 SU/MU planner 收到同一 ordered_users，且没有二次 PF 排序。"),
            ("真实比较", "plan 中同时记录 su/mu useful bytes、队列封顶值与 selected_reason，可从 allocation 重算。"),
            ("损失闭合", "MU MCS trace 展示 CQI+BF+SU OLLA+CorrLoss+powerLoss+MU OLLA 的每一项。"),
            ("资源不双计", "MU pair 的物理 RBG 在小区利用率只计一次，分摊后的用户资源和小区总量可守恒。"),
            ("代价进 MCS", "同一批链路表下开关 MU，pair 表口径的首传平均 MCS 必须显著下降（实测 23.48 → 19.93）；历史标量口径下它几乎不动（22.68 → 22.69），这正是被修掉的问题。"),
            ("代价进抽签", "同一档 MCS 分别按 SU 真值与 pair 真值查 BLER，后者必须更高（实测 0.0008 → 0.0040，真值差 −3.92 dB）。"),
            ("自适应会判 SU 赢", "把两个 UE 的空间相关系数拉到 0.999，配对占比必须坍塌到 0，并且拒配对的原因被显式计数（实测 767 个 TTI 记为 mu_su_wins），不是静默不配。"),
            ("平移量恒等式", "<code>CorrLoss + PowerLoss</code> 与 <code>pred_MU − pred_SU</code> 必须逐元素相等，证明 −3.01 dB 只是标签。"),
        ),
        pitfalls=(
            "先为 SU 排 PF，又为 MU 单独挑高相关/高吞吐用户，比较不再公平。",
            "用 sum(TBS) 而非 min(queue,TBS) 比较，padding 把 MU 方案虚增。",
            "SU 已经清空全部可服务包仍强制 MU，只为了提高 MU 配对比例。",
            "只把配对代价记在 TB 大小上，误块抽签仍用 SU 真值——配对越激进结果越乐观，而且 KPI 上完全看不出来。",
            "把 −3.01 dB 当成一个可以直接套用到 3/4 用户或不等流数的物理近似；它在当前实现里只是 2×rank2 下会被精确抵消的记账标签。",
            "拿 <code>se_ratio_legacy</code> 的旧结果和 <code>pair_table</code> 的新结果放进同一张趋势图。",
        ),
        source_paths=("src/superran/experience.py", "src/superran/mumimo.py", "src/superran/system.py"),
    ),
    "modes": DetailSpec(
        promise="用问题、状态机和 KPI 三个维度区分容量评估与体验评估，并解释预启动窗口为何属于统计合同而不是删除不利数据。",
        principles=(
            "容量评估假设业务持续存在，关注给定传播、干扰和调度下的饱和吞吐/谱效。历史 <code>legacy_v1</code> 每次被调度用户可以拿全带，PF 平均量也按历史全带口径更新。它适合回归旧结果和比较满业务链路算法，但无法回答空闲比例、首包等待、小包抢占或按需资源利用。",
            "体验评估把业务到达、FIFO 包对象、按需 RBG、ACK/NACK、OLLA、SU/MU 方案和用户 KPI 放进连续 TTI 状态机。它必须区分 scheduled TBS、attempted payload、ACK goodput 与 padding；PF 默认按实际 scheduled TBS 记账。两种模式不是“快/慢”或“粗/精”开关，不能把 experience_v2 的某个参数关掉后称为 capacity 等价。",
            "预启动时间让有状态环节先进入稳定区：OLLA 从初值收敛、SRS/PMI/CQI 报告收齐、PF 历史量形成、队列进入代表性负载。仿真仍从 t=0 正常运行，只是正式 KPI 的统计窗口从例如 1 s 开始；预热期间形成的状态继续带入测量期。若重置队列或 OLLA，就不再是预热而是另一次实验。",
            "预热长度应由收敛诊断支撑，而不是永远固定 1 s。可以比较测量期前半/后半 BLER、OLLA、PRB 利用率和队列量；若仍漂移，延长仿真或预热。报告同时给总仿真时长、warmup、有效测量时长与覆盖率。",
        ),
        implementation=(
            ("模式显式分派", "SystemConfig/入口根据 mode/version 进入独立 capacity 或 experience 执行路径，并限制各自支持的 PF accounting、traffic 与 KPI。"),
            ("全程演化状态", "experience 从 TTI 0 生成业务、更新报告和 OLLA；<code>in_measurement</code> 只控制 KPI 累加，不跳过状态更新。"),
            ("窗口边界记账", "到达、服务、资源和 delay 样本标记是否属于测量期；跨过边界的 busy period 按明确 eligibility 处理。"),
            ("结果显式标注", "cell/user 结果携带 mode、warmup_tti、measurement_duration_s、PF 口径和近似说明。"),
        ),
        example_title="5 秒仿真、1 秒预启动究竟保留了什么",
        example=(
            "<p>0～1 s 内 UE 仍产生包、被调度、抽 ACK/NACK，SU/MU OLLA 与 PF R̄持续更新；SRS 报告也按周期到达。到 1 s 时不清空队列、不重置状态，只把 cell/user KPI 的正式计数开关打开。最终 PRB 利用率分母是后 4 s 的可用 DL 等价资源。</p>"
            "<p>若直接丢弃前 1 s 所有事件并从空状态开始统计，首包等待、负载和 OLLA 仍带冷启动偏差；若把前 1 s 的资源计入分母但不计业务，又会压低利用率。正确实现把“状态是否演化”和“样本是否进入统计”分开。</p>"
        ),
        checks=(
            ("路径隔离", "capacity 与 experience 有独立入口、允许参数与 golden regression，不共享含糊的 mode flag 分支。"),
            ("预热不重置", "warmup 边界前后队列、OLLA、PF 与报告状态连续，只有统计累加发生切换。"),
            ("收敛诊断", "测量期前后半的 BLER/OLLA/负载差异可见，未稳时结果标注或阻塞。"),
            ("分母正确", "所有速率、利用率和覆盖率只使用有效测量时长/资源，且跨窗口对象规则可复算。"),
        ),
        pitfalls=(
            "把 warmup 数据从数组头部切掉，却让系统状态也从统计起点重新初始化。",
            "用 capacity 的全带 PF 记账驱动 experience 的按需分配。",
            "比较两种模式的同名 throughput，却不说明业务、资源和分母语义不同。",
        ),
        source_paths=("src/superran/system.py", "src/superran/experience.py", "src/superran/results.py"),
    ),
    "experience": DetailSpec(
        promise="逐事件解释一个 DL TTI 中到达、报告、PF、SU/MU 规划、RBG 分配、BLER、队列、OLLA、PF 平均量与 KPI 的先后关系，并把用户追问的 RU/R̄ 维护口径钉成不变量。",
        principles=(
            "体验仿真是离散事件状态机，顺序会改变结果。当前 TTI 的业务先进入 FIFO，调度候选由有数据且非 outage 用户组成；基站只读取此时已经可用的 CSI/反馈。PF 先排序，SU/MU planner 再按队列和 TBS 查表构造方案。选定 grant 后才用真值 SINR抽 BLER、改变队列、更新 OLLA 和 PF 平均量。把 ACK/NACK 提前用于当次 MCS 就会读未来。",
            "经典 PF 的 RU 即代码中的 <code>r_avg[u]</code>，是每用户指数平均服务 credit。每个 D/S 下行调度机会的权重 <code>a=1/pf_window_tti</code>；所有用户旧值先按 (1−a) 衰减，获 grant 的用户再加 a×credit。U/G 时隙没有下行资源机会，因此不更新。默认 <code>scheduled_tbs</code> 用本次实际 n_rbg/MCS/rank 的 TB bytes，无论 ACK 与否都代表占用的调度机会；<code>acked_goodput</code> 可改为 ACK 字节。未调度用户 credit=0。",
            "这正是按需 RBG 的硬不变量：只拿 1 个 RBG 的用户不能按 17 RBG 或 <code>best_se[snap]</code> 更新。否则 R̄瞬间抬高约 17 倍，下一 TTI PF metric 被压低，小包用户会被系统性饿死。<code>legacy_fullband</code> 只用于反向验证/兼容，不得成为 experience_v2 的默认口径。",
            "资源分配使用真实 RBG index 集合，不只存一个数量。CarrierGrid 按 38.214 Type-0 与 BWP 对齐生成首尾部分组，并把每组真实 PRB 数一路传给 TBS、SINR、功控、MU 和 KPI。正常情况下以轮转 cursor 减少总从低频开始的偏差；大包 required_rbg 被钳到全带组数，自然占满带；多个小包依 PF 顺序消耗剩余 RBG，业务传完后尾料留空。",
        ),
        implementation=(
            ("到达与候选", "TrafficRuntime 在 TTI 边界生成包对象并进入每 UE FIFO；outage 用户保留队列但不进入本 TTI 候选。"),
            ("PF 排序", "按当前 rank/MCS 的满带潜在 TBS 除 r_avg；RR/max-CI/QoS-PF 走各自显式分支，tie-break 使用独立 scheduler 随机流。"),
            ("载波与反查", "CarrierGrid 生成连续完整的 RBG 边界；TbsLookup 按真实 PRB 前缀建表，只要求单调不减，并用 searchsorted(left) 找第一个够用位置。"),
            ("方案与传输", "SU/MU planner 用 required_rbg 和 queue-capped useful bytes；执行选中 grant，记录 rbg_indices、TBS、payload、padding、真实 SINR、BLER 与 ACK。"),
            ("双闭环更新", "传输结果更新 FIFO/首调度时间、SU或MU OLLA；inst[u] 累加本次 PF credit，D/S 调度机会末统一更新 r_avg 并累加测量期 KPI。"),
        ),
        example_title="1 RBG 小包为何不能按全带更新 RU",
        example=(
            "<p>设 PF 窗口 100 TTI，用户 A 用 1 RBG 发出 1,700 B，小包已完成；用户 B 是大包。正确 <code>scheduled_tbs</code> 给 A 的 inst 是约 1,700 B，R̄只增加 17 B。若错误使用 17 RBG 的 29 kB，R̄增加约 290 B，A 后续新包的 PF metric 会被压低一个数量级。</p>"
            "<p>反向验证应在 mixed 话务下把 accounting 故意切到 <code>legacy_fullband</code>，观察小包首包等待/含头速率显著恶化；同时 allocation trace 应显示实际 n_rbg 仍为 1。若两种口径完全没差，可能是分配器仍让每个用户吃全带，或 PF 平均量根本没参与排序。</p>"
        ),
        checks=(
            ("时序因果", "trace 中 MCS/metric 的输入状态来自传输前，ACK 后状态只影响下一 TTI。"),
            ("PF 记账", "每个 allocation 的 pf_credit、actual TBS 与 r_avg_before 可复算 TTI 末更新；1 RBG 不出现 17 RBG credit。"),
            ("资源集合", "RBG 边界连续覆盖全部 PRB，首尾部分组不丢失；同一 TTI SU grant 不重叠，MU 仅在明确 pair 内共享，used_indices 与占用直方图一致。"),
            ("字节守恒", "arrived=ACK+queued+inflight+dropped，padding 单独统计，NACK 后业务仍可追踪。"),
        ),
        pitfalls=(
            "把 RU 理解成用户长期全带谱效，而不是实际调度服务量的指数平均。",
            "每个小包虽只扣 1 RBG，却按满带 credit 更新 PF，造成看不见的长期饥饿。",
            "方案比较使用真实队列，但执行阶段重新计算另一套 MCS/RBG，trace 与结果不一致。",
        ),
        source_paths=("src/superran/carrier.py", "src/superran/experience.py", "src/superran/system.py", "tests/test_carrier.py", "tests/test_system.py"),
    ),
    "traffic": DetailSpec(
        promise="从两个经验 CDF 生成包对象，解释包大小缩放与包间隔缩放如何分别改变负载形态，并给出从目标 30%/50% PRB 利用率反校准话务而不污染正式统计的方法。",
        principles=(
            "一个话务模型至少由包大小分布和包间隔分布共同决定。每次到达分别从两个经验 CDF 逆变换采样：均匀随机数 u 经 <code>searchsorted</code> 找到第一个累计概率≥u 的 value。包大小决定单个 burst 需要多少资源，间隔决定同时活跃用户和排队重叠；只有平均比特率相同并不意味着 MU 概率、首包时延或 RBG 直方图相同。",
            "两个标量提供可解释校准轴。<code>size_scale=0.5</code> 把每次抽到的 payload 等比缩小，平均业务量约减半且 burst 更容易成为小包；<code>interval_scale=0.5</code> 把间隔缩短一半，平均业务量约翻倍，同时更易形成队列重叠和 MU 候选。两者乘积都影响 offered load，但对时域形态的影响不同，因此校准结果必须记录用了哪一轴。",
            "目标 PRB 利用率不是直接塞进调度器的占用概率。校准器在固定用户撒点、算法和业务模型下，选一组标量做短 probe，读取正式定义的 serving-cell PRB utilization，再搜索接近 10%/30%/50% 的参数。最终用独立或明示复用的正式 replication 重跑；校准 probe 不混入性能置信区间。",
            "所有用户可以共享一个 profile，也可按用户/业务类分配 video、XR 等多套 size/interval CDF。混合用户时不仅保存 profile 数量，还应保存每个 UE 的 profile_id 与 scale；否则用户级 KPI 的差异无法区分是无线条件还是业务模型造成。",
        ),
        implementation=(
            ("读取并校验 CDF", "UTF-8 两列文件解析 value/cdf，value 严格递增、CDF 单调且终点归一；路径以项目根稳定解析并按 mtime/size 缓存。"),
            ("独立随机采样", "每个 UE/profile 从 traffic 随机流抽 size 与 interval，应用 scale、单位换算和最小值，创建带 arrival_tti 的包对象。"),
            ("执行负载校准", "固定其他条件，对 size 或 interval 标量做 bracket/search probe，以实测 serving-cell PRB 利用率和容差选参数。"),
            ("正式仿真留痕", "结果记录 CDF 文件哈希/摘要、scale、用户 profile 分配、target、probe 轨迹和 achieved utilization。"),
        ),
        example_title="同样约 30% 负载，为何缩包与缩间隔的 MU 比例不同",
        example=(
            "<p>方案 A 把包大小乘 0.5、保持间隔；方案 B 保持包大小、适当拉长间隔，二者都可能校准到 30% PRB。A 会产生更多短 grant，0/1/2 RBG TTI 比例上升；B 的活跃时段更稀疏但一旦到达更像大包，17 RBG 峰更明显。</p>"
            "<p>若目标是研究 MU，通常选约 50% 并优先通过 interval 轴提高同时活跃概率；但仍需用相同算法正式测量，而不能把“50%场景”直接解释为 MU 比例 50%。负载是话务、用户位置、链路和调度共同结果。</p>"
        ),
        checks=(
            ("CDF 合法", "value/cdf 单调、单位、终点、均值和关键分位数可展示；同 seed 采样可复现。"),
            ("负载单调", "在统计波动容差内，size_scale 增大或 interval_scale 减小应提高 offered load/PRB 利用率。"),
            ("校准隔离", "probe 与 formal run 的种子/用途可区分，正式 CI 不把搜索过程当独立样本。"),
            ("用户可追溯", "每个 UE 能反查 traffic profile、两个 scale、到达字节和包间隔样本。"),
        ),
        pitfalls=(
            "直接把 target_prb_utilization 当作每 TTI 随机占用概率，跳过真实话务与调度。",
            "只看平均 Mbps，不看包大小/间隔形态，随后误解 MU 和首包时延差异。",
            "用正式结果反复调 scale 后仍把同一结果当预注册验证集。",
        ),
        source_paths=("src/superran/traffic.py", "src/superran/system.py", "src/superran/experience.py"),
    ),
    "kpi": DetailSpec(
        promise="把体验 KPI 的对象、起止事件、分子分母和覆盖率逐一说清，并解释单臂小区/用户分析、2~5 算法比较、逐 TTI 钻取与 Agent 自适应优先展示如何共存而不改变底层真值。",
        principles=(
            "体验速率必须先定义一个 DRB busy period 和可计入包。掐头去尾速率从首包第一次调度开始，到倒数一个完整 ACK 包结束；含头速率使用相同 payload 和尾部排除，但分母从首包到达开始，因此明确包含首包等待。首包时延单独量每个包从 arrival 到 first scheduled，不把完整传输或重传时间混进来。",
            "任何带 eligibility 的指标都要同时给覆盖率。仿真结束时尚未第一次调度的包不能填 0；未形成足够完整包的短 burst 不能硬算无限/零速率。结果应给 observed count、eligible count、share 与排除原因。这样，算法通过让困难用户“没有样本”来美化均值时会立即暴露。",
            "PRB 利用率是本小区在测量窗口内已用物理资源/可用资源；0..17 RBG 直方图以每个 DL 等价 TTI 的唯一 used index 数计数。mixed 业务常呈两头高：空闲 TTI 落在 0，大包或积压落在 17，小包填在低 RBG；这是一种预期形态而非硬编码通过条件。MU 配对比例则是 MU PRB/已用 PRB，与 MU 用户传输次数不同。",
            "呈现分小区级和用户级两个 tab。小区级看整体负载、尾部分位和模式；用户级展示每 UE 的无线条件、业务、吞吐、首包时延、资源、MU/BLER，并支持散点、时间序列与跨 UE CDF。Agent 可以根据用户问题给 KPI relevance score，把更相关卡片前置、其他折叠，但所有原始 KPI、公式和选择理由仍可查看，不能让 LLM 在库内偷偷改数。",
            "工作台是 experience_v2 的标准结果面，而不是运行结束后手工挑几张图。当前登记 27 项小区 KPI 与 25 项用户 KPI；可用项取决于 Result 是否真的携带相应数据。页面同时保留 95% CI、replication 数、KPI key、定义、告警、话务 profile 与校准轨迹，使一张卡片能向下追到统计样本和公式。<code>url</code> 只是便利入口，UTF-8 自包含 <code>html_path</code> 才是稳定离线产物；loopback 服务失败必须显式呈现，不能导致数值结果丢失或被悄悄替换。",
            "多算法页面不按算法分 tab：算法是贯穿总览、KPI 矩阵、用户 CDF、TTI 趋势和单 TTI 详情的固定颜色系列，基线不可隐藏；tab 表示读者正在回答的问题。每个算法臂必须携带同一 dataset 与逐位一致的 (master_seed, replication)，主 KPI 的候选对基线复用 Gate 3，并在 2~5 臂场景用 Holm step-down 收紧家族判决。只有 dataset 的生成前 prereg 同时匹配主 KPI 与基线标签时才允许产生 publishable winner；否则即使显著也保持 exploratory_unregistered。单 TTI 只能解释机制分叉，不能从一个事件外推算法收益。",
        ),
        implementation=(
            ("事件级采集", "包对象记录 arrival、first_scheduled、ACK/completion；allocation 记录 TTI、RBG、模式、TBS、payload、SINR/BLER/draw、ACK、OLLA 前后、PF metric 与用户。"),
            ("窗口与 eligibility", "warmup 后才累加正式资源；busy period、包和用户样本按明确跨界规则进入统计，并记录 coverage。"),
            ("双层聚合", "先生成每 UE 指标和原始分布，再在小区层汇总均值/分位/CDF；小区均值不替代用户表。"),
            ("自适应呈现", "kpi_view 根据 intent/relevance 元数据排序卡片、选择图形和折叠次要项；数值只读 Result contract。"),
            ("渲染标准工作台", "<code>render_html()</code> 用 selection 组装精简 Agent focus、27 项小区/25 项用户注册表、CI 卡片、RBG 直方图、负载 gauge、逐 UE 图、经验 CDF、明细和定义折叠区。缺失字段只使对应卡片不可用，不伪造零值。"),
            ("导出与分享", "共用 <code>webui</code> 操作栏下载完整 JSON/小区 CSV/用户长表 CSV、复制摘要、截取当前页签、调用 Web Share 或回退复制，并支持打印/PDF；所有动作只读 Result。"),
            ("落盘并提供入口", "<code>write_kpi_report()</code> 写入 artifacts/kpi 的 UTF-8 HTML，返回 html_path、url/serve_error、tabs、actions、完整 selection 和支持清单。<code>sr_system_sim</code> 捕获呈现层异常并显式返回 error，同时保留已经完成的仿真 Result。"),
            ("保存可比较证据", "每个单臂页面同步保存严格 JSON sidecar：算法标签、逐 replication 小区 KPI、RngBook 与 sampled/full TTI trace。sampled 用一半预算放共同均匀锚点、一半放 MU/NACK/重传/多 UE/outage 事件。"),
            ("构建比较工作台", "<code>kpi_compare</code> 先硬校验 dataset/模式/载波/TDD/话务/KPI/RngRun，再计算候选对基线的配对差值，渲染分组柱形图、KPI 矩阵、用户 CDF、TTI 折线和同 TTI grant 表；缺采样与真实 idle 用不同状态。"),
        ),
        example_title="均值更好但体验更差：覆盖率能把问题指出来",
        example=(
            "<p>算法 A 的已观测首包时延均值为 8 ms、覆盖率 99%；算法 B 均值为 6 ms，但只有 70% 包在仿真结束前获得第一次调度。若把未观测包直接排除只报均值，B 看似更好，实则 30% 包被饿死或窗口不足。</p>"
            "<p>正确页面会并列显示均值/P95、observed share 和未观测数量，并在用户 tab 定位哪些 UE 缺样本。再结合 0..17 RBG 直方图、PF credit 与队列时间序列，可以判断是负载过高、资源留空、outage 还是记账口径导致。</p>"
        ),
        checks=(
            ("公式事件一致", "每个 KPI 的起止事件可从 packet/allocation trace 重算，含头与掐头只差分母起点。"),
            ("资源分母一致", "PRB 利用率、RBG histogram、MU share 使用同一测量窗口和 S slot 等价折算。"),
            ("覆盖率不缺席", "时延/速率指标同时返回 count、eligible、observed share 和排除策略。"),
            ("展示不改真值", "Agent 排序前后 KPI JSON 完全相同，页面能展示推荐理由并展开全部指标。"),
            ("产物合同完整", "体验模式成功时 kpi_view 同时含 html_path、双 Tab、supported KPI 与排序证据；服务失败只改变 url/serve_error。"),
            ("导出逐值复算", "下载 JSON/CSV 可解析且与页面卡片同值；浏览器截图实际产生 PNG 或安全回退 SVG，离线 file:// 仍可用。"),
            ("跨算法严格对齐", "不同 dataset、时长、话务、KPI 窗或 RngRun 任一不一致时比较页拒绝生成；算法配置差异完整展示。"),
            ("TTI 诊断不越权", "趋势点可进入同一绝对 TTI；每个 grant 的 RBG/MCS/rank/SINR/BLER/draw/ACK/OLLA/PF 可复盘，但页面不从该点生成收益结论。"),
        ),
        pitfalls=(
            "只给小区均值，隐藏边缘用户、未观测包和用户间业务差异。",
            "把 MU 用户传输次数或 MU TTI 数当作 MU PRB/已用 PRB。",
            "LLM 为回答问题临时计算一个未进入 Result contract 的指标并与正式 KPI 混排。",
            "只截 KPI 首屏发报告，不同时保留折叠指标、告警、用户明细和 selection 理由。",
            "给每个算法单独做一个 tab，迫使读者凭记忆比较，或让同一算法在不同图上随机换颜色。",
            "把两根带单臂 CI 的柱子肉眼相减，绕过配对差值 CI、Wilcoxon 与多候选校正。",
            "只保存发生调度的 TTI，再把没有记录的 TTI 当成 idle；sampled 缺失必须与真实零占用分开。",
        ),
        source_paths=("src/superran/experience.py", "src/superran/kpi_view.py", "src/superran/kpi_compare.py", "src/superran/webui.py", "src/superran/rng.py", "src/superran/results.py"),
    ),
    "interference": DetailSpec(
        promise="把 S、I、N、邻区负载与逐 RBG 功率控制放在同一公式中，解释为什么抬高 RBG0、压低其他 RBG 即使总功率不变也可能降低性能。",
        principles=(
            "SIR 只描述信号与干扰，SINR 还包含噪声；必须在同一功率参考面上先用 S/SIR 得到 I，再用 S/SINR 得到 I+N，二者相减才是 N。若用不同的 BF 状态、RB 带宽或 dB/线性单位，可能算出负噪声，这不是可截零的小误差，而是口径不一致的报警。",
            "邻区 PRB 利用率是干扰输入，表示邻区每个资源被激活的概率/占用序列；本小区 serving-cell PRB utilization 是调度输出。二者名称相似但因果方向相反。邻区负载可按小区、RBG和时间抽样，并通过 CRN 在算法 A/B 中保持一致；本小区不能用这个输入直接替代实测结果。",
            "逐 RBG 功率重分配会同时改变目标信号和对其他小区的干扰。如果所有小区同步把 RBG0 抬高，RBG0 的信号与主要同频干扰可能同比增长，在噪声非主导区 SINR几乎不升；其他 16 个 RBG 功率下降后，信号下降，而来自未同步邻区或噪声并不同比下降，SINR普遍变差。即便只有服务小区调整，频率选择性、MCS/TBS 离散、饱和和 <code>log(1+γ)</code> 的凹性也使“集中功率”通常不等价于均匀功率。",
            "系统体验还增加资源匹配效应：业务包可能需要多个 RBG 才完整发送。把功率集中到一个 RBG，那个 RBG 的额外容量可能变成 padding，而其余 RBG 少一点就跨过 MCS/TBS 门限，导致需要更多 RBG或多一个 TTI。性能下降不是总功率丢了，而是功率在频率、干扰和离散业务上的边际价值不同。",
        ),
        implementation=(
            ("拆分基础链路预算", "为每 UE/RBG 保存服务信号、每邻区干扰分量和热噪声，统一在线性功率域。"),
            ("生成邻区占用", "按 neighbor load 与独立随机流产生 q_c,r(t)，并让 A/B 复用同一 realization。"),
            ("应用功率向量", "服务与邻区各自的 q 乘到对应 S/I；约束 q 的非负、总功率/每天线功率和可用 RBG。"),
            ("回灌链路与调度", "逐 RBG SINR 进入 MCS/TBS 或有效聚合；记录功率、占用、门限变化和最终 useful bytes。"),
        ),
        example_title="RBG0 +3 dB、其余略降为何总吞吐可能下降",
        example=(
            "<p>把均匀 17 份功率中的 RBG0 加倍，为保持总功率，其余每份约需减少 1/16 份。若邻区也在 RBG0 高功率，目标与干扰一起加倍，RBG0 SINR 提升很小；其他 RBG 的目标信号下降，但热噪声不变，16 个资源都略有损失。</p>"
            "<p>即使无邻区，RBG0 原本已处于高 SINR/MCS 饱和区，+3 dB 可能不跨 TBS 档；多个中等 RBG 的小幅下降却可能跨越 MCS/BLER 门限。验证应同时画逐 RBG S/I/N、MCS、TBS 和包 padding，而不是只看总功率守恒。</p>"
        ),
        checks=(
            ("S/I/N 可重构", "逐项线性功率能复算 SIR、SINR 与 IoT，任何负 N/非有限值直接失败。"),
            ("输入输出分离", "neighbor_prb_util 只控制邻区干扰，serving_cell_prb_utilization 只由本小区真实 grant 统计。"),
            ("功率守恒", "每 TTI/RBG 功率向量满足总功率及所选每天线约束，诊断与实际链路使用同一向量。"),
            ("机制证据", "功率重分配结果附逐 RBG SINR/MCS/TBS/padding，能解释总 KPI 变化而非只报均值。"),
        ),
        pitfalls=(
            "认为总功率相同就必有相同容量，忽略频率选择、干扰、噪声和离散 TBS。",
            "把邻区配置的 30% load 当作本小区结果页的 30% PRB 利用率。",
            "所有小区共享同一功率/占用随机序列却未声明，制造不真实同步。",
        ),
        source_paths=("src/superran/interference.py", "src/superran/power_control.py", "src/superran/experience.py"),
    ),
    "rng": DetailSpec(
        promise="把一个 master seed 展开成按用途隔离、按 replication 可复现、与 workers 无关的随机流，并说明共同随机数为何能提高 A/B 比较精度而不会让样本失去独立性。",
        principles=(
            "无线系统仿真同时含 drop、cluster、traffic、BLER、scheduler tie-break、neighbor load 等随机性。若所有模块共享一个全局 RNG，新增一次日志抽样或改变线程顺序就会消耗不同数量的随机数，后续结果整体漂移。正确做法是从 master seed 和稳定用途标签派生子流，让每个模块只消费自己的 Generator。",
            "replication 是统计独立单元：同一 replication 内的用户/TTI 往往相关，不能把它们全部当 iid 样本放大 n。不同 replication 使用不同派生 seed；算法 A/B 在同一 replication 下复用 drop、traffic、BLER 和 scheduler 子流，形成 CRN 配对。跨 replication 仍独立，因此既降低差值方差又保留有效样本数。",
            "workers 只决定任务在哪个进程、以什么完成顺序执行，不能参与 seed 派生。每个任务的随机身份由 experiment id、replication index、stream tag 等稳定字段决定；结果最终按稳定 key 排序。这样从 1 worker 改成 8 workers，数组内容和统计结果保持逐位或明确容差一致。",
            "至少 6 次、默认 8 次是门槛/工程默认，不是“8 次一定足够”的统计定律。Gate 2 仍要检查置信区间宽度、paired differences、cluster 单位和有效样本；效果接近噪声或尾部指标不稳定时应增加 replication，而不是手算一个更好看的检验。",
            "KPI无定义与replication缺失不能等同于随机缺失。汇总同时报告n_total、有限n_rep、n_nonfinite和coverage；均值/区间只覆盖有限子集。正式A/B配对输入则更严格：必须是非空一维且全部有限，不能静默删掉失败replication后得到更窄区间。",
        ),
        implementation=(
            ("派生命名子流", "RngPlan/SeedSequence 用 master、replication 与用途标签生成 drop/traffic/bler/scheduler/neighbor 等 Generator。"),
            ("绑定任务身份", "任务创建时就冻结 replication seed 与配置哈希，提交到 worker 后不再依赖全局完成顺序。"),
            ("A/B 复用 realization", "两算法读取相同外生随机数据；算法内部新增随机性使用独立 algorithm tag，不能挪动公共流。"),
            ("稳定汇总", "结果按replication/drop/user key排序并保存seed lineage；单臂KPI显式报告finite coverage，paired analysis只接受相同key且全部有限的一维数组。"),
        ),
        example_title="为何增加 workers 不应改变一条 BLER 抽样",
        example=(
            "<p>replication 3、用户 7、TTI 120 的 BLER 均匀数由 master+replication+<code>bler</code> 流决定。1 worker 时它可能第 3 万次被调用，8 workers 时在另一个进程更早调用，但 Generator 初态和本流内事件顺序相同，因此抽样一致。</p>"
            "<p>若使用全局 <code>np.random</code>，调度任务完成顺序会改变消费顺序；甚至在 traffic 模块多抽一次数也会改变所有后续 BLER。worker invariance 测试应比较完整 allocation/KPI 或稳定摘要，而不只比较均值相近。</p>"
        ),
        checks=(
            ("同 seed 复现", "相同配置、master、replication 与 workers 生成相同 manifest、随机摘要和结果。"),
            ("流隔离", "只改变 traffic 模型不改变 drop/BLER realization；新增模块使用新 tag 不扰动旧流。"),
            ("CRN 配对", "A/B 每个外生随机 key 一一对应，缺失 pair 不静默丢弃并有覆盖率。"),
            ("统计单元正确", "CI 的 n 是独立 replication/drop cluster 数，而不是相关 TTI 或用户行数。"),
            ("缺失不隐身", "3次中1次NaN时输出n_total=3、n_rep=2、coverage=2/3；A/B输入含NaN或二维数组时统计函数硬失败。"),
        ),
        pitfalls=(
            "在库中调用全局 np.random 或 Python random，破坏流隔离与多进程复现。",
            "把 worker id 拼进 seed，导致并行度一变结果就变。",
            "把同一 replication 的数万 TTI 当成数万独立样本，置信区间虚假变窄。",
        ),
        source_paths=("src/superran/rng.py", "src/superran/system.py", "src/superran/analysis.py"),
    ),
})

DETAIL_SPECS.update({
    "gates": DetailSpec(
        promise="把三道门理解成三种不同的可失败合同：Gate 1 判断数据能否作为实验输入，Gate 2 判断比较是否可信，Gate 3 判断措辞是否达到可发布标准；任何一道门都不能由后续人工解释替代。",
        principles=(
            "Gate 1 面向数据体检。它不评价某算法好不好，而是确认 shape、dtype、有限性、单位、功率、路径损耗、干扰、h_true/h_est 角色、样本和 metadata 足以支撑后续实验。一个数据集可能能被 NumPy 加载，却因 h_est 来源缺失、跨站状态复制或路径损耗异常而不能用于结论。Gate 1 应在任何大规模算法运行前执行。",
            "Gate 2 面向结果可信度。比较必须使用相同问题、相同数据角色、共同随机数和正确独立统计单元；报告 paired differences、cluster-aware CI、效应量、失败/缺失覆盖率和稳定性。均值方向符合预期不等于通过：如果置信区间过宽、样本太少或 A/B 实际用了不同 traffic realization，就只能保留观察值。",
            "Gate 3 面向发布。它检查实验问题与主指标是否预先冻结，结论是否超出已模拟场景，是否把工程近似误写成标准或现场真值，图表能否回指配置和数据，限制是否与数字同屏。它约束的是措辞强度：同一份结果可以支持“本配置中观察到”，未必支持“普遍提升”。",
            "守恒是不依赖算法优劣的强不变量。体验仿真中的业务字节、物理 RBG、发射功率和用户资源 attribution 都应有可加和账本。守恒失败时，不能靠 KPI 看起来合理来放行，因为丢字节、双计 MU PRB 或 padding 混入 goodput 往往只在特定负载暴露。",
        ),
        implementation=(
            ("构建前置检查", "生成结束立即运行 validator/gate1，输出逐检查 pass/fail、实际值、阈值、样本位置和修复建议。"),
            ("绑定比较合同", "analysis 读取 A/B manifest，核对 dataset、seed streams、KPI 定义、replication key 与缺失 pair，再进行配对统计。"),
            ("审查发布语义", "Gate 3 将 preregistration、effect/CI、覆盖率、边界和结论句绑定；失败时给出允许的降级表述而非静默通过。"),
            ("保存可审计证据", "每道门的输入摘要、版本、结果与时间写入产物，最终 HTML/JSON 只引用这些记录。"),
        ),
        example_title="均值提升 8% 为什么仍可能不能发布",
        example=(
            "<p>8 个 replication 中 A−B 的均值是 +8%，但差值 CI 为 −4%～+20%，两个边缘 UE replication 因未完成 burst 被排除，且算法 B 使用了另一份 traffic seed。均值是真的“算出来了”，却不能把差异归因于算法。</p>"
            "<p>Gate 2 应因 CRN 不一致和区间跨零失败，Gate 3 阻止“提升 8%”。可报告“当前非配对样本均值高 8%，证据不足以判断算法效应”，并修复 seed/覆盖率后重跑；不能手算只保留有利用户或换一个单侧检验救结论。</p>"
        ),
        checks=(
            ("门的职责不串", "Gate 1 不用结果均值救数据，Gate 2 不用业务常识救统计，Gate 3 不把 warning 藏进脚注。"),
            ("失败可行动", "每个 fail 含检查对象、实际值、期望、定位字段和下一步，不只返回 false。"),
            ("守恒逐层", "字节、RBG、功率和用户 attribution 在 toy、压力和正式运行中均闭合。"),
            ("证据不可漂移", "报告中的门结果来自当前 manifest/config hash，旧 HTML 不能冒充新代码状态。"),
        ),
        pitfalls=(
            "把 Gate 当成最后的格式检查，长仿真跑完才发现输入本来不可用。",
            "看到趋势符合预期就绕过统计门，用“总体来看”代替证据。",
            "守恒只检查全局总数，不检查每 UE/TTI，局部双计与丢失互相抵消。",
        ),
        source_paths=("src/superran/validate.py", "src/superran/gates.py", "src/superran/analysis.py", "src/superran/results.py", "src/superran/provenance.py"),
    ),
    "tests": DetailSpec(
        promise="说明测试清单背后的风险模型：哪些检查证明纯函数正确，哪些证明模块合同不漂移，哪些必须靠真实仿真、压力与浏览器才能发现，并给出失败时的定位顺序。",
        principles=(
            "测试数量不是可信度本身。最有价值的是把高风险不变量分层：公式和索引用小尺寸解析案例；数据角色、配置序列化和 API 用合同测试；算法在受控信道上做机制测试；长系统状态用多 seed/负载压力；文档与 KPI 页面用真实 Chromium 检查交互、公式和响应式。每层发现的错误不同，不能用数百个快速 unit test 替代一次端到端链路。",
            "默认路径必须有强回归。引入 NEBF/PEBF 时，EBF 作为历史默认应保持逐位或明确数值容差；统一端口顺序时，所有消费方要么同步迁移，要么在边界显式 permutation。回归不是要求所有新模式与旧结果相同，而是确保未选择新能力的用户不会被隐式改变。",
            "压力测试要覆盖状态空间而不是只增大一次规模。体验仿真至少组合空/满/混合业务、10/30/50%负载、不同 seed、UE 数、S/D slot、SU/MU、ACK/NACK 极端、warmup 边界与 worker 数。每次都检查有限性、守恒、利用率范围、覆盖率和可复现，而不只是程序没崩。",
            "文档测试同样是产品合同。生成器必须与当前源码/预设/Skill 同步；每页都有精简和详细层；每个公式都带符号表与解释；KaTeX 在真实浏览器以 strict 模式通过；hash 路由、搜索、主题、深度切换、localStorage、移动端溢出和打印均需验证。",
        ),
        implementation=(
            ("快速内环", "pytest/直接脚本覆盖纯函数、shape、公式、索引、配置和 regression；开发时秒级运行。"),
            ("模块合同", "扫描 AST/数据文件，验证所有公开 symbol、MCP 工具、预设、测试与 Skill 被文档/manifest 收录。"),
            ("仿真压力", "使用稳定 seed matrix 跑多负载/多模式，保存 KPI 与关键 trace，检查守恒、范围、单调和 worker invariance。"),
            ("真实 UI QA", "Playwright 在 desktop/tablet/mobile 打开 file:// 文档，切路由/深度、渲染公式、搜索和截图，捕获 console/page error。"),
        ),
        example_title="一个“单测全绿”但系统结果错误的典型路径",
        example=(
            "<p>TBSLookup 的 searchsorted 单测正确，PF 公式单测也正确，但集成代码把 1 RBG grant 的 credit 传成了 fullband TBS。两个模块各自都通过，只有 mixed 话务的长状态机出现小包等待恶化。</p>"
            "<p>因此需要反向机制测试：固定同一信道/话务，比较 scheduled_tbs 与故意错误 legacy_fullband，验证 allocation 中 pf_credit 与 n_rbg 一致，并要求小包 KPI 对错误口径显著敏感。若没有敏感性，不应宣布按需分配链路已生效。</p>"
        ),
        checks=(
            ("风险到测试映射", "每项关键不变量能指出至少一个会失败的测试，而不是只列测试文件名。"),
            ("默认回归", "新特性关闭时历史基线逐位/容差一致；差异有批准的 migration note。"),
            ("压力有判据", "多 seed 运行检查物理范围、守恒、覆盖率和机制，不以‘完成运行’作为 pass。"),
            ("UI 有截图与 JSON", "浏览器 QA 同时产出机器可判定报告和可人工查看截图。"),
        ),
        pitfalls=(
            "把测试文件数或通过数当成模块‘彻底可行’的证明。",
            "压力测试只扩大 num_tti，不改变业务、负载、模式和随机边界。",
            "静态搜索到公式文本就算文档通过，没有在 Chromium 中验证 KaTeX 与切换交互。",
        ),
        source_paths=("tests/", "scripts/run_developer_guide_qa.py", "tests/test_developer_guide.py", "src/superran/benchmarks.py"),
    ),
    "tools": DetailSpec(
        promise="从 Agent 的任务意图而不是 35 个函数名理解 MCP 工具：如何发现能力、冻结计划、生成/校验数据、运行算法和系统仿真、注册外部结果并交付证据。",
        principles=(
            "MCP 工具是窄接口，不是让 LLM 随意拼底层函数。每个工具应有稳定输入 schema、明确副作用、结构化错误、产物标识与 lineage。返回摘要适合对话决策，大数组和长报告通过 artifact 路径交付。工具名是否友好不如合同是否能阻止错误角色、未知配置和静默降级重要。",
            "正确调用顺序通常是 discover→plan→generate→gate→run→analyze/deliver。能力发现告诉 Agent 哪些后端真实可用；计划把用户问题转成冻结实验；生成与 Gate 1 建立数据可信度；算法/系统工具只消费已通过的数据；统计和交付工具再形成结论。直接从自然语言跳到某个 throughput 工具，容易遗漏基线、公平条件和门。",
            "工具错误要区分用户可修复、可选后端缺失、合同失败和内部异常。比如不存在的 preset 应返回合法选项，direct RT 缺失应报告 capability，first-party source contract 失败应硬停止；不能捕获异常后改用 toy data。",
            "外部算法通过注册结果合同接入，而不是让服务端执行任意用户代码。注册值要带 dataset/sample key、算法版本、输入角色和 shape，随后走同一 Gate 2/3。这样既保持安全边界，也保证自研算法与内置算法在相同证据框架中比较。",
        ),
        implementation=(
            ("发现与规划组", "capabilities/presets/plan/decisions 工具回答‘能做什么、准备怎么做、还缺什么决策’，不产生结论。"),
            ("数据与物理组", "generate/deliver/measure/channel/scene 工具管理数据集、可观测量和场景，所有返回值带 dataset/artifact id。"),
            ("算法与系统组", "beamforming/link/MU/system/experience 工具读取已解析配置，返回结果合同和诊断，而不是裸数组。"),
            ("证据与外部组", "gate/analyze/register 工具校验数据/结果、配对统计并接入外部输出，最终 deliver 形成可读产物。"),
        ),
        example_title="Agent 为什么不应第一步就调用系统吞吐",
        example=(
            "<p>用户说“比较 NEBF 与 PEBF 的 MU 体验”。若直接调用 system run，默认数据源、负载、MU 规则、warmup 和主 KPI 可能都未确认。正确做法先 discover，计划冻结 50%目标负载、experience_v2、共同随机数、SU/MU useful bytes 与用户级首包时延，再生成/校准数据。</p>"
            "<p>随后两分支共享 dataset 和随机流，只改 power_constraint；每个 result 携带 power diagnostics、MU trace、Gate 与 KPI。Agent 最终可以按用户意图前置 NEBF/PEBF 机制指标，但仍提供全部原始结果和限制。</p>"
        ),
        checks=(
            ("schema 可拒绝", "未知枚举、非法单位、缺字段和不支持组合在入口即报结构化错误。"),
            ("无静默降级", "后端/数据缺失不会自动切换模型；任何替代必须进入返回值和计划。"),
            ("产物可追踪", "tool response 的 id/path 能反查配置、源码版本、seed、输入与 Gate。"),
            ("全量清单同步", "页面工具数量与 server AST 一致，每个公开 sr_* 工具都有摘要、签名与分组。"),
        ),
        pitfalls=(
            "把 35 个工具做成彼此平级菜单，Agent 不知道何时必须先过 Gate。",
            "为了对话流畅捕获错误后返回空数组/默认结果，用户看不到降级。",
            "让 MCP 服务端直接执行外部任意 Python，以方便接自研算法。",
        ),
        source_paths=("src/superran/server.py", "src/superran/plan.py", "src/superran/deliver.py"),
    ),
    "skill": DetailSpec(
        promise="解释 channel-sim Skill 如何把无线问题约束成可验证流程，为什么四项可见计划、三道硬门和 notes/blocked 状态是防止 Agent 越过证据边界的执行协议。",
        principles=(
            "Skill 不是提示词装饰，而是会改变执行顺序的规范。面对谱效、信道估计、MU、体验速率等任务，Agent 先定义目标、基线、主 KPI 与控制变量，再生成数据并过 Gate 1；只有数据可用才跑比较，Gate 2/3 之后才写强结论。流程的价值是把“先看到数字再找解释”的自由度压缩掉。",
            "用户可见计划固定为四项：对齐目标、生成数据、跑对比、写结论。内部可以有大量检查，但不能拆成十几条任务制造完成感。每一步只有在对应证据完成后才标 completed；门未通过时保持失败/阻塞信息，不能用 notes 写‘仅供参考’后继续声称趋势。",
            "硬门还约束语言。当样本、统计或数据合同不足时，禁止用“总体来看”“趋势上”“大部分样本”绕过，也禁止临时手算另一个检验救结论。可做的是补数据、修配置、缩小适用范围或明确报告观察值。Skill 因此把文风与实验状态绑定，而不是只规范命令。",
            "references 按任务路由：数据生成、系统仿真、信道估计或统计结论读取不同细则。主 Skill 必须完整读，相关 reference 只按需加载，避免规则缺失与上下文淹没并存。项目手册应把这些路由和当前文件实时扫描出来，防止 Skill 改了但文档仍讲旧流程。",
            "Skill 还规定何时可以暂停或缩小结论。外部后端不可用、数据门失败、有效 replication 不足分别属于不同状态：前两者优先修复输入，后者可以追加样本或只交付有限观察。Agent 应把阻塞条件、已经完成的安全检查和恢复入口一起留下；不能因用户着急而跳过体检，也不能把‘工作量很大’误报成证据阻塞。",
            "对长时间任务，Skill 要求把中间产物也做成可恢复检查点：计划、resolved config、dataset id、Gate 结果、replication 清单与失败日志分别落盘。重新进入任务时从最近一个已验证边界继续，而不是复用一段无法确认版本的内存描述。这样即便实验跨会话或跨机器，用户仍能看出哪些证据已完成、哪些必须重跑。",
        ),
        implementation=(
            ("触发与宣告", "任务命中信道/系统仿真关键词时加载 Skill，并在 commentary 说明它为何适用、造成了哪些执行约束。"),
            ("冻结四项计划", "计划只显示目标、数据、对比、结论四层；每层内部用产物和 Gate 记录承载细节。"),
            ("按 reference 执行", "根据当前任务读取必要 reference，运行规定脚本/体检，不自行发明捷径或弱化门。"),
            ("证据驱动交付", "final 报告先说结论状态，再给配置、样本、Gate、效应/CI、边界和产物路径。"),
        ),
        example_title="Gate 2 未通过时 Skill 应怎样改变回答",
        example=(
            "<p>假设 NEBF−PEBF 的 MU 中位吞吐为 +2%，但 paired CI 很宽且跨零。普通叙述容易写成“总体上 NEBF 略优”；Skill 明确禁止。Agent 应说“当前样本未支持方向性结论”，展示观察差、CI 和 replication 数，并继续补样本或检查配对。</p>"
            "<p>若用户只要探索性观察，可以在降低措辞强度后交付，但不能把 blocked/limited 隐藏。计划中的“写结论”也只有在完成有限结论与边界后才标完成，而不是把实验失败视为没有产物。</p>"
        ),
        checks=(
            ("触发可见", "命中任务时 commentary 明示 Skill 与约束，用户知道为何先体检或暂停。"),
            ("四项不膨胀", "计划严格四层且状态与真实 Gate/产物一致。"),
            ("门不可绕", "失败状态下自动/人工生成的结论不包含被禁止的强趋势措辞。"),
            ("文档同步", "主 SKILL 与 references 文件路径、摘要和数量由当前树生成。"),
        ),
        pitfalls=(
            "读了 Skill 却继续先跑大实验、后补计划，只在 final 引用规则。",
            "把内部检查全部列成用户可见十几项计划，形式完整但目标失焦。",
            "Gate 失败后改用一个未预注册指标，让结论看起来通过。",
        ),
        source_paths=("skills/channel-sim/SKILL.md", "skills/channel-sim/references/"),
    ),
    "presets": DetailSpec(
        promise="把预设理解成可追溯配置层而不是复制粘贴 YAML：解释默认值、硬件 profile、信道 preset、系统场景和用户 override 的合并次序，以及如何证明最终 resolved config 正是仿真执行的配置。",
        principles=(
            "预设的目标是复用已审核的一组参数与语义，不是给配置起一个短名字。一个场景通常包含载波/天线、信道源、用户撒点、干扰、SRS/CSI、算法、traffic、scheduler、warmup 和 KPI。每一层有所有者；例如预置天线默认由 hardware profile 注入，用户只改 fixed_downtilt 时不能顺带丢掉极化和端口顺序。",
            "合并必须是确定且可解释的。常见顺序为 schema 默认→硬件/信道 preset→系统场景→用户显式 override；深层字典按字段合并，列表是否替换需明示。最终运行只读取 resolved config，并在 manifest 保存来源链与每个关键字段的 provenance。否则同名 preset 更新后，历史结果无法复现。",
            "信道 preset 与系统场景不是同一层。前者描述数据源、channel model、阵列、频率与样本；后者增加 TTI、业务、调度、负载目标和 KPI。capacity 与 experience 也应选择各自允许的场景合同。页面中的全量清单由 YAML 动态扫描，详细阅读重点是字段语义、继承和边界，而不是重复展示每行文本。",
            "实测 CDF 到位后应作为版本化资产进入 traffic profile：文件哈希、单位、采样规则和 scale 与 preset 一起记录。校准得到的 30%/50% scale 是场景/算法/用户数相关结果，不应写成永远有效的全局常数；可以缓存，但命中条件必须包含影响负载的配置哈希。",
        ),
        implementation=(
            ("加载分层配置", "解析 presets.yaml/system_presets.yaml 与 schema 默认，拒绝未知字段、重复名称和非法枚举。"),
            ("应用 profile 与 override", "硬件解析器补齐预置 64T/256T 合同，场景层再合并 traffic/scheduler；用户 override 只覆盖显式字段。"),
            ("生成 resolved snapshot", "运行前写出完全展开的 config、preset 来源、关键 provenance、文件哈希和版本，后续模块只读该快照。"),
            ("动态生成参考页", "生成器扫描当前 YAML，把摘要放精简视图、完整字段与来源放参考项；数量测试防止新增 preset 漏文档。"),
        ),
        example_title="只改 6° 下倾时，哪些字段必须保持不变",
        example=(
            "<p>用户从 company_64t profile 把 <code>fixed_downtilt_deg</code> 由 6 改为 4。resolved config 仍应保留 pol-h-v 端口顺序、+45/−45°、水平 0.5λ、垂直 0.67λ、1驱3、110°/65°临时方向图和载波配置；只有 F 的相位斜坡与 calibration_id 改变。</p>"
            "<p>若浅层字典覆盖把整个 <code>bs_antenna</code> 替换为只有一个 tilt 字段的对象，其他硬件真相会退回库默认并悄悄漂移。provenance diff 和 F 不变量回归应立即指出这一点。</p>"
        ),
        checks=(
            ("解析确定", "相同 preset+override 总产生字节稳定/语义稳定的 resolved config，与加载顺序无关。"),
            ("来源可见", "关键字段能显示 default/profile/scene/user 哪一层最后赋值。"),
            ("历史可复现", "manifest 保存展开配置和外部资产哈希，不依赖未来同名 preset 内容。"),
            ("全量同步", "当前 YAML 中每个预设都在页面和测试中出现，新增/删除会使生成器 check 失败。"),
        ),
        pitfalls=(
            "用浅层 dict.update 合并嵌套天线/traffic 配置，改一个字段丢掉整块默认。",
            "历史结果只保存 preset 名，没有保存当时展开值和文件版本。",
            "把某次 30% 负载校准 scale 写回通用 preset，不带用户数/算法/场景命中条件。",
        ),
        source_paths=("presets/presets.yaml", "presets/system_presets.yaml", "src/superran/sysscenes.py", "src/superran/hardware.py"),
    ),
    "extension": DetailSpec(
        promise="给出新增算法、数据源、KPI 或 MCP 工具时不破坏可信度的统一方法：先选择稳定窄腰、定义角色与不变量，再接实现、测试、证据和文档。",
        principles=(
            "扩展的第一问不是“把函数放哪”，而是“它消费和产生什么合同”。新预编码器读取 h_est 与功率配置，输出物理 Q 与诊断；新 KPI 读取正式 Result 中的事件/聚合，输出值、单位、coverage 与可视化建议；新数据源输出与现有 Dataset 同构的 h_true/h_est 和 metadata。若必须绕过窄腰读取某个后端私有对象，先扩合同而不是在下游写特例。",
            "每个新能力都要声明信息边界和失败模式。算法是否使用 oracle 真值、估计器需要哪些先验、外部文件单位、对 capacity/experience 哪些模式有效、缺数据时是 unavailable 还是 hard fail。默认路径保持原行为，新模式由显式枚举选择；不能为了兼容将未知值悄悄映射到最接近选项。",
            "验证从一个能解析复算的 toy 开始，再做不变量、默认回归、机制反例、压力与端到端。只验证“新算法在某样本更好”不够，还需构造它应更差或退化的条件。例如 NEBF 的验证必须同时有 SU 接近 EBF 与 MU 因干扰低于 PEBF 的样本，证明实现响应机制而非固定排序。",
            "文档是接口的一部分。新增公式必须使用注释卡给出所有符号、逐项解释和代码落点；新增图旁必须有因果说明；新增模块/API/tool/preset 由生成器自动收录；概念章的精简/详细版则需要人工写清原理、实现、算例、验证和误区。",
        ),
        implementation=(
            ("定义窄腰与 schema", "先写 dataclass/Pydantic/Result 字段、单位、shape、角色、枚举和向后兼容策略。"),
            ("实现纯核心", "让算法核心接收 NumPy/稳定对象并返回数值+diagnostics，server/UI 只做适配。"),
            ("接入流程与证据", "在 plan/generate/system/gates 中选择最小必要入口，补 lineage、随机流和失败信息。"),
            ("补全验证与文档", "toy→unit→contract→stress→browser，更新概念详细版与自动参考清单，运行 stale check。"),
        ),
        example_title="新增用户级 jitter KPI 的完整落点",
        example=(
            "<p>先定义 jitter 的事件对象（相邻成功包完成间隔或时延变化）、单位 ms、测量窗口、至少需要的样本数和未观测覆盖率。核心函数只读取每 UE packet trace，返回用户值、样本数与 exclusion reason；小区层再对有效 UE 做分位。</p>"
            "<p>随后在 Result schema 和 kpi_view 注册，Agent relevance 可根据“稳定性/实时业务”意图前置它，但不重算。测试用固定完成时刻序列解析复算、空/单包边界、warmup 跨界和多 UE CDF；详细文档写公式符号与实例。这样新增的是一条可审计能力，不是一张临时卡片。</p>"
        ),
        checks=(
            ("窄腰复用", "核心实现不依赖 MCP/UI/具体后端私有类型，输入输出合同可单测。"),
            ("默认不漂移", "新选项未启用时旧配置与结果保持回归；迁移差异有明确版本。"),
            ("机制正反例", "至少一个应改善和一个应退化/无差的受控案例，避免硬编码或指标泄漏。"),
            ("文档强合同", "新公式有符号表和解释，新章节有双层内容，自动清单与源码数量一致。"),
        ),
        pitfalls=(
            "先在 server.py 写完整算法，随后难以测试、复用和明确数据角色。",
            "为了让旧配置不报错，把未知新枚举静默映射到默认模式。",
            "只更新 API 参考，不解释新能力在物理和系统链路中的意义与边界。",
        ),
        source_paths=("src/superran/spec.py", "src/superran/results.py", "src/superran/server.py", "tests/"),
    ),
    "api": DetailSpec(
        promise="教读者把全量 AST API 图谱当作源码导航器：先按责任定位模块，再看公开符号、签名与 docstring，最后回到调用链和合同测试；详细版不会机械复制已经自动生成的 400+ 条 API。",
        principles=(
            "API 参考的真实性来自构建时 AST 扫描，而不是手工维护清单。每个 <code>src/superran/*.py</code> 模块、公开 class/function 和公开成员都从当前工作树提取签名、首段 docstring、行号和源链接；因此新增 symbol 后若未重建 docs，<code>--check</code> 会失败。它回答“现在代码有什么”，概念章节回答“为什么这样设计”。",
            "阅读顺序应从模块责任开始。编排看 plan/decisions/spec/sysscenes；数据看 generate/channelhub/loader/measure；算法看 beamforming/linklevel/linkadapt/mumimo/power_control；系统看 system/experience/traffic/kpi_view/rng；证据看 validate/gates/analysis/results。先确定所有者，再在模块卡中找 symbol，能避免从 400 个名字中盲搜。",
            "公开不等于稳定承诺。当前图谱以不以下划线开头作为“可见 API”，其中部分可能主要服务项目内部。真正跨模块稳定的接口还需看 Result/Spec schema、MCP 工具和测试。调用一个函数前，应检查参数 shape/单位、返回 diagnostics、可能异常、是否读取外部后端以及默认值来源；签名只是入口，不是完整合同。",
            "参考附录本身已是全量数据，因此详细阅读的增量价值是导航与复核，而不是把 12 万字复制成 24 万字。UI 会保留每个条目的折叠展开；搜索同时索引精简和详细文本；读者可由概念章源码路径跳到模块，再由 source link 进入精确行。",
        ),
        implementation=(
            ("AST 扫描", "解析每个模块顶层 public class/function 和 class public member，保留签名、line、docstring；不 import 业务模块以避免构建副作用。"),
            ("模块分组", "每个 module-card 带源码行数、职责摘要和 symbol details；source link 指向当前仓库相应行。"),
            ("搜索与路由", "页面文本和 tags 进入离线搜索索引，hash 路由与 heading id 可直接链接到模块/章节。"),
            ("漂移检查", "测试重新扫描 AST，核对每个模块 data-module、每个公开 symbol、计数 manifest 与生成结果。"),
        ),
        example_title="从“PF credit 错了”定位到真实更新语句",
        example=(
            "<p>先在概念章确认体验路径属于 <code>experience</code> 而不是历史 <code>system</code> capacity；在 API 页搜索 <code>simulate_experience</code>，展开签名和 docstring，再点源链接。沿局部变量查 <code>accounting</code>、<code>credit</code>、<code>inst</code> 与 <code>r_avg</code>，就能看清 scheduled_tbs/acked_goodput/legacy_fullband 三分支。</p>"
            "<p>接着从测试页搜索 PF accounting 的反向用例，而不是只凭函数名推断。API 图谱提供入口，概念详细版提供正确问题，源码与测试提供最终证据；三者缺一都容易误读。</p>"
        ),
        checks=(
            ("扫描完整", "所有 src/superran/*.py 和非下划线顶层 symbol 都出现在 HTML，计数与 AST 一致。"),
            ("链接有效", "模块/源链接指向存在文件与正确行附近，hash heading 无重复/断链。"),
            ("构建无副作用", "API 扫描仅 AST/文件读取，不触发 Sionna 可选重依赖或运行仿真。"),
            ("概念互链", "关键概念章给出 source_paths，API 参考能通过搜索返回对应模块和 symbol。"),
        ),
        pitfalls=(
            "把所有非下划线函数都当成长期稳定公共 SDK。",
            "只读签名不看 shape、单位、角色、异常与调用方测试。",
            "为满足 2～3 倍篇幅机械复制自动 API 内容，反而降低导航可用性。",
        ),
        source_paths=("scripts/make_developer_guide.py", "src/superran/", "tests/test_developer_guide.py"),
    ),
    "limitations": DetailSpec(
        promise="把已拍板行为、工程近似和下一阶段决策分开管理，说明每个限制会偏向什么方向、影响哪些结论，以及未来替换时需要守住哪些回归。",
        principles=(
            "限制不是一句“仅供参考”，而应包含当前实现、为何暂时接受、可能偏差方向、适用范围、检测信号与替换接口。例如单元方向图当前是 110°/65° 参数化包络而非实测图：可用于比较索引、F、下倾和相对波束机制，但不能宣称绝对旁瓣/覆盖与产品一致。metadata 的 measured flag 防止呈现层误写。",
            "已拍板项与近似项必须分栏。经典 PF、尾料留空、NACK 字节按现状、1 s 可配置 warmup、MU useful bytes 选择等是当前产品/算法合同；全带 SINR 判 BLER、简化 HARQ、MU pair 细节待完善、PMI 周期待标定等是模型边界。前者变更需要 migration，后者替换需要证明精度提高且默认影响可解释。",
            "近似之间会耦合。逐 RBG SINR 暂不做时，全带 BLER 会弱化频率选择与功控效果；未来加入后会改变 UeLinkTable 维度、TBS planner 与系统复杂度，并可能与其他同一结构改动冲突。路线图应按数据合同依赖排序，而不是把每个愿望独立列出。",
            "限制还要进入报告筛选：当实验问题触碰限制时，Agent 自动前置相关说明。例如用户问实测产品覆盖，方向图未实测是阻塞/强限制；用户只比较同一参数化图下两种端口顺序，则它是共同控制条件，不妨碍相对结论。",
            "代码具备但尚未进入主执行链的能力也属于限制，而不是“已实现”勾选项。例如库函数支持 MIESM/EESM，不等于体验链已经使用；InternalSim 有 probe，不等于 Sionna RT 存在等价低成本探测。文档必须同时写清能力所在层和当前调用路径。",
        ),
        implementation=(
            ("结构化登记", "每项 limitation 记录 category、status、scope、bias、evidence、owner/decision 和 replacement path。"),
            ("配置与结果携带", "运行时把相关近似标记写入 manifest/result；kpi_view/交付根据 intent 选择前置但不隐藏其余项。"),
            ("替换时做桥接", "新模型在同一 toy/正式样本与旧近似 A/B，比对接口、性能、资源和结论变化，保留 compatibility mode。"),
            ("文档动态核对", "已决策、当前边界和路线图分别渲染；禁止把未实现项写成现在能力。"),
        ),
        example_title="把参数化方向图换成实测图时不能只换一张曲线",
        example=(
            "<p>实测资产可能包含水平/垂直切面、频率点、极化复响应、校准坐标和旁瓣。接入时需定义插值、角度基准、Jones 基底、频率外推与归一参考，并保留旧 parametric profile 作为回归。</p>"
            "<p>验证不仅比较示意图，还要在波束中心、±HPBW/2、背瓣、交叉极化与多频点做锚点，再检查 H、RSRP、SINR 和系统 KPI 的变化。此前基于临时图的绝对覆盖结论不能自动继承；相同临时图下算法 A/B 的机制结论则可重新评估后保留。</p>"
        ),
        checks=(
            ("三类分开", "已决策默认、工程近似和待产品拍板各有状态，不用一个‘路线图’混写。"),
            ("偏差可解释", "每项限制说明可能高估/低估/未知及触发条件，不只说精度不足。"),
            ("结果带边界", "相关 limitation id 出现在 result/report，用户问题命中时被优先展示。"),
            ("替换可回归", "旧模式、桥接测试和 migration note 允许定位新模型导致的真实差异。"),
        ),
        pitfalls=(
            "把所有限制压成页尾一句免责声明，正文仍使用绝对确定措辞。",
            "把用户已拍板默认继续列为‘待决策’，实现和文档反复漂移。",
            "新模型一上线删除旧路径，无法判断 KPI 变化来自精度还是接口/索引回归。",
        ),
        source_paths=("src/superran/decisions.py", "src/superran/results.py", "src/superran/hardware.py"),
    ),
    "glossary": DetailSpec(
        promise="把术语表从缩写翻译升级为概念索引：每个词能回答它属于哪一层、与哪些相近词不同、在结果中用什么单位/分母，并沿源码路径定位真实实现。",
        principles=(
            "无线术语最危险的不是不知道缩写，而是同名量处于不同参考面。SINR 可以是几何输入、发送侧预测、逐流 post-MMSE 真值或全带有效值；PRB utilization 可以是邻区干扰输入或本小区调度输出；SRS period、CSI lag、snapshot period 也不是一个时间量。术语条目应给限定词和反例，而不只是中文翻译。",
            "符号也需要与术语互链。公式卡中的 H、Q、F、R̄、TBS、U_PRB 等符号给局部定义，术语表提供跨章节的稳定概念；点击公式或搜索词应能到概念页，再沿 source path 到实现。这样读者遇到 <code>Q[frequency,antenna,stream]</code> 时能同时知道数学方向、物理功率轴和代码 shape。PMI/RI 还要标出码本索引、rank 来源和 report source；功控还要标出作用于 antenna、stream、RB 还是邻区活动轴，避免短词把实现自由度压扁。",
            "单位和统计对象属于术语含义的一部分。dB 与 dBm、byte 与 bit、RB 与 RBG、TTI 与 snapshot、用户均值与小区总量、MU PRB share 与 MU user transmission count 都不能互换。结果 schema 和页面标签尽量带单位；无量纲比例仍应写分子/分母。",
            "反查源码应按问题路由，而不是列一个巨大目录。查端口顺序从 hardware/physical，查 SRS 从 physical/channelhub/csi_aging，查 MCS/TBS 从 linkadapt/experience，查 KPI 从 experience/kpi_view，查统计门从 gates/analysis。API 全量页再提供精确 symbol。",
        ),
        implementation=(
            ("定义规范词条", "每个术语包含英文/中文、层级、精确定义、单位或分母、易混词和主章节。"),
            ("连接公式与章节", "搜索索引包含 symbols/tags；公式卡、概念详细版和 glossary 使用一致命名。"),
            ("提供源码路由", "按问题类型给模块路径，动态 API 页负责当前行号与公开 symbol。"),
            ("持续做用词审查", "测试/文档检查禁止已知错误叫法，如把 SRS 周期统称 SRS 年龄，或把邻区 load 当本小区 utilization。"),
        ),
        example_title="看到“全带 SINR”时应追问哪四件事",
        example=(
            "<p>第一，它是发送侧预测还是真值接收 SINR？第二，逐 RB/逐流如何聚合——线性平均、有效 SINR、MCS/TBS 还是 useful bytes？第三，信号和噪声的功率参考面是总载波还是每 RB？第四，它供 MCS、BLER、PF 还是只供展示？</p>"
            "<p>这四问能把同一个短词路由到 linklevel、linkadapt 或 experience 的不同实现。术语表不是替代这些章节，而是帮助读者先识别限定词，再进入正确调用链。</p>"
        ),
        checks=(
            ("相近词可区分", "每个高风险词条列出至少一个不可互换的邻近概念与原因。"),
            ("单位/分母完整", "功率、速率、时延、资源比例和计数都有单位或明确分子分母。"),
            ("命名全站一致", "公式卡、章节、Result key、KPI label 与术语表不使用相互冲突叫法。"),
            ("源码可到达", "问题→模块→API symbol 路径有效，搜索关键术语至少命中一个概念章和一个实现入口。"),
        ),
        pitfalls=(
            "只翻译缩写，不说明参考面、单位和统计对象。",
            "把项目内部工程名当作 3GPP 标准术语，例如把某种 PF 直接称 EPF 标准算法。",
            "术语表手工写死模块行号，源码变化后反查链接漂移。",
        ),
        source_paths=("src/superran/", "scripts/make_developer_guide.py"),
    ),
})


# The code audit found four concepts that were visible only as API rows or a
# paragraph inside another chapter.  They now have first-class detailed layers
# so future edits cannot silently collapse them back into index-only coverage.
DETAIL_SPECS.update({
    "pdp": DetailSpec(
        promise="把 PDP 从一张时延曲线还原为一个受频率采样、窗函数、周期轴和功率守恒共同约束的测量过程。读完后应能从 N_RB 与 SCS 先算出可分辨边界，再判断某个 RMS delay spread 是否真的可观测。",
        principles=(
            "频域信道和时延域响应是一对离散傅里叶表示，但项目只观察每个 RB 的中心频点，而不是连续带宽或每个子载波。相邻样本间隔决定 IFFT 的周期，样本数乘间隔决定总观测带宽。前者限制最大无模糊时延，后者限制分辨率；把这两个量互换，会错误地以为增加 RB 数可以无限扩大最大时延，或以为 zero padding 能增加真实信息。",
            "窗函数是测量仪器的一部分。矩形截断的 Dirichlet 旁瓣会从 0 时延绕回周期末端，Hann 能压低旁瓣，却同时展宽主瓣并改变每条 realization 的能量。当前实现先把 Hann 的均方值归一，再逐 T/BS/UE 恢复原频域能量，并从时延二阶矩中扣除窗核自身方差；因此 power、RMS DS 和守恒比必须作为同一组输出理解。",
            "PDP 的平均时延不是在固定 0..T 直线上做普通均值。2 μs 的真实路径在 2.778 μs 周期上合法，固定把后半轴映到负数或把周期末端当大正时延都会选错分支。圆周均值先从数据确定参考分支，再把残差包回最近镜像；只有局部支持明显小于半周期时，RMS DS 才能稳定对应物理多径扩展。",
        ),
        implementation=(
            ("取得真实频域样本", "<code>Dataset.pdp(index)</code> 读取对应样本的 <code>h_true[T,RB,BS,UE]</code> 与配置 SCS；它不使用 h_est，也不读取一份预生成 PDP，避免真值观察量被估计器或缓存版本污染。"),
            ("窗与正交 IFFT", "RB 轴乘能量归一 Hann，执行 <code>ifft(...)*sqrt(N_RB)</code>。这使未加窗的时频能量满足 Parseval，并把窗的作用集中在可审计的频率权重里。"),
            ("逐 realization 恢复功率", "分别计算窗前频域能量和窗后时延能量，为每个 T/BS/UE 元组乘独立比例。不能只用全数据集均值校正，否则某些端口或时刻仍会被窗口随机放大/缩小。"),
            ("构造周期时延轴与矩", "用 <code>df=12*SCS</code> 得到 delays、resolution 与 period；默认把端口与时间功率平均，per_antenna 模式保留 BS 轴，但矩仍在端口平均后的 flat PDP 上计算。"),
            ("去嵌与回传证据", "对同一 Hann 核计算圆周方差，从观测方差中扣除并截到非负；同时返回 window 名、核方差、功率守恒比和边界量，让调用者能复算而不是只信一个 RMS 数。"),
        ),
        example_title="272 RB 上两径 0/500 ns 为什么得到 100/200 ns",
        example=(
            "<p>设两径线性功率为 0.8 与 0.2、时延为 0 与 500 ns。功率加权均值是 0.8×0+0.2×500=100 ns；方差是 0.8×100²+0.2×400²=40,000 ns²，RMS 为 200 ns。两径都远小于 1.389 μs 半窗，且相隔约 49 个 10.21 ns tap，因此这个解析答案在当前采样网格上可辨。</p>"
            "<p>同样代码还用 13 ns 与 2 μs 单径做哨兵：前者不能因 Hann 旁瓣被报告成数百 ns RMS，后者不能被固定 signed axis 解释成负时延。若只检查曲线峰值位置，两个历史错误都可能漏过；必须同时检查圆周均值、RMS 和能量。</p>"
        ),
        checks=(
            ("解析单径", "0/13 ns 和长正时延单径的 RMS 接近 0，均值落在正确圆周分支。"),
            ("解析两径", "0/500 ns、0.8/0.2 的均值约 100 ns、RMS 约 200 ns。"),
            ("逐 realization 守恒", "power_conservation_ratio 接近 1；per_antenna 开关只改变输出轴，不改变总体能量。"),
            ("边界显式", "报告同时携带 Δτ、Tamb、window 与 kernel correction；超出半窗的 profile 不做强判定。"),
            ("变换回归", "validate 的 Parseval 检查和 PDP 自身窗后复能检查同时通过。"),
        ),
        pitfalls=(
            "把 PDP 峰值归一到 1 后再讨论绝对功率或端口差异；归一化会抹掉本章刻意保留的能量证据。",
            "把 RB 个数当作无模糊周期的决定因素；周期由 RB 中心采样间隔 12·SCS 决定。",
            "用 zero padding 后更密的横坐标宣称时延分辨率提高；它只是插值仪器响应。",
            "对支持超过半周期的多径仍给出精确 RMS DS，并把 wrap ambiguity 当作算法误差。",
        ),
        source_paths=("src/superran/measure.py", "src/superran/loader.py", "src/superran/validate.py", "tests/test_channel_generation_contract.py"),
    ),
    "csi": DetailSpec(
        promise="沿着一份 CSI 从上行 SRS 采样到下行 PDSCH 生效的时间链走一遍，并明确 PMI/CQI 是报告状态而不是每个 snapshot 都可瞬时重算的 oracle。",
        principles=(
            "SRS 周期描述 UE 的同一逻辑端口组多久发送一次参考信号；CSI 陈旧时长描述调度时刻所用信道距其测量时刻多久；PMI/CQI 报告周期描述候选测量多久提交一次；channel snapshot 只是仿真器采样 H(t) 的网格。新数据用<code>sample_interval_s</code>显式绑定该网格，外部源默认变化不得覆盖它。基础2T4R资源profile把slot7→17两个5 ms空口机会绑定成一个10 ms四端口周期；这些时钟不可互相替代。",
            "跳频把时间链变成逐 RBG 状态。C_SRS=63/B_SRS=1 时一次覆盖 16 RB，需要 17 次机会扫完 272 RB；在任意 TTI，各 RBG 的最后采样时刻不同。长时间轮转保证 RBG 统计等价，但一个具体 TTI 上不能用全带同一个平均 lag 替代逐 RBG lag，否则频域老化与 grant bitmap 的耦合会消失。",
            "系统可见 PMI 由候选码本、报告时刻和因果保持共同定义。当前 Type-I-style 搜索使用宽带协方差和端口置换，是工程可反馈方向基线；它没有完整 38.214 多层/子带/restriction 枚举。SVD 是连续方向上界。两者都必须在 stale h_prec 上设计，再到当前 h_true 上经过同一个接收机评价，才能把码本量化、反馈周期和信道老化分开。",
        ),
        implementation=(
            ("建立跳频日历", "<code>hop_order()</code> 从 SuperRAN 单一真源读取 C_SRS=63/B_SRS=1/b_hop=0/n_RRC=0 的 17-hop 顺序并返回版本化 provenance；只接受 17×16，没有外部 helper 与 identity fallback。"),
            ("计算逐 RBG staleness", "<code>rbg_csi_staleness_ms()</code> 找最近一次覆盖并加入 processing delay；<code>rbg_lag_snapshots()</code> 向上取整到物理 trace 网格，确保处理尚未完成的快照不会被使用。"),
            ("生成估计与预编码视角", "<code>stale_channel()</code> 从有限历史选择 h_prec；仿真开头可选择 clamp 或显式 periodic prehistory，不能用负索引绕到未来 trace。"),
            ("提交 PMI/CQI 报告", "系统只在 report instant 更新状态，保存每个 snapshot 的 <code>csi_report_source_snapshot</code>；CQI 的一阶 IIR 只读取到当前时刻的报告，不用全轨迹均值回填。滤波系数 <code>cqi_filter_lambda</code> 与作用域 <code>cqi_filter_domain</code> 随结果上报。"),
            ("在真值上复评", "SVD/PMI 权作用到当前 h_true，post-MMSE 给逐流和逐 RBG SINR。零 lag 必须与历史 rank adaptation 逐位相同，有 lag 才自然产生泄漏。"),
        ),
        example_title="5 ms snapshot、10 ms SRS、20 ms report 到底何时更新",
        example=(
            "<p>假设<code>sample_interval_s=0.005</code>，信道在0/5/10/15/20 ms有快照；SRS每10 ms发送、处理需2 ms，PMI/CQI每20 ms报告。0 ms采样的CSI最早在2 ms可用；10 ms新SRS在12 ms可用，但若报告到20 ms才提交，15 ms PDSCH仍保持上一份PMI/CQI。若外部源隐式默认0.5 ms而数据合同仍写5 ms，整个lag会错10倍，因此新生成在入口就冻结字段。</p>"
            "<p>打开 17-hop 后，10 ms SRS 只更新一个 RBG；其他 RBG 继续沿用各自更早的测量。20 ms 报告可能包含一个由不同采样时刻拼成的全带状态，这正是逐 RBG staleness 存在的意义，而不是把所有 RBG 统一回看两格。</p>"
        ),
        checks=(
            ("零时延退化", "h_prec=h_true 时，aged rank/SINR 与原实现逐位一致。"),
            ("未来隔离", "任意修改 snapshot s 之后的 H，不能改变 s 时刻的 PMI、CQI、BF gain 或 source snapshot。"),
            ("周期分离", "默认报告周期20 ms时不会每个5 ms snapshot更新；显式5 ms CSI report才逐快照更新。基础2T4R SRS资源周期仍从10 ms起，二者不可互换。"),
            ("跳频来源", "预置默认场景返回以 <code>superran:</code> 开头的版本化17-hop provenance与17跳顺序；具体版本号从hardware真相源生成，非272-RB profile必须硬失败。"),
            ("端口合同", "64T/256T 的 Type-I codebook 按 pol-h-v/top-to-bottom 置换后，可直接作用于真实 H。"),
        ),
        pitfalls=(
            "把 CSI 陈旧时长口语化为“SRS 年龄”，导致周期、处理时延和逐 RBG 最近采样混在一起。",
            "对每个 snapshot 离线重算最佳 PMI，再把它当作系统当时已收到的反馈。",
            "用整个仿真窗口的 PMI-SINR 均值生成早期 CQI，泄露未来信道。",
            "在基础2T4R资源分配开启时把srs_period_ms设成5 ms；这会把两条相邻2T腿误写成同一逻辑周期。",
            "把 Type-I-style 贪心列集合写成完整 38.214 Type-I 标准实现。",
        ),
        source_paths=("src/superran/csi_aging.py", "src/superran/measure.py", "src/superran/physical.py", "src/superran/system.py", "src/superran/generate.py", "tests/test_csi_aging.py", "tests/test_physics_contract_extensions.py"),
    ),
    "robust": DetailSpec(
        promise="把“鲁棒权”拆成可计算的误差模型、RZF 对角加载和独立功率约束三层，避免把任何带归一化的权都含糊称为 robust。",
        principles=(
            "ZF 把估计信道上的用户间耦合压到零，但小的 CSI 方向误差会被求逆放大，真实信道上的零陷不再为零。RZF 用对角 loading 限制这种放大；robust RZF 再把估计误差的期望能量写进 loading。它不是保证每个 realization 都更快，而是在声明的误差统计下减少过度相信 Ĥ 的风险。",
            "误差方差和 NMSE 不是同一个直接可填的数字。代码的 sigma_e² 是 H 当前线性归一化下每个复系数的方差；NMSE 是总误差能量除以真信道能量，常以 dB 报告。离线 h_true/h_est 可用于标定全局或分桶参数，但在线预编码只能使用估计器协方差或先验，不得从当前 h_true 反推最有利 loading。",
            "功率约束是第二个正交轴。robust RZF 先决定 W 的列方向，EBF/PEBF/NEBF 再把 W 与功率分配组合成物理 Q。PEBF 的全局缩放保持零陷几何，NEBF 的逐天线缩放可能重新引入干扰；因此出现 robust RZF+NEBF 时，必须同时看 regularization、Q 的每天线功率和真值 leakage，不能把性能变化只归因于鲁棒 loading。",
        ),
        implementation=(
            ("声明误差模型", "配置给出每复系数 <code>mu_csi_error_variance</code>，并与 H 的归一化、端口数和频率粒度一起落盘。非有限、负值当场拒绝。"),
            ("分解 loading", "<code>robust_rzf_regularization()</code> 独立返回 noise、CSI-error 与 total 三项。alpha 只覆盖 noise term，不能把显式误差项悄悄吞掉。"),
            ("设计方向并分配流功率", "<code>mu_precoder()</code> 在每个频率点求 Ĥᴴ(ĤĤᴴ+λI)⁻¹，逐列归一表示方向，再由 equal/waterfilling 决定每流功率。"),
            ("施加物理功率约束", "由 W·sqrt(p) 重建 Q，再一次性施加 EBF/PEBF/NEBF；若不是 EBF，代码把新 Q 唯一分解回单位列 W 与列功率 p，后续 SINR 不会二次归一。"),
            ("预测与真值双评", "MU pair table 在 h_prec/h_prec 上形成 gNB 预测，在 h_true/h_prec 上形成实际接收；两个结果保存同一 loading 与不同 leakage，Phase B 只消费因果表。"),
        ),
        example_title="4 流、64 端口时 1e-3 误差为何比噪声项更大",
        example=(
            "<p>若每用户等效噪声功率为 0.01、总功率 P=1、共有 4 流，则常规 noise loading 为 4×0.01=0.04。若每复系数误差方差为 1e-3，64 个发射端口累积后的 uncertainty loading 为 64×1e-3=0.064，总 loading 为 0.104。这个数字直接来自模型维度，不是手调的“鲁棒系数”。</p>"
            "<p>把 sigma_e² 设为 0 时，robust API 必须与历史 RZF bitwise compatible；固定 imperfect-CSI 反例再要求正误差 loading 的真值 sum SE 高出明确裕量。第二条只是机制哨兵，不是宣称所有场景 robust 都提升；正式增益仍要独立样本、CRN 与 Gate 2/3。</p>"
        ),
        checks=(
            ("分解可复算", "noise=N_stream·noise/P、error=N_BS·sigma_e²、total 为两者之和。"),
            ("零误差兼容", "sigma_e²=0 时权、功率与旧 RZF 逐位相同。"),
            ("功率与鲁棒正交", "切换 EBF/PEBF/NEBF 不改变回传的 loading 定义；Q 仍满足各自约束。"),
            ("真值残留可见", "h_est 设计、h_true 评价；报告保存 leakage、receiver、power diagnostics 和 regularization。"),
            ("系统开关诚实", "只有 mu_precoder=rzf 时误差方差生效；ZF 默认不能被标成 robust。"),
        ),
        pitfalls=(
            "把每天线归一的 NEBF 叫鲁棒权；它解决功率几何，不建模 CSI 不确定性。",
            "把 -20 dB NMSE 直接作为 -20 或 0.01 填入 sigma_e²，而没有核对 H 的平均系数功率。",
            "运行时用当前 h_true-h_est 选择每个 snapshot 的最优 loading，形成不可实现 oracle。",
            "只比较总功率利用率，不在真实信道上检查 MU leakage 与 post-LMMSE SINR。",
        ),
        source_paths=("src/superran/mumimo.py", "src/superran/beamforming.py", "src/superran/system.py", "tests/test_mumimo.py", "scripts/run_srs_pdp_robust_audit.py"),
    ),
    "calibration": DetailSpec(
        promise="理解模型校准为何不是“多跑几个单测”，并能区分按 38.901 口径出统计量、项目内部物理验证和算法效果显著性三种不同证据。",
        principles=(
            "校准的核心是让不同实现按照同一统计定义出数，再与公共参考曲线或独立已校准引擎比较。耦合损耗检查大尺度链路预算，geometry 检查干扰拓扑，DS/AS 检查多径扩展，PRB 奇异值检查空间结构。它们覆盖的物理层次不同，不能用一条平均 SINR 曲线替代。",
            "适用性本身是结果的一部分。固定 CDL/TDL profile 的 nominal delay spread 是查表常数，不具备系统级随机 DS 的 CDF；单小区没有宽带 SIR；射线追踪数据若没有可映射的 CDL path angles，ASD/ASA/ZSD/ZSA 不能凭几何位置伪造。当前报告为每项携带 applicable、reference 和 note，宁可不比，也不画一条伪 CDF。",
            "统计显著不等于工程一致。KS 在样本量大时会对很小的分布差异也拒绝原假设，所以跨引擎报告同时给 D、临界值、n 与中位数差；是否可接受仍要有预先定义的工程容差。相反，样本很小时 KS 不拒绝也不证明相同，必须同时看覆盖范围和 Monte Carlo 收敛。",
        ),
        implementation=(
            ("绑定绝对功率参考", "从 tx_power_dbm 与 rx_power_serving_dbm 算 coupling loss；若 H 已归一化，PRB singular absolute 曲线再减耦合损耗恢复绝对电平。"),
            ("检查 geometry 可用性", "分别读取 SINR/SIR，并识别与纯 SNR 逐点相同或 49.9 dB sentinel。异常项标不适用，不进入假 CDF。"),
            ("计算多径扩展", "DS 优先使用逐样本字段；角扩展从 paths 的功率与角度按圆周定义计算。固定 profile 会说明这是点值/退化分布。"),
            ("计算空间特征", "按标准要求在 t=0 对每 RB 形成 HᴴH，输出第一、第二特征值及 dB ratio 的分位点。"),
            ("组装与交叉比较", "<code>calibration_report()</code> 汇总上下文和适用项；有第二引擎时 <code>cross_engine_compare()</code> 对相同指标做 KS 与中位数差。"),
        ),
        example_title="为什么 359°/1° 的普通标准差会误判",
        example=(
            "<p>两条等功率路径位于 359° 和 1°。在线性角度轴上，平均约 180°、标准差约 179°，仿佛来自两个相反方向；映射到单位圆后，两根矢量几乎同向，合矢量模长接近 1，圆周角扩展很小。这就是 38.901 校准必须引用圆周定义的原因。</p>"
            "<p>同理，PRB singular value 的 λ 是 HᴴH 特征值，已经等于奇异值平方。代码用 10log10(λ)；若误用 20log10(λ)，曲线跨度和绝对电平都会放大一倍，即使 rank 排序仍看似合理也无法对标参考。</p>"
        ),
        checks=(
            ("参考面一致", "coupling loss、绝对 singular value 与几何量都能反查 tx/rx/normalization 元数据。"),
            ("圆周反例", "跨 0/360° 的角度集合给出小扩展，集中方向的浮点残差低于容差。"),
            ("适用性诚实", "单小区、sentinel、固定 profile 与缺 path angles 均给明确 non-applicable 原因。"),
            ("尺度公式正确", "HᴴH 特征值使用 10log10；ratio 在整体缩放后保持不变。"),
            ("跨引擎双口径", "KS D/critical 与 median difference 同时返回，样本量也在报告中。"),
        ),
        pitfalls=(
            "没有参考 CDF 时，把项目自己的历史输出当作 3GPP 通过线。",
            "把 calibration 出数、validate 不变量和算法 A/B 显著性合并成一个 pass 字段。",
            "对固定 CDL profile 的 nominal DS 画出随机 CDF并与系统级 38.901 曲线直接比较。",
            "只报 KS 是否显著，不报样本量和中位数工程差异。",
        ),
        source_paths=("src/superran/calibration.py", "src/superran/validate.py", "src/superran/gates.py", "src/superran/server.py"),
    ),
})


DETAIL_SPECS.update({
    "pmi": DetailSpec(
        promise="把 PMI 从一个缩写拆成可执行的数据链：候选码本怎样由二维阵列和双极化生成，真实端口顺序怎样进入 W，宽带搜索怎样选列，报告时序怎样保持，以及 PMI 如何同时成为 CQI 与 BF Gain 的参照。",
        principles=(
            "PMI 的本质不是“最优预编码矩阵本身”，而是有限候选集合中的可反馈索引。SVD/协方差特征向量可以在连续复向量空间寻找方向，PMI 只能从码本里挑，因此两者之间包含码本量化与结构限制。SuperRAN 又允许 <code>precoder=type1</code> 把该参照权直接用于发射；这时额外 BF Gain 为 0，只说明参照权和发送权相同，PMI 自身的阵列增益已经在 CQI 参照 SINR 中。",
            "候选方向必须和真实物理端口一一对应。Type-I 逻辑行按 polarization block、vertical、horizontal 排列；预置 64T/256T 信道采用 pol-h-v、top-to-bottom。搜索前把码本行置换到 H 的 RF-port 轴，返回的 W 才能直接左乘/右乘信道。只改变 metadata、不重排 H/W，或把 64×rank 的矩阵 reshape 成另一顺序，都会让波束指向错误物理位置。",
            "宽带统计必须积功率而不是积复电压。<code>R_tx=E[HHᴴ]</code> 对每个 snapshot 的公共相位旋转不变，也保留各 RB/UE 端口的方向能量；<code>|E[H]|²</code> 会把相反相位的同一空间方向错误抵消。当前多层算法在残余协方差上逐列贪心，适合作为工程 baseline，但它没有联合枚举完整多层 Type-I 矩阵，因此文档始终写 Type-I-style。",
            "PMI、RI 和报告状态是三个轴。<code>PMIResult.rank</code> 只说明函数最终给了几列；<code>compute_precoder(type1)</code> 可用特征值门限先做工程 rank；体验系统则计算 rank 候选并按 gNB 视角 SE 选择。系统可用 PMI 还要经过报告周期和因果 hold。任何图表都应同时给 <code>indices/rank source/report source snapshot</code>，否则一个“PMI=320”没有可复算意义。",
            "CQI 与 BF Gain 复用同一个 PMI 参照，但评价视角不同。终端把最近报告中的 W_PMI 作用到当前真实 H，得到 PMI-SINR 并形成 CQI；基站在自己可见的 H_prec 上比较 W_tx 与 W_PMI，得到额外 BF Gain。两边必须锁定相同 rank、功率约束、干扰口径和接收机，不能让 SVD 一臂用真值、PMI 一臂用估计。",
        ),
        implementation=(
            ("解析阵列合同", "从 <code>bs_panel</code> 与 antenna metadata 取得 N_H/N_V、双极化、port_order 与 vertical_index_order；64T/256T 缺显式 panel 时只允许落到已登记布局，不能静默变成 64/256 元线阵。"),
            ("生成候选列", "水平/垂直分别生成 O=4 的单位范数过采样 DFT 向量，做 Kronecker 积，再用 1/j/−1/−j 拼接两个极化块。64T 得到 2048 列，256T 得到 8192 列。"),
            ("映射到真实端口", "<code>type1_to_port_permutation()</code> 返回 protocol-row→channel-port 映射；<code>cb_input[perm,:]=cb</code> 使候选列与 H 的端口轴同序。"),
            ("搜索宽带列", "把 H 的时间、RB、UE 端口展成协方差样本，计算 R_tx；每层最大化 wᴴRw，随后用 I−wwᴴ 更新残余，输出 indices、W、layer gain、layout 与码本大小。"),
            ("形成系统报告", "系统在 report instant 的 h_prec 上搜索，<code>csi_report_source_snapshot</code> 记录来源；中间 snapshot 保持同一结果。当前反馈 latency 没有独立参数，处理时延由 SRS/CSI 链承担并显式写边界。"),
            ("进入 CQI/BF/MCS", "在 h_true 上算 PMI-SINR 形成因果 CQI，在 h_prec 上算 W_tx−W_PMI 的 BF Gain，再交给 CQI→MCS→OLLA 链。<code>precoder=type1</code> 的零 BF Gain 是逐值恒等哨兵。"),
        ),
        example_title="64T 的 2048 个候选列与一个两方向 toy 协方差",
        example=(
            "<p>64T 的逻辑阵列是 8H×4V×2pol。水平过采样后有 8×4=32 个方向，垂直有 4×4=16 个方向，每个二维方向再乘 4 个极化共相，所以共有 32×16×4=2048 列；每列长度为 64、范数为 1。256T 同理是 64×32×4=8192 列而不是 256 列，后者更像 CSI-RS beam-sweep 的另一套码本规模。</p>"
            "<p>再看一个可手算例子：若 R_tx 在两个正交码本方向 w₀/w₁ 上的功率为 9 和 4，第一层最大化 wᴴRw 会选 w₀。投影 I−w₀w₀ᴴ 后，残余里 w₀ 的功率变为 0，第二层才会选 w₁。若把第二个 snapshot 整体乘 −1，R_tx 与选择完全不变；若先平均 H，则两个 snapshot 可能抵消成 0。这正是相位不变量测试要锁住的差别。</p>"
        ),
        checks=(
            ("列集合", "码本 shape、单位列范数、默认过采样与 64T/256T 候选数逐项断言；CSI-RS [beam,port] 与 PMI [port,column] 不混。"),
            ("端口置换", "canonical 与 legacy 64T 在物理置换后给相同 layer gain，W 的相关系数接近 1；256T 直接推断保持 16H×8V。"),
            ("相位与宽带", "对 H 每个 realization 乘任意公共相位，R_tx、PMI indices 与 gain 不变；频率轴先平均复 H 的历史错误会被反例抓住。"),
            ("时序因果", "修改当前 snapshot 之后的 H 不能改变当前 report source、PMI/CQI/BF Gain；report 周期之间 index 保持不变。"),
            ("参照一致", "SVD 与 PMI 比较使用同 rank、同 power_constraint、同 noise/interference、同 MMSE；type1 作为实际发射权时 BF Gain 逐位为 0。"),
            ("RI 标签", "输出明确区分 greedy column count、threshold rank 与 system rank_gnb；不把 PMIResult.rank 写成完整标准 RI。"),
        ),
        pitfalls=(
            "把 <code>Dataset.pmi()</code> 对 h_true 的离线结果直接当成系统当时已收到的 PMI。",
            "看到 DFT 就把 CSI-RS beam codebook 与 PMI Type-I-style 列集合当成同一个数组。",
            "把端口迁移写成 reshape，或只改 port_order 标签而没有同步置换 H、W、F。",
            "用 mean(H) 搜宽带方向，导致相位相反的同方向快照互相抵消。",
            "把贪心选出的列数称为完整 RI，或宣称已实现完整 38.214 多层/子带/restriction 反馈。",
            "把 PMI 自身阵列增益和 SVD−PMI 的额外 BF Gain 同时加到发送 SINR，造成双计。",
        ),
        source_paths=("src/superran/measure.py", "src/superran/hardware.py", "src/superran/linklevel.py", "src/superran/system.py", "tests/test_company_256t.py"),
    ),
    "powercontrol": DetailSpec(
        promise="把空间每天线约束、流间功率、逐 RB profile 和邻区活动拆成独立自由度，沿着用户输入、守恒求解、first-party 逐小区功率分解、链路表与体验调度走到最终 SINR。",
        principles=(
            "功率自由度必须按作用轴命名。EBF/PEBF/NEBF 改的是一个频点上 antenna×stream 的物理矩阵；equal/waterfilling 改的是 stream 轴；q[cell,RB] 改的是频率轴；neighbor_prb_util 改的是邻区在时间/资源上的活动比例。四者可以组合，但守恒对象、配置入口和失败模式都不同。把它们都写成“功控开/关”会让一次结果无法归因。",
            "逐 RB profile 是相对均匀 PSD 的连续倍率，不是给总功率加预算。每个小区独立满足 0.1≤q≤4 与 Σq=N_RB。部分指定时，未指定 RB 统一承担差额；若差额要求越界则输入不可行并硬失败。这样 RBG0 抬升必然伴随其他 RB 下降，A/B 才不会把额外能量误写成算法增益。",
            "跨小区耦合要求绝对、逐小区的分母项。对 victim UE，只提高服务小区 q 会增强 S；提高某邻区 q 只增强该邻区的 I_k；η 再表示邻区平均活动。聚合 SIR 只知道 ΣI_k，无法在每个邻区使用不同 q_k 后恢复 Σq_kI_k。因此旧数据缺 <code>[sample,slot,cell]</code> 干扰分解时必须重新生成，不能按比例猜。",
            "频率 profile 必须保留到接收 SINR 后再按调度单位聚合。某个 16-RB RBG 内可以只有一个 RB 被抬升；若建表前先取中心 RB或先把 H 平均成一个 RBG，该变化可能完全消失。当前实现保留每个 RB，通过 MMSE/SVD/Type-I 后先在线性域形成 RBG SINR，再按项目宽带口径聚合。",
            "q 与每天线约束的组合也要诚实。q 乘的是已按空间约束得到的物理矩阵幅度，因而每天线 cap 在高功率 RB 上同比放宽、在低功率 RB 上收紧。EBF/NEBF 通常用满每 RB 预算；PEBF 可能先因峰值天线而留有余量，q 的均值 1 只保证预算包络守恒，不保证 PEBF 实际辐射功率恰好等于包络。",
        ),
        implementation=(
            ("解析并验证 override", "每条记录只能选择单 RB 或闭区间，cell_index 可为 all/整数，倍率必须有限且在 0.1..4；未知字段、重叠、越界和布尔伪数字都拒绝。"),
            ("逐小区补平 profile", "先合并全局与本小区规则，再由 _resolve_one_profile 计算未指定 RB 的唯一 balance，使用 fsum 和尾项 residual 把每行总和闭合到 N_RB。"),
            ("装载绝对功率几何", "geometry_from_dataset 读取 serving cell、S、N 与 I[sample,slot,cell]；服务小区干扰列必须为 0，signal/noise 必须为正，slot 数必须与信道一致。"),
            ("逐快照精确耦合", "couple_rb_power 对每个 RB 计算 η·I·q、N+I、q_serving·S 与 IoT，并返回相对 baseline denominator 的 channel power scale。"),
            ("保留 RB 到链路表", "H_eval 与 H_prec 同乘 sqrt(scale)，防止设计/评价参考面不一致；经过真实预编码和接收机后才聚合成 RBG，并把 profile fingerprint、serving cell 和耦合摘要写入 UeLinkTable。"),
            ("按真实 grant 使用", "experience 调度读取实际 RBG bitmap 的 SINR/MCS，禁止用全带平均替代 grant subset；simulate 复用错 fingerprint 或把不同 serving cell 放进同一资源池时硬失败。"),
        ),
        example_title="把 RBG0 的 16 个 RB 提到 2x，剩余 256 个 RB 会发生什么",
        example=(
            "<p>100 MHz 基线有 272 RB。若小区 0 把 RB0..15 指定为 2x，已指定部分消耗 32 个倍率单位；剩余 256 个 RB 必须共同承担 272−32=240，所以每个自动补偿为 240/256=0.9375x。总和仍是 16×2+256×0.9375=272，没有白拿功率。</p>"
            "<p>对一个服务于小区 0 的 UE，RBG0 目标信号乘 2；对邻区 UE，小区 0 在同 RB 的干扰也乘 2。假设另一个干扰小区恰把该段降到 0.5x，则 victim 的分母必须逐项算 η(2I₀+0.5I₁+…)+N，不能先把 I₀+I₁ 合成一个 SIR 再乘平均 q。最后哪个小区得益由调度概率、各 UE 几何与业务共同决定，守恒本身不保证收益为正。</p>"
        ),
        checks=(
            ("profile 守恒", "每个小区逐行检查 min/max、mean、sum_error；部分 override 不改变用户给定值，不可行输入全部硬失败。"),
            ("S/N/I 重构", "first-party source→NPZ→loader 的 S/(N+ΣI_k) 必须逐位重构原几何 SINR，服务小区列为 0。"),
            ("耦合 toy", "三小区两 RB 手算 q_serving·S/(N+ηΣq_kI_k)，同时核对 controlled interference、IoT 与 channel scale。"),
            ("关闭退化", "enabled=False 的 link table SINR/MCS 与历史均匀 1x 路径逐位相同；enabled=True 但无 override 也显式报告仍是 1x。"),
            ("频率可见", "相反 profile 的两个 RBG 得到方向正确的不同 SINR，单 RBG grant 读取自己的 bitmap，不被全带均值抹平。"),
            ("结果身份", "SystemConfig 同时回传 spatial power_constraint 与 rb_power_control；profile fingerprint 不同的 table 拒绝复用。"),
            ("边界阻断", "跨 serving cell 的统一资源池、capacity 标量 MU+RB 功控、缺逐小区 I_k 数据都必须返回错误而不是近似运行。"),
        ),
        pitfalls=(
            "把 NEBF/PEBF 与 RB profile 当成同一个开关，导致每天线限制与频域功率重排重复施加。",
            "只提高本小区期望信号、不提高它对邻区的干扰，凭空制造网络级收益。",
            "从聚合 SIR 平均拆出每个邻区 I_k，或把一个 slot 行复制成任意数量的信道快照。",
            "先把 272 RB 压成 17 个中心 RB，再试图验证单 RB override。",
            "把 mumimo 链路库的 waterfilling 能力写成体验系统 UI 已开放的调度自由度。",
            "看到 Σq=N_RB 就宣称 PEBF 实际总辐射功率必然用满，忽略峰值天线全局缩放留下的余量。",
            "用当前 dB 平均口径宣称跨频 TB BLER 已达到链路级 EESM/MIESM 精度。",
        ),
        source_paths=("src/superran/power_control.py", "src/superran/beamforming.py", "src/superran/mumimo.py", "src/superran/system.py", "src/superran/server.py", "tests/test_power_control.py"),
    ),
})


DETAIL_SPECS.update({
    "agentloop": DetailSpec(
        promise="把 Agent 式仿真从“模型会聊天”还原成一条可重复执行的编译链：有限任务画像负责识别问题类型，结论槽位决定真正需要追问什么，Draft 保存差分与历史，算法目录和说明书再从同一 resolved config 派生。读完后应能判断一次页面修改究竟有没有进入真实执行。",
        principles=(
            "当前任务分类器是<strong>确定性关键词命中计分</strong>，不是隐藏的 LLM 分类调用。每个 <code>TaskProfile</code> 定义正向关键词、实验设计问题、参数决策、推荐默认、sweep 和物理 guard；最高分画像胜出，无命中则回到 <code>generic</code>。这使相同 intent 在不同机器和模型版本下仍得到相同执行骨架，但同义改写可能漏判，所以 Agent 的语言理解只能帮助补全显式 intent，不能绕开有限画像合同。",
            "问题数量由<strong>结论所需槽位</strong>而不是可配置字段总数决定。基线、主指标、适用范围、控制变量、方向性假设和失败判据缺一项，最终结论就可能不可解释；而阵列、信道、接收机等高影响参数只需在会改变结论时追问。<code>also_configurable</code> 是透明度清单，不是把数百个字段继续问给用户。样本量要由试点差值方差和目标效应计算，不能让用户凭感觉填一个数字。",
            "配置合并是一条有方向的偏序：项目默认提供可运行底座，preset 提供成套场景，task hints 注入意图可可靠推导的参数，用户 override 最后覆盖。右侧优先不等于右侧可以绕过 guard；类型、枚举、物理可行性和跨字段约束仍在 resolved 阶段检查。Draft 只保存显式差分与回答历史，因此第二轮修改一个字段不会把第一轮已经确认的设置恢复成旧默认。",
            "算法目录有两个互补视角。<code>algorithms.py</code> 生成“本次到底用了什么”的实例清单和推导；<code>algo_defs.py</code>/<code>algo_defs2.py</code> 描述算法族、替代项、输入输出和 caveat。前者必须由 resolved config 逐项派生，后者负责解释选择空间。若页面只展示一份静态算法宣传文案，用户无法知道这次究竟使用 LS 还是 LMMSE、EBF 还是 NEBF。",
            "说明书页面不是第二个配置数据库。<code>spec._EDITABLE</code> 同时生成控件与 POST 白名单；bridge 只监听 loopback，要求随机 token、标量值、nonce 和 draft 身份，再把 delta 送回 <code>revise_draft()</code>。页面、Agent 复述和运行参数最终都回到同一个 resolved config，才能消除“看的是 A、跑的是 B”。",
            "这张交互配置 Mock 是 SuperRAN 的运行前主界面，因此必须同时满足“容易看懂”和“不会越权”。容易看懂意味着用户点名的参数通过 <code>highlight</code> 前置，拓扑吸附、端口顺序、PDP、TDD 与算法选择用图形和短解释联动；不会越权意味着高亮不改值、浏览器不直接写 YAML、按钮只发送允许字段的 delta。若 loopback bridge 不可用，页面要显示 clipboard 降级和原因，而不是让用户以为修改已经生效。",
        ),
        implementation=(
            ("画像并解释", "<code>classify_intent()</code> 对规范化 intent 做关键词命中与分数排序并返回 profile；<code>decisions_for()</code>/<code>design_questions_for()</code> 按画像给出参数问题与实验设计问题，sweep 和 guard 由同一 profile 派生。"),
            ("按槽位追问", "<code>next_round()</code> 先检查结论槽位和高影响参数，第一轮尽量一次问全，第二轮只补剩余项；用户接受默认时立即收敛，轮数有硬上限。"),
            ("形成可修订 Draft", "<code>create_draft()</code> 合并 defaults、preset、task hints 与 overrides，保存 draft_id、params、design、history 和 user_set；<code>build_proposal()</code> 每次从 Draft 导出 resolved_config，<code>revise_draft()</code> 只应用本轮 delta。"),
            ("派生真实算法清单", "<code>algorithm_list()</code> 从 resolved config 选择数据源、估计、预编码、接收机、链路自适应和调度项；<code>derivations()</code> 读取同一配置中的阵列、功率与时序值。"),
            ("生成与回传说明书", "<code>build_spec()</code> 画出拓扑、频域、PDP、TDD 与算法链；bridge 校验 host、token、Content-Type、payload size、editable key、标量类型和 nonce 后才返回配置 delta。"),
            ("导出与重算影响", "统一操作栏提供说明书/Resolved config JSON、摘要复制、截图、分享与打印；参数 diff 同时点亮信道、链路表、TTI、KPI 中必须重算的层。"),
            ("突出本轮相关信息", "<code>highlight</code> 只调整关键卡片顺序和视觉强调；用户指定值、系统默认、实际执行吸附与 notes 同时展示。页面离线仍能完整阅读，只有回传按钮根据 post/clipboard 能力改变行为。"),
            ("执行前再次解析", "最终运行始终消费 revised draft 的 resolved_config；页面 ACK 只表示 delta 已被安全接收，不代表仿真已通过数据门或算法门。"),
        ),
        example_title="从“比较估计 CSI 下的 MU 预编码”到一份唯一配置",
        example=(
            "<p>分类器首先命中 MU/precoding 画像，设计槽位要求确认基线和主指标；用户选择 Type-I 作为基线、cell throughput 作为主指标，并把估计模式改为 <code>ls_mmse</code>。Draft 记录的显式差分只有这些回答，其余阵列、场景、随机策略来自 preset 与默认。算法清单随后应明确展示 Type-I baseline、候选 RZF/SVD、估计 CSI 设计与真值评价，而不能继续显示 ideal CSI。</p>"
            "<p>用户又在说明书页面把功率约束改为 NEBF。bridge 只回传该白名单标量和 nonce，Agent 用同一 draft 修订后生成新 resolved config；旧页面若重放相同 nonce 不会重复写入。最终 manifest 里的 power_constraint、算法清单和页面复述都应为 NEBF。任何一处仍显示 EBF 都属于真相源漂移，测试必须失败。</p>"
        ),
        checks=(
            ("分类可重复", "同一 intent 多次运行得到同一 TaskProfile、同一命中证据和稳定 generic fallback；不依赖外部模型状态。"),
            ("两轮收敛", "代表性任务在目标两轮内填满结论槽位；默认接受、已回答项和 also_configurable 不会制造重复问题。"),
            ("优先级可证明", "defaults→preset→task hints→user overrides 用冲突值逐层测试，最终值与 explicit/history 均可追溯。"),
            ("目录来自配置", "切换 channel_est_mode、precoder、power_constraint 或 evaluation_mode 后，algorithm_list、derivations 与 caveat 同步变化。"),
            ("桥接最小权限", "非 loopback、错误 token、未知键、嵌套对象、超大 payload、过期 draft 与重放 nonce 全部拒绝；合法 delta 幂等。"),
            ("执行身份一致", "spec、Agent 摘要、resolved_config 与数据集/系统结果 manifest 对关键字段逐项相等。"),
            ("交互降级可见", "post 模式可按 spec_id 收到 delta；服务关闭/失败时页面和返回值明确为 clipboard，且 serve_error 可审计。"),
            ("产品动作真可用", "桌面/移动 Chromium 真点下载与截图；JSON 可解析、截图文件有有效 PNG/SVG 头，不能只按按钮文案验收。"),
        ),
        pitfalls=(
            "把关键词分类器写成“LLM 自动理解任意无线问题”，从而掩盖同义表达落入 generic 的边界。",
            "为展示平台能力，把所有可配置字段一次问给用户，反而没有锁住基线、指标和失败判据。",
            "在 algorithms 页面维护静态默认文案，配置已经切到 LMMSE/NEBF，说明仍写 LS/EBF。",
            "让浏览器直接写 YAML/执行仿真，绕过 Draft 历史、白名单、token 与物理 guard。",
            "把说明书页面收到 ACK 当成 Gate 通过或结果可信。",
            "把产品首页上的 Mock 示意图当作真实参数值；真实运行必须打开 sr_spec_sheet 返回的本次专属页面。",
        ),
        source_paths=("src/superran/decisions.py", "src/superran/plan.py", "src/superran/algorithms.py", "src/superran/algo_defs.py", "src/superran/algo_defs2.py", "src/superran/spec.py", "src/superran/webui.py", "src/superran/bridge.py"),
    ),
})


DETAIL_SPECS.update({
    "raytracing": DetailSpec(
        promise="把“射线追踪场景”拆成资产准备、后端身份、路径求解、时频采样、数据合同和快速探测六层，并明确 InternalSim probe 为什么能用于话务前的场景量级判断，却绝不能代替完整 RT/宽带信道。",
        principles=(
            "场景名、信道模型名与真正生成后端不是一回事。Sionna 内置场景可由未来 direct adapter 解析，自有城市资产通过独立 OSM/PLY 数据目录提供。唯一可靠身份是结果 metadata 的 <code>channel_generation_mode</code>；不能因为配置写了 <code>sionna_rt</code> 就把输出称为 RT。",
            "上游资产必须只读。Mitsuba 对部分VTK PLY头中的<code>obj_info</code>不兼容，<code>prepare_scene()</code>会在稳定进程锁内复制到独立缓存并清理副本；源树与准备后树各有内容SHA-256。缓存手改或源升级会重建，未完成发布journal会硬失败。",
            "场景几何身份与无线材料身份必须拆开。<code>scene_tree_fingerprint</code>绑定XML/PLY字节；<code>radio_config_revision</code>另绑定材料、玻璃比例和逐建筑无线覆写。这样同一几何的不同材料校准不会静默复用旧RT结果。",
            "场景fidelity必须由实际导出资产反推，而不是看OSM是否出现标签。L0只保证建筑/地面几何；L1要求道路、水体、绿地或植被同时有语义点与RT材质/mesh。材料参数存在仍不等于完成测量校准，calibration_status必须单列。",
            "RT 的时间变化来自路径几何与完整速度向量。最大 Doppler 是 |v|/λ，每条路径再按速度方向和传播方向投影；若先把速度压成到最近站的径向分量再在路径层投影，会重复乘余弦。Sionna 的 CFR 需要真实采样频率，当前先生成 slot 内 14 个 symbol 的物理响应/估计，再保留中间 symbol 作为系统 snapshot，保留因果估计结果而不把系统时长放大 14 倍。",
            "direct adapter 已经实现（<code>src/superran/sionna_rt.py</code>），装了 sionna-rt 即可用；但 Sionna 的 Paths 仍不是落盘合同——适配层只把逐径几何合成为 CFR，不持久化 delay/angle/material interaction，所以 RT 数据上的 <code>Dataset.paths()</code> 依旧硬失败。",
            "RT 只换信道矩阵，不换口径。适配层唯一覆写的是 <code>InternalSimSource._small_scale_channel</code> 这个接缝：以上的站点布局、撒点、LOS、路损、阴影衰落、服务小区选择、预波束 S/N/I 预算，以下的估计噪声、SSB、TDD 成对与元数据全部共用。所以同一套几何下 CDL 与 RT 的 <code>pathloss_dB</code>/<code>snr_dB</code>/<code>sir_dB</code>/<code>sinr_dB</code> 逐位相同，KPI 差异可归因到信道矩阵本身。RT 自己算的路损与时延扩展只写进 <code>meta.rt_pathloss_db</code>/<code>meta.rt_delay_spread_ns</code> 作旁证。",
            "阵列模型必须是共用的那一份。BS 端口阵因子调 <code>_spatial_panel_response</code>（端口相位中心间距 = <code>elements_per_rf_port × ae_vertical_spacing_lambda</code>），固定子阵方向图调 <code>fixed_subarray_response</code>，与 CDL 路径是同两个函数。64T 1 驱 3、256T 1 驱 6、垂直 0.67λ、<code>pol_h_v + top_to_bottom</code> 在换引擎后一个字都不变；再写一份必然漂移。",
            "合成必须带载波相位项：<code>H += g · exp(j2π f_d t) · exp(−j2π (f_c + f) τ) · a_BS · conj(a_UE)</code>。CDL 的时延是合成的、每簇另有随机相位，载波项被吸收了；RT 的径长差是真实的，径与径之间的相对相位就来自这一项。锚点是与 Sionna 自己的 <code>Paths.cfr()</code> 对拍，单端口单极化下相对误差 &lt; 2e-3，即 Sionna 内部 float32 的相位精度。",
            "极化槽顺序与 Sionna 默认相反。Sionna 自带的 <code>\"cross\"</code> 是 [−45°, +45°]，SuperRAN 的 <code>polarization_slant_angles_deg</code> 默认是 [+45°, −45°]。直接用 <code>cross</code> 会把两个极化端口块整体对调，所以适配层按配置 <code>register_polarization</code> 一个同序极化。",
            "站点要平移到场景包围盒中心。内置场景的坐标原点未必在城区里——munich 的包围盒中心是 (−68, −86)，把站点摆在原点实测 40 个随机 UE 只有 6 个能追到径，平移后四个内置场景覆盖率回到 10~12/12。平移只作用于送进 RT 的坐标，SuperRAN 自己的几何与路损仍在原坐标系（距离是平移不变量）。",
            "覆盖空洞是硬错误，不是回退。服务链路一条径都追不到时直接抛错并打印 BS/UE 坐标与修复建议；干扰小区零径返回零信道，因为那是真实的“该小区没有干扰”。任何情况下都不退回 TDL/CDL。",
            "InternalSim probe 快，是因为它刻意不回答需要完整频率矩阵的问题。它把 RB 限到 24、symbol 限到 4 并关闭 SSB，但沿用同一场景 seed 与几何；SNR 需要补回总功率在 RB 数变化产生的偏移，再与原 SIR 在线性域合成 SINR。first-party source 不截断；只有历史导入值落在旧 ±50 dB 边界时才标记为不可逆 clipped。",
            "“不存在便宜的 RT probe”是复杂度边界，不是功能遗漏。射线求解的主要成本在几何可见性、反射/绕射和路径追踪，少几个 RB 并不能等比例省掉；需要快速比较真实场景时，应减少 UE/drop/路径深度跑小 N 完整 RT，并把不确定性写入结果。",
        ),
        implementation=(
            ("解析 scene/preset", "<code>resolve_scene_config()</code> 把内置场景或本地资产 descriptor 展开为资产路径、载频、站点数、站高、ISD 和拓扑参数，未知/缺失字段不靠猜测补齐。"),
            ("准备只读资产", "<code>prepare_scene()</code>在场景目录外的稳定锁上串行准备；双指纹验证缓存，复制PLY后只在副本删除不兼容header，RF revision单独记录材料物理。"),
            ("形成真实性合同", "从实际语义点计数与RT材质count生成L0/L1、逐layer布尔值和calibration_status；配置把几何/RF/fidelity三份provenance带入数据集。"),
            ("执行路径求解", "Sionna 场景按 Tx/Rx 阵列、位置、材料与射线深度形成临时 Paths，并用其中的 delay、complex amplitude 与角度合成 CFR；当前窄腰只保留 CFR，没有导出原始逐径对象。"),
            ("取出引擎无关的径集合", "<code>SionnaRTSource._split_paths()</code> 把 Sionna 的 <code>[num_rx, num_rx_ant, num_tx, num_tx_ant, num_paths]</code> 拆成逐小区的 <code>RayPaths</code>（2x2 极化增益、时延、四个角度、多普勒），并按 <code>valid</code> 与 <code>tau &gt;= 0</code> 双判据剔除无效径。这一层不含 Sionna 类型，所以合成逻辑可以脱离 sionna 单测。"),
            ("合成信道矩阵", "<code>synthesize_channel()</code> 逐径乘上 BS 端口阵因子、1 驱 M 子阵方向图、UE 面板响应、载波+基带时延相位与多普勒时间相位，最后按平均功率归一到 1——与 CDL 路径同一约定，大尺度由上层 38.901 路损承担。"),
            ("同站扇区去重", "三个共址扇区的传播环境完全相同，差别只在天线朝向；按站点位置去重后 21 小区只追 7 次，朝向在 <code>synthesize_channel</code> 里按 <code>sector_azimuth_deg</code> 旋转。"),
            ("形成时频 CFR", "按载波与 OFDM 网格生成频点，使用完整 UE velocity 和平均 symbol 周期采样 14 个 symbol；导频/估计完成后提取中间 symbol 为 slot snapshot。"),
            ("判定真实后端身份", "落盘与加载均传播 <code>channel_generation_mode</code>、fallback reason、场景/资产 provenance；RT 的 <code>Dataset.paths()</code> 明确报未导出，绝不套 CDL 角度。"),
            ("运行有限 probe", "probe 复制配置后缩频域/时间轴，返回 geometry、SIR、pathloss、LOS、position、Doppler；对可逆 SNR 做 RB 功率修正，生成 not_available 和 excluded count。"),
        ),
        example_title="272 RB 正式场景为何不能直接复用 24 RB probe 的 SNR",
        example=(
            "<p>假设正式配置把同一总下行功率均分到 272 RB，而 probe 只生成 24 RB。后端看到的每 RB 信号功率会提高 <code>10log10(272/24)</code>，约 10.54 dB；若直接把 probe SNR 当成正式值，覆盖会虚高。实现从未夹逼的 raw SNR 中减去这项，SIR 因服务与干扰同时按 RB 功率变化而保持几何量级，再用 1/SINR=1/SNR+1/SIR 重算。</p>"
            "<p>first-party raw SNR 保留未截断值，因此可以精确减去 10.54 dB。若导入历史数据恰为旧边界 +50 dB，则无法知道截断前是真 50.2 还是 73 dB，该样本只能从 SNR/SINR 校正统计中剔除。若任务要算 PDP、频选 rank 或吞吐，仍必须升级到完整 H。</p>"
        ),
        checks=(
            ("资产不变", "准备前后源PLY不变；缓存双SHA逐字可复算，手改触发重建，发布journal阻止混版读取，RF材料变化修改revision。"),
            ("身份不冒充", "强制后端失败时 channel_generation_mode 明确为 fallback 并带原因；文档/报告标题不再按请求 source 推断。"),
            ("速度向量守恒", "正交、同向和反向路径的 Doppler toy case 与 v·k/λ 一致，不发生两次方向投影。"),
            ("snapshot 因果", "slot 内 symbol 估计完成后才抽取 snapshot；修改未来 symbol/slot 不改变当前可见 CSI。"),
            ("probe 校正", "同 seed 的 full/probe 在 geometry、SIR、distance、LOS 上一致；first-party 修正 SNR/SINR 与 full 小样本逐位对齐。"),
            ("逐径出口诚实", "RT 数据可计算 H/PDP/协方差/PMI，但 paths() 必须抛未支持；统计 CDL/TDL 才按实际 effective profile 返回剖面路径。"),
            ("与厂商对拍", "单端口单极化下本仓合成与 Sionna <code>Paths.cfr()</code> 相对误差 &lt; 2e-3；时延、载波相位、多普勒三个约定错任何一个都会跳到 O(1)。"),
            ("阵列没被绕过", "单径下 BS 端口响应与共用的 <code>_spatial_panel_response × fixed_subarray_response</code> 逐端口成常数比；把固定下倾改成 0° 信道必须变。"),
            ("换引擎可归因", "同一几何下 RT 与 CDL 的信道矩阵不同，而 pathloss/SNR/SIR/SINR/服务小区/UE 位置逐位相同。"),
            ("不静默回退", "把依赖探测打成缺 sionna 后，<code>require_source(\"sionna_rt\")</code> 硬报错而不是返回 internal_sim；引擎清单长度恒为 3。"),
            ("能力边界", "PDP、SE、throughput、wideband precoding 和 NMSE 在 probe 响应中显式 not_available，而不是返回零或占位数组。"),
        ),
        pitfalls=(
            "只看 <code>channel_model=sionna_rt</code> 就把 TDL fallback 结果称为真实城市射线追踪。",
            "为了让 Mitsuba 能读，直接就地改用户的 PLY 源资产。",
            "未设置 velocity 或仍用 1 Hz 采样，导致 14 个 symbol 只是静态复制。",
            "把 24 RB probe 当作 100 MHz 稀疏频点，继续计算 PDP、宽带 rank 或吞吐。",
            "对历史 ±50 dB 夹逼值做统一减法并把产生的平台解释为真实用户分布。",
            "声称减少 RB 就能同倍率降低 RT 的几何求解成本。",
            "在 ChannelSample 尚未导出 Sionna Paths 时，从 CDL 名称或站点几何拼一组假逐径角度。",
            "在 RT 分支里另写一份阵列响应，把 1 驱 3/1 驱 6 的馈电结构退化成 0.5λ 独立阵元。",
            "合成时只留基带时延相位、漏掉载波项 exp(−j2π f_c τ)，导致径间相对相位全错而幅度看起来正常。",
            "直接用 Sionna 的 <code>cross</code> 极化，把 +45/−45 两个端口块整体对调。",
            "把站点摆在场景坐标原点，然后把随机撒点撞上的大面积覆盖空洞当成“射线追踪算不出来”。",
            "服务链路零径时返回零矩阵或退回统计信道，让覆盖空洞变成一条看起来正常的样本。",
            "把 <code>rt_munich</code> 预设里写死的 6 个 UE 位置当成覆盖率结论——它们只是实测能跑通的参数，换站高或场景就要重扫。",
        ),
        source_paths=("src/superran/scenes.py", "src/superran/scene_assets.py", "src/superran/scenario.py", "src/superran/sionna_rt.py", "src/superran/channelhub.py", "src/superran/generate.py", "src/superran/loader.py", "tests/test_raytracing.py", "tests/test_sionna_rt_source.py", "tests/test_interference.py", "tests/test_physics_contract_extensions.py"),
    ),
})


DETAIL_SPECS.update({
    "referencesignals": DetailSpec(
        promise="把载波资源表、TDD 时隙、SSB/Gold/SRS/CSI-RS 序列与波束选择放在同一物理基线中，解释这些工具函数怎样支撑估计和测量，又为何“序列相关低”并不自动等于现场导频无污染。",
        principles=(
            "NR RB 数必须查 38.104 频率范围对应表，不能用带宽除以子载波间隔后取整。相同 50/100 MHz 与 60 kHz 在 FR1/FR2 可得到不同 RB 数；项目预置基线又显式从标准 273 RB 截成 272 RB，以形成 17 个完整 16-RB RBG。参考信号、SRS 轮转和调度必须共享同一载波对象，否则频域索引会差一个 RB。",
            "TDD 可用下行比例来自 D/U/S pattern 和特殊时隙 symbol 划分。一个 S slot 不能粗暴按完整 DL 或完整 UL 计；DL fraction 为普通 D slot 加上特殊时隙中 DL symbols 的比例，再除以周期总 slot。部分历史算法说明仍出现 0.7 工程值，正式系统和文档应从同一 pattern/special-slot 配置派生，并把固定值视为待迁移兼容边界。",
            "SSB 与 Gold/SRS 序列的作用首先是资源和相关性合同。PSS/SSS/PBCH-DMRS 使用各自序列结构与 cell identity，SRS 通过端口/循环移位/频域轮转区分资源。归一化相关可发现实现级冲突，但真实污染还取决于同步误差、功控、近远效应、频率选择性和复用拓扑；不能因为理想序列互相关低就宣称 LS 不受定向干扰。",
            "CSI-RS DFT 波束扫描与 PMI Type-I-style 是两套对象。前者的候选通常按 <code>[beam, port]</code> 存储，用 <code>argmax ||H w_i||²</code> 选接收功率最大波束；后者是 <code>[port, column]</code>，还涉及双极化共相、多层选择和反馈索引。两套实现都可能含 DFT，但 shape、用途、规模和时序不能互换。",
            "LS/LMMSE 估计不会决定干扰有没有方向性；方向性来自污染信号经过空间信道和导频投影后的协方差。LS 直接把相关干扰留在估计误差里，LMMSE 借助信道/噪声协方差抑制不可信方向。若给 LMMSE 的协方差是单位阵或错误维度，它会退化甚至误导，因此估计模式、协方差来源和序列资源必须共同记录。",
        ),
        implementation=(
            ("建立载波与 TDD", "<code>nr_rb_count()</code> 按 FR1/FR2 表查询；<code>tdd_pattern_info()</code> 展开 D/U/S 与特殊时隙的 DL/UL symbol 数，GP 缺省时由 14−DL−UL 推出，调用者据此导出 DL fraction。"),
            ("生成同步与参考序列", "<code>physical.py</code> 生成 PSS/SSS、PBCH DMRS、Gold sequence 与 SRS 基序列/循环移位，显式保持 cell/port/length 参数。"),
            ("验证相关合同", "<code>sequence_correlation()</code> 比较自相关峰、旁瓣和不同端口/小区互相关；测试覆盖循环移位与长度不一致处理。"),
            ("映射 SRS 频域轮转", "预置基线 C_SRS=63、B_SRS=1 每次覆盖 16 RB，17 次覆盖 272 RB；测量链按周期收集，不使用“年龄”作为周期名称。"),
            ("生成 CSI-RS 扫描码本", "<code>dft_codebook()</code> 构造单位范数候选波束，<code>select_beam()</code> 在当前 H 上计算接收总功率并返回索引与功率。"),
            ("进入估计与干扰投影", "LS/LMMSE 使用同一导频观测；<code>project_interference()</code> 按参考序列投影污染项。若干扰发送信号模型是 rank-1，precoded/isotropic 可能数值相同，不能据此声称方向性不存在。"),
        ),
        example_title="17 次 SRS 轮转、一个特殊时隙和两套 DFT 码本",
        example=(
            "<p>100 MHz 预置基线实际调度 272 RB。SRS 每次覆盖 16 RB，因此 17 次恰好覆盖全带；若误用标准表的 273 RB，第 18 次只剩一个残块，测量与 17-RBG 调度无法一一对齐。再假设 TDD 周期有 7 个 D、2 个 U、1 个 S，S 中 6/14 个 symbol 为 DL，则可用 DL 比例是 (7+6/14)/10，而不是写死 0.7 或把 S 整个算成 DL。</p>"
            "<p>同一个 64 端口阵列可生成 64 个 CSI-RS DFT 扫描波束，数组形状为 beam×port；PMI Type-I-style 则可能有 2048 个 port×column 候选，并含 +45/−45° 共相结构。二者都调用复指数并不意味着 index 17 指向同一个物理对象。文档和 API 必须标出 shape 与角色。</p>"
        ),
        checks=(
            ("标准表反例", "50/100 MHz、60 kHz 的 FR1/FR2 组合返回各自表值；非标准组合硬失败而不是除法估计。"),
            ("TDD symbol 对账", "纯 D/U、带特殊时隙和不同 DL/guard/UL 划分的 fraction 可手算对齐，周期 symbol 总数守恒。"),
            ("序列相关", "同序列零移位相关为 1，预期循环移位峰位置正确，不同资源互相关符合容差。"),
            ("SRS 覆盖", "17 个周期集合无遗漏无重叠地覆盖 0..271 RB，端口资源和元数据周期一致。"),
            ("码本 shape", "CSI-RS 码本为 beam×port 且每行单位范数；PMI 码本为 port×column，测试拒绝轴互换。"),
            ("估计方向性", "构造具有非单位空间协方差的定向干扰，LS 保留污染、LMMSE 按协方差抑制；单位阵退化行为有基线。"),
        ),
        pitfalls=(
            "用带宽除以 SCS 猜 RB 数，忽略 guard band 与 FR1/FR2 表差异。",
            "把特殊时隙完整计为 DL，或在算法说明里永久写死 0.7。",
            "把 SRS 周期称作 SRS 年龄；真正可定义年龄的是当前 CSI 距离最后一次可用测量的 elapsed time。",
            "用理想序列低互相关证明现场不存在导频污染和方向性干扰。",
            "把 CSI-RS DFT beam index 当成 PMI Type-I column index。",
            "认为 LS 导致干扰失去方向；LS 只是没有利用协方差做抑制。",
        ),
        source_paths=("src/superran/physical.py", "src/superran/hardware.py", "src/superran/channelhub.py", "src/superran/csi_aging.py", "tests/test_linklevel.py", "tests/test_csi_aging.py"),
    ),
})


DETAIL_SPECS.update({
    "bler": DetailSpec(
        promise="把一次 TTI/用户 grant 的单码字 TB 误块事件、完整 28 档 MCS 表、56 条原始曲线、1,824 个点、分析 CB→TB 后端和一次 CC/IR 重传逐层分开。读完后不仅能回答这次 ACK/NACK 查了什么，还能仅凭本章 JSON 与参考代码独立重写并复现当前预置 Table 3 路径。",
        principles=(
            "38.214 规定的是 CQI/MCS 表、谱效率和 TBS 过程，不提供一套可直接套用所有接收机的“标准 BLER 曲线”。<code>BlerModel</code> 用有限码长与实现损失构造分析瀑布；<code>preset_20b_256qam</code> 是内置表驱动 profile。两者都可以服务系统抽象，但报告必须带 backend/model_version，不能把分析模型或预置数据写成 3GPP 真值。",
            "预置表口径明确：误块事件是<strong>一个已调度 TTI 中该用户的单码字 TB</strong>，系统不另外暴露 CBLER。BLER 查询只使用跨 RBG、跨 rank stream 做 dB 平均后的码字级有效 SINR与选定 MCS；一次查询只形成一次 TB ACK/NACK。TBS、RE、rank、场景和码字数不作为曲线轴，物理编码内部即使分成多个 CB，也不应在预置表 3 路径上再次套 <code>1-(1-p)^C</code>。",
            "QAM 互信息首先依赖噪声约定。它服务表 1/2 的分析 BLER 与显式链路级 MIESM 调用，不进入当前 experience_v2+预置表 3 主链。代码使用复基带平均能量归一与 σ²=1/γ；若把实维高斯求积尺度误写成 √(2γ) 等价形式而未匹配方差，会引入约 3 dB 偏移。",
            "MIESM 先把逐 RE/RB SINR 映射成对应 QAM 互信息，平均后反解等效 AWGN SINR；EESM 用指数平均并由 β 控制曲率。二者都让深衰落比线性功率平均更重要，但 EESM β 必须按 MCS/链路曲线标定。库中已有两种实现，不代表 experience_v2 已使用：当前体验链仍是 RBG 内线性聚合、跨已分配 RBG 做项目既有 dB 平均近似。",
            "<code>bler_data_20b.py</code> 保存 MCS0..27 的 NewTx/ReTx 原始点；当前系统有意只消费 28 条 NewTx 曲线。<code>_bler_lookup(mcs, codeword_sinr_db)</code> 不接收 TBS/rank/场景，是已确认的通用曲线合同，不再标成导入缺口。TBS 仍参与字节承载、padding、PF credit 和重传身份冻结，但不改变 BLER。",
            "分析后端的 CB→TB 合成假设码块错误近似独立，TB BLER 为一减全部码块成功概率。它仅用于表 1/2；预置表 3 不区分 CB/TB 接口，因此不能读取同一条预置 TBLER 后再暗加一次 CB 合成。",
            "原始 ReTx 曲线不等于标准 HARQ 状态机，也不进入当前系统判错。初传 NACK 后只允许一次重传：默认 IR 把原 MCS 谱效除以 2，映射到不超过半谱效的最高等效 MCS并在原 SINR 上查 NewTx 曲线；CC 保持同档曲线并把 SINR 增加 10log10(2)。等效 MCS 只用于 BLER lookup，空口 MCS、RBG 数、rank 与 TBS 全部冻结。仍不能声称实现 RV、LLR、并行 process 或标准 timing。",
        ),
        implementation=(
            ("解析标准链路表", "<code>linkadapt.py</code> 维护 CQI/MCS/TBS 规则，TBS 对 RBG 数用表驱动反查；不能用全带字节除以 17 估计所需 RBG。"),
            ("生成 QAM MI", "对单位能量 M-QAM 星座和复高斯噪声做 Gauss-Hermite 数值积分，生成单调缓存，并提供正/逆插值。"),
            ("映射频选 SINR", "<code>effective_sinr()</code> 根据modulation/method选择MIESM或EESM；EESM接受显式beta，缺正式标定时结果只能作为参考。输入中的每个RB都必须有限，空数组或任一NaN/Inf当场失败，禁止只丢掉坏RB后用剩余好RB计算。"),
            ("计算分析 BLER", "表 1/2 按 MI 余量、码长和实现损失得到 CB 瀑布，再按 38.212 分段估算 C 并合成 TB BLER；anchor_check 只输出对标点。"),
            ("装载预置曲线", "<code>bler_data_20b.py</code> 提供 28 MCS×NewTx/ReTx 原始点，<code>verify_curves()</code> 检查 SHA、覆盖、横轴/BLER 单调和 10% crossing；系统使用范围明确为 NewTx 28 条，ReTx 只供审计。"),
            ("形成一次 TTI/TB 判错", "调度器先确定 grant、rank、MCS 与 TBS，再以码字级有效 SINR+MCS 查通用 NewTx 曲线并从独立 BLER 随机流抽一次 ACK/NACK；CB 不进入系统状态。"),
            ("进入唯一一次重传", "NACK 后冻结 MCS/RBG 数/rank/TBS；IR 查半谱效等效 MCS 的 NewTx 曲线，CC 查原 MCS NewTx 曲线并加 3.0103 dB。失败后结束 HARQ，字节留队并在后续成为新 TB。"),
            ("独立复现", "从本章复制完整 raw JSON，用纯 NumPy 重建横轴、对数域插值、门限反查、单码字 SINR、MCS 选择与 CC/IR；五个冻结锚点逐值对齐后再接系统状态机。"),
        ),
        example_title="MCS20 的 IR 为什么查 MCS10，却仍发送 MCS20",
        example=(
            "<p>当前预置 MCS 表中，MCS20 的名义谱效为 5.336 bit/RE。IR 抽象将它除以 2 得 2.668 bit/RE，再选谱效不超过 2.668 的最高档，精确得到 MCS10（2.572 bit/RE），不是凭感觉写死的 MCS7/8。</p>"
            "<p>这里的 MCS10 只决定重传 BLER 查哪条 NewTx 曲线。空口记录仍是初传 MCS20，并且 RBG 数、rank 与 TBS 原样不动；allocation 明细同时保存 <code>mcs=20</code> 和 <code>bler_lookup_mcs=10</code>，防止把工程等效档误当成重传 DCI。</p>"
            "<p>若选择 CC，仍查 MCS20 的 NewTx 曲线，只把码字级有效 SINR 增加 3.0103 dB。两种方案都最多一次重传。</p>"
        ),
        checks=(
            ("MI 锚点", "各 QAM 的 I(γ) 单调、范围为 0..log2(M)，逆映射往返误差受控；独立 Monte Carlo/高精度积分反查噪声尺度。"),
            ("有效 SINR 退化", "全元素相等时MIESM/EESM精确返回原SINR；深衰toy case方向与理论一致；空数组或任一非有限RB硬失败，不发生删样。"),
            ("TB 合成", "C=1 时 TB=CB，C 增大时 TB BLER 不下降，概率始终有限且无 NaN。"),
            ("预置数据完整", "固定摘要、28 MCS、56 curves、1,824 points、单调横轴/BLER 与 crossing 全部验证。"),
            ("全曲线可见", "手册 28 行审计表与 56 条瀑布由源常量直接生成；10% crossing、源范围和点数与 verify_curves 一致。"),
            ("参考面与事件标签", "查询结果带 source axis、single-codeword one-TTI/TB event、空口 MCS、lookup MCS、clamp 与通用曲线范围；分析后端不伪装预置表后端。"),
            ("一次重传身份", "强制 NACK 轨迹逐 TB 验证 MCS/RBG 数/rank/TBS 与 D/S 类型不变；失败后没有第二次重传，窗口末 pending 单列右删失。"),
            ("文档可执行性", "完整 MCS 表由代码生成；JSON raw rows 摘要等于 DATA_SHA256；独立参考实现五个锚点通过。"),
        ),
        pitfalls=(
            "把 38.214 的 MCS/TBS 表称为 3GPP BLER 曲线。",
            "QAM MI 的实/复噪声方差约定不匹配，造成整体约 3 dB 偏移。",
            "看到 <code>effective_sinr()</code> 已存在，就宣称体验系统已改成 MIESM/EESM。",
            "把含NaN/Inf的逐RB数组过滤后继续映射；这会系统性删除最差或未知RB并高估码字能力。",
            "使用未标定 EESM beta 生成正式性能结论。",
            "对预置曲线范围外做无依据外推，或把 MMSE SINR 曲线用于不同接收机参考面。",
            "把预置的一 TTI/TB 事件错误地展开为独立 CBLER，导致同一源曲线被重复合成。",
            "看到 TBS 不进入 BLER 查询就误报为遗漏；当前这是已确认的通用曲线合同。",
            "擅自把 <code>20B</code> 扩写成字节数、带宽或其他配置；该标识的确切展开与 reference profile 尚未结构化保存。",
            "把 IR 的等效低档 MCS 写回空口 MCS，破坏预置表规定的重传身份。",
            "重传失败后继续挂第二次重传，或把窗口末 pending TB 当作成功。",
        ),
        source_paths=("src/superran/linkadapt.py", "src/superran/bler_curves.py", "src/superran/bler_data_20b.py", "src/superran/system.py", "src/superran/experience.py", "tests/test_linkadapt.py"),
    ),
})


DETAIL_SPECS.update({
    "externalresults": DetailSpec(
        promise="说明自研算法怎样在不把任意代码交给 MCP 执行的情况下加入 SuperRAN：分析计划在数据生成前预注册，外部进程读取同一数据合同并写回逐样本 ResultArtifact，平台再验证数据摘要、样本顺序、指标身份和代码版本后才做配对统计。",
        principles=(
            "外部算法边界首先是安全与复现边界。<code>sr_export_eval_template</code> 返回可审阅的 Python 模板和数据路径，用户在自己的环境运行；MCP 不提供 exec/eval，也不导入用户模块。大型逐样本数组保存在 NPZ artifact，JSON 只承载标识、摘要和少量统计，因此对话长度和服务进程权限都不会决定实验能否运行。",
            "预注册锁的是分析身份，不是阻止研究迭代。<code>analysis.lock()</code> 对只含有限值、非空指标/单位和唯一secondary metrics的规范化JSON计算SHA-256，每次改baseline、primary metric、方向、样本规则或排除条件都会产生新文件；<code>sr_generate</code>在昂贵仿真开始前验证文件存在、digest未篡改且draft身份一致。结果注册只能继承数据集当时绑定的同一id，禁止看完数据后补绑或换绑。探索结果仍可计算，但分类为exploratory/secondary。",
            "可配对不只要求数组长度相同。ResultArtifact 同时绑定 dataset digest 与有序 sample_ids：前者防止相同 dataset_id 下底层 NPZ 被替换，后者防止筛选、排序或 off-by-one 让 A[i] 和 B[i] 不再来自同一个 realization。统计检验只会接受两列数字，不会主动发现这种身份错配，所以逐位置检查必须先于 Wilcoxon/置信区间。",
            "指标也是合同。metric、unit、higher_is_better、method 名称、代码 SHA 和 values SHA 共同定义一条结果臂；有限性在注册时硬检查。两臂即使 shape 相同，只要单位、方向、dataset digest 或样本身份不同，就不能进入配对结论。CRN 的意义是同一 sample_id 共享信道/话务/BLER 随机流，而不是只把顶层 seed 写成同一个整数。",
            "外部进程的 CSI 使用角色不可由 MCP 观察。<code>method_metadata</code> 可以声明只用 h_est、rank、接收机与超参，模板也默认按 h_est 设计/h_true 评价；但用户完全可以改脚本读取 h_true。平台只能把声明与 code hash 留作审计，不能把声明写成已验证事实。需要更强保证时必须引入受控执行沙箱或可检查的中间产物，这是下一层能力。",
        ),
        implementation=(
            ("锁定分析计划", "把 baseline、arms、primary/secondary metrics、direction、paired unit、exclusions 与 stopping rule 规范化为 canonical JSON，写新 prereg artifact 并返回 id/digest。"),
            ("绑定数据生成", "<code>sr_generate</code>先验证prereg存在、digest和draft，再开始信道生成；summary/manifest保存prereg_id与digest。计划变化必须重新锁定并生成新数据，不能回填旧数据。"),
            ("导出评测模板", "<code>deliver.build_code()</code> 生成读取 Dataset、按 h_est 设计、按 h_true 评价并写 ResultArtifact 的模板；用户可替换算法主体但保留合同。"),
            ("注册逐样本结果", "<code>ResultArtifact</code>要求values本来就是一维，验证有限值、sample_ids长度/唯一性/确属本数据集、metric/unit与数据摘要；只继承生成前已绑定prereg，写values.npz、manifest和SHA。"),
            ("比较两条结果臂", "先验证 dataset/prereg/metric/unit/direction 和逐位置 sample_ids，再在相同样本上计算差值、置信区间、效应量与预注册检验。"),
            ("通过发布门", "Gate 2 判断数据/配对/统计质量，Gate 3 根据预注册身份、primary/secondary、覆盖和限制决定 statement 能否使用强结论措辞。"),
        ),
        example_title="长度都是 1,000 的两列 SE，为什么仍可能完全不可比较",
        example=(
            "<p>A 臂按 sample_id 0000..0999 保存，B 臂误把结果循环移位为 0001..0999,0000。两列长度、均值、方差都正常，Wilcoxon 甚至可能给出很小 p 值；但每个差值都比较了不同信道 realization。ResultArtifact 的有序 ID 检查会在位置 0 报 0000≠0001，并说明两边集合相同但顺序不同，比较在统计前硬停止。</p>"
            "<p>另一种事故是同名 dataset_id 的 channels.npz 或物理语义 summary 被替换。sample_ids 仍相同，却对应不同信道/配置；dataset_digest 会阻断。只有摘要、顺序、指标、单位、方向和 prereg identity 全部一致，CRN 才能让逐样本差值隔离算法效应。外部脚本声明使用 h_est 仍只是声明，最终报告会保留这项审计限制。</p>"
        ),
        checks=(
            ("规范摘要稳定", "键顺序/空白不同的等价 prereg 得到同一 canonical digest；任一分析语义字段变化产生新 digest 与新文件。"),
            ("数据不可偷换", "替换 channels.npz 或 manifest 后 dataset digest 不匹配，旧 ResultArtifact 无法与新数据比较。"),
            ("有序配对", "顺序平移、缺样本、重复 ID、集合不同和长度不同分别给出明确错误，不进入统计函数。"),
            ("数值与身份", "NaN/Inf、unit mismatch、higher_is_better 冲突、metric 不同和 values SHA 漂移全部拒绝。"),
            ("外部不执行", "MCP 工具只生成模板/注册 artifact，不存在接受源码字符串并执行的路径；模板在独立用户进程可直接跑通 baseline。"),
            ("发布受预注册约束", "primary/secondary/exploratory 分类与绑定摘要一致；未注册或篡改计划只能输出受限陈述。"),
            ("禁止事后绑定", "未绑定数据集在register阶段传prereg_id、或已绑定数据集换成另一个id均硬失败；不存在/篡改/跨draft的prereg在生成前失败。"),
        ),
        pitfalls=(
            "为了方便接入，在 MCP 服务端增加任意 Python exec/eval。",
            "只比较数组 shape 或样本集合，不比较逐位置 sample_id。",
            "数据生成后改 prereg 文件内容，仍沿用旧 prereg_id。",
            "数据生成时容忍不存在的prereg、或在register阶段事后补绑；二者都会抹掉‘看数据前锁定’的时间边界。",
            "把二维结果数组静默flatten，或接受不属于当前dataset的显式sample_id子集。",
            "把逐样本 values 直接塞进 MCP JSON，造成传输、精度和审计问题。",
            "看到 method_metadata 写了 h_est 就宣称平台验证过外部算法没有偷看 h_true。",
            "两臂单位或 higher_is_better 不同仍强行计算一个百分比。",
        ),
        source_paths=("src/superran/analysis.py", "src/superran/results.py", "src/superran/deliver.py", "src/superran/loader.py", "src/superran/gates.py", "tests/test_results.py"),
    ),
})


# Every KaTeX expression in the guide must appear inside one of these cards.
# The symbol table is intentionally explicit: a formula is not considered
# documented merely because its surrounding paragraph names the topic.
FORMULA_SPECS: dict[str, FormulaSpec] = {
    "F_CHANNEL": FormulaSpec(
        "宽带双极化 MIMO 信道的逐径叠加",
        "每条射线先形成一个接收阵列向量与发射阵列向量的外积，再乘路径功率、频率时延相位、时间多普勒相位和极化耦合；所有射线做复数相干求和，得到时频点上的 H。",
        (("H<sub>u</sub>(t,f)", "用户 u 在时间 t、频率 f 的复 MIMO 信道矩阵。"),
         ("L / ℓ", "有效射线总数 / 当前射线索引。"),
         ("P<sub>ℓ</sub>", "第 ℓ 条射线的功率；平方根把功率换成复电压幅度。"),
         ("τ<sub>ℓ</sub>", "路径时延，决定随频率 f 旋转的相位。"),
         ("ν<sub>ℓ</sub>", "路径多普勒频移，决定随时间 t 旋转的相位。"),
         ("a<sub>UE,ℓ</sub> / a<sub>BS,ℓ</sub>", "该射线方向上的 UE / 基站阵列响应向量；上标 H 表示共轭转置。"),
         ("J<sub>ℓ</sub>", "2×2 路径极化耦合矩阵，包含同极化与交叉极化复系数。"),
         ("j", "虚数单位；两个指数都是纯相位项，不直接改变单径功率。")),
    ),
    "F_CHANNEL_SHAPE": FormulaSpec(
        "64T4R 场景中上下行矩阵为何互为 4×64 与 64×4",
        "矩阵行是接收侧端口，列是发射侧端口。下行由 64 个基站端口发、4 个 UE 端口收；上行角色交换，因此 shape 交换。TDD 互易不等于可以忽略这个方向约定。",
        (("H<sub>DL</sub>", "下行信道，接收端为 UE，shape 为 4×64。"),
         ("H<sub>UL</sub>", "上行信道，接收端为 gNB，shape 为 64×4。"),
         ("N<sub>UE,Rx</sub>", "UE 下行接收端口数，本场景为 4。"),
         ("N<sub>BS,Tx</sub>", "基站下行发射 RF 端口数，本场景为 64。"),
         ("ℂ", "复数域；每个矩阵元素同时含幅度和相位。")),
    ),
    "F_DATASET": FormulaSpec(
        "落盘信道张量的五个轴",
        "h_true 与 h_est 采用相同 shape，便于逐样本配对，但角色不同：前者用于物理评价，后者是算法设计时可见 CSI。每个轴都必须由 metadata 给出坐标和单位。",
        (("h<sub>true</sub>", "真实/评价信道；不能被算法当作可见 CSI，除非显式运行 ideal 上界。"),
         ("h<sub>est</sub>", "经导频、噪声、估计、插值和时延后可供设计的信道。"),
         ("N", "独立样本或 drop 数。"),
         ("T", "每个样本中的 channel snapshot 数，不等同于 OFDM symbol 数。"),
         ("RB", "频率轴上的资源块数。"),
         ("N<sub>BS</sub>", "基站 RF 端口数。"),
         ("N<sub>UE</sub>", "UE 天线/端口数。")),
    ),
    "F_PATTERN": FormulaSpec(
        "单元方向图的水平与垂直衰减",
        "离开波束中心后，衰减按归一化角度的平方增长，并被最大前后比/旁瓣衰减封顶。这里的 A 是正的 dB 衰减量，不是线性增益。",
        (("A<sub>H</sub>(φ)", "相对水平波束中心偏角 φ 处的水平衰减，单位 dB。"),
         ("A<sub>V</sub>(ε)", "相对垂直波束中心偏角 ε 处的垂直衰减，单位 dB。"),
         ("φ / ε", "水平方位偏角 / 垂直仰角偏角，单位需与波宽一致。"),
         ("φ<sub>3dB</sub> / θ<sub>3dB</sub>", "水平 / 垂直 3 dB 全波宽；偏到半波宽时衰减约 3 dB。"),
         ("A<sub>m</sub>", "最大允许衰减，用来限制抛物线包络。"),
         ("12", "3GPP 式抛物线系数，使半个 3 dB 波宽对应约 3 dB。")),
    ),
    "F_PATTERN_COMBINE": FormulaSpec(
        "从二维 dB 包络到复信道电压幅度",
        "水平和垂直衰减先在 dB 域相加并封顶，再从峰值增益中扣除。最终乘进复信道的是电压幅度 g_E，所以除以 20；若用除以 10 会把功率效应重复一次。",
        (("G<sub>E</sub>(φ,ε)", "该方向上的单元功率增益，单位 dB/dBi。"),
         ("G<sub>max</sub>", "单元波束中心的峰值增益。"),
         ("A<sub>H</sub> / A<sub>V</sub>", "水平 / 垂直方向的正 dB 衰减。"),
         ("A<sub>m</sub>", "合成方向图的最大衰减上限。"),
         ("g<sub>E</sub>", "乘入 Jones/阵列响应的线性电压幅度。")),
    ),
    "F_JONES": FormulaSpec(
        "+45°/−45° 极化单元的 Jones 向量",
        "同一个方向图幅度乘以极化基底上的方向向量，得到某个极化端口对两正交场分量的复电压响应。两种斜极化不是复制两根天线，而是不同的场方向。",
        (("f<sub>p</sub>(φ,ε)", "极化端口 p 在方向 (φ,ε) 上的二维 Jones 响应。"),
         ("g<sub>E</sub>", "单元方向图给出的线性电压幅度。"),
         ("ζ<sub>p</sub>", "端口 p 的极化斜角。"),
         ("ζ<sub>0</sub> / ζ<sub>1</sub>", "预置交叉极化的 +45° / −45°。"),
         ("cos / sin 分量", "Jones 基底中两个正交电场方向上的投影。")),
    ),
    "F_SUBARRAY_PATTERN": FormulaSpec(
        "垂直子阵因子与端口响应",
        "一个 RF 端口驱动 M 个垂直阵元。各阵元接收/发射相位与馈电权做复数相干求和，得到随仰角和频率变化的子阵因子；Fᴴ 再把物理阵元响应压到端口空间。",
        (("S<sub>M</sub><sup>RX</sup>(ε,f)", "M 阵元垂直子阵在仰角 ε、频率 f 的接收方向因子。"),
         ("M / q", "每 RF 端口驱动的阵元数 / 子阵内阵元索引。"),
         ("w<sub>q</sub>", "第 q 个阵元的复馈电权；接收表达式使用其共轭。"),
         ("z<sub>q</sub>", "第 q 个阵元的垂直坐标，以参考波长为单位。"),
         ("f/f<sub>ref</sub>", "当前频率相对馈电校准参考频率的比例。"),
         ("a<sub>AE</sub> / a<sub>port</sub>", "物理阵元级 / RF 端口级阵列响应。"),
         ("F", "物理阵元到 RF 端口的稀疏馈电耦合矩阵。")),
    ),
    "F_RAY_POLARIZATION": FormulaSpec(
        "一条射线如何同时经过极化与空间阵列",
        "发射 Jones 向量先经过路径极化矩阵，再投影到接收 Jones 向量，得到该极化对的复标量 c。这个标量与收发阵列外积、路径幅度及时频相位相乘，形成单径矩阵贡献。",
        (("c<sub>ℓ,p_t,p_r</sub>", "路径 ℓ 从发射极化 p_t 到接收极化 p_r 的复耦合系数。"),
         ("f<sub>TX,p_t</sub> / f<sub>RX,p_r</sub>", "发射 / 接收端口的 Jones 响应向量。"),
         ("J<sub>ℓ</sub>", "路径极化耦合矩阵。"),
         ("H<sub>ℓ</sub>", "第 ℓ 条射线对完整 MIMO 信道的矩阵贡献。"),
         ("a<sub>TX,ℓ</sub> / a<sub>RX,ℓ</sub>", "该射线方向上的发射 / 接收空间阵列响应。"),
         ("P<sub>ℓ</sub>, τ<sub>ℓ</sub>, ν<sub>ℓ</sub>", "路径功率、时延和多普勒。")),
    ),
    "F_FEED": FormulaSpec(
        "固定电下倾如何写进子阵复馈电权",
        "阵元原始幅相校准乘以随垂直位置线性变化的相位斜坡，再整体做二范数归一。正负号与 z 坐标定义共同决定主瓣向上还是向下，不能只凭角度数值判断。",
        (("w<sub>q</sub>", "归一化后第 q 个垂直阵元的复馈电权。"),
         ("A<sub>q</sub> / ψ<sub>q</sub>", "第 q 个阵元的校准幅度 / 固有相位。"),
         ("z<sub>q</sub>", "第 q 个阵元的物理垂直坐标。"),
         ("θ<sub>tilt</sub>", "目标固定电下倾角；项目默认 6° 且允许配置。"),
         ("‖·‖<sub>2</sub>", "对子阵所有未归一复权取二范数，保证权向量能量为 1。"),
         ("k / q", "归一分母中的阵元索引 / 当前阵元索引。")),
    ),
    "F_COUPLING": FormulaSpec(
        "64T 1驱3 的 192×64 稀疏 F",
        "每个 RF 端口只连接同一极化、同一水平位置、对应垂直端口下的三个物理阵元。其他行列为零，因此 F 同时表达接线拓扑和复馈电校准。",
        (("F<sub>e,r</sub>", "物理阵元 e 到 RF 端口 r 的复耦合系数。"),
         ("e / r", "192 个物理阵元的展平索引 / 64 个 RF 端口的展平索引。"),
         ("h", "水平端口位置索引。"),
         ("v<sub>RF</sub>", "RF 端口层的垂直位置索引。"),
         ("p", "+45°/−45° 极化索引。"),
         ("q", "一个 1驱3 子阵内部的阵元索引 0..2。"),
         ("w<sub>q</sub>", "该子阵阵元的归一化复馈电权。")),
    ),
    "F_COUPLING_256": FormulaSpec(
        "256T 1驱6 的端口编号与 1536×256 稀疏 F",
        "端口采用统一的 pol-h-v 展平顺序；每个端口列连接六个垂直物理阵元。端口编号合同与物理阵元编号是两层对象，不能用一个公式混代。",
        (("r<sub>256</sub>(p,h,v)", "256T 场景中极化 p、水平 h、垂直 v 对应的 RF 端口索引。"),
         ("p", "极化块索引 0 或 1；每块 128 个端口。"),
         ("h / v", "16 个水平位置 / 8 个垂直 RF 端口位置的索引。"),
         ("F<sub>e,r</sub>", "物理阵元 e 到 RF 端口 r 的复馈电系数。"),
         ("q", "1驱6 子阵内索引 0..5；物理垂直位置为 6v+q。"),
         ("1536 / 256", "物理阵元总数 / RF 端口总数。")),
    ),
    "F_EFFECTIVE": FormulaSpec(
        "从物理阵元空间压到 RF 端口空间",
        "F 的列描述端口如何激励物理阵元。发射侧把端口激励乘 F，等价地把阵元级信道右乘 F；接收阵列响应则用 Fᴴ 合成。两式应使用同一 F 与索引合同。",
        (("a<sub>AE</sub>", "物理天线阵元级响应向量。"),
         ("a<sub>port</sub>", "经过馈电网络后的 RF 端口级响应。"),
         ("H<sub>AE</sub>", "发射侧仍以物理阵元为列的阵元级信道。"),
         ("H<sub>port</sub>", "以 RF 端口为列、供预编码使用的有效信道。"),
         ("F / Fᴴ", "阵元到端口耦合矩阵 / 其共轭转置。")),
    ),
    "F_SRS_RX": FormulaSpec(
        "SRS 接收观测模型",
        "这是完整4端口信道的概念观测式。2T4R基线不会同时发送4端口，而是在相邻两个SRS资源上分别观察H的两列组；去扩频/估计的输入始终是Y，而不是直接访问H。",
        (("Y<sub>SRS</sub>[k]", "第 k 个 SRS 频域资源上的基站接收矩阵。"),
         ("H<sub>UL</sub>[k]", "该频点真实上行信道，shape 为 64×4。"),
         ("X<sub>SRS</sub>[k]", "已知SRS导频；2T4R每次只包含当前两个UE端口。"),
         ("I[k]", "同一资源上的其他用户/小区干扰，保留 64 端口空间方向。"),
         ("N[k]", "接收机热噪声。"),
         ("k", "SRS 所在的子载波或等效频域导频索引。")),
    ),
    "F_SRS_2T4R_STITCH": FormulaSpec(
        "两个64×2估计怎样拼成2T4R终端的64×4信道",
        "终端只有两条同时发射链。端口0/1在当前可用SRS机会发送，端口2/3在5 ms后的下一可用机会发送；gNB按逻辑天线身份拼列。两列组不是同一时刻，因此系统必须分别维护CSI lag。",
        (("Ĥ<sub>01</sub>", "第一次2-port SRS得到的64×2估计，对应UE逻辑天线端口0/1。"),
         ("Ĥ<sub>23</sub>", "下一可用SRS机会得到的64×2估计，对应UE逻辑天线端口2/3。"),
         ("t<sub>0</sub>", "第一条SRS资源的测量时刻，例如slot7。"),
         ("t<sub>1</sub>", "第二条SRS资源的测量时刻，即下一个可用SRS机会。"),
         ("5 ms", "30 kHz、8:2 TDD表中slot7到slot17的时间间隔。"),
         ("k", "当前被17-hop选中的16-RB RBG/频域资源。"),
         ("[A | B]", "按列拼接矩阵A和B；不代表两次测量同时发生。"),
         ("64×4", "64个gNB接收端口乘4个UE逻辑天线端口的完整互易信道。")),
    ),
    "F_LS": FormulaSpec(
        "导频点上的最小二乘信道估计",
        "把接收观测右乘已知导频矩阵的 Moore–Penrose 伪逆，得到使平方误差最小的 H。LS 不使用信道协方差先验；非正交干扰会作为有方向的估计污染留下。",
        (("Ĥ<sub>LS</sub>[k]", "第 k 个导频频点上的 LS 信道估计。"),
         ("Y<sub>SRS</sub>[k]", "实际接收的 SRS 观测。"),
         ("X<sub>SRS</sub>[k]†", "已知导频矩阵的伪逆；† 不是普通转置。"),
         ("k", "导频频域索引。")),
    ),
    "F_LMMSE": FormulaSpec(
        "从导频 LS 值插值到目标位置的 LMMSE",
        "用目标位置与导频位置的交叉协方差对带噪 LS 导频做维纳加权。先验与真实统计匹配时，平均 MSE 可下降；R_pp、R_v 或时延扩展失配时不保证每条样本都优于 LS。",
        (("ĥ<sub>t,LMMSE</sub>", "目标频点/时刻 t 上的 LMMSE 信道估计向量。"),
         ("ĥ<sub>p,LS</sub>", "所有导频位置 p 上堆叠的 LS 估计。"),
         ("R<sub>tp</sub>", "目标位置与导频位置之间的信道交叉协方差。"),
         ("R<sub>pp</sub>", "导频位置之间的信道协方差。"),
         ("R<sub>v</sub>", "LS 观测中的噪声与干扰误差协方差。"),
         ("t / p", "需要估计的目标位置 / 已知导频位置集合。")),
    ),
    "F_SRS_LAG": FormulaSpec(
        "SRS 周期、处理时延与可用 CSI snapshot",
        "调度时刻先从周期机会集合中选择最近一条‘测量时刻+处理时延不晚于当前时刻’的SRS，再计算当前时刻距该测量的陈旧时长。处理时延通过可用性约束选择更早的测量，不能从lag里机械相减；连续lag再向上量化为snapshot步数。",
        (("𝒯<sub>b</sub>", "资源/RBG与天线端口组b的全部周期SRS测量时刻集合，可延伸到预启动历史。"),
         ("t<sup>★</sup><sub>m,b</sub>(t)", "在时刻t已经完成处理的最近一次测量时刻；必须满足t_m+D_proc≤t。"),
         ("τ<sub>b</sub>(t)", "当前时刻距离最近可用测量的CSI陈旧时长，天然包含等待处理造成的回看。"),
         ("D<sub>proc</sub>", "从SRS采样到该估计可供调度使用的处理时延。"),
         ("q<sub>b</sub>(t)", "把连续CSI时延向上量化后的整数snapshot滞后步数。"),
         ("Ĥ<sub>b</sub>(s)", "系统在 snapshot s 实际可用的估计信道。"),
         ("Δt<sub>snap</sub>", "相邻 channel snapshot 的时间间隔。"),
         ("⌈·⌉", "向上取整，确保不使用尚未完成的更近 snapshot。")),
    ),
    "F_EBF": FormulaSpec(
        "EBF：只约束总发射功率",
        "单位范数方向矩阵 W 的 L 个流等分总功率 P。所得物理矩阵 Q_EBF 的总协方差迹不超过 P，但单根天线功率可以不相等。",
        (("Q<sub>EBF</sub>", "真正送入物理信道的 EBF 预编码矩阵，shape 为 M×L。"),
         ("W", "每列为一个单位范数预编码方向的矩阵。"),
         ("P", "该频点/RBG 可用的总发射功率。"),
         ("L", "同时发送的数据流数；每流分到 P/L。"),
         ("tr(QQᴴ)", "所有物理天线发射功率之和。"),
         ("H", "上标 H 表示共轭转置，不是信道矩阵 H。")),
    ),
    "F_PEBF": FormulaSpec(
        "PEBF：由最大功率天线决定的全局缩放",
        "先找 EBF 中功率最大的天线，用同一个 α 缩放整张 Q，使它满足每天线上限 P/M。由于所有元素同比例缩放，波束几何保持，但总功率常用不满。",
        (("Q<sub>PEBF</sub> / Q<sub>EBF</sub>", "每天线受限后的物理权 / 原总功率受限权。"),
         ("α", "0..1 的全局电压缩放因子；功率因此缩放 α²。"),
         ("P/M", "总功率 P 均分到 M 根物理发射天线后的单天线上限。"),
         ("q<sub>m,:</sub>", "Q 中第 m 根天线跨全部 L 个流的行向量。"),
         ("‖q<sub>m,:</sub>‖²", "第 m 根天线的发射功率。"),
         ("max<sub>m</sub>", "在所有天线中选择当前功率最大者。")),
    ),
    "F_NEBF": FormulaSpec(
        "NEBF：逐天线行归一并用满每天线预算",
        "每根非零天线的行向量分别缩放到范数 sqrt(P/M)。总功率因此可达到 P，但不同天线缩放因子不同，会改变各流列向量的内积并可能破坏 MU 零陷。",
        (("q<sub>m,:</sub><sup>NEBF</sup>", "NEBF 中第 m 根天线跨所有流的物理行向量。"),
         ("q<sub>m,:</sub><sup>EBF</sup>", "归一前 EBF 的对应天线行。"),
         ("P/M", "每根天线允许使用的功率。"),
         ("‖·‖<sub>2</sub>", "行向量的二范数；其平方等于该天线功率。"),
         ("m / M", "当前发射天线索引 / 发射天线总数。")),
    ),
    "F_MMSE": FormulaSpec(
        "线性 MMSE 接收矩阵",
        "接收机在匹配目标有效信道的同时，用流间 Gram、外部干扰协方差和热噪声做正则化。它不是把干扰当作一个固定 dB 损失，而是在接收天线空间内利用方向性抑制干扰。",
        (("G", "线性 MMSE 接收滤波矩阵；每一列/行对应一个待检测流，取决于实现约定。"),
         ("H<sub>eff</sub>", "已经包含发射预编码和功率的有效接收信道。"),
         ("H<sub>eff</sub>ᴴH<sub>eff</sub>", "各发送流在接收空间中的 Gram 矩阵。"),
         ("R<sub>uu</sub>", "邻区/其他未联合检测信号的干扰协方差。"),
         ("N<sub>0</sub>", "每接收维的热噪声功率。"),
         ("I", "与 Gram 矩阵同维的单位阵。")),
    ),
    "F_STREAM_SINR": FormulaSpec(
        "post-MMSE 的逐流 SINR 分解",
        "分子是接收向量对目标流的投影功率；分母依次包含其他流泄漏、空间有色干扰和热噪声。所有项都在同一个接收后参考面计算，才能相除。",
        (("γ<sub>ℓ</sub>", "第 ℓ 个数据流经过接收机后的线性 SINR。"),
         ("g<sub>ℓ</sub>", "检测第 ℓ 流的 MMSE 接收向量。"),
         ("h<sub>ℓ</sub> / h<sub>j</sub>", "目标流 ℓ / 其他流 j 的有效接收信道向量。"),
         ("P<sub>ℓ</sub> / P<sub>j</sub>", "目标流 / 干扰流的发射功率。"),
         ("R<sub>uu</sub>", "外部干扰的接收协方差。"),
         ("N<sub>0</sub>I", "空间白热噪声协方差。"),
         ("|·|²", "复内积的模平方，即投影后的功率。")),
    ),
    "F_RB_LINK_BUDGET": FormulaSpec(
        "总载波功率与每 RB 噪声的参考面",
        "总发射功率均匀分给 N_RB 个 RB 时需扣除 10log10(N_RB)；单 RB 热噪声从 −174 dBm/Hz 乘 12 个子载波的带宽，再加接收机噪声系数。两式单位都是 dBm。",
        (("P<sub>tx,RB</sub>", "每个 RB 上的发射功率，单位 dBm。"),
         ("P<sub>tx,total</sub>", "整载波总发射功率，单位 dBm。"),
         ("N<sub>RB</sub>", "分功率时使用的活动/建模 RB 数。"),
         ("−174", "常温热噪声功率谱密度，单位 dBm/Hz。"),
         ("Δf", "子载波间隔；一个 RB 含 12 个子载波。"),
         ("NF", "接收机噪声系数，单位 dB。"),
         ("N<sub>RB</sub>[dBm]", "左式符号表示单 RB 噪声功率；与 RB 个数同名时需结合上下文区分。")),
    ),
    "F_PREBEAM_ANCHOR": FormulaSpec(
        "把几何 SINR 锚定到波束赋形前的信号与干噪",
        "先用平均信道能量和发射功率定义未波束赋形信号 S0，再从几何 SINR 反推出 I+N。rank-1 波束后的 SINR 只乘奇异值相对平均能量的增益，避免重复加入阵列增益。",
        (("S<sub>0</sub>", "波束赋形前的平均接收信号功率锚点。"),
         ("E[|H|²]", "所选信道元素/端口上的平均功率增益。"),
         ("P", "当前资源上的总发射功率。"),
         ("I+N", "外部干扰与热噪声总功率。"),
         ("γ<sub>geo,dB</sub> / γ<sub>geo</sub>", "几何 SINR 的 dB / 线性形式。"),
         ("σ<sub>1</sub>", "信道最大奇异值；平方对应最佳 rank-1 方向的功率增益。"),
         ("γ<sub>rank1</sub>", "应用最佳 rank-1 波束方向后的线性 SINR。")),
    ),
    "F_RANK": FormulaSpec(
        "rank 自适应为何比较 r×谱效",
        "每个候选 rank 先得到包含功率平分、接收机和干扰的有效 SINR，再映射为单流谱效 η；乘流数 r 得到总谱效，选择最大者。不能只选 SINR 最高的 rank 1。",
        (("r*", "被选中的空间层数/rank。"),
         ("r", "候选 rank，本场景枚举 1、2、3、4。"),
         ("η(·)", "SINR 到单流可用谱效/MCS 效率的映射。"),
         ("γ<sub>eff</sub>(r)", "使用 rank r 后的有效 SINR，已包含每流功率和接收处理。"),
         ("arg max", "返回使总可用谱效最大的候选索引，而不是最大值本身。")),
    ),
    "F_SVD_DIRECTION": FormulaSpec(
        "SVD 发送方向怎样从代码信道得到",
        "SuperRAN 保存的 H 轴序是 [frequency, BS-port, UE-port]，因此代码里的 Hcode 是常见数学下行矩阵的共轭转置。每个频点对可见时间样本的发射协方差做特征分解，取最大 r 个特征向量；T=1 时与 Hcodeᴴ 的右奇异向量严格等价。这里仅确定方向，功率在下一式施加。",
        (("H<sup>code</sup><sub>t,f</sub>", "代码中频点 f、时刻 t 的 [BS-port, UE-port] 复信道矩阵。"),
         ("R<sub>tx,f</sub>", "发射端口协方差；对时间做功率平均，不先平均复信道。"),
         ("T", "当前用于设计这套静态方向的可见时间样本数；系统逐快照路径通常为 1。"),
         ("V<sub>f</sub> / Λ<sub>f</sub>", "协方差的单位正交特征向量与按强到弱排列的特征值。"),
         ("W<sub>SVD,f</sub>", "前 r 个 SVD/协方差特征方向组成的 [BS-port, stream] 矩阵，列范数为 1。"),
         ("r", "本次 PMI/SVD 公平对照共同使用的 rank。")),
    ),
    "F_PMI_CODEBOOK": FormulaSpec(
        "Type-I-style PMI 列如何生成和选择",
        "水平/垂直 O1=O2=4 过采样 DFT 向量先做 Kronecker 积，再用四个 QPSK 共相位构成双极化候选列。多层 PMI 逐层选择残余协方差上接收功率最大的列并投影掉已选方向；这是可复现的工程列子集近似，不冒充 38.214 完整多层矩阵码本枚举。",
        (("N<sub>1</sub> / N<sub>2</sub>", "水平/垂直 RF 端口网格尺寸；双极化总端口数 M=2N1N2。"),
         ("O<sub>1</sub> / O<sub>2</sub>", "水平/垂直 DFT 过采样倍数，当前均为 4。"),
         ("a<sub>h</sub> / a<sub>v</sub>", "单位范数水平/垂直 DFT 导向矢量。"),
         ("v<sub>i,j</sub>", "二维空间波束，展平顺序再按阵列 metadata 做端口置换。"),
         ("p", "双极化相对共相位索引，取 0/1/2/3，对应 1/j/−1/−j。"),
         ("c<sub>q</sub>", "一个单位范数 Type-I-style 双极化候选列；q 合并 i、j、p。"),
         ("R<sub>res,l</sub>", "选择第 l 层前的残余宽带发射协方差。"),
         ("q<sub>l</sub>", "第 l 层返回的 PMI 列索引。")),
    ),
    "F_SPATIAL_POWER": FormulaSpec(
        "EBF、PEBF、NEBF 怎样把方向变成物理发射 Q",
        "方向矩阵的每列先分到 P/r，形成总功率 EBF 权 Q0。PEBF 只用一个全局系数受最大发射天线限制，因此保持列间几何但可能用不满功率；NEBF 对每根天线单独缩放到 P/M，能用满总功率但会改变列内积。代码 Q 的形状是 [antenna, stream]，所以每天线约束是行范数；若采用 [stream, antenna] 记法，它就是用户常说的列范数归一。",
        (("W<sub>dir</sub>", "SVD 或 PMI 给出的单位列方向矩阵。"),
         ("Q<sub>0</sub>", "含每流功率幅度的 EBF 物理发射矩阵。"),
         ("P / r / M", "RBG 总发射功率 / rank / 发射 RF 端口数。"),
         ("p<sub>m</sub><sup>(0)</sup>", "Q0 第 m 根天线的行范数平方，即该天线发射功率。"),
         ("α", "PEBF 的唯一全局缩放系数，由最大发射天线决定。"),
         ("D", "NEBF 的逐天线对角缩放矩阵；要求每一行非零。"),
         ("C", "后续公式中的空间功率约束选择，C∈{EBF,PEBF,NEBF}，默认 NEBF。")),
    ),
    "F_BF_STREAM": FormulaSpec(
        "同一 gNB CSI 上两套权的逐 RB、逐流 post-MMSE SINR",
        "实际发送权 TX 和 PMI 参照权必须使用同一份基站可见 CSI、同 rank、同功率约束、同损伤协方差和同一经典 MMSE 接收机。这一层得到线性 SINR，尚未做 RBG 或宽带聚合。",
        (("γ<sub>f,k</sub><sup>(x)</sup>", "权 x 在 RB f、第 k 流上的线性 post-MMSE SINR。"),
         ("x", "比较分支：TX 为实际发送权（默认 SVD），PMI 为 Type-I-style 宽带参照权。"),
         ("f / k / r", "RB 索引 / 流索引 / 当前比较的 rank。"),
         ("H<sup>code</sup><sub>gNB,f</sub>", "该时刻 gNB 可用的 [BS,UE] 信道；可能是 SRS 估计或陈旧 CSI，不是当前 h_true。"),
         ("Q<sub>x,f</sub>", "已经包含每流功率且施加相同 C=EBF/PEBF/NEBF 的物理发射矩阵；不得再乘一次 P/r。"),
         ("G<sub>x,f</sub>", "常见 [UE,stream] 数学方向的有效信道 (Hcode)ᴴQ。"),
         ("R<sub>n,f</sub>", "噪声加干扰协方差；当前系统级经典 MMSE 基线把总损伤白化为标量乘单位阵。"),
         ("[·]<sub>kk</sub>", "矩阵逆的第 k 个对角元素。")),
    ),
    "F_BF_RBG": FormulaSpec(
        "RB 先在线性功率域聚合成 RBG",
        "每个 RBG 内先对其 RB 的线性 SINR 取平均，然后转 dB。直接平均 RB dB 会得到不同结果，因此实现顺序是合同的一部分。",
        (("γ̄<sub>b,k</sub><sup>(x)</sup>", "RBG b 内第 k 流的线性平均 SINR。"),
         ("ℱ<sub>b</sub>", "RBG b 所包含的 RB 索引集；默认 16 RB，尾组可更短。"),
         ("|ℱ<sub>b</sub>|", "该 RBG 的实际 RB 数。"),
         ("Γ<sub>b,k</sub><sup>(x)</sup>", "线性平均转成的 dB SINR。"),
         ("10log<sub>10</sub>", "线性功率比到 dB 的转换。")),
    ),
    "F_BF_GAIN": FormulaSpec(
        "逐 RBG BF Gain 与用户级宽带 BF Gain",
        "每个 RBG 上先对各流的 TX-PMI dB 差取平均，再对全部 B 个 RBG 取算术平均。该值只是 gNB 的发送侧预测；当前真实信道上重算的差值只作审计，不进入 MCS。",
        (("G<sub>BF,b</sub>", "RBG b 上的流平均 BF Gain，单位 dB。"),
         ("G<sub>BF</sub>", "用户级宽带 BF Gain，加到 CQI 初始 MCS 的目标 BLER SINR 门限。"),
         ("B", "当前载波的 RBG 数；100 MHz TDD 默认为 17。"),
         ("r", "两套权强制共用的 rank，不允许一边 rank1、一边 rank2。"),
         ("Γ<sup>(TX)</sup>-Γ<sup>(PMI)</sup>", "除预编码权外所有条件相同时的 dB SINR 差。")),
    ),
    "F_AMC_PRED": FormulaSpec(
        "SU MCS 所依据的 gNB AMC 预测坐标",
        "CQI 先查内部离散表得基础 MCS 及其 SINR 门限，再加波束赋形增益得到 SINR_AMC_PRED。它不是物理 SINR_NEBF/PEBF/EBF，也不是接收端真值；OLLA 不进入该 dB 坐标，而是在反折 MCS 后以连续 MCS offset 叠加。",
        (("γ<sub>AMC,pred</sub>", "gNB 用于无 OLLA MCS 反折的预测坐标，单位 dB。"),
         ("Γ(MCS(CQI))", "CQI 经 MCS 映射后对应的基准 SINR 门限。"),
         ("G<sub>BF</sub>", "基于当前可见 CSI 预测的波束赋形增益，单位 dB。")),
    ),
    "F_RX_BLER": FormulaSpec(
        "最终 BLER 必须落到真实接收 SINR",
        "gNB 设计出的同一个物理 Q 作用到 h_true 后，按同一经典 MMSE 公式得到逐 RB/流 SINR，再按 RBG 内线性、RBG/流 dB 平均形成单码字有效 SINR。只有这个接收端量和最终发送 MCS 才能查询 NewTx 曲线；仅有 CQI/BF/OLLA 时 BLER 是 unknown。",
        (("γ<sub>RX</sub>", "当前 TB 在真实接收信道上的单码字有效 SINR。"),
         ("H<sub>true</sub>", "当前时刻真实信道，只用于接收评估，不回填当次 BF/MCS 决策。"),
         ("Q<sub>SVD+C</sub>", "gNB 在 h_prec 上设计、并按选定功率约束 C 形成的实际发射矩阵。"),
         ("A<sub>RBG,stream</sub>", "RBG 内线性平均，再跨 RBG/stream 做 dB 算术平均的聚合算子。"),
         ("m<sub>final</sub>", "CQI→BF→MCS→OLLA→floor/clip 后真正发出的 MCS。"),
         ("C<sub>m</sub>(·)", "预置 profile 中 MCS m 的 NewTx BLER 插值曲线。"),
         ("P<sub>TB,error</sub>", "本用户本 TTI 单码字 TB 的误块概率。")),
    ),
    "F_MU_SINR": FormulaSpec(
        "MU 发送侧 MCS 比 SU 多出的三项",
        "MU 从同一 SU 基准出发，在 SINR 域加入用户配对残留相关性损失和流/用户平分功率损失。损失项通常为负 dB；反折基准 MCS 后才叠加 SU/MU OLLA。",
        (("γ<sub>tx,MU</sub>", "MU 调度时用于选该用户 MCS 的发送侧预测 SINR。"),
         ("Γ(MCS(CQI))", "CQI 给出的 SU 基准 SINR 门限。"),
         ("G<sub>BF</sub>", "基于可见 CSI 的波束赋形增益。"),
         ("L<sub>corr</sub>", "配对用户残留相关性/零陷不完美造成的 dB 损失。"),
         ("L<sub>power</sub>", "MU 同资源上总功率在更多流之间平分造成的 dB 损失。")),
    ),
    "F_POWER_LOSS": FormulaSpec(
        "MU 并发用户数带来的等功率损失",
        "若同一 RBG 总功率固定并在 K_MU 个等 rank 用户之间均分，则每个用户相对 SU 少 10log10(K_MU) dB。两个用户时就是 −3.01 dB。",
        (("L<sub>power</sub>", "MU 相对 SU 的每用户功率损失，单位 dB，取非正值。"),
         ("K<sub>MU</sub>", "同一资源上并发、分享总功率的 MU 用户数。"),
         ("log<sub>10</sub>", "功率比例到 dB 的十进对数。")),
    ),
    "F_CQI_IIR": FormulaSpec(
        "宽带 CQI 的一阶 IIR 滤波",
        "每个 CSI 上报时刻把新观测按系数 λ 混进状态；第一次上报直接初始化状态，"
        "不从 0 缓慢爬升。取 floor 得到真正上报的 4-bit codepoint。λ=1 等价于不滤波。"
        "早先用的是对全部历史取平均，记忆无限长，跑得越久越跟不上信道变化。",
        (("s<sub>k</sub>", "第 k 次上报之后的滤波状态；域由 cqi_filter_domain 决定。"),
         ("x<sub>k</sub>", "第 k 次上报的原始观测：CQI 档或量化前的 PMI-SINR。"),
         ("λ", "滤波系数 cqi_filter_lambda，(0,1]；越小记忆越长。0.25 已由负责人确认为工程默认，但尚未经现场测量/设备数据标定。"),
         ("⌊·⌋", "取整到整数 codepoint；保守方向，不四舍五入。"),
         ("CQI<sub>rep</sub>", "本快照实际上报并用于查 Γ 的 4-bit codepoint。")),
    ),
    "F_GRANT_SINR": FormulaSpec(
        "解码 SINR 只在实际授予的 RBG 上聚合",
        "误块抽签用的接收 SINR 必须由同一个发射权作用到真实信道算出，并且只在本次 "
        "grant 真正占用的那些 RBG 上聚合：RBG 内在线性功率域平均 RB，跨 RBG 与流在 "
        "dB 域算术平均。用全带均值判一个只占 1~2 个 RBG 的小包，频率选择性越强错得越多。",
        (("γ<sup>grant</sup><sub>RX</sub>", "本次 grant 的单码字有效接收 SINR（dB）。"),
         ("G", "本次 grant 实际占用的 RBG 集合；|G| 是它的元素个数。"),
         ("γ<sub>RX,g</sub>", "第 g 个 RBG 的接收 SINR（dB）。"),
         ("γ<sup>lin</sup><sub>RX,b</sub>", "第 b 个 RB 的线性域接收 SINR。"),
         ("|g|", "该 RBG 包含的 RB 数；Type-0 首尾 RBG 可能不足名义值。")),
    ),
    "F_RANK_SE": FormulaSpec(
        "逐 rank 的估计谱效与它的滤波",
        "对每个 rank 假设，用该 rank 的 AMC 预测坐标反折出真会发下去的 MCS，"
        "谱效记为 rank×MCS 谱效，再乘一个 DMRS 开销系数，最后做一阶 IIR。"
        "每流功率 P/r 已经在坐标里（CQI 与 BF Gain 都按该 rank 的每流功率算过），"
        "不需要再补 10log10 项。预估 MCS 低于闸门的 rank 谱效直接置 0——那一层"
        "基本传不动，不配当有效层。",
        (("SE&#770;<sub>r</sub>", "rank r 在当前快照下的瞬时估计谱效。"),
         ("γ<sup>(r)</sup><sub>AMC,pred</sub>", "rank r 的 AMC 预测坐标：Γ(MCS(CQI_r)) + BF Gain_r。"),
         ("S(·,p)", "在预置 NewTx 曲线中选择 BLER≤p 的最高 MCS 的查表算子。"),
         ("⊕Δ", "叠加连续 MCS 域 OLLA 偏置后 floor 并钳位，与实际发送同一条路径。"),
         ("m<sub>r</sub>", "rank r 假设下真会发下去的 MCS。"),
         ("m<sub>min</sub>", "最小 MCS 闸门 min_mcs_threshold，现场默认 9。"),
         ("ρ<sub>r</sub>", "rank r 的资源消耗系数 resource_cost_ratio，现场 [1.0, 0.97, 0.95, 0.93]，体现高 rank 的 DMRS 开销。"),
         ("SE&#772;<sub>r</sub>", "滤波后的估计谱效，周期决策只看它。"),
         ("β", "谱效滤波系数 se_filter_beta，现场默认 0.1。")),
    ),
    "F_RANK_SWITCH": FormulaSpec(
        "Rank 的周期决策与迟滞",
        "只在决策周期到达、且谱效滤波样本攒够时判一次。升 rank 要求最优的滤波谱效"
        "严格超过当前的 G↑ 倍（现场 1.1，即高 10%），两个几乎并列的候选因此不会每个"
        "周期互相顶替。默认 unified_ratio 让降 rank 使用同一条式子：最优的滤波谱效"
        "也必须严格超过当前的 G↓ 倍；G↑=G↓=1.1，因此升降都要 10% 余量。"
        "spec_asymmetric 仅保留作反向对照。每次快速回退让判决周期翻倍，最多 2⁴。",
        (("r<sup>★</sup>", "本次决策的最优 rank，由滤波谱效的 argmax 给出。"),
         ("SE&#772;<sub>r</sub>", "rank r 的滤波估计谱效。"),
         ("r<sub>cur</sub>", "当前正在使用的 rank。"),
         ("G<sub>↑</sub>", "升 rank 迟滞 gain_factor_raise，现场默认 1.1。"),
         ("G<sub>↓</sub>", "降 rank 迟滞 gain_factor_reduce，默认 1.1（最优需高 10%）。"),
         ("T<sub>rank</sub>", "判决周期 period_tti，现场默认 1000 个 TTI。"),
         ("n", "快速回退次数，判决周期按 2ⁿ 指数退避，n ≤ max_backoff_times。")),
    ),
    "F_HARQ_DELAY": FormulaSpec(
        "ACK/NACK 生效时刻由 TDD 图案决定",
        "TB 在下行时隙发出后，先等到第一个上行时隙把 ACK/NACK 带回来，再等到其后"
        "第一个下行时隙才生效——OLLA 更新与该 TB 的重传资格都从这一刻开始。"
        "偏移完全由图案推出，不引入 k1/k2 参数，也不建模 PUCCH 资源或并行 HARQ 进程。",
        (("t", "这次下行传输所在的 TTI 索引。"),
         ("u", "t 之后第一个上行时隙的 TTI 索引，ACK/NACK 搭它回传。"),
         ("t<sub>eff</sub>", "反馈真正生效的 TTI：u 之后第一个 D/S 时隙。"),
         ("slot(·)", "TDD 图案在该 TTI 的时隙类型，取值 D / S / U。")),
    ),
    "F_TBS": FormulaSpec(
        "从可用 RE 到 38.214 TBS",
        "先用资源元素数、调制阶数、码率和层数形成未量化信息量，再经过 38.214 的分段量化、码块和对齐规则得到离散 TBS。最后一步使 TBS 只近似线性。",
        (("N<sub>info</sub>", "量化前的可承载信息比特数。"),
         ("N<sub>RE</sub>", "扣除 DMRS、控制与开销后可用于数据的资源元素数。"),
         ("Q<sub>m</sub>", "每个调制符号的比特数，如 QPSK=2、16QAM=4。"),
         ("R", "目标编码率。"),
         ("ν", "传输层/rank 数。"),
         ("Q<sub>38.214</sub>(·)", "3GPP TS 38.214 规定的 TBS 离散量化过程。"),
         ("TBS", "本次 transport block 的最终比特/字节容量，使用时需注明单位。")),
    ),
    "F_RBG_SEARCH": FormulaSpec(
        "按队列字节反查“恰够”的 RBG 数",
        "在固定 slot、MCS 和 rank 下，预先计算各 RBG 前缀的单调不减 TBS 行；searchsorted(side='left') 找到第一个不小于队列需求的位置。量化平台合法，它仍会选择最早位置；资源增加却令 TBS 下降才阻断。",
        (("n<sub>u</sub>*", "用户 u 本次传完当前需求所需的最小 RBG 数。"),
         ("n", "候选 RBG 数，当前系统范围 1..17。"),
         ("TBS(s,m<sub>u</sub>,r<sub>u</sub>,n)", "slot 类型 s、用户 MCS m_u、rank r_u 和 n 个 RBG 对应的真实 TBS。"),
         ("B<sub>u</sub>", "用户 u 当前 FIFO 队首/可发送业务字节需求。"),
         ("TBS 向量", "对 1..17 RBG 预计算的单调查表行。"),
         ("searchsorted", "返回第一个值 ≥B_u 的零基位置，因此公式再加 1 转成 RBG 数。")),
    ),
    "F_PF": FormulaSpec(
        "经典 PF 的瞬时机会与历史服务比",
        "分子用用户当前拿满 17 RBG 时的可调度 TBS 表示瞬时机会，分母是同一记账口径下的指数平均服务量。排序后实际只分按需 RBG，平均量再用实际 credit 更新。",
        (("M<sub>u</sub>(t)", "用户 u 在 TTI t 的 PF 排序度量，越大越优先。"),
         ("TBS<sub>u</sub>(17,t)", "当前 CSI/MCS/rank 下用户占满 17 RBG 的潜在 TBS。"),
         ("R̄<sub>u</sub>(t)", "用户 u 的历史指数平均 PF credit，不是全带谱效常数。"),
         ("ε", "防止新用户或长期未调度用户分母为零的极小正数。"),
         ("u / t", "用户索引 / 当前调度 TTI。")),
    ),
    "F_QOS_PF": FormulaSpec(
        "参数化 QoS-PF 如何退化回经典 PF",
        "业务权重乘瞬时速率幂、历史平均速率反幂和时延因子。默认 α=β=1、γ=0、w=1 时就是经典 PF；当前决策先使用这一退化点。",
        (("M<sub>u</sub>", "用户 u 的 QoS-PF 排序度量。"),
         ("w<sub>u</sub>", "业务/优先级权重。"),
         ("R<sub>u</sub><sup>inst</sup>", "当前 TTI 的潜在瞬时服务量。"),
         ("R̄<sub>u</sub>", "历史指数平均服务量。"),
         ("D<sub>u</sub><sup>HoL</sup>", "队首包已经等待的时间。"),
         ("D<sub>u</sub><sup>budget</sup>", "该业务的时延预算/PDB。"),
         ("α / β / γ", "历史公平、瞬时机会和时延紧迫度三个非负指数。")),
    ),
    "F_RAVG": FormulaSpec(
        "PF 历史平均量的指数更新",
        "每个 D/S 下行调度机会先让旧平均衰减，再加入本次实际 credit；U/G 时隙不更新。未调度用户的 credit 为零，所以平均量自然下降；只分到一个 RBG 的用户绝不能按 17 RBG 记账。",
        (("R̄<sub>u</sub>(t)", "更新前用户 u 的历史平均 PF 服务量。"),
         ("R̄<sub>u</sub>(t+1)", "更新后、供下一 TTI 排序使用的平均量。"),
         ("R<sub>u</sub><sup>credit</sup>(t)", "本 TTI 实际记账值：默认 scheduled TBS，也可配置 ACK goodput。"),
         ("a", "指数平均的新样本权重。"),
         ("T<sub>PF</sub>", "PF 平均窗口的等效 TTI 数，a=1/T_PF。")),
    ),
    "F_OLLA": FormulaSpec(
        "ACK/NACK 驱动的 OLLA 闭环",
        "ACK 时向更激进方向小步上调，NACK 时向保守方向较大步下调，再限制在上下界。步长比例决定长期目标 BLER；SU 与 MU 使用用户级独立状态。",
        (("Δ(t)", "当前用户在时刻 t 的连续 MCS-index OLLA 偏置。"),
         ("Δ(t+1)", "处理本次 ACK/NACK 后的偏置。"),
         ("1<sub>ACK</sub> / 1<sub>NACK</sub>", "本次结果对应的 0/1 指示函数。"),
         ("s<sub>↑</sub> / s<sub>↓</sub>", "ACK 上调步长 / NACK 下调步长，单位 MCS 档。"),
         ("clip", "把偏置限制在配置上下界；历史 *_db 字段名仅为兼容保留。")),
    ),
    "F_FINAL_MCS": FormulaSpec(
        "基准 MCS 与最终空口 MCS 分属 SINR 反折和 MCS-domain OLLA 两步",
        "系统先用不含 OLLA 的发送侧基准 SINR查预置 BLER 表，保存 m_base 供审计；随后把用户级连续 MCS-index OLLA 加到 m_base，floor 并钳位。MU 先把相关性损失与功率损失加在 SINR 域后反折 m_base,MU，再加 SU/MU 两份 OLLA。",
        (("S(γ,p)", "在预置 NewTx 曲线中选择 BLER≤p 的最高 MCS 的查表算子。"),
         ("γ<sub>base</sub>", "不含 OLLA 的发送侧基准 SINR；来自可用 CQI/BF 预测。"),
         ("m<sub>base,SU</sub>/m<sub>base,MU</sub>", "SU/MU 各自在完成 SINR 域增益/损失后反折的无 OLLA 基准档，结果字段为 mcs_without_olla。"),
         ("m<sub>tx,SU</sub>/m<sub>tx,MU</sub>", "本 TTI 真正写入 grant 的最终发送 MCS。"),
         ("Δ<sub>SU</sub>/Δ<sub>MU</sub>", "用户级 SU/MU OLLA 连续 MCS-index 状态。"),
         ("L<sub>corr</sub>/L<sub>power</sub>", "MU 残留相关性与并发 rank 功率分摊损失，通常≤0 dB。"),
         ("p<sub>target</sub>", "MCS 查表的目标初传 BLER，默认 10%。"),
         ("⌊·⌋ / clip", "先向下取整连续 MCS offset，再限制到当前 profile 的 MCS 上下界。")),
    ),
    "F_BUSY_RATE": FormulaSpec(
        "掐尾体验速率与含头体验速率",
        "两者使用相同的已完成 payload，并排除最后一个不完整/尾包；差别只在分母起点。掐头去尾从首包第一次调度算，含头从首包到达算，因此把首包排队等待纳入体验。",
        (("R<sub>trim</sub>", "不含首包等待的掐头去尾体验速率。"),
         ("R<sub>head</sub>", "包含首包等待的含头体验速率。"),
         ("B<sub>i</sub>", "busy period 中第 i 个已计入包的有效字节。"),
         ("K", "该 busy period 的包计数；求和到 K−1 表示排除尾包。"),
         ("t<sub>ACK,K−1</sub>", "最后一个被计入包的 ACK 完成时刻。"),
         ("t<sub>first TX</sub>", "首包第一次被调度的时刻。"),
         ("t<sub>arrival,1</sub>", "首包到达 FIFO 的时刻。")),
    ),
    "F_FIRST_PACKET": FormulaSpec(
        "首包时延只量到第一次调度",
        "它衡量包从进入队列到第一次获得空口资源的等待，不包含后续重传或完整传输时间。包从未被调度时不应填零，而应计入观测覆盖率分母。",
        (("D<sub>first</sub>", "单个包的首包/首次调度等待时延。"),
         ("t<sub>first scheduled</sub>", "该包第一次出现在有效 grant 中的时刻。"),
         ("t<sub>arrival</sub>", "该包对象生成并进入用户 FIFO 的时刻。")),
    ),
    "F_PRB_UTIL": FormulaSpec(
        "本小区 PRB 利用率与 MU 配对比例",
        "PRB 利用率按所有可用 DL/S slot 的 RBG 等价资源做分母，S slot 用可用比例 f_slot 折算。MU 比例按生效 MU 的物理 PRB 等价数除以已使用 PRB，而不是除以全部可用 PRB。",
        (("U<sub>PRB</sub>", "测量窗口内本小区已用 PRB/RBG 等价资源占可用资源的比例。"),
         ("n<sub>RBG,used</sub>(t)", "TTI t 中至少被一个 SU/MU grant 占用的物理 RBG 数；MU 不重复计两遍。"),
         ("f<sub>slot</sub>(t)", "完整 D slot 为 1，特殊 S slot 为其可承载下行数据的比例。"),
         ("17", "当前载波每个完整 DL TTI 的可用 RBG 总数。"),
         ("U<sub>MU</sub>", "已使用资源中实际生效 MU 配对资源的比例。"),
         ("MU PRB equivalent", "考虑 S slot 折算后的 MU 物理 PRB 使用量。"),
         ("used PRB equivalent", "考虑 S slot 折算后的全部已用物理 PRB 量。")),
    ),
    "F_RB_COUPLING": FormulaSpec(
        "逐 RB 功率与邻区活动如何同时改变信号和干扰",
        "服务小区在 RB r 的功率系数乘目标信号，各邻区自己的功率系数乘对应干扰，再由邻区 PRB 活动比例缩放干扰总和并加热噪声。调整某个 RB 会改变跨小区同频耦合，不能只保持总功率就假设性能不变。",
        (("γ<sub>u,r</sub>", "用户 u 在 RB r 上的线性 SINR。"),
         ("c(u)", "用户 u 的服务小区索引。"),
         ("q<sub>c,r</sub>", "小区 c 在 RB r 上相对均匀 PSD 的线性功率倍率。"),
         ("S<sub>u,c(u),r</sub>", "服务小区到用户 u 在 RB r 的目标信号功率。"),
         ("I<sub>u,c,r</sub>", "邻小区 c 到用户 u 在该 RB 的干扰功率。"),
         ("N<sub>u,r</sub>", "用户 u 在该 RB 的热噪声功率。"),
         ("η<sub>u</sub>", "用户 u 所在系统场景采用的邻区 PRB 活动比例；当前为全网统一配置。"),
         ("Σ<sub>c≠c(u)</sub>", "对所有非服务小区的同频干扰求和。")),
    ),
    "F_IOT": FormulaSpec(
        "从 SIR/SINR 拆出干扰、噪声与 IoT",
        "给定同一参考信号功率 S，SIR 决定 I，SINR 决定 I+N，两者相减得到 N。IoT 描述干扰把噪声底抬高多少 dB；若差值为负，输入口径不一致。",
        (("IoT", "Interference over Thermal：干扰加噪声相对纯噪声的抬升，单位 dB。"),
         ("S", "统一参考面上的目标信号功率，使用线性单位。"),
         ("I", "同一参考面上的总干扰功率。"),
         ("N", "同一参考面上的热噪声功率。"),
         ("SIR / SINR", "信干比 / 信干噪比，公式中的指数输入为 dB 值。"),
         ("10log<sub>10</sub>", "线性功率比到 dB 的转换。")),
    ),
    "F_CRN": FormulaSpec(
        "共同随机数让 A/B 差值逐样本配对",
        "A 与 B 在相同 drop、话务、BLER 和调度随机流上运行，先算每个样本的差 d_i，再对差值做统计。这样环境难易度的共同波动被抵消，置信区间通常比两组独立抽样更窄。",
        (("d<sub>i</sub>", "第 i 个配对样本上算法 A 与 B 的 KPI 差值。"),
         ("Y<sub>i</sub><sup>A</sup> / Y<sub>i</sub><sup>B</sup>", "同一随机场景下 A / B 的观测 KPI。"),
         ("i", "独立统计单元，如 drop 或 replication，而不是任意 TTI。"),
         ("drop", "用户撒点、路径和大尺度状态的随机流。"),
         ("traffic / BLER / scheduler", "话务到达、误块抽样和调度 tie-break 随机流，A/B 必须同名同种子。")),
    ),
    "F_CONSERVE": FormulaSpec(
        "体验仿真的字节守恒",
        "所有已到达业务字节在任一时刻必须且只能位于四个互斥去向之一：已 ACK、仍排队、已发未决或按策略丢弃。padding 不属于到达业务字节，不能拿来补平等式。",
        (("B<sub>arrived</sub>", "截至统计时刻所有生成并进入系统的业务 payload 字节。"),
         ("B<sub>ACK</sub>", "已经成功 ACK、从队列永久移除的业务字节。"),
         ("B<sub>queued</sub>", "仍在用户 FIFO 中等待发送的业务字节。"),
         ("B<sub>inflight</sub>", "已提交传输但尚未得到最终 ACK/NACK 处理的业务字节。"),
         ("B<sub>dropped</sub>", "按显式丢包策略移除的业务字节。")),
    ),
}


FORMULA_SPECS.update({
    "F_PDP_IFFT": FormulaSpec(
        "从 RB 中心频域信道得到未归一 PDP",
        "频域复信道先乘窗，再沿 RB 轴执行带 sqrt(N_RB) 的 IFFT。时延响应取模平方并跨时间与端口平均，得到仍保留线性能量的 PDP，而不是把峰值缩放到 1。",
        (("H<sub>t,k,m,u</sub>", "时间 t、RB 索引 k、基站端口 m、UE 端口 u 上的复频域信道。"),
         ("w[k]", "能量归一的频域 Hann 窗；N_RB&lt;3 时退化为矩形窗。"),
         ("g<sub>t,m,u</sub>[ℓ]", "对应 realization 在时延 tap ℓ 上的复响应。"),
         ("N<sub>RB</sub>", "观测到的 RB 中心频点数；sqrt 因子采用正交能量尺度。"),
         ("P[ℓ]", "第 ℓ 个 tap 的平均线性功率，未做峰值归一。"),
         ("E<sub>t,m,u</sub>", "对时间、基站端口与 UE 端口 realization 取均值。")),
    ),
    "F_PDP_AXIS": FormulaSpec(
        "RB 中心采样决定的时延分辨率与无模糊周期",
        "相邻频域观测间隔是一个 RB 的 12 个子载波跨度。频点数扩大总观测带宽并改善分辨率；相邻间隔单独决定 IFFT 周期。",
        (("Δf<sub>obs</sub>", "相邻 RB 中心频点的间隔。"),
         ("Δf<sub>SCS</sub>", "OFDM 子载波间隔，例如 30 kHz。"),
         ("12", "一个 NR RB 包含的子载波数。"),
         ("Δτ", "时延 tap 的物理分辨率。"),
         ("N<sub>RB</sub>", "频域观测点数。"),
         ("T<sub>amb</sub>", "IFFT 时延轴的无模糊周期；超出后会模周期折返。")),
    ),
    "F_PDP_MOMENT": FormulaSpec(
        "圆周局部矩与 Hann 仪器核去嵌",
        "先选功率加权圆周均值 bar-tau，再把每个 tap 到均值的差包回最近的半周期。观测二阶矩减去 Hann 核自身方差，负值截到 0 后开方得到物理 RMS delay spread。",
        (("τ<sub>ℓ</sub>", "第 ℓ 个 IFFT tap 在 0..T_amb 周期上的时延。"),
         ("bar τ", "由 PDP 功率决定分支的圆周平均时延。"),
         ("δ<sub>ℓ</sub>", "tap 到平均时延的最近周期残差，范围为负半周期到正半周期。"),
         ("P[ℓ]", "第 ℓ 个时延 tap 的线性功率。"),
         ("σ<sub>w</sub>²", "相同频域窗在时延域形成的确定性仪器核方差。"),
         ("τ<sub>rms</sub>", "去除窗核方差后的 RMS 时延扩展。")),
    ),
    "F_CSI_SWEEP": FormulaSpec(
        "跳频完整采集窗与平均 CSI 陈旧时长",
        "一次 SRS 只覆盖一个跳频片段时，要 H_hop 次机会才扫完全带。均匀轮转下，跳频等待加周期内相位的平均正好是完整扫描时间的一半，再加固定处理时延。",
        (("T<sub>sweep</sub>", "一轮 SRS 跳频覆盖全部目标 RBG 所需时间。"),
         ("H<sub>hop</sub>", "一轮完整覆盖所需跳数；预置 272-RB 基线为 17。"),
         ("T<sub>SRS</sub>", "相邻 SRS 发送机会的周期。"),
         ("bar τ<sub>CSI</sub>", "跨 RBG、周期相位平均后的 CSI 陈旧时长。"),
         ("D<sub>proc</sub>", "信道估计、算权与调度可用之间的固定处理时延。")),
    ),
    "F_CSI_REPORT_HOLD": FormulaSpec(
        "PMI/CQI 报告的因果保持",
        "在当前 snapshot s，只能选择时间不晚于 t_s 的最新已到达报告 q。PMI/CQI 在下一份报告到达前保持不变，不能按物理 snapshot 频率即时重算。",
        (("s / t<sub>s</sub>", "当前 channel snapshot 索引 / 对应物理时刻。"),
         ("q<sub>i</sub> / t<sub>qi</sub>", "第 i 份 CSI 报告及其可用时刻。"),
         ("q(s)", "当前时刻最近一份已经可用的报告。"),
         ("PMI", "预编码矩阵指示；当前为宽带 Type-I-style 工程近似。"),
         ("CQI", "与该报告链绑定的宽带信道质量指示。"),
         ("max", "只在满足 t_qi≤t_s 的历史集合中取最新项，保证因果性。")),
    ),
    "F_CSI_AGING_SINR": FormulaSpec(
        "旧 CSI 设计、当前真值评价的老化链",
        "基站用 d_s 个 snapshot 之前的估计信道设计 W，接收端在当前真实 H 上加入干扰协方差和热噪声后计算 post-MMSE SINR。老化因此通过方向错位产生泄漏，而不是固定减一个 dB。",
        (("W<sub>s</sub>", "snapshot s 实际用于发送的预编码矩阵。"),
         ("Ĥ<sub>s-ds</sub>", "基站当时可见的陈旧估计信道。"),
         ("d<sub>s</sub>", "由逐 RBG staleness 向上换算得到的 snapshot lag。"),
         ("H<sub>s</sub>", "实际传输时的当前真实信道。"),
         ("R<sub>uu</sub>", "邻区或未联合检测信号的干扰协方差。"),
         ("N<sub>0</sub>", "热噪声功率；与 H、R_uu 使用相同接收参考面。"),
         ("γ<sub>s</sub>", "当前发送在 post-MMSE 接收机后的逐流/聚合 SINR。")),
    ),
    "F_CSI_ERROR_MODEL": FormulaSpec(
        "鲁棒 RZF 的加性 CSI 误差模型",
        "真实等效用户信道由估计值和误差相加。若每个复系数误差独立同分布，沿 N_BS 个发射端口累加后的误差 Gram 期望是 N_BS·sigma_e² 倍单位阵。",
        (("H", "真实等效 MU 信道矩阵，行对应流、列对应基站端口。"),
         ("Ĥ", "基站用于设计预编码的估计/陈旧信道。"),
         ("E", "估计误差矩阵 H-Ĥ。"),
         ("N<sub>BS</sub>", "预编码器看到的基站发射端口数。"),
         ("σ<sub>e</sub>²", "每个复信道系数的线性误差方差。"),
         ("I", "流维度的单位阵；i.i.d. 假设把误差加载简化为各向同性。")),
    ),
    "F_CSI_ERROR_VARIANCE": FormulaSpec(
        "用真值/估计对离线标定每系数误差方差",
        "把同一参考面、同一 shape 的 H 与 Ĥ 相减，Frobenius 能量除以复系数总数，得到一个离线标定样本。运行时不能用当前 H 真值计算它。",
        (("hat σ<sub>e</sub>²", "由离线真值数据估计的每复系数误差功率。"),
         ("H / Ĥ", "配对的真实信道 / 估计信道，必须同 shape 同归一化。"),
         ("‖·‖<sub>F</sub>²", "矩阵所有复系数模平方之和。"),
         ("N<sub>coef</sub>", "参与求和的复系数个数。"),
         ("离线", "该式依赖 H 真值，只能用于标定或仿真审计，不能成为现网 oracle。")),
    ),
    "F_ROBUST_RZF": FormulaSpec(
        "噪声与 CSI 不确定性共同加载的 robust RZF",
        "RZF 在估计信道 Gram 上加 lambda I 后再求逆。lambda 的第一项抑制噪声放大，第二项抑制因 CSI 误差导致的过深零陷；sigma_e²=0 时精确退化为历史 RZF。",
        (("W<sub>rRZF</sub>", "鲁棒 RZF 的未归一预编码方向矩阵。"),
         ("Ĥ", "用于求权的多流等效估计信道。"),
         ("λ", "Gram 矩阵的总对角加载。"),
         ("N<sub>s</sub>", "同时发送的总数据流数。"),
         ("σ<sub>n</sub>²", "各用户/流平均等效噪声功率。"),
         ("P", "当前频率点的总发射功率。"),
         ("N<sub>BS</sub>σ<sub>e</sub>²", "i.i.d. 每系数误差在端口维累积后的不确定性加载。"),
         ("I", "与流 Gram 同维的单位阵。")),
    ),
    "F_CAL_COUPLING": FormulaSpec(
        "服务小区耦合损耗的绝对功率口径",
        "耦合损耗等于发射与接收功率之差；把链路预算展开后，就是传播路损减去收发天线增益。它同时检验路损、方向图、下倾和小区选择。",
        (("CL", "Coupling Loss，服务小区耦合损耗，单位 dB。"),
         ("P<sub>tx</sub> / P<sub>rx</sub>", "同一链路参考面上的发射 / 接收功率，单位 dBm。"),
         ("PL", "传播路径损耗，单位 dB。"),
         ("G<sub>tx</sub> / G<sub>rx</sub>", "发射 / 接收天线在该链路方向上的增益，单位 dB。"),
         ("dB/dBm", "两个 dBm 相减得到 dB；右侧各项也按链路预算的 dB 加减。")),
    ),
    "F_CAL_ANGLE": FormulaSpec(
        "38.901 校准使用的功率加权圆周角扩展",
        "每条路径角度先变成单位圆上的复矢量并按路径功率相加。合矢量越接近 1，方向越集中；跨 0/360 度的两条近邻路径不会被普通线性标准差错误拉开。",
        (("AS", "角度扩展，输出为弧度，随后可转成度。"),
         ("φ<sub>n</sub>", "第 n 条路径的方位角或天顶角，使用弧度。"),
         ("P<sub>n</sub>", "第 n 条路径的线性功率权重。"),
         ("e<sup>jφn</sup>", "把角度映射到单位圆上的复矢量。"),
         ("|·|", "功率加权平均方向矢量的模，范围 0..1。"),
         ("ln", "自然对数；合矢量模接近 1 时 AS 接近 0。")),
    ),
    "F_CAL_SINGULAR": FormulaSpec(
        "PRB 空间奇异值校准的功率尺度",
        "每个 RB 在 t=0 形成接收侧 Gram R=H^H H，其特征值等于奇异值平方。第一/第二特征值用 10log10 转 dB，二者 dB 差等价于线性比值的 10log10。",
        (("H<sub>r</sub>", "RB r、t=0 的复 MIMO 信道矩阵。"),
         ("R<sub>r</sub>", "该 RB 的接收侧 Gram/平均协方差矩阵。"),
         ("λ<sub>r,1</sub> / λ<sub>r,2</sub>", "最大 / 第二大 Gram 特征值，即前两奇异值的平方。"),
         ("Δλ<sub>r</sub>", "两个空间模的 dB 间隔，用于 CDF 校准。"),
         ("10log<sub>10</sub>", "功率/特征值比转换到 dB；不能对 λ 再使用 20log10。"),
         ("r", "PRB/RB 频率索引；实现对每个频点独立出样本。")),
    ),
})


FORMULA_SPECS.update({
    "F_SRS_RESOURCE_COLLISION": FormulaSpec(
        "两个2T4R周期资源何时真正相撞",
        "每个UE有leg0/leg1两条2-port资源。只要存在一对leg在周期时刻、symbol、comb、17档频域相位和2-CS块上全部重合，就记为基础导频碰撞；不能只检查第一腿。",
        (("a / b", "来自两个 UE（可跨小区）的周期 SRS 资源。"),
         ("i / j", "UE a/b的资源腿索引0或1，分别对应天线端口组0/1与2/3。"),
         ("s", "该leg所在OFDM symbol；BBL保留格不会出现在普通候选中。"),
         ("c", "comb offset 0或1。"),
         ("f", "17个frequency_resource_id之一；不同值表示当前机会使用不同RBG。"),
         ("𝒞", "该leg占用的2-CS集合，只允许(0,1)或(2,3)。"),
         ("o", "该leg在一个SRS周期内的slot offset。"),
         ("T", "SRS 周期，按 30 kHz slot 数表达。"),
         ("gcd", "两个周期的最大公约数；offset 差能被它整除时，两个周期机会最终重合。"),
         ("1[·]", "条件成立为 1，否则为 0 的指示函数。")),
    ),
    "F_SRS_TOY_CONTAMINATION": FormulaSpec(
        "同资源 SRS 碰撞为什么会直接污染 LS 信道方向",
        "基站收到的是服务 UE 与碰撞 UE 的复信道矢量之和。用服务 UE 的已知导频做 LS 解扩后，若两份导频不可分离，干扰信道不会消失，而是作为一个有方向的向量留在估计中；不同 symbol/comb 时该干扰项根本不会进入本次观测。",
        (("h<sub>A</sub>", "服务 UE A 到本基站的真实上行空间信道；toy example 中只有 2 个接收维度。"),
         ("h<sub>B</sub>", "相邻小区 UE B 到本基站的干扰信道；它携带自己的空间方向。"),
         ("y<sub>A</sub>", "基站在分给 UE A 的 SRS 资源上收到的复向量。"),
         ("x<sub>A</sub> / x<sub>B</sub>", "A/B 在该资源上发送的已知复 SRS 符号；完全同码时二者均可取 1。"),
         ("x<sup>*</sup>", "复共轭；标量 LS 用它对接收信号解扩。"),
         ("n", "热噪声与尚未显式展开的接收误差。"),
         ("ĥ<sub>A</sub>", "基站最终拿去求预编码的 UE A 信道估计；碰撞时它不是 h_A，而是混入 h_B。")),
    ),
    "F_SRS_TOY_BEAM": FormulaSpec(
        "被污染的信道估计如何让基站高估 BF Gain",
        "rank-1 SVD/EBF 的方向就是归一化信道估计。基站在 ĥ 上比较该方向与 PMI 参照权，得到 estimated BF Gain；真正下行却经过 h，因此真实增益可能更小，甚至与估计值符号相反。",
        (("w<sub>A</sub>", "用被污染估计设计的 UE A 单流单位范数发射波束。"),
         ("w<sub>PMI</sub>", "宽带 Type-I/PMI 参照权；toy example 为便于手算取 [1,0]ᵀ。"),
         ("‖·‖<sub>2</sub>", "欧氏范数，用于把 rank-1 波束归一到单位总功率。"),
         ("(·)<sup>H</sup>", "共轭转置；内积的模平方给出沿该波束的接收功率。"),
         ("ΔG<sub>est</sub>", "基站在估计信道上算到的 SVD 相对 PMI 增益，进入发送 MCS 预测。"),
         ("ΔG<sub>true</sub>", "同一发射权在真实信道上的实际相对增益；它决定真实接收 SINR。")),
    ),
    "F_SRS_TOY_LINK_ADAPT": FormulaSpec(
        "发送 MCS 看估计，误块判断看真实接收 SINR",
        "当前系统先把 CQI 映射回一个目标 BLER 门限，加上基站侧 estimated BF Gain 后重查 MCS，再在 MCS 编号域叠加用户 OLLA。ACK/NACK 不使用这条预测 SINR，而是用真实信道、实际发射权得到的 receive SINR 查询该 MCS 的预置 BLER 曲线。",
        (("θ<sub>CQI</sub>", "CQI 先映射到内部 MCS，再从该 MCS 的目标 BLER 曲线反查得到的 SINR 门限，单位 dB。"),
         ("f<sub>MCS</sub>", "把 SINR 按当前预置 MCS/BLER 表反查为基准 MCS 的离散映射。"),
         ("m<sub>base</sub>", "叠加 BF Gain 后、尚未应用 OLLA 的基准 MCS 编号。"),
         ("Δm<sub>OLLA</sub>", "当前实现中用户级 OLLA 的 MCS 编号偏移；ACK 小步上调，NACK 大步下调。"),
         ("m<sub>TX</sub>", "最终实际发送 MCS，限制在当前预置表的 0..27。"),
         ("γ<sub>RX</sub>", "在真实下行信道和实际发射权上计算的码字级有效接收 SINR。"),
         ("P<sub>NACK</sub>", "该 TB 首传失败的概率；由 m_TX 与 γ_RX 查预置 NewTx BLER 曲线。")),
    ),
    "F_PCI_MOD3": FormulaSpec(
        "PCI 模3把物理小区标识压缩成三种硬隔离颜色",
        "先对NR物理小区标识取余得到0/1/2。当前工程预置只允许本色普通H叶子；资源不足时全局延长周期，不借用另外两类颜色。这是项目干扰协调策略，不是3GPP强制资源表。",
        (("N<sub>ID</sub><sup>cell</sup>", "NR Physical Cell ID；标准定义的物理小区标识。"),
         ("g<sub>c</sub>", "小区 c 的 PCI 模3结果，也就是首选资源颜色 0/1/2。"),
         ("mod 3", "除以 3 取余；例如 PCI 100、101、102 分别得到 1、2、0。"),
         ("𝒜<sub>c</sub>", "该小区允许的颜色集合；当前严格等于只含g_c的单元素集合。"),
         ("c", "被配置 SRS 资源的服务小区。")),
    ),
    "F_SRS_POOL_CAPACITY": FormulaSpec(
        "2T4R双腿SRS资源池的每色UE容量",
        "一个候选已经绑定当前/下一SRS机会两条leg，因此容量只乘互不重叠的机会pair、本色普通叶子、17个频域相位和4CS中可切出的2-CS块。10/20/40 ms分别有1/2/4个pair，容量为68/136/272。",
        (("N<sub>UE,c</sub>(T)", "PCI模3颜色c在全局周期T下可容纳的2T4R UE数。"),
         ("N<sub>pair</sub>(T)", "周期内互不重叠的相邻SRS机会pair数；10/20/40 ms为1/2/4。"),
         ("N<sub>leaf,c</sub>", "每个SRS机会属于颜色c的普通symbol+comb叶子数，固定为2；BBL已剔除。"),
         ("N<sub>FDM</sub>", "可同时区分的16-RB频域资源相位数，固定为17。"),
         ("N<sub>CS</sub>", "工程基线开放的循环移位总数，固定为4；标准允许更多不等于项目必须启用。"),
         ("N<sub>Tx/occasion</sub>", "2T4R终端一次SRS实际发送的端口/占用CS数，固定为2。"),
         ("floor", "向下取整；4/2=2个不重叠CS块，可让两个UE共享同一时频/RBG叶子。")),
    ),
    "F_RESOURCE_LEDGER": FormulaSpec(
        "物理 PRB 与逻辑 layer-PRB 是两本不同的账",
        "MU 的两个用户共享同一组物理 RBG，因此物理 PRB 只扣一次；基带却要同时处理两人的所有层，所以逻辑资源按总层数乘 PRB。两本账任一超预算，grant 都不能提交。",
        (("g", "本 TTI 中的一个 SU 或 MU grant。"),
         ("𝓡_g", "grant g 占用的 RBG bitmap。"),
         ("N_PRB,r", "RBG r 实际包含的 PRB 数；当前固定为 16。"),
         ("u∈g", "共享该 grant 的用户；SU 一个、当前 MU 两个。"),
         ("L_u", "用户 u 在该 grant 上的 rank/层数。"),
         ("P_phys", "小区物理频域占用，MU 共享资源只计一次。"),
         ("P_logical", "基带空间处理工作量，按 layer×PRB 累加。")),
    ),
    "F_FREQUENCY_OBJECTIVE": FormulaSpec(
        "频选在可用 RBG 中最大化真实可服务字节",
        "候选 RBG 先按 gNB 可见的逐 RBG predicted SINR 排序，再逐个前缀重算一个码字的有效 SINR、MCS 和量化 TBS。若某个前缀能发完队列，取最小 RBG 数；否则取 useful bytes 最大者。顺序分配前缀也同时参与，形成不劣于旧基线的安全网。",
        (("𝒜", "当前 ResourceLedger 中仍可用的 RBG 集合。"),
         ("𝓡", "被评估的一个 RBG 子集。"),
         ("Q_u", "用户 u 在本 TTI 决策时的队列字节数。"),
         ("γ_u,r^dB", "gNB 对用户 u 在 RBG r 的 predicted SINR。"),
         ("γ̄_u", "当前已确认口径：grant 内 RBG 和 rank stream 的 dB 算术平均。"),
         ("L_u", "该用户的发送 rank。"),
         ("TBS", "按最终 MCS、rank 和真实 PRB 数经过 38.214 量化后的字节数。")),
    ),
    "F_MU_CANDIDATE_SCORE": FormulaSpec(
        "PF anchor 固定后，用 useful bytes/RBG 选择 MU 伙伴",
        "PF 已经决定 anchor，不在伙伴评分中被替换。对每个通过相关性、层数和预测 BLER 门的伙伴，使用 CorrLoss、均分功率损失与 SU+MU OLLA 重算两份 TB，再用队列封顶的总有用字节除以共享物理 RBG 数。",
        (("u", "当前 PF 排序选出的 anchor UE。"),
         ("v", "被枚举的一个伙伴 UE。"),
         ("𝒱_u^feasible", "通过 pair link、相关性、层数和 predicted BLER 门的伙伴集合。"),
         ("Q_u/Q_v", "两位用户当前真实队列字节数。"),
         ("TBS_u/TBS_v", "同一共享 bitmap 上分别计算的两个单码字 TB 大小。"),
         ("𝓡_u,v", "该 MU grant 共享的物理 RBG bitmap。"),
         ("S(u,v)", "候选对每个物理 RBG 可交付的 queue-limited useful bytes。")),
    ),
})


# Code-to-manual coverage audit, 2026-08-14: formulas for five capabilities that
# previously appeared only in the API inventory or as short boundary notes.
FORMULA_SPECS.update({
    "F_PROFILE_SCORE": FormulaSpec(
        "自然语言意图到有限任务画像的确定性评分",
        "决策引擎不让 LLM 自由发明任务类型，而是在预定义 TaskProfile 集合中统计关键词命中数，选择命中最多的一项；全部为零时回到 generic。它是可复现的路由启发式，不是语义模型。",
        (("p<sup>★</sup>", "最终选中的任务画像 TaskProfile。"),
         ("𝓟", "代码中预定义的全部任务画像集合。"),
         ("𝓚<sub>p</sub>", "画像 p 配置的关键词集合。"),
         ("intent", "用户给出的自然语言仿真意图。"),
         ("1[·]", "条件成立取 1、否则取 0 的指示函数。"),
         ("lower", "大小写归一化；当前算法仍是关键词包含匹配。")),
    ),
    "F_CONFIG_PRECEDENCE": FormulaSpec(
        "配置解析的右侧覆盖优先级",
        "默认值只补空项，preset 提供场景骨架，任务画像给高影响提示，用户显式 override 最后生效。最终落盘的是 resolved config；若页面显示值与该结果不同就是合同漂移。",
        (("C<sub>resolved</sub>", "真正交给 first-party source/系统仿真的解析后配置。"),
         ("C<sub>default</sub>", "决策表和本地硬件提供的缺省值。"),
         ("C<sub>preset</sub>", "信道或系统场景预设中的配置骨架。"),
         ("C<sub>task</sub>", "任务画像为该类问题补充的 config_hints。"),
         ("C<sub>user</sub>", "用户在 plan/revise 或说明书页面显式修改的键。"),
         ("▷", "字典合并运算；右侧出现的同名键覆盖左侧。")),
    ),
    "F_REQUIRED_SLOTS": FormulaSpec(
        "由结论模板反推下一轮真正需要问的问题",
        "决策引擎维护一组发布结论必需的槽位，只追问尚未回答的部分。样本数不在这里让用户拍脑袋，而是在试点后由差值方差和期望效应计算。",
        (("𝓠", "本轮仍需要询问的结论槽位集合。"),
         ("𝓢<sub>required</sub>", "发布一条可解释结论必须具备的槽位。"),
         ("𝓢<sub>answered</sub>", "用户已经明确回答或配置已可靠提供的槽位。"),
         ("baseline", "被测方法的对照基线。"),
         ("metric/effect", "主指标及希望检出的最小效应。"),
         ("csi/scenario/scope", "CSI 公平口径、仿真场景和结论适用范围。")),
    ),
    "F_PREREG_DIGEST": FormulaSpec(
        "预注册口径的规范化内容摘要",
        "实现把九个分析身份字段按稳定键序及固定分隔符序列化，再计算 SHA-256；prereg_id 与 created_at 不进入摘要。文件被原地修改后摘要校验失败，改口径应创建新 prereg，而不是覆盖旧文件。",
        (("Θ<sub>pr</sub>", "进入摘要的完整 payload；不含随机 prereg_id 和创建时间。"),
         ("d<sub>pr</sub>", "预注册内容的 SHA-256 摘要。"),
         ("JSON<sub>canonical</sub>", "按键排序、固定分隔符的确定性 JSON 表示。"),
         ("draft", "可选的来源 Draft 标识。"),
         ("metric/unit/direction", "预注册主指标、单位和 higher_is_better 方向。"),
         ("baseline", "预先承诺的比较基线。"),
         ("CSI", "两臂应使用的 CSI 角色，例如 estimated。"),
         ("effect", "希望检出的期望效应；可为空。"),
         ("secondary/note", "排序后的次要指标列表与分析备注。")),
    ),
    "F_RESULT_CONTRACT": FormulaSpec(
        "外部算法做配对比较前的四项硬相等合同",
        "两臂必须来自同一数据内容、长度相同、逐位置样本 ID 相同，且指标名与单位一致。任何一项失败都阻断统计；仅有相同长度不能发现顺序错位。",
        (("d<sub>D</sub><sup>A/B</sup>", "A/B 结果绑定的 NPZ + 物理语义 summary 摘要。"),
         ("n<sub>A/B</sub>", "两臂逐样本结果数量。"),
         ("id<sub>i</sub><sup>A/B</sup>", "第 i 个结果对应的数据集样本标识。"),
         ("m", "指标名称，例如 spectral_efficiency。"),
         ("u", "指标单位，例如 bit/s/Hz。"),
         ("∀i", "要求每一个位置都相等，而不是只比较 ID 集合。")),
    ),
    "F_PREREG_CLASS": FormulaSpec(
        "结果指标的预注册身份分类",
        "只有与预注册主指标完全一致的量才能承载主结论；事先列出的次要指标可作为支持证据，其余只能标探索性。没有 prereg 时另报 unregistered，不能默认 primary。",
        (("class(m)", "当前报告指标 m 的证据身份。"),
         ("m<sub>primary</sub>", "生成数据前锁定的唯一主指标。"),
         ("𝓜<sub>secondary</sub>", "生成前登记的次要指标集合。"),
         ("primary", "可以进入预注册主结论。"),
         ("secondary", "可报告，但不能替代主指标判决。"),
         ("exploratory", "看过数据后才选择或未登记的探索性指标。")),
    ),
    "F_PROBE_SNR": FormulaSpec(
        "缩窄 RB 探测后还原全载波每 RB SNR",
        "总载波功率均匀分给更少 RB 时，探测口径的每 RB PSD 人为升高。减去 RB 数比对应的 dB 才回到正式载波口径。first-party 值不截断；历史边界值才按不可逆 clipped 处理。",
        (("SNR<sub>full,dB</sub>", "正式全带宽配置下的每 RB SNR。"),
         ("SNR<sub>probe,dB</sub>", "缩窄 RB 探测运行直接返回的 SNR。"),
         ("N<sub>RB,full</sub>", "正式载波 RB 数。"),
         ("N<sub>RB,probe</sub>", "探测载波 RB 数，当前最多取 24。"),
         ("10log<sub>10</sub>", "功率分摊比例转 dB。"),
         ("夹逼", "历史值被限幅后无法通过减常数恢复真实值；新数据不采用。")),
    ),
    "F_SINR_COMBINE": FormulaSpec(
        "同一信号参考面上的 SNR 与 SIR 合成 SINR",
        "SNR 的倒数给噪声相对信号功率，SIR 的倒数给干扰相对信号功率，两者相加得到 SINR 的倒数。实现先在线性域合成，再转回 dB；直接把两个 dB 数相加是错误的。",
        (("γ<sub>SINR</sub>", "线性信干噪比。"),
         ("γ<sub>SNR</sub>", "同一信号参考面上的线性信噪比。"),
         ("γ<sub>SIR</sub>", "同一信号参考面上的线性信干比。"),
         ("SNR<sub>dB</sub>/SIR<sub>dB</sub>", "对应比值的 dB 表示。"),
         ("−10log<sub>10</sub>", "从倒数功率和转换回 dB SINR。")),
    ),
    "F_MAX_DOPPLER": FormulaSpec(
        "终端速度到逐径 Doppler 的两级映射",
        "速度模长和波长先确定最大 Doppler，每条路径再按运动方向与路径方向夹角投影。不能先只取最近站径向速度、再在路径模型中投影一次，那会重复压低 Doppler。",
        (("f<sub>D,max</sub>", "给定终端速度与载频下的最大 Doppler。"),
         ("v", "终端三维速度向量，公式取其模长。"),
         ("λ", "载波波长。"),
         ("ν<sub>ℓ</sub>", "第 ℓ 条传播路径的实际 Doppler。"),
         ("ψ<sub>ℓ</sub>", "速度方向与该路径传播方向之间的夹角。")),
    ),
    "F_SEQUENCE_CORR": FormulaSpec(
        "参考序列的归一化周期相关",
        "把序列 b 循环移位 k 后与 a 做复内积并按长度归一。自相关希望零移位峰值高、旁瓣低；不同端口/小区的互相关越大，导频污染越容易投影进信道估计。",
        (("R<sub>ab</sub>[k]", "序列 a、b 在循环移位 k 下的相关幅度。"),
         ("a<sub>n</sub><sup>*</sup>", "序列 a 第 n 项的复共轭。"),
         ("b<sub>(n+k) mod N</sub>", "循环移位后的序列 b。"),
         ("N", "比较使用的共同序列长度。"),
         ("k", "循环移位索引。"),
         ("|·|", "实现返回相关复数的幅度。")),
    ),
    "F_BEAM_SELECT": FormulaSpec(
        "CSI-RS DFT 候选中的最大接收功率波束",
        "每个候选列 w_i 作用到当前信道，比较接收端总功率并返回最大者索引。这个 DFT 波束扫描集合形状为 beam×port，与 PMI Type-I-style 的 port×column 候选和多层搜索不是同一对象。",
        (("i<sup>★</sup>", "被选中的 CSI-RS 波束索引。"),
         ("H", "用于波束测量的复 MIMO 信道。"),
         ("w<sub>i</sub>", "第 i 个单位范数 DFT 发送波束。"),
         ("𝓦<sub>CSI-RS DFT</sub>", "CSI-RS 扫描候选波束集合。"),
         ("‖·‖<sub>F</sub>²", "跨接收端口的总接收功率。")),
    ),
    "F_TDD_FRACTION": FormulaSpec(
        "普通下行时隙与特殊时隙共同形成可用下行比例",
        "D slot 全部计入，U slot 不计，S slot 只按其中下行 OFDM symbol 的比例计入。页面和系统仿真必须从同一 pattern/special-slot 配置导出，不能长期写死 0.7。",
        (("ρ<sub>DL</sub>", "一个 TDD pattern 周期内的下行资源比例。"),
         ("N<sub>D</sub>/N<sub>S</sub>/N<sub>U</sub>", "周期内普通下行/特殊/普通上行时隙数。"),
         ("f<sub>S</sub>", "一个特殊时隙中可用于下行的 symbol 比例。"),
         ("N<sub>DL,sym</sub>", "特殊时隙里的下行 OFDM symbol 数。"),
         ("N<sub>sym/slot</sub>", "每时隙总 OFDM symbol 数，常规 CP 通常为 14。")),
    ),
    "F_QAM_MI": FormulaSpec(
        "有限 QAM 星座的对称互信息",
        "对每个等概率发送点和复高斯噪声求期望，得到调制受限容量。它在低 SNR 处接近 Shannon，高 SNR 饱和到 log2(M)；代码用 Gauss-Hermite 求积生成缓存表。",
        (("I<sub>M</sub>(γ)", "M-QAM 在 SNR γ 下的互信息，单位 bit/复符号。"),
         ("M", "星座点数，如 4/16/64/256。"),
         ("x<sub>m</sub>", "单位平均能量归一后的第 m 个星座点。"),
         ("L<sub>m</sub>(n)", "发送 x<sub>m</sub>、噪声为 n 时的对数似然竞争项。"),
         ("Δ<sub>mm′</sub>(n)", "候选点 x<sub>m′</sub> 相对真实点 x<sub>m</sub> 的噪声距离增量。"),
         ("n", "复高斯噪声样本。"),
         ("σ²", "代码约定下的等效噪声方差，等于 1/γ。"),
         ("E<sub>n</sub>", "对噪声分布求期望。")),
    ),
    "F_MIESM": FormulaSpec(
        "互信息有效 SINR映射",
        "逐 RE/RB SINR 先映射到同一调制阶数下的互信息，取平均后再反解 AWGN 等效 SINR。深衰 RE 的损失不会被高 SINR RE 按线性功率完全补回。",
        (("γ<sub>eff</sub>", "整块编码传输的等效 AWGN SINR。"),
         ("I<sub>M</sub>", "M-QAM 互信息映射。"),
         ("I<sub>M</sub><sup>−1</sup>", "从平均互信息反解到 SINR。"),
         ("γ<sub>n</sub>", "第 n 个 RE/RB 的局部 SINR。"),
         ("N", "参与同一码块映射的局部 SINR 数量。")),
    ),
    "F_EESM": FormulaSpec(
        "指数有效 SINR映射及 beta 标定项",
        "EESM 通过指数平均强化低 SINR 样本的影响。beta 决定映射曲率，必须按 MCS/链路曲线标定；当前库的按调制默认值只是参考，不能冒充已完成链路级标定。",
        (("γ<sub>eff</sub>", "EESM 输出的线性等效 SINR。"),
         ("γ<sub>n</sub>", "第 n 个局部线性 SINR。"),
         ("β", "需要链路级标定的 EESM 缩放参数。"),
         ("N", "局部 SINR 样本数量。"),
         ("ln/exp", "自然对数与指数；公式在线性 SINR 域计算。")),
    ),
    "F_TB_BLER": FormulaSpec(
        "多个码块合成一个传输块错误率",
        "若一个 TB 被分成 C 个近似独立码块，只有全部码块正确时 TB 才成功，因此 TB BLER 是一减去全部成功概率。这一式只属于 SuperRAN 的表 1/2 分析后端；预置表口径直接把一调度 TTI 的 TB 作为误块事件，不再从 CBLER 合成。",
        (("P<sub>TB</sub>", "整个 transport block 的错误概率。"),
         ("P<sub>CB</sub>", "单个 code block 的错误概率。"),
         ("C", "该 TB 按 38.212 分段后的码块数。"),
         ("1−P<sub>CB</sub>", "一个码块正确的概率。"),
         ("独立近似", "把各码块成功事件相乘所采用的分析模型假设。")),
    ),
    "F_MCS_PROFILE": FormulaSpec(
        "预置 MCS 表怎样得到码率与名义谱效",
        "每档 MCS 给出调制阶数 Qm 和以 1024 为分母的码率索引 rm。真实码率是 rm/1024，名义谱效是每个调制符号携带的比特数乘码率。预置 Table 3 的 rm 可以是小数，因为它直接由十进制码率换算；不要误当成 38.214 标准表中的整数索引。",
        (("m", "MCS index，当前预置 profile 为 0..27。"),
         ("r<sub>m</sub>", "以 1024 为分母表示的码率数值，即 1024×R_m。"),
         ("R<sub>m</sub>", "MCS m 的编码率，范围 (0,1]。"),
         ("Q<sub>m</sub>", "调制阶数对应的每符号比特数：QPSK/16QAM/64QAM/256QAM 分别为 2/4/6/8。"),
         ("η<sub>m</sub>", "MCS m 的名义谱效，单位 bit/RE；未乘 rank，也未计 TBS 量化。")),
    ),
    "F_CODEWORD_SINR": FormulaSpec(
        "逐 RB、逐流 SINR 压成单码字有效 SINR",
        "SuperRAN 当前预置表口径先在每个 RBG 内对 RB 的线性 SINR逐流平均，再转 dB；随后对选定 rank 的所有 stream 与实际 grant 的所有 RBG 做 dB 算术平均。这个透明基线不是已标定的 EESM/MIESM。",
        (("γ<sub>b,s</sub>", "RB b、stream s 的线性 SINR。"),
         ("B<sub>g</sub>", "第 g 个 RBG 所包含的 RB 索引集合；固定系统中每组 16 RB。"),
         ("γ<sub>g,s</sub><sup>dB</sup>", "RBG g、stream s 在 RBG 内线性平均后转成的 dB SINR。"),
         ("N<sub>G</sub>", "本 TB 实际获配的 RBG 数，不一定是全带 17。"),
         ("N<sub>s</sub>", "选定 rank 的 stream 数；不是 rank1/2/3/4 四个候选之间求平均。"),
         ("γ<sub>cw</sub><sup>dB</sup>", "最终用于预置 BLER 曲线查询的唯一单码字有效 SINR。")),
    ),
    "F_MCS_SELECT": FormulaSpec(
        "按目标初传 BLER 选择最高可用 MCS",
        "对同一个码字有效 SINR依次检查 28 条 NewTx 曲线，选择 BLER 不超过目标值的最高 MCS。若集合为空，代码返回 MCS0 并由 outage/实际高 BLER 暴露不可发送状态，不虚构 MCS−1。",
        (("m*", "最终选择的空口 MCS index。"),
         ("f<sub>m</sub>(·)", "预置 profile 中 MCS m 的 NewTx SINR→BLER 查表函数。"),
         ("γ<sub>cw</sub><sup>dB</sup>", "该用户、该 grant、该选定 rank 的单码字有效 SINR。"),
         ("p<sub>target</sub>", "初传目标 BLER，默认 0.1；必须位于 (0,1)。"),
         ("max", "满足约束的档位中取 index 最大者；不能按谱效除法猜档。")),
    ),
    "F_PRESET_TTI_BLER": FormulaSpec(
        "预置表口径：一次调度 TTI 的 TB 就是一次 BLER 事件",
        "每个用户在一个 TTI 内的 grant 视为一个独立单码字 TB。初传只用该 MCS 的通用 NewTx 曲线和码字级有效 SINR 查一次 BLER，再用独立随机数形成 ACK/NACK；预置表路径不额外观察 CB 错误。",
        (("p<sub>t</sub><sup>NewTx</sup>", "第 t 个已调度 TTI 中该用户初传 TB 的误块概率。"),
         ("f<sub>m</sub>(·)", "预置 profile 中 MCS m 的 NewTx SINR→BLER 曲线。"),
         ("γ<sub>t</sub>", "跨 RBG、跨 rank stream 做 dB 算术平均后的单码字有效 SINR。"),
         ("m<sub>t</sub>", "本 TTI 选定的 MCS index。"),
         ("t", "系统仿真的 TTI 索引。"),
         ("U<sub>t</sub>", "0..1 均匀随机数；大于 BLER 时判 ACK。")),
    ),
    "F_HARQ_CC": FormulaSpec(
        "追逐合并：同档曲线上的 3.0103 dB 收益",
        "两次等功率重复发送的系统级近似把线性 SINR 相加，因此 dB 域增加 10log10(2)。重传空口仍保持初传 MCS、RBG 数、rank 与 TBS。",
        (("p<sub>t</sub><sup>CC</sup>", "唯一一次 CC 重传的 TB-BLER。"),
         ("f<sub>m_t</sub>(·)", "初传 MCS 对应的预置 NewTx 曲线；不读取原始 ReTx 行。"),
         ("γ<sub>t</sub>", "该重传 grant 的单码字有效 SINR，单位 dB。"),
         ("10log<sub>10</sub>2", "两次等功率重复观测合并的精确 dB 增益，约 3.0103 dB。"),
         ("m<sub>t</sub>", "冻结不变的初传 MCS index。")),
    ),
    "F_HARQ_IR": FormulaSpec(
        "增量冗余：半谱效等效 MCS，只改 BLER 查表档位",
        "初传和唯一一次重传共同承载一份业务数据，工程上把所需谱效减半，再向下查到不超过该谱效的最高 MCS。这个等效 MCS 只用于 BLER lookup，空口仍发送初传 MCS 和相同 RBG 数。",
        (("η<sub>m_t</sub>", "初传 MCS 在预置表中的名义谱效。"),
         ("η<sub>eq</sub>", "两次传输共同解一份数据后的半谱效目标。"),
         ("m<sub>eq</sub>", "谱效不超过 η_eq 的最高 MCS；只用于查 BLER。"),
         ("j", "遍历预置 MCS 表 0..27 的候选索引。"),
         ("p<sub>t</sub><sup>IR</sup>", "唯一一次 IR 重传在等效低档 NewTx 曲线上的 BLER。"),
         ("γ<sub>t</sub>", "重传 grant 的单码字有效 SINR，IR 抽象中不额外抬升。"),
         ("P<sub>res</sub>", "初传和唯一重传都失败的残留误块概率。")),
    ),
    "F_LOG_BLER_INTERP": FormulaSpec(
        "表驱动 BLER 曲线的对数域线性插值",
        "在相邻 SINR 网格点之间对 log10(BLER) 做线性插值，能保留瀑布区的数量级变化。低于实测范围钳到 1，高于范围钳到最后一个实测 BLER，不外推一条虚构尾部。",
        (("p(x)", "查询 SINR x 对应的插值 BLER。"),
         ("p<sub>i</sub>/p<sub>i+1</sub>", "包围 x 的两个原始 BLER 点。"),
         ("x<sub>i</sub>/x<sub>i+1</sub>", "对应的原始 SINR 网格点。"),
         ("α", "x 在两个网格点之间的线性位置，范围 0..1。"),
         ("log<sub>10</sub>", "对 BLER 数量级插值，而不是直接插值线性概率。")),
    ),
})


FORMULA_SPECS.update({
    "F_TYPE1_COLUMN": FormulaSpec(
        "Type-I-style 双极化过采样 DFT 列",
        "水平与垂直阵列分别生成过采样 DFT 向量，做 Kronecker 积得到一个二维空间方向；两个极化块再用四种 QPSK 共相因子拼接。每一列单位范数，只表示候选方向，不自带流功率。",
        (("a<sub>H</sub>(k) / a<sub>V</sub>(k)", "水平 / 垂直阵列第 k 个过采样 DFT 方向向量。"),
         ("N<sub>H</sub> / N<sub>V</sub>", "水平 / 垂直逻辑 RF 端口数；64T 为 8/4，256T 为 16/8。"),
         ("O<sub>H</sub> / O<sub>V</sub>", "水平 / 垂直过采样倍数，当前默认都是 4。"),
         ("n / k", "阵列位置索引 / 过采样空间频率索引。"),
         ("⊗", "Kronecker 积，把两个一维方向组合成二维面阵方向。"),
         ("φ<sub>p</sub>", "两极化块之间的 QPSK 共相因子，取 1、j、−1、−j。"),
         ("w", "一个候选预编码列；协议 p/v/h 顺序随后映射到真实端口顺序。"),
         ("j", "虚数单位。")),
    ),
    "F_PMI_COVARIANCE": FormulaSpec(
        "PMI 搜索使用宽带发射协方差而不是平均复信道",
        "每个时间、RB 和 UE 接收端口都贡献一个发射端口方向样本。先形成 H Hᴴ 再平均可保留功率，并对整块信道的公共相位旋转不变；若先平均复 H，相反相位的快照会错误抵消。",
        (("R<sub>tx</sub>", "基站发射端口域的宽带协方差，shape 为 N_BS×N_BS。"),
         ("H<sub>t,k</sub>", "时间 t、RB k 上按 [BS port, UE port] 存储的复信道。"),
         ("T", "当前搜索窗口中的 channel snapshot 数。"),
         ("N<sub>RB</sub>", "参与宽带搜索的 RB 数。"),
         ("N<sub>UE</sub>", "UE 接收端口数；每一列都作为协方差样本。"),
         ("H", "共轭转置上标；H Hᴴ 把接收端口折叠到发射端口协方差。")),
    ),
    "F_PMI_GREEDY": FormulaSpec(
        "多层 PMI 工程近似的增量贪心选列",
        "第 ℓ 层从尚未使用的码本列中选宽带接收功率最大的方向，再用正交投影把该方向从残余协方差中扣掉。这样可避免重复选择同一列，但并不等价于联合枚举 38.214 的完整多层矩阵码本。",
        (("i<sub>ℓ</sub><sup>★</sup>", "第 ℓ 层选中的码本列索引。"),
         ("𝓘<sub>ℓ</sub>", "此前已经选中的索引集合。"),
         ("w<sub>i</sub>", "第 i 个单位范数 Type-I-style 候选列。"),
         ("R<sub>ℓ</sub>", "选择第 ℓ 层前的残余发射协方差；R₀=R_tx。"),
         ("I", "发射端口维单位阵。"),
         ("w wᴴ", "已选方向的一维正交投影矩阵。")),
    ),
    "F_PMI_REFERENCE": FormulaSpec(
        "PMI 参照权把 CQI 与额外 BF Gain 分开",
        "在基站可见的同一份陈旧 CSI 上比较实际发射权和 PMI 参照权，差值才是可加到 CQI 链的 BF Gain；终端反馈侧的 PMI-SINR则必须把报告中保持的 PMI 权作用到当前真实信道上。",
        (("G<sub>BF</sub>(s,r)", "snapshot s、rank r 下实际发射权相对 PMI 参照权的 dB SINR 增益。"),
         ("γ(H,W)", "把预编码 W 作用于信道 H 后，经相同功率约束与接收机得到的聚合 SINR。"),
         ("H<sub>prec,s</sub>", "基站在 s 时刻可见的估计/陈旧信道。"),
         ("H<sub>true,s</sub>", "s 时刻只用于物理评价的真实信道。"),
         ("W<sub>tx,s,r</sub>", "实际发送使用的 rank-r 权，例如逐 RBG SVD。"),
         ("W<sub>PMI,q(s),r</sub>", "最近已到达报告 q(s) 中的宽带 PMI 权前 r 列。"),
         ("γ<sub>PMI,true</sub>", "终端侧当前真值上测得、供 CQI 报告链使用的 PMI-SINR。")),
    ),
    "F_POWER_COMPOSITION": FormulaSpec(
        "频域功率倍率与空间预编码矩阵的正交组合",
        "先用 EBF/PEBF/NEBF 得到一个频点上的空间物理矩阵，再乘 RB 相对功率倍率的平方根。倍率 q 作用于功率，因此矩阵幅度乘 sqrt(q)；每天线功率上限也随该 RB 的功率预算同比缩放。",
        (("Q<sub>c,r</sub><sup>phys</sup>", "小区 c、RB r 最终进入空口的物理预编码矩阵。"),
         ("Q<sub>c,r</sub><sup>spatial</sup>", "已按 EBF/PEBF/NEBF 处理的空间方向与流功率矩阵。"),
         ("q<sub>c,r</sub>", "该小区该 RB 相对均匀 PSD 的线性功率倍率。"),
         ("m", "物理发射天线/RF 端口索引。"),
         ("Q(m,:)", "第 m 根天线跨所有数据流的行向量。"),
         ("‖·‖₂²", "行向量模平方和，即该 RB 上的每天线功率。")),
    ),
    "F_RB_POWER_CONSTRAINT": FormulaSpec(
        "逐小区 RB 功率自由度的边界与宽带守恒",
        "每个 RB 的相对倍率限制在 0.1 到 4，并且每个小区自己的 RB 倍率均值严格为 1。于是频谱形状可以改变，但均匀功率预算的宽带总和不变；不同小区可以使用不同 profile。",
        (("q<sub>c,r</sub>", "小区 c 在 RB r 的线性功率倍率。"),
         ("c / r", "小区索引 / RB 索引。"),
         ("N<sub>RB</sub>", "载波中参与功控的 RB 数，当前 100 MHz 基线为 272。"),
         ("0.1 / 4.0", "实现的逐 RB 最小 / 最大倍率硬边界。"),
         ("均值 1", "每个小区独立守恒，而不是全网所有小区合计守恒。")),
    ),
    "F_RB_AUTOBALANCE": FormulaSpec(
        "部分 RB 指定后其余 RB 的唯一等功率补偿值",
        "用户只指定集合 Ω 中的 RB 时，未指定 RB 统一取 q_bal，使整行倍率总和仍等于 N_RB。若补偿值越过 0.1..4，输入不可行并硬失败；实现不会偷偷修改用户已指定的值。",
        (("q<sub>c,bal</sub>", "小区 c 所有未指定 RB 共用的自动补偿倍率。"),
         ("Ω<sub>c</sub>", "小区 c 已由用户 override 指定的 RB 集合。"),
         ("|Ω<sub>c</sub>|", "已指定 RB 的数量。"),
         ("Σ<sub>r∈Ωc</sub>q<sub>c,r</sub>", "用户指定部分消耗的倍率总和。"),
         ("N<sub>RB</sub>−|Ω<sub>c</sub>|", "仍需自动平衡的 RB 数。")),
    ),
    "F_STREAM_POWER": FormulaSpec(
        "ZF/RZF 有效增益上的经典流间注水",
        "预编码列先只表达方向，再按每流有效增益与噪声决定 p。水位 μ 使全部非负功率之和等于总功率；弱到低于水位门限的流可被分到零功率。体验系统当前固定 equal，此式只描述 MU 链路库已具备的可选能力。",
        (("p<sub>ℓ</sub>", "第 ℓ 条数据流获得的线性发射功率。"),
         ("μ", "由总功率约束反解的注水水位。"),
         ("σ<sub>ℓ</sub>²", "第 ℓ 流的等效噪声功率。"),
         ("g<sub>ℓ</sub>", "该流在单位范数预编码方向上的有效信道功率增益。"),
         ("L", "同时发送的总流数。"),
         ("P", "当前频率点的全小区总发射功率。")),
    ),
})


DETAIL_SPECS.update({
    "srsallocation": DetailSpec(
        promise="从仿真需求者的视角回答四个问题：SRS 为什么需要资源分配、PCI 模3到底是什么、相邻小区在哪些物理维度错开、这套预置在什么负载和信道条件下会失效。实现类名只放在最后的开发者映射中。",
        principles=(
            "SRS 是基站观察上行空间信道的探针。若两个不可分离的 SRS 同时到达，LS/LMMSE 的输入已经是信道叠加；后续预编码不是“估计稍微有噪声”，而是方向本身被污染。SU 波束增益会下降，MU 的零陷尤其敏感，因此资源分配属于信道可信度模型，不只是 MAC 配置管理。",
            "PCI 模3只做<strong>三色分类</strong>。NR PCI 是标准定义的物理小区标识，取余数后得到 0/1/2；但“黄色对应哪些 symbol/comb”是当前工程预置。标准给了 period/offset、resourceMapping、transmissionComb、cyclicShift、freqHopping 与 sequenceId 等自由度，并没有强制所有实现采用这张三色表。",
            "每个SRS机会的8个symbol+comb格中，symbol11/comb1与symbol13/comb0是加粗BBL保留叶子，普通用户不得使用。剩余6格由PCI0/1/2各取2格；四个机会合计每色8个可见普通叶子。跨小区先靠时频颜色隔离，不依赖可能受TA/多径破坏的CS正交。",
            "工程基线只开放4个循环移位，不是标准允许的8个上限。2T4R终端每次最多发2个SRS端口，固定使用CS0/1或CS2/3，因此同一symbol/comb/RBG叶子可承载两个UE的当次2T发送。端口0/1与2/3分别在当前和下一可用SRS机会发送。",
            "PCI颜色是硬分区，不是偏好。一个小区只能使用自己的两个普通叶子；资源不足时不得占BBL或借其他颜色，而是把所有小区的全局周期从10 ms原子提升到20 ms，再到40 ms。",
            "全局周期容量由完整2T4R资源pair计算。10/20/40 ms分别有1/2/4组不重叠机会pair；每pair每色2叶子×17频域相位×2个2-CS块，得到68/136/272个UE。5 ms只能表达单次2-port空口机会，无法在一个逻辑周期内同时容纳slot7→17两腿，因此基础分配前门直接拒绝；档位0～4和80/160 ms本阶段也明确不实现。",
            "17个frequency_resource_id既是同一机会的FDM资源，也是17-hop起始相位。一个UE的两腿保持同一frequency id，先在同一RBG完成两个64×2估计，才推进到下一hop；10 ms时全带约170 ms，但空口上发生34次2-port发送。",
            "跨周期碰撞要检查两个UE的四种leg组合。只有周期时刻、symbol、comb、frequency id和2-CS集合全部重合才算碰撞；不同周期仍用gcd同余判断未来是否相遇。",
            "当前跨小区数值是 allocator-level 方向门：等功率碰撞者各贡献 1 单位 I/S，LS NMSE proxy=N/S+I/S。它能检验表是否把三小区首选资源错开，却没有根序列、TA、时延扩展、真实功率差、接收滤波和非理想 CS，不能拿来预测现场 NMSE。",
            "资源向后影响仿真有两条物理路径。两个leg的offset和frequency id分别形成端口组0/1、2/3的逐RBG CSI lag，系统从不同历史快照拼出64×4 h_prec；symbol/comb/CS/sequence则决定导频是否混入邻区H_iX_i。前一条已接通，后一条仍需资源表驱动导频生成器。",
            "一个 2 维 rank-1 toy 足以看清方向污染：h_A=[1,0]、h_B=[0.6,0.8] 且同码碰撞时，LS 得 ĥ_A=[1.6,0.8]，归一波束变为 [0.894,0.447]。在污染估计上它相对 PMI 显示 +0.97 dB BF Gain，在真实 h_A 上却是 −0.97 dB，估计乐观偏差 1.94 dB；打向 h_B 的功率由 0.36 升到 0.80。若资源错开，h_B 项从观测里消失，以上偏差都回到 0。",
            "MCS 与 BLER 必须保持两个视角。基站用 CQI 反查门限，加 estimated BF Gain 后重新映射 MCS，再在 MCS 编号域叠加用户 OLLA；误块事件则固定发送 MCS，用真实 receive SINR 查 NewTx 曲线。toy 中若干净链路 15 dB 可发 MCS16，污染波束把真实 SINR 降到 14.03 dB，预置曲线 BLER 从 4.92% 升到 99.78%，随后 NACK 才驱动 OLLA 回拉。不能用 14.03 dB 先重选 MCS再说污染没有 BLER 代价。",
            "当前实现有一条必须公开的桥接缺口：2T4R双腿assignment已进入端口组CSI lag；独立的paired/BOTH数据生成链路也会构造Y=H_sX_s+ΣH_iX_i+N并生成h_ul_est；但assignment的两腿/symbol/comb/CS/frequency id尚未自动驱动这条导频观测。因此资源时序对BF/KPI的影响可测，PCI模3抗污染的最终吞吐收益仍不能直接宣称。",
        ),
        implementation=(
            ("冻结表驱动真相源", "<code>SRS_LEAF_ROLE_BY_SYMBOL_COMB</code>逐格保存PCI0/1/2或BBL；profile v3固定4 CS、17 FDM与2T4R双腿。代码、测试和文档读取同一常量。"),
            ("建立2T4R请求", "请求中的4表示待探测逻辑天线端口；单次同时Tx固定为2。其他端口形态在该产品profile下硬拒绝，不再把4解释成4Tx同时发送。"),
            ("生成UE资源bundle", "先展开本色普通叶子，再展开17个frequency id和CS0/1、CS2/3；一个候选同时包含当前机会ports0/1与下一机会ports2/3两条leg。"),
            ("禁止跨颜色", "候选集合只包含本PCI模3颜色，BBL与其他两色根本不生成；<code>preference_tier</code>兼容字段恒为0。"),
            ("全局自适应周期", "整批UE先用10 ms在新allocator中试分配；失败结果全部丢弃并用20/40 ms重试。任一小区最先超容量就决定所有小区的统一周期。"),
            ("检查双腿碰撞", "任意leg组合按period gcd、symbol、comb、frequency id和CS交集检查；一个UE的两腿保持同一频域相位。"),
            ("进入分组CSI时间轴", "两个offset分别生成[2,RBG] lag；<code>stale_channel_by_antenna_group</code>从不同历史快照拷贝端口0/1与2/3，形成用于求权的64×4。"),
            ("导频污染独立链路", "paired/BOTH 数据源在 pilot grid 上形成 <code>Y_total=Y_serving+interference_total+noise</code>，再走 LS/频域 LMMSE 得到 <code>h_ul_est</code>；<code>ul_sir_dB</code> 由同一份服务/干扰导频功率计算。"),
            ("估计与真值分视角", "本地适配器把 <code>h_ul_est</code> 按版本化 TDD 互易合同变成 <code>h_prec</code>，权值和 estimated BF Gain 只看它；接收 SINR/BLER 使用 <code>h_dl_true</code>，源端任何 <code>w_dl</code> 都不参与。"),
            ("显式暴露未接桥", "当前不把 assignment 的 symbol/comb/CS 自动写进导频生成器；文档、summary 和实验解释都必须把 collision proxy、CSI 老化路径和导频污染路径分开。"),
            ("留存用户级证据", "每个UE返回两个leg、天线组、period、offset、symbol/comb、2-CS块、frequency id与profile；小区汇总返回选中的全局周期和4/2/17合同。"),
            ("运行正反例", "测试锁定BBL排除、每色8个可见叶子、68/136/272容量、68→69升周期、同叶两UE CS复用、双腿碰撞与64×2拼接来源。"),
        ),
        example_title="PCI 100/101/102 三个相邻小区怎样选出三份不同 SRS 资源",
        example=(
            "<p>三个小区PCI为100/101/102，颜色依次1/2/0。10 ms下第一个UE都绑定slot7→17：颜色1用symbol12/comb0，颜色2用symbol12/comb1，颜色0用symbol13/comb1；leg0发ports0/1，leg1发ports2/3，两腿均用frequency id0和CS0/1。三者时刻相同，但本色symbol/comb不同，因此不碰撞。</p>"
            "<p>若故意把三小区都伪装成颜色0，它们会拿到相同两腿、frequency id0和CS0/1，三对组合全部相撞；每个UE见两个等功率污染者，I/S=2、加N/S=0.01后proxy=2.01。恢复0/1/2硬分区后碰撞为0/3。资源不足时不会再跨色，而是全局把周期升到20/40 ms。</p>"
            "<p>同一小区第二个UE可在完全相同的slot pair、symbol/comb和frequency id上使用CS2/3；第三个UE再转到frequency id1。这个例子说明：<strong>PCI颜色保护跨小区时频隔离，4个CS只服务本小区两个2T UE复用</strong>。</p>"
            "<p>再把三小区例子压成两根接收天线：A 的真实信道 [1,0]，碰撞者 B 为 [0.6,0.8]。同码碰撞后 LS 把二者相加为 [1.6,0.8]，SVD 波束变成 [0.894,0.447]；基站在错误估计上算到 +0.97 dB BF Gain，真实 A 信道上却损失 −0.97 dB。若干净链路真实 SINR 为 15 dB，污染后为 14.03 dB；仍发送 MCS16 时，当前预置 NewTx BLER 从 4.92% 跳到 99.78%。这个数字把“资源碰撞”一直连到了 NACK，但它仍是人为选定信道方向的可手算机制例，不是 PCI 模3现场增益承诺。</p>"
        ),
        checks=(
            ("表逐格一致", "八格角色严格为0,1,2,BBL,1,2,BBL,0；每个机会每色2格，20 ms图每色8格。"),
            ("4CS与2T复用", "同一时频/RBG叶子的前两个UE必须分别使用CS0/1、CS2/3；每个UE两腿保持同一CS块。"),
            ("容量与全局升周期", "10/20/40 ms容量严格为68/136/272；第69个UE使所有小区从10升20，第137个升40，第273个硬失败。"),
            ("双腿周期数学", "碰撞检查枚举leg0/leg1并包含frequency id；20 ms offset27/37与10 ms 7/17的周期重合反例被捕获。"),
            ("时间轴生效", "链路表保存[S,2,RBG] lag，确定性快照逐值证明端口0/1和2/3来自各自因果历史。"),
            ("toy 数值可复算", "文档中 MCS16 的 15.00/14.03 dB BLER 直接从 <code>bler_curves</code> 当前预置曲线生成；BF Gain、真实损失和邻区泄漏由同一组 h_A/h_B 手算，不允许复制陈旧数字。"),
            ("桥接状态不越界", "正文必须同时说明“2T4R分组老化已接通”和“资源表尚未驱动导频波形生成器”。"),
            ("同小区正交", "任意已分配两UE的两条leg都不碰撞；重复请求幂等，release后资源确定性恢复。"),
            ("模3方向", "轻载三小区同色3/3碰撞、0/1/2硬分区0/3；不再存在spill tier。"),
            ("公开边界", "每份 assignment/summary 都写 P-H/F、BWP2、根序列、非理想正交和波形级污染未建模。"),
        ),
        pitfalls=(
            "把 PCI 模3写成3GPP强制资源表。标准定义资源字段，具体三色映射是工程预置。",
            "把两个加粗BBL叶子继续当成黄色/绿色普通资源，造成3/3/2的假容量。",
            "把38.211允许的8个CS上限当成项目配置；当前工程基线只开放4个。",
            "把2T4R写成4Tx同时发，遗漏两个64×2估计之间5 ms时间差。",
            "把每个 UE 都设为同一个 SRS 周期，就误以为资源已经分开。周期相同不代表 offset/symbol/comb/CS 正交。",
            "只把双腿assignment写进JSON，不按端口组拼历史信道；这种实现仍会伪装成四列同时测量。",
            "本色不足后跨PCI颜色借资源；正确行为是全局10→20→40 ms，仍不足硬失败。",
            "把档位0～4、80/160 ms写成已实现；当前只有一个全局自适应10/20/40 ms周期。",
            "把5 ms信道snapshot、5 ms单腿空口机会与10 ms四端口SRS周期当成同一个参数。",
            "把 allocator-level LS NMSE proxy 写成完整 SRS 链路仿真。",
            "把 independently configured 的 srs_congested/clean 导频场景写成 PCI 模3 assignment 的端到端效果；当前两条配置链尚未自动桥接。",
            "用污染后的真实 receive SINR先重选一个更低 MCS，再计算 BLER；这让调度器偷看真值，会把 CSI 估计误差的后果抹掉。",
            "宣称频域 LMMSE 能自动分离完全同码碰撞者；没有额外序列或空间协方差信息时，它只会把 h_A+h_B 一起平滑。",
            "遇到资源耗尽后静默复用叶子、降端口或改周期，导致结果看似能跑却违反输入。",
        ),
        source_paths=("src/superran/hardware.py", "src/superran/physical.py", "src/superran/srs_resource.py", "src/superran/csi_aging.py", "src/superran/system.py", "tests/test_srs_resource.py", "tests/test_csi_aging.py", "scripts/run_scheduler_p0_validation.py"),
    ),
    "schedulerp0": DetailSpec(
        promise="沿一个 TTI 从 PF 排序走到不可变 FinalGrant，理解资源账本、逐 RBG 频选、全 MU 伙伴评分和统一定稿器如何闭成一条可审计流水线；并用真实数值解释为什么每一步存在。",
        principles=(
            "调度被拆成<strong>估值与提交</strong>。SU/MU PlanBuilder 可以无副作用地估计多个方案，但不能改队列、HARQ、OLLA 或随机流；两套方案先分别通过 ResourceLedger，使用受限后的 useful bytes 决定胜负。选中的计划再由 GrantFinalizer 重算最终 MCS/TBS/useful bytes，最后才抽 BLER 随机数并原子更新状态。",
            "物理 RBG 与逻辑 layer-PRB 必须分账。rank2+rank2 MU 在 3 个 16-PRB RBG 上只占 48 个物理 PRB，却消耗 3×16×4=192 个逻辑 layer-PRB。账本的 reserve/commit/rollback 不接触业务队列；任何重叠、层数或逻辑预算失败都能回滚到完全空的状态。按当前确认范围，PDCCH/CCE 和最大 grant 数明确不建模。",
            "RBG不重叠不代表grant一定合法。同一UE若在同一TTI进入两份独立grant，执行阶段会双重更新队列、HARQ与PF；未知mode也会绕过SU/MU人数约束。因此ResourceLedger把<code>duplicate_user_in_tti</code>和<code>unknown_mode</code>列为结构性硬失败，同一UE的RBG必须合并成一份grant。",
            "频选不再与 RB 功控绑定。链路表本来就保存逐 RBG predicted/receive SINR；<code>auto</code> 在字段完整时启用，<code>on</code> 缺字段硬失败，<code>off</code> 提供宽带/顺序基线。每个候选 bitmap 都按一个码字重新平均 SINR、选 MCS、查量化 TBS，不能把每 RBG TBS 线性相加。",
            "频选策略同时评估质量排序前缀和旧的轮转顺序前缀。能排空队列时优先最少 RBG；排不空时取 useful bytes 最大者。顺序前缀作为 safety net，使开频选在 gNB 预测口径下不会比旧分配更差。真实 ACK 仍取决于 receive SINR，不能拿 safety net 冒充每个随机 realization 都必胜。",
            "MU 先固定 PF anchor，再枚举全部待选伙伴。每个伙伴必须有 pair link、相关性不超门限、总层数不超预算，且 <code>CQI+BF+CorrLoss+powerLoss+SU-OLLA+MU-OLLA</code> 对应的预测 BLER 不超过 0.5。可行者按 queue-limited useful bytes/RBG 排序；相关性和 PF 顺序只作并列决胜。",
            "MU两用户共享同一bitmap，因此“RBG够了”的条件必须是<strong>两位用户都够</strong>。小包需要1 RBG、大包需要17 RBG时，取min会在1 RBG后移除两人并遗留16 RBG；当前取max(required)并在资源上限处截断。小包多出的TBS只是padding，物理RBG同时真实服务大包，不算浪费两遍。",
            "SU/MU 总方案仍遵循已确认规则：若 SU 能在本 TTI 发完所有可服务队列，直接 SU；否则比较两套 data-limited plan 的总 useful bytes，MU≥SU 才走 MU。伙伴评分解决“MU 内部选谁”，PlanSelector 解决“这个 TTI 是否值得用 MU”，两层不能混成一个阈值。",
            "排序度量与分配策略是<strong>两件事</strong>。PF 用 <code>TBS/R_avg</code> 追长期公平，EDF（包长感知）用 <code>TBS/Buffer</code> 追短期排空：缓冲区小 + 信道好的用户先走，一次传完就释放资源。分母换成当前队列后度量变成无状态的，不必维护历史平均。<code>qos_pf_edf</code> 按蓝本原式 <code>((1−w)·s·EPF + w·EDF)×w(priority)</code> 在两者之间连续可调。**两个分量不同量纲**：EPF 是 bytes^β/bytes^α，EDF 是无量纲比值，标定系数 <code>s</code> 不配平时名义 w=0.5 在饱和工作点实际只有 0.002（同一个 s 在轻载下却是 0.507——**s 是逐工作点的，不是常数**），因此结果必须回报 <code>effective_edf_share</code>。",
            "EDF 的定价是公平性，而且必须被量出来。24 UE 饱和实测（2026-09-03 在下行 AMC 链修正后的基线上重测）：Jain 从 PF 的 0.4764 掉到 0.2708，代价是 1 个 UE 被完全饿死（served=0，正是全小区最差的那条链路：−3.00 dB），换来的小包即时服务比例只从 0.5235 升到 0.5280，**收益 0.45 个百分点**。收益这么小是因为 SuperRAN 的按需 RBG 分配早就让小包在 PF 下也基本即时服务。想要 EDF 的取舍又不想饿死人，就把 <code>epf_scale</code> 标定到分量可比（本例 1e-4），混合模式能拿到 Jain 0.2788 且零饿死。旧基线上这组数字是 0.4707→0.3032、2 个饿死、0.7665→0.7891（+2.3 pp）；AMC 链修正之后收益缩到约五分之一而公平性代价更大，「纯 EDF 不划算」这个结论比原来更成立。",
            "GrantFinalizer 是执行前唯一真相。它按 base predicted SINR、CorrLoss、powerLoss 和 OLLA 重新得 MCS，再按实际 bitmap/rank 算 TBS 和 padding；HARQ 则冻结 MCS/RBG 数/rank/TBS。Planner 估值与 Finalizer 任何一项不同立即硬失败，当前压力结果 mismatch count=0。",
        ),
        implementation=(
            ("形成只读快照", "TTI 起点读取队列、HOL、HARQ、PF 平均、OLLA、snapshot 和逐 RBG 链路表；HARQ/tie-break 随机数已按 [TTI,UE] 固定，不随候选顺序漂移。"),
            ("做一次优先级排序", "potential full-band TBS 除以 scheduled-TBS 指数平均；QoS-PF 只在显式开启时加入业务权重和 HOL/PDB 因子。<code>edf</code> 改除以当前 buffer bytes，<code>qos_pf_edf</code> 取两者的蓝本加权原式。重传不靠魔数常数，而是把 HARQ pending 用户整体前置并按首传时间排序。"),
            ("构造 SU 计划", "依 PF 顺序给每个 UE 选最小够用或 useful 最大的 RBG 子集；所选 bitmap 上重新算 predicted/receive SINR、MCS 与 TBS。尾料没有需求就留空。"),
            ("构造 MU 计划", "对每个anchor枚举所有伙伴、记录可行性和拒绝原因，选useful bytes/RBG最高者；共享bitmap持续到两位队列都满足或RBG耗尽，不能在第一个小包满足时提前停止。"),
            ("两套计划过账", "ResourceLedger 对 SU/MU 分别 reserve+commit 物理 bitmap、层数和逻辑 PRB；预算拒绝后的实际 useful bytes 才进入 SU/MU 比较。"),
            ("选择与定稿", "HARQ 优先、SU 清空优先、否则 MU useful≥SU；GrantFinalizer 重算并对计划估值做逐值 hard compare，绑定 reservation id。"),
            ("原子更新和留证", "按 FinalGrant 抽 BLER、更新队列/HARQ/OLLA/PF；TTI trace 保存 RBG bitmap、候选评分、拒绝原因、账本快照、MCS 输入、receive SINR、BLER draw 与 ACK。"),
        ),
        example_title="UE0 为什么跳过更早的 UE1，和 UE2 做 rank2+rank2 MU",
        example=(
            "<p>压力例中 PF 先选 UE0 为 anchor；伙伴顺序里 UE1 的 <code>pf_order=1</code>，UE2 的 <code>pf_order=2</code>。UE1 与 UE0 的 CorrLoss 是 −5 dB，17 RBG 上两份最终 MCS 为 12/11，合计 useful density 约 <b>3315.8 B/RBG</b>；UE2 虽排得更后，但 CorrLoss 只有 −1 dB，最终 MCS 17/14，density 约 <b>4701.6 B/RBG</b>，因此选择 UE2。</p>"
            "<p>该 TTI 的 SU-only plan 只能交付 54,285 B；选中 MU plan 在同一 17-RBG bitmap 上交付 79,927 B，所以 <code>MU_useful_bytes_ge_SU</code> 成立。资源账显示物理 272 PRB 只扣一次、总层数为 4；两个用户共享同一个 <code>tti0-res0</code>。若把 UE2 相关性改到门限以上，它会留下 <code>correlation_threshold</code> 拒绝记录，而不是从结果中消失。</p>"
            "<p>互补子带频选反例中，off 基线小区 ACK 吞吐为 148.71 Mbps、每忙 TTI 只服务 1 个 UE；on 在相同 CRN 下为 486.52 Mbps、每忙 TTI 服务 2 个 UE，约 3.27×。这个构造证明频选机制的方向和 bitmap 落账正确；它不是对一般现场信道承诺 3.27×。</p>"
            "<p>小包+大包反例中，两位MU用户队列为1,000 B和500,000 B，required RBG为1/17。旧式min规则只给1 RBG并把两人移出pending；当前共享bitmap给满17 RBG，小包交付1,000 B、大包交付26,647 B。该反例专门证明“任一用户够”不能替代“两位用户都够或资源耗尽”。</p>"
        ),
        checks=(
            ("资源事务", "MU 3×16 PRB/rank2+rank2精确得到physical=48、logical=192；rollback回到0；未知mode和同TTI重复UE均硬失败。"),
            ("频选不劣估值", "每个 grant 同时评估质量/顺序前缀，predicted useful 增量非负；互补子带端到端吞吐与同 TTI 用户数显著上升。"),
            ("MU 全候选", "刻意让早到伙伴更差，trace 仍选择后到高密度伙伴；candidate/feasible/selected 与拒绝原因都有计数。"),
            ("MU共享bitmap", "1,000 B/500,000 B反例严格得到required=1/17、allocated=17，不在小包先完成时遗留RBG。"),
            ("统一定稿", "所有 SU/MU/NewTx/ReTx 都有 <code>finalizer_version</code> 和 reservation id；计划/定稿 MCS、TBS、useful mismatch 恒为 0。"),
            ("HARQ 身份", "一次重传保持 MCS、RBG 数、rank、TBS 与 D/S 类型；只在 BLER lookup 使用 IR/CC 合并增益。"),
            ("度量可切换且两端严格退化", "<code>qos_pf_edf</code> 在 w=0 时与纯 <code>qos_pf</code>、w=1 时与纯 <code>edf</code> 的小区/用户 KPI 逐位相同；pf/qos_pf/rr/max_ci 四条既有路径在 15 个跨场景运行下数值零漂移。"),
            ("EDF 定价被量出来", "24 UE 饱和下 Jain 必须低于 PF、小包即时服务比例必须高于 PF、且 PF 零饿死而 EDF 有饿死用户——三条都是断言，不是描述。"),
            ("混合权重不许假装生效", "24 UE 饱和工作点下 <code>epf_scale=1.0</code> 给出 <code>effective_edf_share&lt;0.01</code> 与告警；同工作点标定到 1e-4 后占比 &gt;0.5 且不再饿死任何用户。**两个数都是逐工作点的**，轻载下 1.0 反而是 0.507。"),
            ("长序列守恒", "系统回归检查 arrived=ACK+queue+inflight+dropped、用户归因 PRB 求和回到小区物理 PRB、RBG overlap=0。"),
        ),
        pitfalls=(
            "把频选开关继续写成 rb_power_control.enabled；这会在均匀功率时错误关闭频率选择性。",
            "伙伴循环遇到第一个相关性过门者就 break，使 PF 顺序代替了 MU 收益评分。",
            "在 MU 中给两个用户各扣一遍物理 RBG，或只扣物理资源却不记四层逻辑工作量。",
            "把MU的sufficient写成any：第一个小包够了就停止共享bitmap，并把仍有大队列的伙伴一并移出pending。",
            "只检查RBG重叠，不检查同一UE是否被两个独立grant重复登记。",
            "Planner 和执行循环各算一遍 MCS/TBS，结果稍有漂移却不报错。",
            "用 true SINR 选择候选 bitmap 或伙伴；调度只能消费 gNB 当时可见的 predicted 信息。",
            "把互补子带 3.27× 构造结果扩写成一般现场收益；方向性反例不是统计发布结论。",
            "在 EDF 上再叠一个 +10000 的重传常数。HARQ 前置已经是结构性绝对优先，加常数只会打乱按首传时间的排序。",
            "给 EDF 喂 full_buffer。队列被钉在 2**50 B 后比值退化成 max_ci 的常数缩放，而且已服务字节会让分母缓慢变小，形成被服务越多越优先的建模伪影。",
            "看到混合模式的 w 调了却没效果就以为接错了。先看 <code>effective_edf_share</code>——那是量纲没配平，不是接线问题。",
            "把某个场景标定出来的 <code>epf_scale</code> 当常数搬到别的负载。实测 1.0 在轻载是完美平衡的 0.507、在饱和只剩 0.002，1e-4 恰好反过来。",
            "以为 <code>effective_edf_share=0.002</code> 就等于弱分量毫无作用。它量的是数值占比，排序看的是离散度——该占比下公平度仍走了纯 EPF→纯 EDF 全程的约 2.4%。",
            "把饥饿的受害者说成「大包用户」。分子是信道相关的 TBS，EDF 对坏信道和大积压是乘性双重惩罚——实测被饿死的是全小区最差的两条链路（−1.87 / −3.00 dB），而另外 10 个同为 large 类的 UE 活得好好的。准确说法是「大缓冲 + 边缘信道」。",
            "把 EDF 下变好的 large_queue_wait 分位数当成大包也受益。饿死用户的到达对象被右删失，分位数只统计到被服务的那些，方向会反过来。",
            "以为 SRB 的 +5000 在跑。SuperRAN 不建模逻辑信道，除非显式声明 <code>resource_type='signalling'</code> 的业务类，否则该加值恒不触发。",
            "忘记当前 PDCCH/CCE 不建模，进而把一个 TTI 可服务的小包 UE 数说成产品级精确上限。",
        ),
        source_paths=("src/superran/scheduler_resource.py", "src/superran/scheduler_edf.py", "src/superran/scheduler_frequency.py", "src/superran/scheduler_mu.py", "src/superran/scheduler_finalize.py", "src/superran/experience.py", "tests/test_scheduler_p0.py", "tests/test_scheduler_edf.py", "scripts/run_scheduler_p0_validation.py"),
    ),
})
