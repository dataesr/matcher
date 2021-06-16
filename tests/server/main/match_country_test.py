import re

import pytest

from matcher.server.main.init_country import init_country
from matcher.server.main.match_country import get_countries_from_query, get_regex_from_country_by_fields
from matcher.server.main.my_elastic import MyElastic


@pytest.fixture(scope='module')
def elasticsearch() -> dict:
    index = 'country-test'
    es = MyElastic()
    es.create_index(index=index)
    yield {'es': es, 'index': index}
    es.delete_index(index=index)


class TestMatchCountry:
    @pytest.mark.parametrize('fields,values,is_complex,expected_regex', [
        (['cities'], [['city_01']], True, re.compile('(?<![a-z])city 01(?![a-z])', re.IGNORECASE)),
        (['cities'], [['city_01', 'city_02']], True, re.compile('(?<![a-z])city 01(?![a-z])|(?<![a-z])city 02(?![a-z])',
                                                                re.IGNORECASE)),
        (['cities', 'names'], [['city_01'], ['name_01']], True,
         re.compile('(?<![a-z])city 01(?![a-z])|(?<![a-z])name 01(?![a-z])', re.IGNORECASE)),
        (['universities'], [['université']], True, re.compile('(?<![a-z])universite(?![a-z])', re.IGNORECASE)),
        (['stop_words'], [['word_01', 'word_02']], False, re.compile('word 01|word 02', re.IGNORECASE))
    ])
    def test_get_regex_from_country_by_fields(self, elasticsearch, fields, values, is_complex, expected_regex) -> None:
        country = 'fr'
        body = {'alpha_2': country}
        for (field, value) in zip(fields, values):
            body[field] = value
        elasticsearch['es'].index(index=elasticsearch['index'], body=body, refresh=True)
        regex = get_regex_from_country_by_fields(elasticsearch['es'], elasticsearch['index'], country, fields,
                                                 is_complex)
        assert regex == expected_regex
        elasticsearch['es'].delete_all_by_query(index=elasticsearch['index'])

    @pytest.mark.parametrize(
        'query,criteria,expected_country', [
            # Query with no meaningful should return no country
            ('Not meaningful string', ['wikidata_cities'], []),
            # Simple query with a city should match the associated country
            ('Tour Mirabeau Paris', ['wikidata_cities'], ['fr']),
            # Complex query with a city should match the associated country
            ('Inserm U1190 European Genomic Institute of Diabetes, CHU Lille, Lille, France', ['wikidata_cities'],
             ['fr']),
            # Country with only alpha_3
            ('St Cloud Hospital, St Cloud, MN, USA.', ['alpha_3'], ['us']),
            ('Department of Medical Genetics, Hotel Dieu de France, Beirut, Lebanon.',
             ['wikidata_cities', 'wikidata_hospitals', 'names'], ['lb']),
            # Even if city is unknown, the university name should match the associated country
            ('Université de technologie de Troyes', ['wikidata_cities'], ['fr']),
        ])
    def test_get_countries_from_query(self, elasticsearch, requests_mock, query, criteria, expected_country) -> None:
        requests_mock.real_http = True
        requests_mock.get('https://query.wikidata.org/bigdata/namespace/wdq/sparql',
                          json={'results': {'bindings': [
                              {'country_alpha2': {'value': 'fr'}, 'label_native': {'value': 'Paris'}},
                              {'country_alpha2': {'value': 'fr'}, 'label_native': {'value': 'Lille'}},
                              {'country_alpha2': {'value': 'lb'}, 'label_native': {'value': 'Beirut'}},
                              {'country_alpha2': {'value': 'fr'}, 'label_native':
                                  {'value': 'Université de technologie de Troyes'}}
                          ]}})
        index = elasticsearch['index']
        init_country(index=index)
        matched_country = get_countries_from_query(query=query, criteria=criteria, index=index)
        matched_country.sort()
        expected_country.sort()
        assert set(matched_country) == set(expected_country)
