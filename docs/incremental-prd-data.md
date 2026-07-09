# 增量 PRD — 补全 vendored 静态数据（消除降级项）

**文档类型**：增量 PRD（仅描述相对 `docs/prd.md` 的变更部分）
**关联原文档**：`docs/prd.md`（原版简单 PRD）、`docs/design.md`（T0–T7 架构）
**作者**：许清楚（Xu），产品经理
**语言**：简体中文
**状态**：Draft，待主理人/用户拍板 §7 待确认问题

---

## 0. 现状基线（实测，当前 checkout）

> 基于当前 checkout 实际运行 `import idleon_saver.data` 的运行时验证（非凭描述推断）。

| 任务描述的降级项 | 实测结果 | 结论 |
|---|---|---|
| (1) `Vendored data directory missing; using empty data: ...\vendored\wiki` | **复现**：`vendored/wiki` 目录不存在 → `wiki_data = {}` | 真实缺口，需修复 |
| (2) `No StarSigns wiki data; deriving starsign_names from starsign_ids` | **复现**：`starsign_names` 回退到 hardcoded `starsign_ids`（`fallback=True`） | 真实缺口，需修复 |
| (3) `bag_maps` = 0/0（因 `vendored/maps/bags.json` 不存在） | **未复现**：`bags.json` 已存在；`bag_maps[INV]=37`、`[STORAGE]=44` 均非空 | 疑似描述基于更早 checkout；当前已满足，见 §7-Q1 |

其余数据实测正常（来自 `vendored/maps` 其他文件）：`statues=19`、`card_reqs=134`、`vial_names=42`、`stamp_names=[35,36,20]`。
GEM 背包本就故意留空（`Bags.GEM = {}`，wiki-only，已文档化），不在本次范围。

---

## 1. 产品目标

消除上述两类**真实降级**（wiki 数据缺失导致的两条 warning 与星座名回退），让 `idleon-saver` 在 **W7+ 数据**（含 Spelunking/Research 技能、Shimmerfin Deep 世界、Cosmos 系星座等）上也能全量可用、导入零告警。

> 注：本次目标聚焦"补齐 vendored 静态数据"，**不**涉及解析/导出逻辑的代码改动（见 §5 范围边界）。

---

## 2. 变更范围

| 维度 | 原 PRD/设计 | 本次增量变更 |
|---|---|---|
| `vendored/wiki/*.json` | 目录缺失，`wiki_data` 恒为空（设计已容忍） | **新增** `StarSigns.json` + `EnemyDetails.json`（至少），使 `wiki_data` 非空 |
| `vendored/maps/bags.json` | 设计假定可能缺失 | **保持/验证**已存在；视 W7+ 覆盖情况决定是否刷新 |
| 数据抽取管线 | 无（数据靠人工放置） | **新增** 一次性抽取脚本：从 `IdleOnAutoReviewBot` 的 `consts/*.py` 生成上述 JSON（仅产出数据文件，不改 `data/__init__.py` 契约） |
| `idleon_saver/data/__init__.py` | 防御性加载契约（T2 已落地） | **不改**；仅消费新增的 vendored 文件 |
| 冻结核心 `ldb.py` / `stencyl/*` | 冻结 | **不动**（硬约束） |
| CLI / 导出层重写 / GUI | 已交付 | **不动** |

---

## 3. 用户故事

| ID | As a… | I want… | so that… |
|----|-------|---------|----------|
| US-I1 | idleon 玩家 | 导入工具时不出现 `Vendored data directory missing` / `No StarSigns wiki data` 两条 warning | 我能确认 wiki 维度（星座/怪物）已正确加载，而非回退到硬编码 |
| US-I2 | idleon 玩家 | `bag_maps` 的 inventory / storage 名称能正确显示（非空） | 清单/背包相关导出可读、可追溯，而非空白 |
| US-I3 | W7+ 玩家 | 工具在 W7+（Spelunking/Research、Shimmerfin Deep、Cosmos 系星座）数据上也能全量可用 | 我的存档解析不丢数据、不因版本过旧而缺字段 |

---

## 4. 需求池

### P0 — 必做（消除真实降级）

- **P0-a** 生成 `vendored/wiki/StarSigns.json`，使 `wiki_data["StarSigns"]` 为 list-of-`{name}`（idleon-saver 期望形态），从而 `starsign_names` 来自 wiki 而非回退。
  - 来源：`IdleOnAutoReviewBot/mysite/consts/consts_w1.py::StarSigns`（list-of-lists `[name, bonus1, bonus2, bonus3]`）→ 需转换只取 `name`。
- **P0-b** 生成 `vendored/wiki/EnemyDetails.json`（怪物维度），使 `wiki_data["EnemyDetails"]` 非空，供导出层 `get_cards` 的 `safe_get(wiki_data, "EnemyDetails", {})` 命中真实数据。
  - 来源：`IdleOnAutoReviewBot/mysite/consts/generated/monster_data.py::monster_data` / `consts_monster_data.py`（ARB 无 `EnemyDetails` 同名键，需映射，详见 §7-Q3）。
- **P0-c** 确保 `vendored/maps/bags.json` 存在且 `bag_maps[Bags.INV]` / `[Bags.STORAGE]` 非空。
  - 当前 checkout 已满足（INV=37/STORAGE=44）；本项转为**验证 + W7+ 覆盖度检查**，必要时从 ARB `consts_general.py::inventory_bags_dict` + `inventory_other_sources_dict` 刷新（形态不同，需转换，见附录 A）。

### P1 — 应做（W7+ 覆盖与补充维度）

