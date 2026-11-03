# JR-nearby

全国铁路车站的「站边情报」：每站一份 JSON，覆盖车站步行范围内的景点、吃喝玩乐与公共服务设施，
日语 / 英语 / 简体中文三语。一站一文件，增量推送。

## 数据口径

- 车站全集与 ID 与主站及 [`ooep/JR-images`](https://github.com/ooep/JR-images) 一致：
  `stNNNNN`（8,909 站，`all8909` 口径），可直接 join。
- 检索半径默认步行 1,200 m；最远 2,000 m，必须写 `distance_m` 与 `walk_min`。
- POI 数量随车站规模浮动：城市核心 8–20、郊区 4–10、乡野 2–6；荒凉站允许更少，
  但必须在 `coverage.gaps` 写明（禁止凑数）。
- 类别：`sight` 观光名胜 / `shrine_temple` 神社佛阁 / `food` 饮食 / `cafe` 咖啡 /
  `shopping` 购物 / `onsen_sento` 温泉·钱汤 / `park` 公园 / `entertainment` 娱乐 /
  `lodging` 住宿 / `public_service` 公共服务（役所、邮局、医院、便利店、观光案内所、ATM 等）。

## 结构

```
data/<station_key>.json      # 一站一文件（stNNNNN.json）
schema/station.v1.json       # JSON Schema（CI 校验用）
index.json                   # {station_key: {name, pref, file, poi_count, updated_at, sha256}}
manifest.json                # 覆盖统计：done/total、三语缺失、URL 不可达、按都道府县分布
scripts/validate.py          # 校验：结构/三语/去重/距离/URL 抽样/索引一致性
scripts/fetch_overpass.py    # Overpass 候选抓取（仅原料，成品需人工核源）
scripts/gh_push.py           # blob 级提交（不 clone 整仓）
```

## 硬性纪律

1. 不许编造：每个 POI 至少一个可点击来源；`url` 必须是该场所自身官网 / 官方页 / 地图页，
   实测 HTTP 200；禁止搜索引擎结果页与聚合站首页。
2. 营业时间没有可靠来源就写 `null`，不许估。
3. `name` / `desc` 三语齐全：zh 用简体，不得残留日文假名（专名可保留原文但须在 desc 给中文说明）；
   en 用通用译名。
4. 同站内同名 / 同坐标（<50 m）的 POI 必须合并。
5. 只追加 / 替换本站自己的 JSON；`force:false`。

## 校验

```
python3 scripts/validate.py --all --check-urls          # 全量
python3 scripts/validate.py --mode changed              # 只查本次变更（CI 用）
```

CI（push 触发）：JSON Schema、三语完整性与质量、站内去重、距离合理性、索引一致性、
URL 可达性抽样（每次 ≤20 条）。
