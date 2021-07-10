import pycountry

from matcher.server.main.elastic_utils import get_analyzers, get_char_filters, get_filters, get_index_name, get_mappings
from matcher.server.main.logger import get_logger
from matcher.server.main.my_elastic import MyElastic

SOURCE = 'country'

logger = get_logger(__name__)


def download_country_data():
    countries = [c.__dict__['_fields'] for c in list(pycountry.countries)]
    return countries


def transform_country_data(raw_data):
    subdivisions, subdivisions_code = {}, {}
    for s in pycountry.subdivisions:
        alpha2 = s.country_code.lower()
        if alpha2 not in subdivisions:
            subdivisions[alpha2] = []
            subdivisions_code[alpha2] = []
        subdivisions[alpha2].append(s.name)
        if alpha2 == 'us':
            subdivisions_code[alpha2].append(s.code[3:])
        if alpha2 == 'gb':
            subdivisions[alpha2].append('northern ireland')
    countries = []
    for c in raw_data:
        # Alpha 2 - 3
        alpha2 = c['alpha_2'].lower()
        country = {'alpha2': alpha2, 'alpha3': [c['alpha_3']]}
        if alpha2 == 'gb':
            country['alpha3'].append('uk')
        # Names
        all_names = []
        for field_name in ['name', 'official_name', 'common_name']:
            if field_name in c:
                all_names.append(c[field_name])
        if alpha2 == 'ru':
            all_names.append('russia')
        if alpha2 == 'ci':
            all_names.append('ivory coast')
        if alpha2 == 'cv':
            all_names.append('cape verde')
        if alpha2 == 'kp':
            all_names.append('north korea')
        if alpha2 == 'kr':
            all_names.append('south korea')
        if alpha2 == 'la':
            all_names.append('laos')
        if alpha2 == 'sy':
            all_names.append('syria')
        if alpha2 == 'tw':
            all_names.append('taiwan')
        if alpha2 == 'vn':
            all_names.append('vietnam')
        all_names = list(set(all_names))
        country['all_names'] = all_names
        if 'name' in c:
            country['name'] = c['name']
        # Subdivisions
        if alpha2 in subdivisions:
            country['subdivisions'] = list(set(subdivisions[alpha2]))
            country['subdivisions_code'] = list(set(subdivisions_code[alpha2]))
        countries.append(country)
    return countries


def load_country(index_prefix: str = 'matcher') -> dict:
    es = MyElastic()
    settings = {
        'analysis': {
            'char_filter': get_char_filters(),
            'filter': get_filters(),
            'analyzer': get_analyzers()
        }
    }
    analyzers = {
        'all_names': 'name_analyzer',
        'subdivisions': 'light',
        'subdivisions_code': 'light',
        'alpha3': 'light'
    }
    criteria = list(analyzers.keys())
    es_data = {}
    for criterion in criteria:
        index = get_index_name(index_name=criterion, source=SOURCE, index_prefix=index_prefix)
        analyzer = analyzers[criterion]
        es.create_index(index=index, mappings=get_mappings(analyzer), settings=settings)
        es_data[criterion] = {}
    raw_countries = download_country_data()
    countries = transform_country_data(raw_countries)
    # Iterate over country data
    for country in countries:
        for criterion in criteria:
            criterion_values = country.get(criterion)
            if criterion_values is None:
                logger.debug(f"This element {country} has no {criterion}")
                continue
            for criterion_value in criterion_values:
                if criterion_value not in es_data[criterion]:
                    es_data[criterion][criterion_value] = []
                es_data[criterion][criterion_value].append({'country_alpha2': country['alpha2']})
    # Bulk insert data into ES
    actions = []
    results = {}
    for criterion in es_data:
        index = get_index_name(index_name=criterion, source=SOURCE, index_prefix=index_prefix)
        analyzer = analyzers[criterion]
        results[index] = len(es_data[criterion])
        for criterion_value in es_data[criterion]:
            action = {'_index': index,
                      'country_alpha2': list(set([k['country_alpha2'] for k in es_data[criterion][criterion_value]])),
                      'query': {
                          'match_phrase': {'content': {'query': criterion_value, 'analyzer': analyzer, 'slop': 2}}}}
            actions.append(action)
    es.parallel_bulk(actions=actions)
    return results
