import json
import re
import time
import urllib.parse

from gcm import embeds, store


def image_version(html):
    return re.search(r'og:image" content="[^"]*&amp;v=(\w+)"', html).group(1)


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

    def test_no_capacity_shows_just_stored(self, client, api_headers):
        # The wireless network, which power_monitor.lua posts with capacity 0.
        client.post("/api/power", json={"stored": 2e18, "capacity": 0, "machine": "Wireless"}, headers=api_headers)
        html = client.get("/power?machine=Wireless").get_data(as_text=True)
        assert '<meta property="og:description" content="2.00E EU stored">' in html

    def test_image_url_changes_with_the_chart(self, client, api_headers):
        # Discord's media proxy keeps an image by URL: a new reading must
        # give a new one, and no reading the same one.
        client.post("/api/power", json={"stored": 1, "capacity": 4}, headers=api_headers)
        first = image_version(client.get("/power").get_data(as_text=True))
        assert image_version(client.get("/power").get_data(as_text=True)) == first
        client.post("/api/power", json={"stored": 2, "capacity": 4}, headers=api_headers)
        assert image_version(client.get("/power").get_data(as_text=True)) != first

    def test_uses_latest_reading_even_when_older_than_an_hour(self, client, api_headers):
        client.post(
            "/api/power",
            json={"stored": 1000000, "capacity": 4000000, "timestamp": time.time() - 7200},
            headers=api_headers,
        )
        html = client.get("/power").get_data(as_text=True)
        assert "25.0%" in html

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

    def test_essentia(self, client, api_headers):
        run_scan(client, api_headers, [
            {"name": "Ordo", "size": 24900, "mod": None, "internal": "ordo", "damage": None, "kind": "essentia"},
        ])
        html = client.get("/network/item/@ordo").get_data(as_text=True)
        assert '<meta property="og:title" content="Ordo">' in html
        assert 'content="Currently stored: 24,900"' in html  # no unit, unlike a fluid
        assert "1 essentia tracked" in client.get("/network").get_data(as_text=True)

    def test_nbt_variant_url_shows_that_variant(self, client, api_headers):
        from nbt_fixtures import seed_tag
        seed = {"name": "Sugar Beet Seeds", "size": 27, "mod": "cropsnh", "internal": "genericSeed",
                "damage": 0, "kind": "item", "hasTag": True}
        run_scan(client, api_headers, [
            dict(seed, tag=seed_tag("sugarbeet", 1, 1, 1)),
            dict(seed, name="Wheat Seeds", size=3100, tag=seed_tag("wheat", 1, 1, 1)),
        ])
        items = client.get("/api/network").get_json()["items"]
        variant = next(it["variant"] for it in items if it["name"] == "Sugar Beet Seeds")
        html = client.get(f"/network/item/cropsnh:genericSeed:0~{variant}").get_data(as_text=True)
        assert '<meta property="og:title" content="Sugar Beet Seeds">' in html
        assert "Currently stored: 27" in html
        assert f"variant={variant}" in html

    def test_image_url_changes_with_the_chart(self, client, api_headers):
        iron = {"name": "Iron Ingot", "size": 500, "mod": "minecraft", "internal": "iron_ingot",
                "damage": 0, "kind": "item"}
        key = store.items.key_of(iron)
        path = self._item_path("minecraft", "iron_ingot", 0, "item")
        # Only a change from over a day ago: the chart holds it flat, and
        # the held lead row's moving start time mustn't change the URL.
        store.items.add_rows([(key, "Iron Ingot", 400, time.time() - 2 * 86400)])
        first = image_version(client.get(path).get_data(as_text=True))
        assert image_version(client.get(path).get_data(as_text=True)) == first
        store.items.add_rows([(key, "Iron Ingot", 500, time.time())])
        assert image_version(client.get(path).get_data(as_text=True)) != first

    def test_malformed_variant_is_ignored(self, client):
        html = client.get('/network/item/cropsnh:genericSeed:0~"><script>').get_data(as_text=True)
        assert "variant=" not in html

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


def component_embed(html):
    """The page's Discord component embed payload, or None."""
    found = re.findall(r'<script id="discord:component-embed" type="application/json">(.*?)</script>', html)
    assert len(found) <= 1
    return json.loads(found[0]) if found else None


def components(payload):
    """Every component in a payload, the container first."""
    out, todo = [], [payload["component"]]
    while todo:
        c = todo.pop(0)
        out.append(c)
        todo += c.get("components", [])
        if "accessory" in c:
            todo.append(c["accessory"])
    return out


