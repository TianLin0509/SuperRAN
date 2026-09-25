# MCP 服务端、进程与性能

> 本文件由 `CLAUDE.md`「踩过的坑」按主题拆出（2026-09-25），正文原样搬移，条目标题保留原文；索引在 `CLAUDE.md`。
> 每一条都是真实事故，**删之前先想清楚**；改动口径时同步更新本条和 `CLAUDE.md` 索引的一句话要点。

**什么时候读**：改 `server.py` / warmup / `generate` 并行 / 子进程 / 性能基准。

### scipy 子模块必须在主线程预热

`channelhub.warmup()` 里那串 `import scipy.interpolate / special / io / spatial / stats`
**不是冗余**。first-party 估计/校准路径会用这些子模块，而 MCP 把工具
调用派到工作线程执行——在工作线程里首次加载 scipy 的 C 扩展会撞上 import 死锁。

症状极其隐蔽：请求永久无响应、无异常、无日志，看起来像仿真跑不完。
用 `faulthandler.dump_traceback_later` 抓栈才定位到卡在
`scipy/interpolate/_fitpack_impl.py` 的 `create_module`。

调试时设 `SUPERRAN_DEBUG=1`，会开 faulthandler 并打点到 stderr。

### MCP 服务端的内存：先别为没用的依赖买单，再压线程 arena

MCP 服务端是**每个 CLI 会话各起一个进程**。2026-08-29 在 20 逻辑核 / 32 GB 的机器上
实测：一个进程恒定提交 **2.72 GB**，而实占只有 20–30 MB。并存 13 个 Claude 会话时
单是 superran 就锁掉 34.6 GB 提交内存（系统总额度的三分之一），最后表现成一个
看起来毫不相干的故障——AI Hub 文件预览打不开（提交内存见底，Chromium 起不了渲染进程）。

以下是**脱钩前的历史诊断**，用于解释为什么禁止恢复外部 source import：

    到 scipy 全部子模块为止              330 MB   ← 其中 numpy/scipy 的 BLAS 线程 arena 占 ~250 MB
    + msg_embedding.data.sources      1645 MB   ← **+1314 MB，全是 PyTorch**

PyTorch 是被 MSG-Platform 的 `sionna_rt.py` 一段**模块级可选依赖探测**拉进来的：
它 `try: import sionna / sionna.rt / torch` 只为算出一个 `_SIONNA_AVAILABLE` 布尔值，
而这台机器装了 CUDA 版 torch（单独 import = 提交 1507 MB）。**superran 一行 torch 都不用。**

当时的两条过渡修法是外部延迟导入与本仓 `_probe_module`；现在已经进一步删除
外部 runtime import，只保留顶层包名探测给可选 direct adapter：

1. 可用性改用 `importlib.util.find_spec`，**只探顶层包名**。
   `find_spec("sionna.rt")` 会为了拿父包 `__path__` 真的 import sionna，
   连带拉起 mitsuba / drjit / matplotlib / IPython / pythreejs（+455 MB）——
   顶层名字则完全不触发 import。
2. 真正的 `import sionna/torch` 推迟到确实要跑 RT 时（`_ensure_sionna()`）。

再叠上 `SUPERRAN_BLAS_THREADS`（默认 **1**，压 OpenBLAS 按核数预留的线程 arena）：

    auto（不限） 2718 MB → 4 线程 365 MB → 2 线程 236 MB → 1 线程 172 MB

差额**全部**落在 numpy 与 scipy.special 的 import 上（逐段量过，其余步骤逐字节相同）。

**默认 1 不是保守，是实测最快的一档。** 按真实矩阵尺寸测（273 个 RB 的 4×64 / 4×256
SVD、64×64 eigh、2048×64 ifft），多线程只剩调度开销：4 线程在其中三项上都比 1 线程慢，
只有人造的 2000×2000 GEMM 才吃多线程，而 SuperRAN 不做那种运算。脚本模式跑
`tests/test_gates.py` 复核：1 线程 87.5s / 2 线程 96.2s / 4 线程 88.1s，差异在噪声内。
真正的并行度来自 generate 的多进程分块，那些 worker 本来就各自压成 1 线程
（见「多进程必须先压 BLAS 线程数」）。要放开就设 `SUPERRAN_BLAS_THREADS=auto`。
这是**性能取舍不是精度取舍**，数值逐位不变。

当时的 172 MB 近似地板由 Python、numpy/scipy、SuperRAN 与 BLAS/FFT 池构成；
当前版本已不再包含 `msg_embedding` 那一项，具体提交内存必须按当前 HEAD 重测。
后两项里的 scipy 子模块与池初始化都是主线程预热的防死锁动作，不能省。

**别想着把 numpy 改成懒加载**——试过，两次死锁：

    sr_mcs_info → linkadapt.py → numpy/_core/multiarray.py → create_module   ← 卡死
    # 只预载 numpy/scipy 后，历史卡点会在首次估计器 C 扩展 import 处移动

危险的**只是 numpy / scipy 及其子模块**的首次加载，这正是上一节「scipy 子模块必须在
主线程预热」说的事，所以 `main()` 里的 `ch.warmup()` 是正确性依赖、**不提供跳过开关**。
边界要说清楚：事件循环起来之后再 import torch / sionna.rt 实测**不会**挂
（1.4s / 1.0s 正常导完），前提是 numpy/scipy 已预热——所以它们可以安全地按需加载。

