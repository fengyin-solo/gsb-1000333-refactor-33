"""管沟风险判定测试。

覆盖三类容易出分歧的场景：缺失数据、临界阈值、旧记录与新规则并存，
并验证采集/展示/处置三个阶段（service 各出口）拿到的是同一份结论。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import trench as trench_service_module  # noqa: E402
from app.services.trench_risk import (  # noqa: E402
    GAS_HIGH_PCT,
    RULE_VERSION,
    WATER_HIGH_CM,
    apply_action,
    attach_risk,
    evaluate_trench,
)
from app.store import store  # noqa: E402

MODULE = "trench"


def base_entry(**overrides: object) -> dict[str, object]:
    entry = {
        "id": 99,
        "管沟编号": "TREN-T",
        "管沟位置": "测试沟段",
        "沟内管线": "供水管线",
        "积水情况": "无积水",
        "盖板完好": "完好",
        "气体浓度": "0%LEL",
    }
    entry.update(overrides)
    return entry


class EvaluateRiskTest(unittest.TestCase):
    def test_all_normal_is_low(self) -> None:
        result = evaluate_trench(base_entry())
        self.assertEqual(result.level, "低风险")
        self.assertEqual(result.missing_fields, [])

    def test_missing_each_factor_defaults_to_medium(self) -> None:
        # 关键观测字段缺失：结论必须确定（中风险）且说明缺了什么。
        for blank in (None, "", "   "):
            result = evaluate_trench(base_entry(积水情况=blank))
            self.assertEqual(result.level, "中风险")
            self.assertIn("积水情况", result.missing_fields)
            self.assertTrue(result.factors["积水"].missing)

    def test_missing_required_pipeline_is_medium_with_reason(self) -> None:
        result = evaluate_trench(base_entry(沟内管线=""))
        self.assertEqual(result.level, "中风险")
        self.assertIn("沟内管线", result.missing_fields)
        self.assertTrue(any("缺失" in reason for reason in result.reasons))

    def test_unparseable_value_is_medium_not_crash(self) -> None:
        result = evaluate_trench(base_entry(气体浓度="昨天好像有点味"))
        self.assertEqual(result.level, "中风险")
        self.assertTrue(result.factors["气体"].missing)

    def test_water_boundary_is_deterministic(self) -> None:
        # 临界口径：<40 中，==40（>=）高，两个紧邻值结论必须明确不模糊。
        self.assertEqual(evaluate_trench(base_entry(积水情况="39.9cm")).level, "中风险")
        self.assertEqual(
            evaluate_trench(base_entry(积水情况=f"{WATER_HIGH_CM:g}cm")).level, "高风险"
        )
        self.assertEqual(evaluate_trench(base_entry(积水情况="0cm")).level, "低风险")

    def test_gas_boundary_is_deterministic(self) -> None:
        self.assertEqual(
            evaluate_trench(base_entry(气体浓度=f"{GAS_HIGH_PCT - 0.1}%LEL")).level, "中风险"
        )
        self.assertEqual(
            evaluate_trench(base_entry(气体浓度=f"{GAS_HIGH_PCT:g}%LEL")).level, "高风险"
        )

    def test_overall_takes_worst_factor(self) -> None:
        result = evaluate_trench(base_entry(盖板完好="破损", 气体浓度="25%LEL"))
        self.assertEqual(result.level, "高风险")

    def test_high_consequence_pipeline_medium_when_alone(self) -> None:
        # 燃气管线本身只把权重抬到中风险，配合另一个中风险仍是中风险，不会越级。
        result = evaluate_trench(base_entry(沟内管线="燃气管线"))
        self.assertEqual(result.level, "中风险")

    def test_result_is_pure_and_repeatable(self) -> None:
        entry = base_entry(积水情况="50cm")
        first = evaluate_trench(entry).to_snapshot()
        second = evaluate_trench(entry).to_snapshot()
        self.assertEqual(first, second)
        self.assertNotIn("风险等级", entry)  # 纯函数不污染入参


class MitigationTest(unittest.TestCase):
    def test_action_lowers_factor_until_source_updated(self) -> None:
        entry = base_entry(积水情况="50cm")
        self.assertEqual(evaluate_trench(entry).level, "高风险")
        apply_action(entry, "疏排积水")
        self.assertEqual(evaluate_trench(entry).level, "低风险")
        self.assertIn("疏排积水", evaluate_trench(entry).applied_mitigations)

        # 源字段更新（新一轮采集）：旧处置作废，按新观测判定。
        entry["积水情况"] = "50cm（雨后回升）"
        result = evaluate_trench(entry)
        self.assertEqual(result.level, "高风险")
        self.assertIn("疏排积水", result.expired_mitigations)

    def test_expired_mitigation_does_not_revive(self) -> None:
        entry = base_entry(积水情况="50cm")
        apply_action(entry, "疏排积水")
        attach_risk(entry)  # 出口处固化过期状态
        entry["积水情况"] = "50cm（雨后回升）"
        attach_risk(entry)
        # 即使把值改回处置时的快照，粘性 expired 标记也不允许结论复活。
        entry["积水情况"] = "50cm"
        again = attach_risk(entry)
        self.assertEqual(again["风险等级"], "高风险")

    def test_unrelated_update_keeps_mitigation(self) -> None:
        entry = base_entry(积水情况="50cm")
        apply_action(entry, "疏排积水")
        entry["管沟位置"] = "更正后的位置"  # 位置更新不影响积水处置覆盖
        result = evaluate_trench(entry)
        self.assertEqual(result.level, "低风险")
        self.assertEqual(result.expired_mitigations, [])


class ServiceConsistencyTest(unittest.TestCase):
    def setUp(self) -> None:
        # 每个用例从种子数据重新开始。
        store.__init__()
        self.service = trench_service_module.TrenchService()

    def test_views_share_same_conclusion(self) -> None:
        entry, missing = self.service.create_entry(
            {
                "管沟编号": "TREN-C",
                "管沟位置": "一致性测试沟段",
                "沟内管线": "燃气管线",
                "积水情况": "40cm",
                "气体浓度": "0%LEL",
                "盖板完好": "完好",
            }
        )
        self.assertEqual(missing, [])
        entry_id = int(entry["id"])
        expected = "高风险"  # 积水正好临界高风险
        self.assertEqual(self.service.get_entry(entry_id)["风险等级"], expected)
        items, _ = self.service.list_entries(page=1, size=100)
        listed = next(row for row in items if row["id"] == entry_id)
        self.assertEqual(listed["风险等级"], expected)
        export_items, _ = self.service.list_entries(page=1, size=10000)
        exported = next(row for row in export_items if row["id"] == entry_id)
        self.assertEqual(exported["风险等级"], expected)
        self.assertEqual(self.service.risk_summary()["高风险"] >= 1, True)

    def test_old_record_recomputed_at_every_exit(self) -> None:
        # 种子 id=4 带着旧版规则的「低风险」缓存，但观测字段缺失，应重算为中风险。
        old = store.find(MODULE, 4)
        self.assertNotEqual(old["风险判定"]["rule_version"], RULE_VERSION)
        items, _ = self.service.list_entries(page=1, size=100)
        list_view = next(row for row in items if row["id"] == 4)
        for view in (self.service.get_entry(4), list_view):
            self.assertEqual(view["风险等级"], "中风险")
            self.assertEqual(view["风险判定"]["rule_version"], RULE_VERSION)
            self.assertTrue(view["风险说明"])  # 可解释：有中文理由

    def test_seed_levels(self) -> None:
        summary = self.service.risk_summary()
        self.assertEqual(self.service.get_entry(1)["风险等级"], "低风险")
        self.assertEqual(self.service.get_entry(2)["风险等级"], "中风险")  # 35cm/10%LEL 临界以下
        self.assertEqual(self.service.get_entry(3)["风险等级"], "高风险")  # 40cm/25%LEL 临界值
        self.assertEqual(self.service.get_entry(4)["风险等级"], "中风险")  # 旧记录+缺数据
        self.assertEqual(summary["合计"], 4)

    def test_update_recomputes_and_invalidates_mitigation(self) -> None:
        entry, _ = self.service.create_entry(
            {
                "管沟编号": "TREN-U",
                "管沟位置": "更新测试",
                "沟内管线": "排水管线",
                "积水情况": "60cm",
                "盖板完好": "完好",
                "气体浓度": "0%LEL",
            }
        )
        entry_id = int(entry["id"])
        self.assertEqual(self.service.get_entry(entry_id)["风险等级"], "高风险")

        self.service.run_action(entry_id, "疏排积水")
        self.assertEqual(self.service.get_entry(entry_id)["风险等级"], "低风险")

        updated, missing = self.service.update_entry(entry_id, {"积水情况": "40cm"})
        self.assertEqual(missing, [])
        self.assertEqual(updated["风险等级"], "高风险")
        self.assertTrue(updated["风险判定"]["expired_mitigations"])

    def test_recollect_same_value_invalidates_mitigation(self) -> None:
        # 处置后即便新观测值与处置前相同（雨后水位又涨回 40cm），
        # 只要采集了新值，旧处置结论也必须作废。
        entry, _ = self.service.create_entry(
            {
                "管沟编号": "TREN-X",
                "管沟位置": "同值复测沟段",
                "沟内管线": "排水管线",
                "积水情况": "40cm",
                "盖板完好": "完好",
                "气体浓度": "0%LEL",
            }
        )
        entry_id = int(entry["id"])
        self.service.run_action(entry_id, "疏排积水")
        self.assertEqual(self.service.get_entry(entry_id)["风险等级"], "低风险")
        updated, _ = self.service.update_entry(entry_id, {"积水情况": "40cm"})
        self.assertEqual(updated["风险等级"], "高风险")
        self.assertIn("疏排积水", updated["风险判定"]["expired_mitigations"])

    def test_action_with_missing_other_factors_stays_medium(self) -> None:
        # 只疏排了积水，但气体、盖板数据缺失：不能因此给出低风险结论。
        entry, _ = self.service.create_entry(
            {"管沟编号": "TREN-W", "管沟位置": "数据不全沟段", "沟内管线": "排水管线", "积水情况": "60cm"}
        )
        self.service.run_action(int(entry["id"]), "疏排积水")
        view = self.service.get_entry(int(entry["id"]))
        self.assertEqual(view["风险等级"], "中风险")
        self.assertTrue(view["风险判定"]["missing_fields"])

    def test_update_required_field_cannot_be_blanked(self) -> None:
        entry, _ = self.service.create_entry(
            {"管沟编号": "TREN-V", "管沟位置": "x", "沟内管线": "排水管线"}
        )
        _, missing = self.service.update_entry(int(entry["id"]), {"沟内管线": "  "})
        self.assertIn("沟内管线", missing)

    def test_overview_matches_risk_summary(self) -> None:
        # 看板（overview）与管沟模块自身统计必须同口径。
        trench_overview = next(item for item in store.overview()["modules"] if item["name"] == MODULE)
        summary = self.service.risk_summary()
        self.assertEqual(trench_overview["abnormal"], summary["高风险"])
        self.assertEqual(trench_overview["pending"], summary["待处理"])

    def test_filter_by_risk_level(self) -> None:
        items, total = self.service.list_entries(risk_level="高风险", page=1, size=100)
        self.assertTrue(items)
        self.assertTrue(all(row["风险等级"] == "高风险" for row in items))

    def test_snapshot_byte_identical_across_reads(self) -> None:
        first = self.service.get_entry(3)["风险判定"]
        second = self.service.get_entry(3)["风险判定"]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
