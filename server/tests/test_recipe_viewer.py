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


def test_catalog_search_is_paged_and_independent_of_network_stock(client, data):
    body = request(client, '/api/recipes/search', q='planks', limit=1).get_json()
    assert body['total'] == 1 and body['items'][0]['key'] == 'minecraft:planks:0'
    assert body['items'][0]['name'] == 'Planks' and 'info' in body['items'][0]
    assert body['version'] == '2.9.0' and body['fingerprint'] == data.fingerprint
    assert request(client, '/api/recipes/search', q='planks', offset=1).get_json()['items'] == []
    assert request(client, '/api/recipes/search', q='minecraft:planks:0').get_json()['total'] == 1
    all_items = request(client, '/api/recipes/search', limit=1).get_json()
    assert all_items['total'] > 1 and len(all_items['items']) == 1
    assert request(client, '/api/recipes/search', q='fluid', kind='fluid').get_json()['items'][0]['key'] == 'fluid:mod:fluid'
    assert request(client, '/api/recipes/search', q='@minecraft log', kind='item').get_json()['total'] == 2
    assert request(client, '/api/recipes/search', q='@mine log', kind='item').get_json()['total'] == 2
    assert request(client, '/api/recipes/search', q='not a real material').get_json()['total'] == 0


def test_catalog_recents_follow_current_dataset_and_drop_unknown_keys(client, data):
    path = '/api/recipes/search?recent_version=2.9.0&key=minecraft:planks:0&key=bad&key=example:missing:0&key=fluid:mod:fluid'
    body = client.get(path).get_json()
    assert [i['key'] for i in body['recent']] == ['minecraft:planks:0', 'fluid:mod:fluid']
    assert client.get(path.replace('2.9.0', 'old')).get_json()['recent'] == []
    assert client.get(path+'&q=planks').get_json()['recent'] == []


def test_catalog_reports_missing_legacy_names_instead_of_empty_search(client, data, monkeypatch):
    monkeypatch.setattr(data, 'has_names', lambda: False)
    response = request(client, '/api/recipes/search', q='planks')
    assert response.status_code == 409
    assert response.get_json()['code'] == 'catalog_unavailable'
    assert 'Settings → Game data' in response.get_json()['error']


def test_catalog_keeps_compatible_legacy_data_and_explains_missing_recipes(client, data, monkeypatch):
    monkeypatch.setattr(data, 'source_format', 1)
    assert request(client, '/api/recipes/search', q='planks').status_code == 200
    monkeypatch.setattr(recipes, 'index', lambda _: None)
    response = request(client, '/api/recipes/search')
    assert response.status_code == 404 and response.get_json()['code'] == 'missing_recipes'
    assert 'update' in response.get_json()['error']
    def failed(_):
        raise ValueError('unsupported source format')
    monkeypatch.setattr(recipes, 'index', failed)
    response = request(client, '/api/recipes/search')
    assert response.status_code == 503 and response.get_json()['code'] == 'index_failed'
    assert 'Settings → Game data' in response.get_json()['error']


@pytest.mark.parametrize('query', [{'limit': 1025}, {'offset': -1}, {'offset': 'nan'}, {'kind': 'essentia'},
                                 {'q': 'x'*161}, {'q': 'a '*13}, {'q': '%'}, {'q': '_'}])
def test_catalog_search_validates_bounds_and_treats_wildcards_literally(client, data, query):
    result = request(client, '/api/recipes/search', **query)
    if query.get('q') in ('%', '_'):
        assert result.status_code == 200 and result.get_json()['total'] == 0
    else:
        assert result.status_code == 400


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


