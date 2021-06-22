import pytest

from matcher.server.main.init_country import init_country
from matcher.server.main.match_country import get_countries_from_query
from matcher.server.main.my_elastic import MyElastic


@pytest.fixture(scope='module')
def elasticsearch() -> dict:
    index = 'country-test'
    es = MyElastic()
    es.create_index(index=index)
    yield {'es': es, 'index': index}
    es.delete_index(index=index)


class TestMatchCountry:
    @pytest.mark.parametrize(
        'query,strategies,expected_results,expected_logs', [
            # Query with no meaningful should return no country
            ('Not meaningful string', [['wikidata_cities']], [], 'No results'),
            # Simple query with a city should match the associated country
            ('Tour Mirabeau Paris', [['wikidata_cities']], [{'alpha_2': 'fr', 'name': 'France'}],
             'wikidata_cities'),
            # Complex query with a city should match the associated country
            ('Inserm U1190 European Genomic Institute of Diabetes, CHU Lille, Lille, France', [['wikidata_cities']],
             [{'alpha_2': 'fr', 'name': 'France'}], 'wikidata_cities'),
            # Country with only alpha_3
            ('St Cloud Hospital, St Cloud, MN, USA.', [['alpha_3']], [{'alpha_2': 'us', 'name': 'United States'}],
             'alpha_3'),
            ('Department of Medical Genetics, Hotel Dieu de France, Beirut, Lebanon.',
             [['wikidata_cities', 'wikidata_hospitals', 'all_names']], [{'alpha_2': 'lb', 'name': 'Lebanon'}],
             'wikidata_cities\', \'wikidata_hospitals\', \'all_names'),
            ('Department of Medical Genetics, Hotel Dieu de France, Beirut, Lebanon.',
             [['wikidata_cities_2', 'wikidata_hospitals', 'all_names']], [{'alpha_2': 'lb', 'name': 'Lebanon'}],
             'wikidata_cities_2\', \'wikidata_hospitals\', \'all_names'),
            # Even if city is not unknown, the university name should match the associated country
            ('Université de technologie de Troyes', [['wikidata_universities']], [{'alpha_2': 'fr', 'name': 'France'}],
             'wikidata_universities'),
            # Fort-de-France
            ('Hotel Dieu de France', [['wikidata_cities_2']], [], 'No results found'),
            ('Fort-de-France', [['wikidata_cities_2']], [{'alpha_2': 'fr', 'name': 'France'}], 'wikidata_cities_2'),
            ('CHU de Fort-de-France', [['wikidata_cities_2']], [{'alpha_2': 'fr', 'name': 'France'}], 'wikidata_cities_2'),
        ])
    def test_get_countries_from_query(self, elasticsearch, requests_mock, query, strategies, expected_results,
                                      expected_logs) -> None:
        requests_mock.real_http = True
        requests_mock.get('https://query.wikidata.org/bigdata/namespace/wdq/sparql',
                          json={'results': {'bindings': [
                              {'country_alpha2': {'value': 'fr'}, 'label_native': {'value': 'Paris'}},
                              {'country_alpha2': {'value': 'fr'}, 'label_native': {'value': 'Lille'}},
                              {'country_alpha2': {'value': 'lb'}, 'label_native': {'value': 'Beirut'}},
                              {'country_alpha2': {'value': 'fr'}, 'label_native': {'value': 'Fort-de-France'}},
                              {'country_alpha2': {'value': 'fr'}, 'label_native':
                                  {'value': 'Université de technologie de Troyes'}}
                          ]}})
        index = elasticsearch['index']
        init_country(index=index)
        results = get_countries_from_query(query=query, strategies=strategies, index=index)
        assert results['results'] == expected_results
        assert expected_logs in results['logs']
