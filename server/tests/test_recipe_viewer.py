from urllib.parse import urlencode

import pytest

from conftest import login_as
from gcm import auth, gamedata, recipe_viewer, recipes, state
from test_mcp_game import game_recipes  # noqa: F401
from test_recipe_index import typed_index  # noqa: F401


@pytest.fixture()
def data(typed_index, monkeypatch):
    monkeypatch.setattr(gamedata, "selected_version", lambda: "2.9.0")
    monkeypatch.setattr(recipes, "index", lambda version: typed_index)
    return typed_index


def request(client, path, **query):
    return client.get(path + '?' + urlencode(query))


def test_header_counts_metadata_and_coverage_work_outside_the_network(client, data):
    response = request(client, '/api/recipes/item', key='minecraft:planks:0')
    assert response.status_code == 200
    body = response.get_json()
    assert body['item']['name'] == 'Planks'
    assert sum(body['making'].values()) == 5
    assert body['maps'][0]['id'] == 'crafting'
    assert body['maps'][0]['machines'][0]['key'] == 'minecraft:crafting_table:0'
    assert body['fingerprint'] == data.fingerprint and body['version'] == '2.9.0'
    assert 'classes' not in body['coverage']['crafting']


def test_pages_present_grids_wildcards_chances_and_fluids(client, data):
    body = request(client, '/api/recipes', key='minecraft:planks:0', limit=2).get_json()
    assert body['total'] == 5 and len(body['recipes']) == 2
    grid = body['recipes'][0]['grid']
    assert (grid['w'], grid['h']) == (2, 2) and grid['slots'][1] is None
    slot = grid['slots'][0]
    assert slot['ore'] == 'logWood' and slot['alternatives'][1]['key'] == 'minecraft:log:*'
    assert slot['alternatives'][1]['damage'] is None and slot['alternatives'][1]['wildcard'] is True
    assert grid['slots'][2]['alternatives_total'] == 20
    assert len(grid['slots'][2]['alternatives']) == 16
    assert 'eut' not in body['recipes'][0] and 'ticks' not in body['recipes'][0]
    assert request(client, '/api/recipes', key='minecraft:planks:0', offset=2).get_json()['recipes'][0]['type'] == 'furnace'
    fluid = request(client, '/api/recipes', key='fluid:mod:fluid').get_json()['recipes'][0]
    assert fluid['outputs'][0]['chance'] == 50
    assert fluid['outputs'][1]['key'] == 'fluid:mod:fluid'


def test_using_matches_all_alternatives_and_category_filter(client, data):
    body = request(client, '/api/recipes', key='example:part:19', dir='using', map='crafting').get_json()
    assert body['total'] == 1 and body['recipes'][0]['type'] == 'shaped'
    body = request(client, '/api/recipes', key='minecraft:log:0', dir='using', map='crafting').get_json()
    assert body['total'] == 2  # exact plus wildcard is one recipe, not two


@pytest.mark.parametrize('query', [
    {'key': 'bad'}, {'key': 'minecraft:log:-1'}, {'key': 'minecraft:log:999999999999'}, {'key': 'fluid:'},
    {'key': 'minecraft:log:0', 'dir': 'bad'}, {'key': 'minecraft:log:0', 'limit': 21},
    {'key': 'minecraft:log:0', 'offset': -1}, {'key': 'minecraft:log:0', 'limit': 'nan'},
    {'key': 'minecraft:log:0', 'map': 'missing'},
])
def test_bad_queries(client, data, query):
    assert request(client, '/api/recipes', **query).status_code == 400


def test_stale_dataset_never_appends_mixed_pages(client, data):
    for query in ({'version': '2.8.4'}, {'fingerprint': 'old'}, {'assets': 'old-textures'}):
        result = request(client, '/api/recipes', key='minecraft:planks:0', **query)
        assert result.status_code == 409 and result.get_json()['code'] == 'stale_recipes'


def test_data_errors_are_distinct(client, monkeypatch):
    monkeypatch.setattr(gamedata, 'selected_version', lambda: None)
    response = request(client, '/api/recipes/item', key='minecraft:log:0')
    assert response.status_code == 404 and response.get_json()['code'] == 'missing_version'
    monkeypatch.setattr(gamedata, 'selected_version', lambda: '2.9.0')
    monkeypatch.setattr(recipes, 'index', lambda _: None)
    assert request(client, '/api/recipes/item', key='minecraft:log:0').get_json()['code'] == 'missing_recipes'
    def failed(_):
        raise ValueError('Index build failed: test reason')
    monkeypatch.setattr(recipes, 'index', failed)
    response = request(client, '/api/recipes/item', key='minecraft:log:0')
    assert response.status_code == 503 and 'test reason' in response.get_json()['error']


def test_private_data_requires_a_viewer_but_page_is_public(client, data):
    auth.set_private(True)
    for endpoint in ('/api/recipes', '/api/recipes/item'):
        assert request(client, endpoint, key='minecraft:planks:0').status_code == 401
    assert client.get('/recipes?key=minecraft:planks:0').status_code == 200
    login_as(client, 'reader', 'viewer')
    assert request(client, '/api/recipes', key='minecraft:planks:0').status_code == 200


def test_keys_preserve_registry_colons_and_do_not_invent_concrete_damage():
    assert recipe_viewer.parse_key('fluid:mod:fluid') == (1, 'mod:fluid')
    assert recipe_viewer.parse_key('mod:internal:name:*') == (0, 'mod:internal:name:*')


