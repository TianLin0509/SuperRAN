# Synthetic traffic CDF examples

这两份 CDF 只用于验证 superran 的文件解析、双标量、负载校准和 KPI 页面。
它们不是公司实测曲线，不是 3GPP 标准话务，也不能用于现场性能结论。

- `synthetic_packet_size.csv`：value 单位 byte。
- `synthetic_interarrival_ms.csv`：value 单位 ms。
- CDF 可写 0..1 或 0..100；value 必须严格递增，末项必须收敛到 1/100%。
- 相对路径从项目根解析，例如
  `packet_size_cdf="presets/traffic/synthetic_packet_size.csv"`。

接入公司曲线时保留两列 `value,cdf` 即可；结果会记录绝对路径、SHA-256、均值和
P50/P95，便于确认跑的是哪一版输入。