class TestComponentEmbeds:
    def test_accent_is_the_dark_theme_color(self, client):
        # Discord takes the last theme-color, whatever its media query.
        html = client.get("/crafts").get_data(as_text=True)
        assert re.findall(r'name="theme-color" content="([^"]+)"', html)[-1] == "#0f1115"

    def test_crafts_lists_busy_cpus_with_their_output_icon(self, client, api_headers):
        client.post("/api/crafts", json={"source": "me_controller", "jobs": [
            {"name": "W01", "busy": True, "final_output": "Iron Ingot",
             "final_output_mod": "minecraft", "final_output_internal": "iron_ingot", "final_output_damage": 0},
            {"name": "W02", "busy": False}]}, headers=api_headers)
        html = client.get("/crafts").get_data(as_text=True)
        assert 'og:title" content="Crafts"' in html  # the fallback stays
        payload = component_embed(html)
        text = json.dumps(payload)
        assert "W01" in text and "Iron Ingot" in text and "W02" not in text
        button = next(c for c in components(payload) if c["type"] == embeds.BUTTON)
        assert button == {"type": 2, "style": 5, "url": "http://localhost/crafts", "label": "Open"}

    def test_power_and_item_pages_show_their_chart(self, client, api_headers):
        client.post("/api/power", json={"stored": 1, "capacity": 4}, headers=api_headers)
        run_scan(client, api_headers, [
            {"name": "Iron Ingot", "size": 500, "mod": "minecraft", "internal": "iron_ingot", "damage": 0, "kind": "item"},
        ])
        for path, chart in (("/power", "/api/power/chart.png"),
                            ("/network/item/minecraft:iron_ingot:0", "/api/network/history/chart.png")):
            gallery = next(c for c in components(component_embed(client.get(path).get_data(as_text=True)))
                           if c["type"] == embeds.GALLERY)
            assert chart in gallery["items"][0]["media"]["url"], path

    def test_network_page_keeps_the_plain_card(self, client):
        assert component_embed(client.get("/network").get_data(as_text=True)) is None

    def test_names_cant_close_the_tag_or_add_markdown(self, client, api_headers):
        client.post("/api/crafts", json={"source": "me_controller", "jobs": [
            {"name": "</script><b>[x](https://evil)", "busy": True}]}, headers=api_headers)
        html = client.get("/crafts").get_data(as_text=True)
        assert "</script><b>" not in html
        cpu = next(c for c in components(component_embed(html)) if c["type"] == embeds.TEXT and "evil" in c["content"])
        assert cpu["content"] == r"**\</script\>\<b\>\[x\](https\://evil)**"

    def test_names_lose_their_formatting_codes(self, client, api_headers):
        client.post("/api/crafts", json={"source": "me_controller", "jobs": [
            {"name": "CPU", "busy": True, "final_output": "§a16k§r ME Storage Component"}]}, headers=api_headers)
        html = client.get("/crafts").get_data(as_text=True)
        assert any(c["content"] == "**CPU**\n16k ME Storage Component"
                   for c in components(component_embed(html)) if c["type"] == embeds.TEXT)


class TestComponentEmbedLimits:
    def jobs(self, count, name="CPU"):
        return [{"name": f"{name} {i}", "busy": True, "final_output": "Iron Ingot"} for i in range(count)]

    def test_many_cpus_are_capped_with_a_more_line(self):
        payload = embeds.crafts(self.jobs(30), "30 of 30", lambda j: "http://x/i.png", "http://x/crafts")
        listed = [c for c in components(payload) if c["type"] == embeds.SECTION]
        assert len(listed) == embeds.MAX_CPUS
        assert any("+22 more" in c.get("content", "") for c in components(payload))
        assert len(components(payload)) <= 40
        assert embeds.script_tag(payload)

    def test_one_container_and_link_buttons_only(self):
        payload = embeds.item("Iron Ingot", "500", "http://x/i.png", "http://x/c.png", "http://x/p")
        found = components(payload)
        assert [c["type"] for c in found].count(embeds.CONTAINER) == 1
        for button in (c for c in found if c["type"] == embeds.BUTTON):
            assert button["style"] == 5
            assert set(button) <= {"id", "type", "url", "style", "label", "emoji", "disabled"}

    def test_long_cpu_names_list_fewer_cpus(self):
        payload = embeds.crafts(self.jobs(8, "x" * 400), "8 of 8", lambda j: None, "http://x/crafts")
        assert embeds.script_tag(payload)
        assert any("more" in c.get("content", "") for c in components(payload))

    def test_an_oversized_payload_falls_back_to_og(self):
        payload = embeds.item("x" * 3000, "500", None, None, "http://x/p")
        assert embeds.script_tag(payload) == ""

    def test_size_is_counted_after_escaping(self):
        # 2,900 bytes of "<", each sent as the 6-byte <.
        payload = embeds.container([embeds.text("<" * 490)])
        assert len(json.dumps(payload)) < embeds.MAX_BYTES
        assert embeds.script_tag(payload) == ""
