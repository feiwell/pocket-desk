"""Synthetic-only offline layout tests: python3 -m unittest discover -s tests -v."""
from copy import deepcopy
from datetime import datetime
import unittest

from pocketdesk import layout, rules
from tests.synthetic import assignments, folder, icon, state


class LayoutTests(unittest.TestCase):
    def test_dock_is_not_a_page(self):
        source = state()
        self.assertEqual(layout.dock_of(source), source[0])
        self.assertEqual(layout.pages_of(source), source[1:])
        self.assertEqual(len(layout.collect_icons(source, True)), 9)
        self.assertEqual(len(layout.collect_icons(source)), 5)

    def test_build_packs_and_preserves_all_icons_and_dock_fields(self):
        source = state()
        source[0][0]["customField"] = "preserve"
        plan = layout.build_layout(source, {"New": ["test.new.a", "test.new.b"]}, page_capacity=2)
        self.assertEqual(layout.verify(source, plan), [])
        self.assertEqual(source[0], plan[0])
        self.assertTrue(all(len(page) == 2 for page in plan[1:-1]))
        self.assertTrue(all(len(page) <= 2 for page in plan[1:]))
        folders = [item for page in plan[1:] for item in page if item.get("listType") == "folder"]
        self.assertTrue(folders)
        self.assertNotIn("iconList", folders[0])
        self.assertIsInstance(folders[0]["iconLists"][0], list)

    def test_rule_sets_load(self):
        self.assertTrue({"fine", "default"} <= set(rules.available()))
        for name in rules.available():
            groups, first, _ = rules.load(name)
            self.assertTrue(groups)
            self.assertIsInstance(first, list)

    def test_unknown_apps_survive_and_identifier_needs_no_display_name(self):
        source = state()
        del source[-1][0]["displayName"]
        self.assertEqual(layout.verify(source, layout.build_layout(source)), [])

    def test_build_rejects_widgets_duplicates_and_invalid_capacity(self):
        for bad in ([{"listType": "widget"}], [icon("test.new.a")]):
            source = state()
            source.append(bad)
            with self.assertRaises(ValueError):
                layout.build_layout(source)
        with self.assertRaises(ValueError):
            layout.build_layout(state(), page_capacity=0)

    def test_verify_rejects_dock_mutation(self):
        for change in ("order", "field", "missing"):
            source, after = state(), state()
            if change == "order":
                after[0].reverse()
            elif change == "field":
                after[0][0]["displayName"] = "Changed"
            else:
                after[0].pop()
            self.assertTrue(layout.verify(source, after), change)

    def test_verify_rejects_missing_extra_and_duplicate_icons(self):
        for change in ("missing", "extra", "duplicate", "dock_duplicate"):
            source, after = state(), state()
            if change == "missing":
                after[-1].pop()
            elif change == "extra":
                after[-1].append(icon("test.unknown"))
            elif change == "duplicate":
                after[-1].append(icon("test.new.a"))
            else:
                after[-1].append(deepcopy(source[0][0]))
            self.assertTrue(layout.verify(source, after), change)
        before = state()
        before[-1].append(icon("test.new.a"))
        self.assertTrue(layout.verify(before, state()))

    def test_verify_readback_rejects_wrong_folder_order_and_page_boundary(self):
        before = state()
        expected = layout.merge_layout(before, assignments())
        wrong_folder = deepcopy(expected)
        wrong_folder[1][1]["iconLists"][0].append(wrong_folder[1][0]["iconLists"][0].pop())
        wrong_order = deepcopy(expected)
        wrong_order[1].reverse()
        wrong_page = deepcopy(expected)
        wrong_page.append([wrong_page[1].pop()])
        for actual in (wrong_folder, wrong_order, wrong_page):
            self.assertEqual(layout.verify(before, actual), [])  # counts alone pass
            self.assertTrue(layout.verify(before, actual, expected=expected))
        self.assertEqual(layout.verify(before, deepcopy(expected), expected=expected), [])

    def test_readback_allows_dock_rows_and_non_layout_metadata_updates(self):
        source = state()
        actual = deepcopy(source)
        actual[0][0]["displayName"] = "Localized name"
        actual[0][0]["iconModDate"] = datetime(2026, 2, 3)
        actual[0] = [actual[0][:2], actual[0][2:]]
        actual[2][0]["bundleVersion"] = "new-version"
        self.assertEqual(layout.verify(source, actual, expected=source), [])
        self.assertTrue(layout.verify(source, actual))  # raw plans may not change Dock metadata
        actual[0][0].reverse()
        self.assertTrue(layout.verify(source, actual, expected=source))

    def test_verify_normalizes_only_row_wrappers(self):
        source = state()
        rows = deepcopy(source)
        rows[1] = [[source[1][0]], [source[1][1]]]
        self.assertEqual(layout.verify(source, rows, expected=source), [])
        rows.append([])
        self.assertTrue(layout.verify(source, rows, expected=source))