def test_machine_summaries_share_network_info_and_omit_generic_category_labels(client, data, monkeypatch):
    from gcm import item_info
    monkeypatch.setattr(item_info, 'describe', lambda item: {'description': 'Machine details', 'rarity': 'd'})
    body = request(client, '/api/recipes', key='minecraft:planks:0').get_json()
    machine = body['machines']['crafting'][0]
    assert machine['key'] == 'minecraft:crafting_table:0'
    assert (machine['kind'], machine['mod'], machine['internal'], machine['damage']) == ('item', 'minecraft', 'crafting_table', 0)
    assert machine['info']['description'] == 'Machine details' and machine['info']['rarity'] == 'd'
    rows = [{'map': 'test', 'machines': [{'name': 'Category', 'key': None},
                                       {'name': 'Machine', 'key': machine['key']}]}] * 20
    assert len(recipe_viewer.machine_summaries(rows)['test']) == 1
    assert recipe_viewer.machine_summaries([{'map': 'unknown', 'machines': [{'name': 'Unknown'}]}]) == {
        'unknown': [{'name': 'Unknown', 'key': None, 'kind': 'item', 'info': {}}]}


def test_gt_category_titles_use_machine_type_without_losing_variant_names(data, monkeypatch):
    from gcm import item_info
    monkeypatch.setattr(data, 'machines', lambda name: [{'name': 'Basic Canning Machine', 'key': 'gregtech:gt.blockmachines:321'},
                                                      {'name': 'Advanced Canning Machine', 'key': 'gregtech:gt.blockmachines:322'}])
    monkeypatch.setattr(item_info, 'describe', lambda item: {'description': '\u00a77Machine Type: \u00a7eCanner\nCapacity: 100 EU'})
    info = recipe_viewer.map_info(data, 'gt.recipe.canner')
    assert info['name'] == 'Canner'
    assert [m['name'] for m in info['machines']] == ['Basic Canning Machine', 'Advanced Canning Machine']


def test_exported_category_title_takes_precedence_over_shared_machine_type(data, monkeypatch):
    monkeypatch.setattr(data, 'layout', lambda name: {'title': 'Condensate Assembling'})
    assert recipe_viewer.map_info(data, 'gt.recipe.assemble-condensate')['name'] == 'Condensate Assembling'


def test_nei_metadata_fluid_display_is_distinct_from_production_inputs(data, monkeypatch):
    monkeypatch.setattr(data, 'layout', lambda name: {'width': 170, 'height': 90, 'slots': [
        {'role': 'f', 'index': 2, 'x': 106, 'y': 44}]})
    body = {'type': 'gt', 'i': [], 'o': [['minecraft:planks:0', 1]], 'f': [], 'g': [], 'e': 503316480, 'd': 1000000, 's': 0,
            'preview_stacks': {'f': [['mod:fluid', 10000]]}, 'preview_indices': {'f': [2]},
            'requirements': {'metadata': {'condensate_input': [{'fluid': 'mod:fluid', 'amount': 10000}],
                                          'nanite_tiers': ['MagMatter', 'Carbon'], 'cleanroom': True},
                             'max_nanite_tier': 'Magmatter (Tier 10)'}}
    row = recipe_viewer.present_recipes([(1, 'gt.recipe.assemble-condensate', body)], data)[0]
    assert row['inputs'] == []
    fluid = row['preview']['slots'][0]['alternatives'][0]
    assert (fluid['key'], fluid['size']) == ('fluid:mod:fluid', 10000)
    assert row['ticks'] == 1000000
    assert row['requirement_notes'] == ['Needs Cleanroom', 'Max Nanite Tier:', 'Magmatter (Tier 10)']


