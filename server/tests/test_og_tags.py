import urllib.parse


def run_scan(client, api_headers, items):
    start_res = client.post("/api/network/scan/start", headers=api_headers)
    token = start_res.get_json()["scan_token"]
    client.post("/api/network/scan/batch", json={"items": items, "scan_token": token}, headers=api_headers)
    return client.post(
        "/api/network/scan/finish",
        json={"scan_token": token, "chunks_sent": 1, "total_errors": 0},
        headers=api_headers)


class TestAllFourRoutesServeSuccessfully:
    def test_root(self, client):
        assert client.get("/").status_code == 200

    def test_crafts(self, client):
        assert client.get("/crafts").status_code == 200

    def test_power(self, client):
        assert client.get("/power").status_code == 200

    def test_network(self, client):
        assert client.get("/network").status_code == 200

    def test_network_item(self, client):
        assert client.get("/network/item/minecraft:iron_ingot:0").status_code == 200


class TestCraftsOgTags:
    def test_no_data_yet(self, client):
        html = client.get("/crafts").get_data(as_text=True)
        assert '<meta property="og:title" content="Crafts">' in html
        assert "No crafting data yet" in html

    def test_reflects_busy_count(self, client, api_headers):
        client.post(
            "/api/crafts",
            json={"source": "me_controller", "jobs": [
                {"name": "W01", "busy": True}, {"name": "W02", "busy": False}]},
            headers=api_headers)
        html = client.get("/crafts").get_data(as_text=True)
        assert "1 of 2 crafting CPUs busy" in html


class TestPowerOgTags:
    def test_no_data_yet(self, client):
        html = client.get("/power").get_data(as_text=True)
        assert '<meta property="og:title" content="Power">' in html
        assert "No power data yet" in html
        assert "og:image" not in html  # no chart without any real data

    def test_reflects_real_reading(self, client, api_headers):
        client.post("/api/power", json={"stored": 3500000, "capacity": 5000000}, headers=api_headers)
        html = client.get("/power").get_data(as_text=True)
        assert "70.0%" in html
        assert 'og:image" content="' in html
        assert "/api/power/chart.png" in html

    def test_has_theme_color_and_site_name(self, client):
        html = client.get("/power").get_data(as_text=True)
        assert 'og:site_name" content="GTNH Monitor"' in html
        assert 'name="theme-color"' in html


class TestNetworkOgTags:
    def test_no_scan_yet(self, client):
        html = client.get("/network").get_data(as_text=True)
        assert "No network scan yet" in html

    def test_reflects_item_and_fluid_counts(self, client, api_headers):
        run_scan(client, api_headers, [
            {"name": "Iron Ingot", "size": 500, "mod": "minecraft", "internal": "iron_ingot", "damage": 0, "kind": "item"},
            {"name": "Molten Silicone", "size": 9500, "mod": None, "internal": "molten.silicone", "damage": None, "kind": "fluid"},
        ])
        html = client.get("/network").get_data(as_text=True)
        assert "1 items, 1 fluids tracked" in html


class TestNetworkItemOgTags:
    def _item_path(self, mod, internal, damage, kind):
        if kind == "fluid":
            # A fluid URL is the BARE internal name, no colon at all -
            # that's the exact signal the real parser uses to infer
            # kind=fluid vs kind=item. Building an item-shaped path
            # (mod:internal) here even with an empty mod would put a
            # colon in the URL and get misparsed as an item instead.
            return "/network/item/" + urllib.parse.quote(str(internal), safe="")
        parts = [mod or "", internal]
        if damage:
            parts.append(str(damage))
        return "/network/item/" + ":".join(urllib.parse.quote(str(p), safe="") for p in parts)

    def test_title_is_the_item_name_not_prefixed(self, client, api_headers):
        run_scan(client, api_headers, [
            {"name": "Neutronium Ingot", "size": 42, "mod": "gregtech", "internal": "gt.metaitem.01",
             "damage": 11129, "kind": "item"},
        ])
        html = client.get(self._item_path("gregtech", "gt.metaitem.01", 11129, "item")).get_data(as_text=True)
        assert '<meta property="og:title" content="Neutronium Ingot">' in html
        assert "Currently stored: 42" in html

    def test_fluid_shows_mb_unit(self, client, api_headers):
        run_scan(client, api_headers, [
            {"name": "Molten Silicone", "size": 9500, "mod": None, "internal": "molten.silicone",
             "damage": None, "kind": "fluid"},
        ])
        html = client.get(self._item_path(None, "molten.silicone", None, "fluid")).get_data(as_text=True)
        assert "Currently stored: 9,500 mB" in html

    def test_unknown_item_still_serves_a_reasonable_fallback(self, client):
        html = client.get(self._item_path("nomod", "nothing_here", 0, "item")).get_data(as_text=True)
        assert '<meta property="og:title" content="Item">' in html

    def test_og_url_includes_the_full_item_path_not_just_bare_network(self, client, api_headers):
        # Regression check for a real fix: og:url used to drop the item
        # identifier from the path entirely.
        run_scan(client, api_headers, [
            {"name": "Iron Ingot", "size": 500, "mod": "minecraft", "internal": "iron_ingot",
             "damage": 0, "kind": "item"},
        ])
        path = self._item_path("minecraft", "iron_ingot", 0, "item")
        html = client.get(path).get_data(as_text=True)
        assert f'og:url" content="http://localhost{path}"' in html