- **P1-a** 尽量覆盖 W7+ 数据：确认 `StarSigns` 列表含 W7 Cosmos 系（`Chronus_Cosmos`/`Hydron_Cosmos`/`Seraph_Cosmos` 等 passive starsigns，见 `consts_w1.py::passive_starsigns`），与硬编码 `starsign_ids`（35 项）对齐/补全。
- **P1-b** 抽取更多 wiki 维度以增厚数据：
  - items 维度 → `generated/raw_item_data.py::raw_item_data` / `consts_item_data.py::ITEM_DATA`（注：`itemNames.json` 已在 maps 中，wiki 维度为可选增厚）。
  - vials / stamps 维度：若对应 maps 文件偏薄则补充（当前 `vial_names=42`、`stamp_names=[35,36,20]` 已正常，低优先级）。

### P2 — 可选（次来源评估）

- **P2-a** 评估 `idleon-efficiency-main` 的 `data/domain/data/*Repo.ts`（TypeScript）能否转写补充（格式不同，需成本收益评估，见 §7-Q4）。
- **P2-b** 评估 `IdleonToolbox-main/public/` 的 PNG 图片资源是否纳入（多为图片，非结构化静态数据，预期价值低）。

---

## 5. 范围边界（Out-of-scope）

明确**不**在本任务内：

- ❌ 改动冻结核心：`idleon_saver/ldb.py`、`idleon_saver/stencyl/{common,decoder,encoder}.py`（硬约束）。
- ❌ 改动 CLI（`cli.py` / `scripts/*` argparse）。
- ❌ 重写导出层（`exporters/*`、`get_cards` 逻辑本身）——只消费新增 vendored 数据。
- ❌ GUI 现代化（`gui/*`）。
- ❌ 改动 `data/__init__.py` 的防御性加载契约（T2 已落地，只新增被加载的数据文件）。
- ❌ 修复 GEM 背包留空（wiki-only，已文档化，非缺陷）。

---

## 6. 验收标准（可测试）

1. `import idleon_saver.data` 控制台**不再**出现 `Vendored data directory missing` 与 `No StarSigns wiki data` 两条 warning。
2. `bag_maps[Bags.INV]` 与 `bag_maps[Bags.STORAGE]` 均**非空**（当前基线已满足，回归不退化）。
3. `wiki_data` 非空，且 `starsign_names` 来自 `wiki_data["StarSigns"]`（`starsign_names == list(starsign_ids.keys())` 必须为 **False**，即不再回退）。
4. 原有 **29 个测试**仍全部通过（回归：`test_stencyl` / `test_scripts` / `test_export` / `test_gui` + `test_data` 等）。
5. 新增 vendored 文件可被 `_load_json_files(..., recursive=True)` 正常加载，且不破坏防御性契约（缺失即 `{}`+warning，永不 raise）。

---

## 7. 待确认问题

需主理人/用户拍板或提供信息：

- **Q1（最高优先级）**：`bag_maps=0/0` 在当前 checkout **未复现**——`bags.json` 已存在且 `bag_maps[INV]=37/STORAGE=44`。任务描述的 0/0 疑似基于更早 checkout。请确认：
  (a) 是否仍要从 ARB 重新生成/刷新 `bags.json` 以覆盖 W7+？还是保留现有文件、仅做验证即可？
- **Q2**：`wiki_data["StarSigns"]` 在 ARB 中为 `consts_w1.py::StarSigns`（list-of-lists `[name, b1, b2, b3]`），而 idleon-saver 期望 list-of-`{name}`。请确认转换脚本**只取 name 字段**即可，并确认 ARB 列表是否覆盖全部星座（含 W7 Cosmos 系），否则 `starsign_names` 与 `starsign_ids` 顺序/数量可能错位。
- **Q3**：`EnemyDetails` 在 ARB 中**无同名键**；最接近的是 `monster_data`（怪物名/属性）。请确认导出层 `get_cards` 实际读取 `EnemyDetails` 的哪些字段，以决定如何映射/转换（避免生成了 JSON 却用不上）。
- **Q4**：`idleon-efficiency` 的 TypeScript `Repo.ts` 数据是否值得投入转写？请评估成本收益（格式差异大，P2 级）。

---

## 附录 A：数据源映射（IdleOnAutoReviewBot 主来源）

| idleon-saver 维度 | 目标文件 | ARB 来源 | ARB 形态 → 目标形态 | 转换难度 |
|---|---|---|---|---|
| `wiki_data["StarSigns"]` | `vendored/wiki/StarSigns.json` | `consts/consts_w1.py::StarSigns` | list-of-lists `[name,b1,b2,b3]` → list-of-`{name}` | 低（取 name） |
| `wiki_data["EnemyDetails"]` | `vendored/wiki/EnemyDetails.json` | `consts/generated/monster_data.py::monster_data` + `consts_monster_data.py` | dict（怪物定义）→ 待定（见 Q3） | 中（字段待定） |
| items（增厚，可选） | `vendored/wiki/items.json`（或复用 maps/itemNames.json） | `consts/generated/raw_item_data.py::raw_item_data` / `consts_item_data.py::ITEM_DATA` | dict → 名称表 | 低 |
| `bags.json`（验证/刷新） | `vendored/maps/bags.json` | `consts/consts_general.py::inventory_bags_dict` + `inventory_other_sources_dict` | `{index:count}` + 源字典 → `{inventory:[{index,id,name}], storage:[...]}` | 中（形态差异大） |

**次来源**（低优先级/格式不同）：
- `idleon-efficiency-main/data/domain/data/*Repo.ts`（TypeScript，需转写）→ P2-a。
- `IdleonToolbox-main/public/`（多为 PNG 图片）→ P2-b，预期价值低。

---

*本增量 PRD 仅描述变更与数据补齐，不改动任何代码或冻结文件。所有代码/数据落地由后续「增量设计」与「工程实现」任务承接。*
