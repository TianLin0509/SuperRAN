# 话务 CDF 配置

## 固定体验基线

`experience_baseline_v1` 是用户确认的 `experience_v2` 固定下行业务基线。
一个业务 profile 只使用两份边缘 CDF：

- `experience_baseline_packet_size_cdf.txt`：包大小，value 单位为 **byte**；
- `experience_baseline_interarrival_s_cdf.txt`：包间隔，value 单位为**秒**，
  例如 `0.010` 表示 10 ms。

两个变量使用独立命名随机子流抽样。`packet_size_scale` 与
`interarrival_scale` 分别乘对应 CDF 的 **value 横轴**，不改变累计概率。
包大小缩放后取最近整数 byte，且至少为 1 byte。业务输入负载的一阶关系为
`packet_size_scale / interarrival_scale`.

运行时把每次到达放入 UE 的 DRB FIFO；当前 MCS、rank 与 38.214 TBS 查表
共同决定最小够用 RBG 数。CDF 不直接规定 RBG 数，本合同之上也不增加应用会话、
Reading 状态、IP/PDCP 头或 trace 回放。

机器可读的身份、源文件哈希、单位和参考分位数位于
`experience_baseline.json`。开发者文档构建时读取同一批文件绘制两张 CDF 图，
数据或单位漂移不能留下过期手绘曲线。

Example:

```python
TrafficConfig(
    model="cdf",
    packet_size_cdf="presets/traffic/experience_baseline_packet_size_cdf.txt",
    interarrival_cdf="presets/traffic/experience_baseline_interarrival_s_cdf.txt",
    interarrival_cdf_unit="s",
    packet_size_scale=1.0,
    interarrival_scale=1.0,
)
```

这些曲线只是话务输入，不是 PRB 或性能结论。先用包大小系数调每个 grant 的
RBG 形态，再用包间隔系数调总业务负载；最终必须在 Gate 1 通过的数据集上检查
0..17 RBG 分布与服务小区 PRB 利用率。

## 合成解析样例

这两份 CDF 只用于验证 superran 的文件解析、双标量、负载校准和 KPI 页面。
它们不是实测曲线，不是 3GPP 标准话务，也不能用于现场性能结论。

- `synthetic_packet_size.csv`：value 单位 byte。
- `synthetic_interarrival_ms.csv`：value 单位 ms。
- CDF 可写 0..1 或 0..100；value 必须严格递增，末项必须收敛到 1/100%。
- 相对路径从项目根解析，例如
  `packet_size_cdf="presets/traffic/synthetic_packet_size.csv"`。

接入实测曲线时保留两列 `value,cdf` 即可；结果会记录绝对路径、SHA-256、均值和
P50/P95，便于确认跑的是哪一版输入。
