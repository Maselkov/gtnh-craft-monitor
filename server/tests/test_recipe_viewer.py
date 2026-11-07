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