def test_unknown_recipe_items_keep_their_network_display_name(client, data, monkeypatch):
    monkeypatch.setitem(state.network, "items", [{"mod": "example", "internal": "unknown", "damage": 3,
                                               "kind": "item", "name": "Network-only Item"}])
    body = request(client, '/api/recipes/item', key='example:unknown:3').get_json()
    assert body['item']['name'] == 'Network-only Item' and body['making'] == {}


def test_old_gt_only_bundles_still_present_recipes(client, game_recipes):
    body = request(client, '/api/recipes/item', key='gregtech:gt.metaitem.01:12028').get_json()
    assert body['coverage']['handlers'] == ['gregtech'] and 'crafting' not in body['making']
    rows = request(client, '/api/recipes', key='gregtech:gt.metaitem.01:12028').get_json()['recipes']
    assert rows[0]['type'] == 'gt' and rows[0]['eut'] == 120 and 'grid' not in rows[0]


def test_assembly_and_nei_presentation_keeps_research_separate_from_consumed_ingredients(tmp_path):
    from test_recipe_index import PLANKS, build_index, format3
    from gcm import recipe_index
    idx = build_index(tmp_path, format3())
    try:
        rows, _ = idx.made_by(recipe_index.ITEM, PLANKS)
        shown = recipe_viewer.present_recipes(rows, idx)
        assembly = next(r for r in shown if r['type'] == 'assembly_line')
        assert assembly['eut'] == 32768 and assembly['research']['eut'] == 8192
        assert assembly['research']['item']['key'] == 'minecraft:log:0'
        assert assembly['groups'][0]['alternatives_total'] == 20
        assert len(assembly['groups'][0]['alternatives']) == 16
        display = next(r for r in shown if r['type'] == 'nei')
        assert display['groups'][0]['alternatives'][0]['key'] == 'fluid:water'
        assert display['requirements']['mana'] == 2000 and 'eut' not in display and 'ticks' not in display
    finally:
        idx.close()


def test_preview_variants_geometry_nc_and_static_research(tmp_path):
    from test_recipe_index import EBF, PLANKS, build_index, format4
    from gcm import recipe_index
    idx = build_index(tmp_path, format4())
    try:
        shown = recipe_viewer.present_recipes(idx.making(recipe_index.ITEM, PLANKS), idx)
        row = next(r for r in shown if r['map'] == EBF and r['auxiliary'])
        slots = row['preview']['slots']
        choices = slots[0]['alternatives'][0]['display_variants']
        assert [e['name'] for e in choices] == ['Charged Log', 'Empty Log']
        assert all(e['key'] == 'minecraft:log:*' and e['wildcard'] and e['damage'] is None for e in choices)
        assert choices[0]['icon'].endswith('item/charged.webp')
        assert slots[1]['alternatives'] == []  # a hole keeps its original position
        assert slots[2]['alternatives'][0]['size'] == 0
        assert slots[0]['frame'].endswith('ui/frame.webp')
        assert row['preview']['layers'][0]['uv'] == [0, 0, 1, .5]
        assert row['preview']['aspects'][0]['name'] == 'Order'
        assert row['preview']['research']['state'] == 'unknown'
        assert row['preview']['research']['name'] == 'Infusion Research'
    finally:
        idx.close()


@pytest.mark.parametrize('path', ['https://evil.test/item/a.webp', 'ui/../x.webp', '/ui/a.webp', 'ui/a.svg', 'ui\\x.webp'])
def test_preview_images_reject_foreign_and_traversal_paths(path):
    from gcm import icons
    assert icons.recipe_image(path) is None


def test_recipe_tooltips_use_display_identity_without_changing_recipe_navigation(tmp_path, monkeypatch):
    from test_recipe_index import EBF, PLANKS, build_index, format4
    from gcm import item_info, recipe_index
    raw = format4()
    raw['display_stacks']['charged']['nbt'] = '{charge:10}'
    seen = []
    def describe(item):
        seen.append(dict(item))
        return {'description': f"{item['name']} / {item.get('damage')}", 'shift_description': 'More details', 'rarity': 'd'}
    monkeypatch.setattr(item_info, 'describe', describe)
    monkeypatch.setattr(item_info, 'mod_name', lambda mod: 'Minecraft' if mod == 'minecraft' else mod)
    idx = build_index(tmp_path, raw)
    try:
        shown = recipe_viewer.present_recipes(idx.making(recipe_index.ITEM, PLANKS), idx)
        row = next(r for r in shown if r['map'] == EBF and r['auxiliary'])
        charged = row['preview']['slots'][0]['alternatives'][0]['display_variants'][0]
        assert charged['info'] == {'description': 'Charged Log / 7', 'shift_description': 'More details',
                                   'rarity': 'd', 'mod_name': 'Minecraft'}
        assert charged['key'] == 'minecraft:log:*' and charged['damage'] is None
        assert any(i['name'] == 'Charged Log' and i['damage'] == 7 and i.get('variant') for i in seen)
        assert row['inputs'][0]['info'] == charged['info']
    finally:
        idx.close()


def test_category_metadata_finds_mod_after_the_category_label(tmp_path, monkeypatch):
    from test_recipe_index import EBF, build_index, format2
    from gcm import item_info
    raw = format2()
    raw['maps'][EBF]['machines'] = [{'name': 'Blast Furnace', 'key': None},
                                  {'name': 'Machine', 'key': 'gregtech:gt.blockmachines:1000'}]
    monkeypatch.setattr(item_info, 'mod_name', lambda mod: 'GregTech' if mod == 'gregtech' else None)
    idx = build_index(tmp_path, raw)
    try:
        info = recipe_viewer.map_info(idx, EBF)
        assert info['name'] == 'Blast Furnace' and info['mod_name'] == 'GregTech'
        assert info['machines'][1]['key'] == 'gregtech:gt.blockmachines:1000'
    finally:
        idx.close()