@pytest.mark.parametrize('shape,layers', [('shaped', 3), ('shapeless', 1)])
def test_arcane_preview_uses_aspect_glyphs_and_short_research_without_raw_nei_fields(data, shape, layers):
    data.presentation['aspects']['ordo'] = {'name': 'Order', 'icon': 'aspect/ordo.webp'}
    layout = {'family': 'positioned', 'width': 166, 'height': 142, 'slots': [
        {'role': 'groups', 'index': 0, 'x': 46, 'y': 37},
        {'role': 'o', 'index': 0, 'x': 74, 'y': 4},
        {'role': 'extras', 'index': 0, 'x': 55, 'y': 121}]}
    body = {'type': 'nei', 'i': [], 'o': [['minecraft:planks:0', 1]], 'f': [], 'g': [],
            'groups': [{'kind': 'item', 'alts': [['minecraft:log:0', 1]]}], 'preview': layout,
            'requirements': {'aspects': {'ordo': 10},
                'prereqs': [{'research': 'Digi-sentia Storage', 'category': 'Energistics', 'isResearched': False, 'prevX': 0}],
                'vis': [{'relx': 56, 'rely': 122, 'chance': 10000,
                         'item': {'item': 'aspectrecipeindex:aspect:1', 'amount': 10, 'nbt': '{Aspect:"ordo"}'}}]}}
    row = recipe_viewer.present_recipes([(1, f'nei.example::thaumcraft.arcane.{shape}', body)], data)[0]
    preview = row['preview']
    assert preview['family'] == 'arcane'
    assert [(s['role'], len(s['alternatives'])) for s in preview['slots']] == [('groups', 1), ('o', 1)]
    assert all(s['framed'] is False for s in preview['slots'])
    assert preview['aspects'] == [{'tag': 'ordo', 'name': 'Order', 'amount': 10, 'icon': 'aspect/ordo.webp',
                                  'x': 56, 'y': 122, 'w': 16, 'h': 16}]
    assert preview['research'] == {'name': 'Digi-sentia Storage', 'state': 'unknown'}
    assert row['requirement_notes'] == []
    assert len(recipe_viewer.arcane_layout(layout, shaped=shape == 'shaped')['layers']) == layers
    assert layout['family'] == 'positioned' and len(layout['slots']) == 3


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
    for endpoint in ('/api/recipes', '/api/recipes/item', '/api/recipes/search'):
        assert request(client, endpoint, key='minecraft:planks:0').status_code == 401
    assert client.get('/recipes?key=minecraft:planks:0').status_code == 200
    login_as(client, 'reader', 'viewer')
    assert request(client, '/api/recipes', key='minecraft:planks:0').status_code == 200


def test_keys_preserve_registry_colons_and_do_not_invent_concrete_damage():
    assert recipe_viewer.parse_key('fluid:mod:fluid') == (1, 'mod:fluid')
    assert recipe_viewer.parse_key('mod:internal:name:*') == (0, 'mod:internal:name:*')


def test_requirements_drop_empty_and_false_values_without_losing_zero_tiers_or_chances():
    assert recipe_viewer.requirement_notes({'metadata': {'cleanroom': False}, 'mSpecialItems': None,
                                          'empty': [], 'unexpanded': {'class': 'Example'}}) == []
    assert recipe_viewer.requirement_notes({'metadata': {'cleanroom': True, 'low_gravity': True,
                                                       'casing_tier': 0}, 'mInputChances': [0, 5000]}) == [
        'Needs Cleanroom', 'Needs Low Gravity', 'Casing tier: 0',
        'Input consumption chances (out of 10,000): 0, 5,000']


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
        scan = assembly['research']
        assert scan['name'] == 'Research Station' and scan['tier'] == 'IV'
        assert scan['usage'] == 8192 * 4
        assert scan['max_eu'] == str(7812 * 20 * 8192 * 4)
        assert assembly['groups'][0]['alternatives_total'] == 20
        assert len(assembly['groups'][0]['alternatives']) == 16
        display = next(r for r in shown if r['type'] == 'nei')
        assert display['groups'][0]['alternatives'][0]['key'] == 'fluid:water'
        assert display['requirements']['mana'] == 2000 and 'eut' not in display and 'ticks' not in display
    finally:
        idx.close()


def test_scanner_time_energy_and_voltage_stay_separate_from_assembly_costs(tmp_path):
    from test_recipe_index import PLANKS, build_index, format3
    from gcm import recipe_index
    source = format3()
    source['maps']['assembly_line']['recipes'][0]['research'] = {
        'method': 'scanner', 'item': [0, 1], 'eut': 120, 'ticks': 100,
    }
    idx = build_index(tmp_path, source)
    try:
        rows = recipe_viewer.present_recipes(idx.making(recipe_index.ITEM, PLANKS), idx)
        assembly = next(r for r in rows if r['type'] == 'assembly_line')
        assert assembly['eut'] == 32768
        scan = assembly['research']
        assert scan['name'] == 'Scanner' and scan['tier'] == 'MV'
        assert scan['usage'] == 120 and scan['total_eu'] == '12000'
        assert scan['ticks'] == 100 and 'max_eu' not in scan
    finally:
        idx.close()