class MergeTests(unittest.TestCase):
    def test_merge_is_pure_preserves_dates_order_metadata_and_clears_tail(self):
        source = state()
        source[0][0]["customField"] = "dock metadata"
        source[1][0]["folderMetadata"] = "keep"
        source[1][0]["iconLists"].append([icon("test.existing.second")])
        original = deepcopy(source)
        result = layout.merge_layout(source, dict(reversed(list(assignments().items()))))
        self.assertEqual(source, original)
        self.assertEqual(result[0], source[0])
        self.assertEqual(len(result), 2)
        self.assertEqual([x["displayName"] for x in result[1]], ["Tools", "Reading"])
        tools = result[1][0]
        self.assertEqual(tools["folderMetadata"], "keep")
        self.assertEqual(tools["iconLists"][0], source[1][0]["iconLists"][0])
        self.assertEqual([x["displayIdentifier"] for x in tools["iconLists"][-1]],
                         ["test.existing.second", "test.new.a", "test.new.c"])
        self.assertIsInstance(tools["iconLists"][-1][1]["iconModDate"], datetime)
        self.assertEqual(layout.verify(source, result), [])
        tools["iconLists"][-1][0]["displayName"] = "changed"
        self.assertEqual(source, original)

    def test_preserves_existing_page_boundaries_holes_and_rows(self):
        source = state()
        source.insert(2, [[source[1].pop()], [False]])
        source[1].append(False)
        result = layout.merge_layout(source, assignments())
        self.assertEqual(len(result), 3)
        self.assertIs(result[1][-1], False)
        self.assertIs(result[2][1][0], False)
        self.assertEqual(result[2][0][0]["displayName"], "Reading")

    def test_empty_tail_and_empty_folder(self):
        source = [[], [folder("Empty")], [], [False]]
        result = layout.merge_layout(source, {})
        self.assertEqual(result, source[:2])
        source[-1] = [icon("test.new")]
        source[1][0]["iconLists"] = []
        result = layout.merge_layout(source, {"test.new": "Empty"})
        self.assertEqual(result[1][0]["iconLists"], [[icon("test.new")]])

    def test_rejects_incomplete_unknown_and_non_loose_assignments(self):
        for mapping in ({}, {**assignments(), "test.unknown": "Tools"},
                        {**assignments(), "test.existing.tool": "Tools"},
                        {**assignments(), "test.dock.0": "Tools"},
                        {**assignments(), "test.new.a": "Not a folder"},
                        [], {"test.new.a": None}):
            source, original = state(), state()
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                layout.merge_layout(source, mapping)
            self.assertEqual(source, original)

    def test_rejects_duplicates_ambiguous_folders_and_mixed_earlier_pages(self):
        variants = []
        duplicate = state()
        duplicate[-1].append(icon("test.new.a"))
        variants.append(duplicate)
        ambiguous = state()
        ambiguous[1].append(folder("Tools", icon("test.other")))
        variants.append(ambiguous)
        early = state()
        early[1].append(early[-1].pop())
        variants.append(early)
        variants.append([[], [icon("test.only")]])
        for source in variants:
            with self.subTest(source=source), self.assertRaises(ValueError):
                layout.merge_layout(source, assignments())

    def test_rejects_unsupported_widget_and_malformed_states_anywhere(self):
        for bad in ({"listType": "widget", "displayIdentifier": "test.widget"},
                    {"widgetKind": "weather", "displayIdentifier": "test.widget"},
                    None, True, "icon", {"iconList": []}, [icon("test.nested")]):
            for location in ("dock", "tail", "folder"):
                source = state()
                target = source[0] if location == "dock" else source[-1]
                if location == "folder":
                    target = source[1][0]["iconLists"][0]
                target.append(bad)
                with self.subTest(bad=bad, location=location), self.assertRaises(ValueError):
                    layout.merge_layout(source, assignments())
        for source in ([], {}, [[], "bad page"], [[], [{"listType": "folder", "displayName": "Bad"}]]):
            with self.assertRaises(ValueError):
                layout.merge_layout(source, {})


if __name__ == "__main__":
    unittest.main()
