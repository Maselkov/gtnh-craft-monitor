import app as app_module
from gcm import charts, db, icons


class TestItemKey:
    def test_item_with_damage(self):
        assert db.item_key("gregtech", "gt.blockmachines", 123, "item") == "gregtech|gt.blockmachines|123|item"

    def test_fluid_no_mod_no_damage(self):
        # Confirmed real shape from the Cryotheum investigation - mod and
        # damage are both None for a fluid, not empty string or 0.
        assert db.item_key(None, "cryotheum", None, "fluid") == "|cryotheum||fluid"

    def test_damage_zero_is_not_the_same_as_damage_none(self):
        # damage=0 is a real, valid value (the common case for
        # non-variant items) and must not collapse to the same key as
        # damage=None (a fluid, or genuinely absent).
        key_zero = db.item_key("minecraft", "stone", 0, "item")
        key_none = db.item_key("minecraft", "stone", None, "item")
        assert key_zero != key_none
        assert key_zero == "minecraft|stone|0|item"
        assert key_none == "minecraft|stone||item"

    def test_missing_kind_defaults_to_item(self):
        assert db.item_key("mod", "internal", 0, None) == "mod|internal|0|item"


class TestParseItemUrlPath:
    def test_item_with_damage(self):
        result = app_module._parse_item_url_path("gregtech:gt.blockmachines:123")
        assert result == {"mod": "gregtech", "internal": "gt.blockmachines", "damage": 123, "kind": "item"}

    def test_item_without_damage_defaults_to_zero(self):
        result = app_module._parse_item_url_path("minecraft:stone")
        assert result["damage"] == 0
        assert result["kind"] == "item"

    def test_fluid_bare_internal_no_colon(self):
        # The exact real-world case that motivated the clean-URL feature.
        result = app_module._parse_item_url_path("molten.silicone")
        assert result == {"mod": None, "internal": "molten.silicone", "damage": None, "kind": "fluid"}

    def test_matches_the_frontend_js_parser_shape(self):
        # Both sides need to agree exactly - this mirrors the manual
        # cross-check already done for the real feature (see the
        # conversation history for the JS-side equivalent test).
        assert app_module._parse_item_url_path("cryotheum") == {
            "mod": None, "internal": "cryotheum", "damage": None, "kind": "fluid",
        }


class TestFormatQtyPy:
    def test_thousands(self):
        assert charts.format_qty(1500) == "1.5k"

    def test_millions(self):
        assert charts.format_qty(2_500_000) == "2.50M"

    def test_billions(self):
        assert charts.format_qty(3_000_000_000) == "3.00B"

    def test_trillions(self):
        assert charts.format_qty(4_000_000_000_000) == "4.00T"

    def test_small_numbers_unabbreviated(self):
        assert charts.format_qty(42) == "42"

    def test_none_is_zero(self):
        assert charts.format_qty(None) == "0"

    def test_negative_numbers(self):
        # Discharging power trend can genuinely be negative.
        result = charts.format_qty(-500)
        assert result.startswith("-")


class TestDownsample:
    def test_under_the_cap_returns_unchanged(self):
        rows = [(i, i * 10, 1000) for i in range(10)]
        result = app_module._downsample(rows, max_points=100)
        assert result == rows

    def test_over_the_cap_reduces_point_count(self):
        rows = [(i, i * 10, 1000) for i in range(1000)]
        result = app_module._downsample(rows, max_points=100)
        assert len(result) <= 100

    def test_averaging_preserves_overall_range(self):
        # Bucket-averaging shouldn't invent values wildly outside the
        # real data's own min/max.
        rows = [(i, i * 10, 1000) for i in range(1000)]
        result = app_module._downsample(rows, max_points=50)
        stored_values = [r[1] for r in result]
        assert min(stored_values) >= 0
        assert max(stored_values) <= 9990


class TestDownsampleSteps:
    def test_under_the_cap_returns_unchanged(self):
        rows = [(i, i) for i in range(5)]
        result = app_module._downsample_steps(rows, max_points=100)
        assert result == rows

    def test_picks_real_recorded_values_not_averages(self):
        # Unlike _downsample (power, continuous signal), this must
        # preserve GENUINE observed values, not invent averaged ones -
        # item quantity is a step function.
        rows = [(i, 100 if i % 2 == 0 else 999999) for i in range(1000)]
        result = app_module._downsample_steps(rows, max_points=50)
        observed_values = {r[1] for r in rows}
        for _, size in result:
            assert size in observed_values


class TestResolveIcon:
    def test_item_key_lookup(self, monkeypatch):
        monkeypatch.setattr(icons, "_icons_by_key", {"gregtech:gt.blockmachines:123": "path/to/icon.png"})
        monkeypatch.setattr(icons, "_fluids_by_key", {})
        monkeypatch.setattr(icons, "_icons_by_label", {})
        result = icons.resolve_icon("gregtech", "gt.blockmachines", 123, "Robot Arm")
        assert result == "path/to/icon.png"

    def test_fluid_lookup_no_mod(self, monkeypatch):
        # Confirmed real shape: fluids resolve via fluids_by_key using
        # just the bare internal name, since mod is None for a fluid.
        monkeypatch.setattr(icons, "_icons_by_key", {})
        monkeypatch.setattr(icons, "_fluids_by_key", {"cryotheum": "path/to/cryotheum.png"})
        monkeypatch.setattr(icons, "_icons_by_label", {})
        result = icons.resolve_icon(None, "cryotheum", None, "Cryotheum")
        assert result == "path/to/cryotheum.png"

    def test_falls_back_to_label(self, monkeypatch):
        monkeypatch.setattr(icons, "_icons_by_key", {})
        monkeypatch.setattr(icons, "_fluids_by_key", {})
        monkeypatch.setattr(icons, "_icons_by_label", {"Some Item": "path/to/fallback.png"})
        result = icons.resolve_icon("unknownmod", "unknown_internal", 0, "Some Item")
        assert result == "path/to/fallback.png"

    def test_no_match_returns_none(self, monkeypatch):
        monkeypatch.setattr(icons, "_icons_by_key", {})
        monkeypatch.setattr(icons, "_fluids_by_key", {})
        monkeypatch.setattr(icons, "_icons_by_label", {})
        result = icons.resolve_icon("x", "y", 0, "z")
        assert result is None
