# 近地小天体短弧观测审计

观测站会把同一候选天体在数小时内的天球位置汇成一个 JSON 文件。请完成 `neo_audit.py`，使它可按以下方式运行：

```sh
python3 neo_audit.py INPUT_JSON OUTPUT_JSON
```

输入对象必须具有 `object_id`（非空字符串）和 `observations`（至少三条记录）。每条记录必须且只能使用 `epoch_s`、`ra_deg`、`dec_deg`、`sigma_arcsec` 四个数值字段；布尔值不算数值。`epoch_s` 必须为有限数值且严格递增，`ra_deg` 必须在 `[0, 360)`，`dec_deg` 必须在 `[-90, 90]`，`sigma_arcsec` 必须为有限正数。相同 `epoch_s` 的记录属于重复历元，必须拒绝。

对有效输入，按时间用 `1 / sigma_arcsec²` 作权重，对赤经和赤纬分别作线性拟合。时间自首条观测起以小时计。赤经是圆周角：相邻赤经应沿不超过 180° 的连续方向展开后再拟合，不能把 359.9° 到 0.1° 当成跨越整圈。赤经残差换算角秒时须乘以拟合参考赤纬的余弦，赤纬残差直接换算；`rms_arcsec` 是所有二维残差平方和的均方根。

成功输出为 UTF-8、紧凑 JSON，且以一个换行结束。键顺序必须为：`object_id`、`classification`、`motion_ra_arcsec_per_hour`、`motion_dec_arcsec_per_hour`、`arc_hours`、`rms_arcsec`、`observation_count`。数值保留六位小数。结果按下列规则分类：

- 有效观测弧少于 0.5 小时，或总角速度小于 0.15 角秒/小时，分类为 `REVIEW`。
- 否则，若 `rms_arcsec` 大于 `max(1.5, 3 × median(sigma_arcsec))`，分类为 `REVIEW`。
- 其余有效输入分类为 `ACCEPT`。

输入结构、范围、时间、重复历元或不可拟合几何不满足要求时，程序以非零状态退出，在 stderr 输出以 `E_SCHEMA:`、`E_TIME:`、`E_DUPLICATE:` 或 `E_GEOMETRY:` 开头的说明；不得创建、截断或改写原有输出文件，也不得遗留临时文件。成功写入必须是原子替换，重复运行同一输入必须产生完全相同的字节。