`_lazy.py` 的占位模块只服务「不跑服务端」的场景：`import superran` 658→10 MB、
`import superran.server` 1323→49 MB，测试和取数脚本因此变轻。

### 多进程必须先压 BLAS 线程数

`_chunk_worker` 里在 import numpy **之前**把 `OMP_NUM_THREADS` 等设成 1。
不设的话每个 worker 各开满核数的线程：20 worker × 20 线程抢 20 个核，
上下文切换吃掉全部收益——实测 10 进程只有 1.34 倍加速，设了才拿到应有的加速比。

并行分块**不能给每块换 seed**。static `internal_sim` 的一个 seed 固定 UE 几何；旧实现
给 worker 用 `seed=S..S+W-1`，于是并行混入 W 个几何，串行却只有一个，连条件分布都变了。
现在 first-party `InternalSimSource` 支持 `sample_index_offset`，所有 worker 保持同一 seed，只切互不重叠的全局
sample index；`workers=1/4` 必须逐样本、逐位相同。移动轨迹、拒绝采样或未实现全局索引的
source 会显式回退串行并把原因写进摘要，不能以“统计等价”为理由偷偷改变实验条件。

多进程在某些宿主里起不来（Windows spawn 需要可导入的 `__main__`，REPL 和
`python -c` 里没有）。`generate` 会**降级串行并把原因记进摘要**，不让整次生成失败——
但也不能静默，否则用户会纳闷为什么没变快。

### 子进程编码要在子进程侧统一，不能只在父进程解码

`test_e2e.py` 起子进程跑取货代码。只在父进程写 `encoding="utf-8",
errors="replace"` 是不够的：Windows 默认 GBK 时子进程按 GBK 输出中文，
父进程按 UTF-8 解码得到乱码，`errors="replace"` 把它换成 U+FFFD，
**测试照样"通过"**，等父进程把 U+FFFD 打到 GBK 控制台时才炸，且报的是父进程的错。

正确做法：给子进程设 `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1`，父进程用
`errors="strict"`，并断言输出里没有 U+FFFD、且预期中文短语在。

### 引擎清单长度不能随环境变化

`probe_capabilities()` 早先在找不到 ChannelHub 时只返回 `internal_sim` 一条，
于是调用方写 `engines["sionna_rt"]` 会 KeyError，看起来像工具坏了。
清单必须恒为两条（`internal_sim` / `sionna_rt`），变的只是 `available` 与 `missing`。

### mcp 1.x 与 2.x 的服务端类换了位置

`mcp 2.0` 删掉了整个 `mcp.server.fastmcp` 子模块，`FastMCP` 改名叫
`mcp.server.mcpserver.MCPServer`。两者 `.tool()` 与 `.run(transport=...)` 签名一致，
`server.py` 里用 try/except 兼容，`MCP_MAJOR` 记录当前版本。

不做这层兼容的后果：今天新装的用户 `pip install mcp` 拿到 2.x，服务端在 import
阶段就 `ModuleNotFoundError`，而且报的是 mcp 的错，看起来像用户环境问题。
**本机装的是 1.27，所以本地测试永远发现不了**——这个 bug 是打离线包时在干净
venv 里才暴露出来的。改 `server.py` 的导入前先想清楚两个版本都要能跑。

### stdio 传输下 stdout 是 JSON-RPC 通道

任何调试输出只能走 stderr。`_dbg()` 已经这么做了，别图省事用 `print()`。

### 比耗时必须交错重测

第一版性能基准把变体一个接一个顺序跑，耗时单调下降（4830→4054→…→1591 ms），
得出"关掉 measurements 里的可选项能快 2.55 倍"。**全是预热与缓存的假象**——
代码层面 `internal_sim` 只读 `ssb_rsrp` 与 `interferer_channels` 两个开关，
其余四个根本没被引用。

正确方法：每轮把所有变体各跑一次、轮转多轮取中位数，并把基准自身的轮间波动
一起报出来。`num_interfering_ues` 设小确实会改变工作量，但也改变物理，不能
作为无损优化。关 SSB 也会少算数据；旧内核的 1.40× 在 20-ray 版本未重标，
不再当作当前性能承诺。

### 先批量线性代数，再谈 workers

`linklevel.post_equalizer_sinr` 的 `[T,RB]` 小矩阵必须走 NumPy batch，不能恢复
Python 双循环。固定 `[8,272,rank4,4R]` 交错审计中 MMSE/IRC/ZF 为 9.8~11.4×，
逐值完全一致；MRC 为 102.6×、最大误差 1.34e-15。机制记录在
`artifacts/results/performance_audit.json`。

系统重复实验与信道生成是两套并行轴。`replication_workers="auto"` 只并行独立
RngRun，链路表通过 initializer 每进程传一次，结果按 replication index 还原；
5 s/50 s 固定基准分别为 1.61×/3.16×；有限 KPI exact、非有限类别一致、差异路径为空。
不要加线程后端：4 线程实测只有 0.72~0.74×。库函数默认 1 保护 REPL/脚本；MCP 前门默认 auto。
显式 worker 失败或超过安全上限必须报错，只有 auto 可带明示原因回退串行。
