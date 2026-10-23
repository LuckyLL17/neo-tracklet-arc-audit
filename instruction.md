# 近地小天体短弧的地心归算与候选星历关联审计

实现 `/app/main.py`，命令行为：

```sh
python3 /app/main.py INPUT_JSON OUTPUT_JSON
```

这是一项短弧天体测量归算任务。输入给出多站对同一近地小天体候选体的角位置、观测站相对地心的局部基线，以及待关联候选星历。程序须先将观测归算到指定切平面，再从全部观测共同反演地心角速度与视差，最后按协方差门控关联候选星历并给出物理质量结论。

运行镜像仅含 `/app/main.py` 和 `/app/sample_request.json`。实现只能导入 `json`、`math`、`sys` 三个标准库模块；程序以 `runner` 身份运行。

## 输入契约

顶层对象必须且只能有下列键：

- `object_id`：非空字符串；
- `reference`：恰有 `ra_deg`、`dec_deg` 的对象，表示所有切平面坐标共同使用的参考天球位置；
- `observations`：至少四条、按时间严格递增的观测；
- `candidates`：至少一条候选星历。

所有数值必须为有限的 JSON 数值；布尔值不算数值。

每条 `observations` 记录必须且只能有：

```text
epoch_s, ra_deg, dec_deg, sigma_arcsec, station_east_au, station_north_au
```

其中 `epoch_s` 是 UTC 秒，严格递增（相同值以 `E_DUPLICATE:` 拒收）；`ra_deg` 属于 `[0, 360)`，`dec_deg` 属于 `[-90, 90]`，`sigma_arcsec` 为正数。`station_east_au` 和 `station_north_au` 是观测站相对地心在参考切平面东、北方向上的 AU 基线。

每条 `candidates` 记录必须且只能有：

```text
candidate_id, epoch_s, xi_arcsec, eta_arcsec,
motion_xi_arcsec_per_hour, motion_eta_arcsec_per_hour,
covariance_arcsec2
```

`candidate_id` 为非空字符串；`covariance_arcsec2` 为对称、严格正定的 2×2位置协方差矩阵（单位为角秒平方）。候选位置和速度已经在 `reference` 定义的同一切平面中表示；候选位置对应其自身的 `epoch_s`。

参考赤纬的余弦绝对值小于 `1e-4` 时，或联合反演不可辨识时，必须以 `E_GEOMETRY:` 拒收。候选协方差或候选字段不合规时必须以 `E_CATALOG:` 拒收。其他结构、类型、范围或 JSON 问题必须以 `E_SCHEMA:` 拒收；倒退时间必须以 `E_TIME:` 拒收。

任何可预见的拒收都必须在打开输出文件前发生：原输出文件必须逐字节保持不变，且不得创建临时报告文件。

## 地心归算

令首条观测历元为 `t0`，并令 `t_i = (epoch_s_i - t0) / 3600`。赤经是圆周角：必须沿相邻差值绝对值不超过 180° 的连续分支展开，再相对 `reference.ra_deg` 取同一连续分支。将观测转换为切平面角秒：

```text
xi_i  = (unwrapped_ra_i - reference.ra_deg) * cos(reference.dec_deg) * 3600
eta_i = (dec_deg_i - reference.dec_deg) * 3600
```

对两个坐标以 `1 / sigma_arcsec²` 作权重，进行**一个共享视差参数**的联合加权最小二乘反演：

```text
xi_i  = xi0  + mu_xi  * t_i - parallax_arcsec * station_east_au_i
eta_i = eta0 + mu_eta * t_i - parallax_arcsec * station_north_au_i
```

不得把两个坐标分别拟合后再拼接视差。需要防止法方程数值退化；不可辨识或非有限结果均为 `E_GEOMETRY:`。`parallax_significance` 是反演视差除以其由逆法矩阵得到的 1σ 标准差。`rms_arcsec` 是每条观测二维残差平方和的平均值再开方。`arc_hours` 是最后与第一条观测的时间差。

## 候选关联

将每个候选线性传播到 `t0`：

```text
candidate_xi(t0)  = xi_arcsec  + motion_xi_arcsec_per_hour  * (t0 - epoch_s) / 3600
candidate_eta(t0) = eta_arcsec + motion_eta_arcsec_per_hour * (t0 - epoch_s) / 3600
```

对地心拟合位置与传播后候选位置的差 `(dx, dy)`，用该候选的 2×2位置协方差计算 Mahalanobis 距离。仅当距离不大于 `3.0` 且地心速度与候选速度的欧氏差不大于 `1.5` 角秒/小时，候选才可关联。多个可关联候选按以下顺序确定唯一结果：较小 Mahalanobis 距离、较小速度差、字典序较小的 `candidate_id`。

没有可关联候选时，`association_status` 为 `UNMATCHED`，`matched_candidate_id` 与 `association_mahalanobis_distance` 均为 JSON `null`。

## 分类与输出

对有效的几何反演输出紧凑 UTF-8 JSON，以一个换行结束，键顺序严格如下：

```text
object_id
classification
geocentric_motion_xi_arcsec_per_hour
geocentric_motion_eta_arcsec_per_hour
parallax_arcsec
parallax_significance
association_status
matched_candidate_id
association_mahalanobis_distance
arc_hours
rms_arcsec
observation_count
```

所有非空数值一律保留六位小数。分类规则按顺序应用：

1. 没有可关联候选、反演视差不为正、或 `rms_arcsec > max(1.5, 3 * median(sigma_arcsec))`：`REJECT`；
2. 否则若 `arc_hours < 1.0`、地心总角速度 `< 0.25` 角秒/小时、`parallax_significance < 3.0` 或已匹配候选的 Mahalanobis 距离 `> 2.0`：`REVIEW`；
3. 其余：`ACCEPT`。
