"""独立增量验收（PRD: incremental-prd-data.md §6）。

由 QA（严过关）编写，独立于工程师 test_data.py 的视角，覆盖 §6 五项验收：
  #1 导入无两条 warning（logging WARNING + warnings 双重捕获）
  #2 bags 回归不变（INV/STORAGE 非空，且 == 37 / 44 基线）
  #3 starsign_names 来自 wiki 且前 57 项与 starsign_ids 顺序一致
  #5 新加载测试：StarSigns/EnemyDetails 非空且 EnemyDetails 含 Name
  + 主理人独立核实项：get_starsign_from_index(0)=="1"、index 57 不抛 KeyError

#4（原有可用测试不回归）由 test_data/converters/cli/export 的本机 green 部分覆盖。

运行：pytest tests/test_acceptance_wiki_data.py --noconftest -q
"""

import importlib
import logging
import warnings

import pytest

import idleon_saver.data as data_module
from idleon_saver.core.parsers import get_starsign_from_index
from idleon_saver.data import (
    Bags,
    bag_maps,
    starsign_ids,
    starsign_names,
    wiki_data,
)


def test_acceptance_1_no_two_warnings(caplog):
    """#1: import/reload idleon_saver.data 不再发出两条降级 warning。

    同时捕获 logging 的 WARNING 与 warnings 模块的 warning，
    确保 'Vendored data directory missing' 与 'No StarSigns wiki data'
    两条都不会出现。
    """
    with warnings.catch_warnings(record=True) as wlist:
        warnings.simplefilter("always")
        with caplog.at_level(logging.WARNING):
            importlib.reload(data_module)
    messages = [r.message for r in caplog.records] + [
        str(w.message) for w in wlist
    ]
    assert not any("Vendored data directory missing" in m for m in messages)
    assert not any("No StarSigns wiki data" in m for m in messages)


def test_acceptance_2_bags_regression():
    """#2: bag_maps[INV]/[STORAGE] 非空且回归基线不变（本次未改 bags.json）。"""
    inv = bag_maps[Bags.INV]
    storage = bag_maps[Bags.STORAGE]
    assert inv, "bag_maps[Bags.INV] 应为非空"
    assert storage, "bag_maps[Bags.STORAGE] 应为非空"
    assert len(inv) == 37, f"INV 应回归为 37，实际 {len(inv)}"
    assert len(storage) == 44, f"STORAGE 应回归为 44，实际 {len(storage)}"
    # GEM 故意留空（wiki-only），不崩溃。
    assert bag_maps[Bags.GEM] == {}


def test_acceptance_3_starsign_from_wiki():
    """#3: starsign_names 来自 wiki 而非回退，且前 57 项与 starsign_ids 顺序一致。"""
    ids_keys = list(starsign_ids.keys())
    assert starsign_names != ids_keys, "不应再回退到硬编码 ids"
    assert len(starsign_names) >= len(ids_keys)
    assert (
        starsign_names[: len(ids_keys)] == ids_keys
    ), "前 57 项必须保持 starsign_ids 的权威顺序"
    # 主理人核实项：index 0 命中 canonical '1'，index 57（wiki-only）不抛 KeyError。
    assert get_starsign_from_index(0) == "1"
    wiki_only_index = len(ids_keys)
    wiki_only_name = starsign_names[wiki_only_index]
    assert get_starsign_from_index(wiki_only_index) == wiki_only_name


def test_acceptance_5_wiki_loaded():
    """#5: 新增 vendored 文件被正常加载，EnemyDetails 含 Name。"""
    assert isinstance(wiki_data, dict) and wiki_data, "wiki_data 应非空"
    stars = wiki_data.get("StarSigns", [])
    assert isinstance(stars, list) and len(stars) == 94, (
        f"StarSigns 应为 94 项 list，实际 {len(stars) if hasattr(stars, '__len__') else type(stars)}"
    )
    assert all(isinstance(s, dict) and "name" in s for s in stars)
    names = [s["name"] for s in stars]
    assert "Chronus Cosmos" in names, "应含 W7 Cosmos 被动星座"
    assert "Hydron Cosmos" in names, "应含 W7 Cosmos 被动星座"

    enemy = wiki_data.get("EnemyDetails", {})
    assert isinstance(enemy, dict) and enemy, "EnemyDetails 应非空"
    assert len(enemy) == 405, f"EnemyDetails 应 405 条，实际 {len(enemy)}"
    assert "Bandit_Bob" in enemy
    assert enemy["Bandit_Bob"]["Name"] == "Bandit Bob"
    assert all("Name" in v for v in enemy.values())
