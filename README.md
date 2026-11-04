# JR-nearby

全国铁路车站的「站边情报」：每站一份 JSON，覆盖车站步行范围内的景点、吃喝玩乐与公共服务设施，
日语 / 英语 / 简体中文三语。一站一文件，增量推送。

## 数据口径

- 车站全集与 ID 与主站及 [`ooep/JR-images`](https://github.com/ooep/JR-images) 一致：
  `stNNNNN`（8,909 站，`all8909` 口径），可直接 join。
- **收录半径恒为 2,000 m**（步行口径），不允许按站缩小；每条 POI 必须写
  `distance_m` 与 `walk_min`。
- POI 数量随车站规模浮动：城市核心 8–20、郊区 4–10、乡野 2–6；荒凉站允许更少，
  但必须在 `coverage.gaps` 写明（禁止凑数）。
- 类别（冻结枚举 v2，16 值）：`sight` 观光名胜 / `shrine_temple` 神社佛阁 /
  `food` 饮食 / `cafe` 咖啡·茶座 / `shopping` 购物 / `onsen_sento` 温泉·钱汤 /
  `park` 公园·庭园 / `entertainment` 娱乐 / `lodging` 住宿 /
  `convenience` 便利店（24h 属性写进 hours）/ `bank_atm` 银行·ATM /
  `medical` 医院·诊所·药房 / `post_office` 邮局 / `police` 警察·交番 /
  `tourist_info` 观光案内所 / `public_service` 其余 civic（市役所·区役所、图书馆等）。
  新增类别必须修改 `schema/station.v1.json` 并全量重跑 `scripts/validate.py` 后方可推送。

## 选材规则（公示）

**样板期规则（原文，R1 前事实上执行的）**：

1. Overpass 候选按「类别多样性 + 知名度」人工筛选；城市核心站 8–20 条、
   郊区 4–10、乡野 2–6；
2. 优先有官网或 OSM 实体页的场所；无来源不收录；
3. 同类连锁（便利店/银行）只保留最近 1 家；
4. **无类别配额**——缺陷：密集核心站因展示上限被知名度排序挤掉的类别会整体
   消失（R2 审查发现 10 个样板站 medical 全军覆没，而 2km 圈内实际存在医院）。

**修订后规则（R3 起执行）**：

1. 展示上限 `selected_cap`：每站最多收录 20 条，写入 `coverage.selected_cap`；
   候选数远超上限属正常筛选，与抓取截断（`fetch_truncated`）严格分开表述。
2. **类别保底配额**：对 `medical` / `bank_atm` / `convenience` / `post_office` /
   `police` / `tourist_info` 六类做存在性检查——2km 圈内 Overpass 有候选则
   **至少收录 1 条**；`medical` 在全量站中至少 5 站必须有（10 样板站中
   東京/大阪/京都/広島/仙台 5 站已落实）。
3. 保底类的候选选择：同类候选中按「设施等级（medical：医院>诊所>药房）→
   信息完备度（有官网 > 有营业时间 > 有电话 > 仅 OSM）→ 距离最近」依次取舍；
   未选最近候选时必须在审查报告说明理由。
4. 其余名额按「类别多样性 + 知名度」排序填充；同设施别名/多入口合并须带
   `aka[]` 与 `merged_ids[]`；不同名字的独立景点各留一条。
5. 抓到但未入选的候选（如山峰等非设施类）写入 `coverage.excluded_candidates`
   对账，不允许静默丢弃。

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

## URL 可达性口径（CI 复测）

- **404 / 410 = 死链**：任何环境都判错（红）。死链不许洗成"被墙"。
- **0 / 403 / 429 / 502 / 503 = `unreachable(runner-blocked)`**：warn 不判红，
  但逐条计入日志与台账（`url_ledger.json`），不许静默。
- 已有本地 200 证据的 URL，状态记录在 `url_ledger.json`（`local_status=200`），
  复测输出会带上，避免后人误判死链。

## medical 硬规则（放量期适用，validate.py 强制）

每站 `coverage.floor_check` 记录六类保底候选存在性（medical / bank_atm / convenience /
post_office / police / tourist_info）：

- `candidates > 0` → 该类**必须至少收录 1 条**（有候选未收 = 校验错）；
- `candidates == 0` → `coverage.gaps` 必须写明缺失（如"2km 内无医疗机构"），
  无候选且无说明 = 校验错。

## 校验

```
python3 scripts/validate.py --all --check-urls          # 全量
python3 scripts/validate.py --mode changed              # 只查本次变更（CI 用）
```

CI（push 触发）：JSON Schema、三语完整性与质量、站内去重、距离合理性、索引一致性、
URL 可达性抽样（每次 ≤20 条）。