def test_research_categories_query_native_requirements_without_becoming_manufacturing(client, tmp_path, monkeypatch):
    import copy
    from test_recipe_index import LOG, PLANKS, build_index, format3
    from gcm import recipe_index
    source = format3()
    scanner = copy.deepcopy(source['maps']['assembly_line']['recipes'][0])
    scanner['research'] = {'method': 'scanner', 'item': [0, 1], 'eut': 120, 'ticks': 100}
    source['maps']['assembly_line']['recipes'].append(scanner)
    source['maps']['nei.gt.gt.recipe.scanner'] = {'machines': [
        {'name': 'Basic Scanner', 'key': 'gregtech:gt.blockmachines:341'},
        {'name': 'Advanced Scanner', 'key': 'gregtech:gt.blockmachines:342'}], 'recipes': []}
    source['format'] = 4
    source['items'].append('gregtech:gt.metaitem.01:32708')
    source['item_names'].append('Data Stick')
    source['display_stacks'] = {
        'stick': {'key': 'gregtech:gt.metaitem.01:32708', 'name': 'Data Stick', 'icon': 'item/stick.webp'},
        'target': {'key': PLANKS, 'name': 'Target', 'icon': 'item/target.webp'},
    }
    native = source['maps']['assembly_line']['recipes'][0]
    native['aux'] = [[len(source['items'])-1, 0, None, ['stick']]]
    native['o'][0] = [2, 16, None, ['target']]
    idx = build_index(tmp_path, source)
    monkeypatch.setattr(gamedata, 'selected_version', lambda: '2.9.0')
    monkeypatch.setattr(recipes, 'index', lambda version: idx)
    try:
        header = request(client, '/api/recipes/item', key=PLANKS).get_json()
        assert header['making']['research.scanner'] == 1
        assert header['making']['research.research_station'] == 1
        assert {'Scanner', 'Research Station'} <= {m['name'] for m in header['maps']}
        native_total = sum(idx.counts_making(recipe_index.ITEM, PLANKS).values())
        page = request(client, '/api/recipes', key=PLANKS, map='research.scanner').get_json()
        assert page['total'] == 1
        row = page['recipes'][0]
        assert row['type'] == 'research' and row['reference_only'] is True
        assert row['scan']['total_eu'] == '12000' and row['scan']['eut'] == 120
        assert row['preview']['family'] == 'scanner'
        assert [m['name'] for m in row['machines']] == ['Basic Scanner', 'Advanced Scanner']
        assert len(page['machines']['research.scanner']) == 2
        assert row['inputs'][0]['key'] == LOG and row['outputs'][0]['key'] == PLANKS
        station = request(client, '/api/recipes', key=PLANKS, map='research.research_station').get_json()['recipes'][0]
        assert station['auxiliary'][0]['size'] == station['auxiliary'][0]['display_variants'][0]['size'] == 1
        assert station['outputs'][0]['size'] == station['outputs'][0]['display_variants'][0]['size'] == 1
        combined = request(client, '/api/recipes', key=PLANKS, limit=2, offset=native_total-1).get_json()
        assert combined['total'] == native_total + 2
        assert len(combined['recipes']) == 2 and combined['recipes'][0]['type'] != 'research'
        assert combined['recipes'][1]['type'] == 'research'
        last = request(client, '/api/recipes', key=PLANKS, limit=2, offset=native_total+1).get_json()
        assert len(last['recipes']) == 1
        uses = request(client, '/api/recipes', key=LOG, dir='using', map='research.scanner').get_json()
        assert uses['total'] == 1
        # Assembly ingredients are not scanner inputs.
        unrelated = request(client, '/api/recipes', key='example:part:19', dir='using', map='research.scanner').get_json()
        assert unrelated['total'] == 0
        assert not any(m.startswith('research.') for m in idx.counts_making(recipe_index.ITEM, PLANKS))
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
