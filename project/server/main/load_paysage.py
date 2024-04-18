import os
import datetime
import pandas as pd
import numpy as np
import requests
import json
from elasticsearch.client import IndicesClient
from project.server.main.elastic_utils import (
    get_analyzers,
    get_tokenizers,
    get_char_filters,
    get_filters,
    get_index_name,
    get_mappings,
)
from project.server.main.logger import get_logger
from project.server.main.my_elastic import MyElastic
from project.server.main.utils import (
    city_zone_emploi_insee,
    get_alpha2_from_french,
    FRENCH_STOP,
    clean_list,
    ACRONYM_IGNORED,
    clean_url,
    get_url_domain,
)

logger = get_logger(__name__)

SOURCE = "paysage"
ODS_KEY = os.getenv("ODS_KEY")
ODS_PAYSAGE = "structures-de-paysage-v2"
ES_URL = os.getenv("ES_PAYSAGE_URL")
ES_TOKEN = os.getenv("ES_PAYSAGE_TOKEN")

WANTED_CATEGORIES = [
    "Université",
    "Établissement public expérimental",
    "Établissement supérieur d'architecture",
    "Organisme de recherche",
    "Société d'accélération du transfert de technologies",
    "Établissement d'enseignement supérieur privé d'intérêt général",
    "Tutelle des établissements",
    "Incubateur public",
    "Liste des établissements publics relevant du ministre chargé de l'Enseignement supérieur",
    "Etablissements d’enseignement supérieur techniques privés (hors formations relevant du commerce et de la gestion)",
    "Etablissement publics d’enseignement supérieur entrant dans la cotutelle du ministre chargé de l’enseignement supérieur (Art L 123-1 du code de l’éducation)",
    "Commerce et gestion - Etablissements d’enseignement supérieur techniques privés et consulaires autorisés à délivrer un diplôme visé par le ministre chargé de l’enseignement supérieur et/ou à conférer le grade universitaire",
    "Opérateur du programme 150 - Formations supérieures et recherche universitaire",
    "Structure de recherche",
    # "Établissement d'enseignement supérieur étranger"
]

def load_paysage(index_prefix: str = "matcher") -> dict:
    logger.debug("Start loading Paysage data...")
    es = MyElastic()
    indices_client = IndicesClient(es)
    settings = {
        "analysis": {
            "analyzer": get_analyzers(),
            "tokenizer": get_tokenizers(),
            "char_filter": get_char_filters(),
            "filter": get_filters(),
        }
    }
    exact_criteria = [
        "id",
        "city",
        "zone_emploi",
        "acronym",
        "name",
        "year",
        "wikidata",
        "web_url",
        "web_domain",
    ]
    txt_criteria = ["name_txt"]
    analyzers = {
        "id": "light",
        "city": "city_analyzer",
        "zone_emploi": "city_analyzer",
        "acronym": "acronym_analyzer",
        "name": "heavy_fr",
        "name_txt": "heavy_fr",
        "wikidata": "wikidata_analyzer",
        "year": "light",
        "web_url": "url_analyzer",
        "web_domain": "domain_analyzer",
    }
    criteria = exact_criteria + txt_criteria
    criteria_unique = []
    for c in criteria_unique:
        criteria.append(f"{c}_unique")
        analyzers[f"{c}_unique"] = analyzers[c]

    logger.debug(f"Criteria {criteria}")

    # Create Elastic Search index
    es_data = {}
    for criterion in criteria:
        index = get_index_name(index_name=criterion, source=SOURCE, index_prefix=index_prefix)
        analyzer = analyzers[criterion]
        es.create_index(index=index, mappings=get_mappings(analyzer), settings=settings)
        es_data[criterion] = {}

    # Download paysage data
    raw_records = download_data()

    # Transform paysage data
    transformed_data = transform_data(raw_records)

    # Iterate over paysage data
    logger.debug("Prepare data for elastic")
    for data_point in transformed_data:
        for criterion in criteria:
            criterion_values = data_point.get(criterion.replace("_txt", ""))
            if criterion_values is None:
                # logger.debug(f'This element {data_point} has no {criterion}')
                continue
            if not isinstance(criterion_values, list):
                criterion_values = [criterion_values]
            for criterion_value in criterion_values:
                if criterion_value not in es_data[criterion]:
                    es_data[criterion][criterion_value] = []
                es_data[criterion][criterion_value].append(
                    {"id": data_point["id"], "country_alpha2": data_point["country_alpha2"]}
                )
    # Add unique criterion
    for criterion in criteria_unique:
        for criterion_value in es_data[criterion]:
            if len(es_data[criterion][criterion_value]) == 1:
                if f"{criterion}_unique" not in es_data:
                    es_data[f"{criterion}_unique"] = {}
                es_data[f"{criterion}_unique"][criterion_value] = es_data[criterion][criterion_value]

    # Bulk insert data into ES
    actions = []
    results = {}
    for criterion in es_data:
        index = get_index_name(index_name=criterion, source=SOURCE, index_prefix=index_prefix)
        analyzer = analyzers[criterion]
        results[index] = len(es_data[criterion])
        for criterion_value in es_data[criterion]:
            # if criterion in ['name']:
            #    tokens = get_tokens(indices_client, analyzer, index, criterion_value)
            #    if len(tokens) < 2:
            #        logger.debug(f'Not indexing {criterion_value} (not enough token to be relevant !)')
            #        continue
            action = {
                "_index": index,
                "paysages": [k["id"] for k in es_data[criterion][criterion_value]],
                "country_alpha2": list(set([k["country_alpha2"] for k in es_data[criterion][criterion_value]])),
            }
            if criterion in exact_criteria:
                action["query"] = {
                    "match_phrase": {"content": {"query": criterion_value, "analyzer": analyzer, "slop": 1}}
                }
            elif criterion in txt_criteria:
                action["query"] = {
                    "match": {
                        "content": {"query": criterion_value, "analyzer": analyzer, "minimum_should_match": "-10%"}
                    }
                }
            actions.append(action)
    logger.debug("Start load elastic indexes")
    es.parallel_bulk(actions=actions)
    return results


def download_dataframe() -> pd.DataFrame:
    logger.debug(f"Download Paysage data from {ODS_PAYSAGE}")
    data = pd.read_csv(
        f"https://data.enseignementsup-recherche.gouv.fr/explore/dataset/{ODS_PAYSAGE}/download/?format=csv&apikey={ODS_KEY}",
        sep=";",
        low_memory=False,
    )
    return data.replace(np.nan, None)


def download_categories() -> dict:
    logger.debug(f"Download Paysage categories from {ES_URL}")
    keep_alive = 1
    scroll_id = None
    categories = {}
    hits = []
    size = 10000
    count = 0
    total = 0
    headers = {"Authorization": ES_TOKEN}
    url = f"{ES_URL}/paysage/_search?scroll={keep_alive}m"
    query = {
        "size": size,
        "_source": ["id", "category"],
        "query": {"match": {"type": "structures"}},
    }

    # Scroll to get all results
    while total == 0 or count < total:
        if scroll_id:
            url = f"{ES_URL}/_search/scroll"
            query = {"scroll": f"{keep_alive}m", "scroll_id": scroll_id}
        res = requests.post(url=url, headers=headers, json=query)
        if res.status_code == 200:
            json = res.json()
            scroll_id = json.get("_scroll_id")
            total = json.get("hits").get("total").get("value")
            data = json.get("hits").get("hits")
            count += len(data)
            sources = [d.get("_source") for d in data]
            hits += sources
        else:
            logger.error(f"Elastic error {res.status_code}: stop scroll ({count}/{total})")
            break

    if hits:
        categories = {item["id"]: item["category"] for item in hits}
    return categories


def download_data() -> list:
    # Download data
    df = download_dataframe()

    # Download categories
    categories = download_categories()
    df["category"] = df["id"].apply(lambda x: categories.get(x))

    # Filter wanted categories
    df_filter = df[df["category"].isin(WANTED_CATEGORIES)].copy()
    logger.debug(f"Filter {len(df_filter)}/{len(df)} entries with wanted categories")

    # Cast as records
    records = df_filter.to_dict(orient="records")

    return records


def transform_data(records: list) -> list:
    logger.debug(f"Start transform of Paysage data ({len(records)} records)")

    # Loading zone emploi data
    logger.debug(f"Load insee data")
    try:
        city_zone_emploi, zone_emploi_composition = city_zone_emploi_insee()
    except Exception as error:
        city_zone_emploi = {}
        zone_emploi_composition = {}
        logger.error(f"Error while loading insee data: {error}")

    # Setting a dict with all names, acronyms and cities
    logger.debug("Get data from Paysage records")
    name_acronym_city = {}
    for record in records:
        current_id = record["id"]
        name_acronym_city[current_id] = {}

        # Acronyms
        acronyms_list = ["acronymfr", "acronymen", "acronymlocal"]
        acronyms = [record.get(acronym) for acronym in acronyms_list if record.get(acronym)]

        # Names
        names_list = ["usualname", "officialname", "nameen"]
        names = [record.get(name) for name in names_list if record.get(name)]

        short_name = record.get("shortname")
        if short_name:
            if short_name.isalnum():
                acronyms.append(short_name)
            else:
                names.append(short_name)

        acronyms = list(set(acronyms))
        names = list(set(names))
        names = list(set(names) - set(acronyms))

        # City
        localisation = json.loads(record.get("currentlocalisation", "{}"))
        city = record.get("com_nom") or localisation.get("city") or localisation.get("locality")
        if city:
            clean_city = " ".join([s for s in city.split(" ") if s.isalpha()])
            city = clean_city if clean_city else city

        # Zone emploi (+ academie + urban unit)
        zone_emploi = []
        city_code = record.get("cityid")
        if city_code in city_zone_emploi:
            zone_emploi += city_zone_emploi[city_code]
        academie = record.get("aca_nom")
        if academie:
            zone_emploi.append(academie)
        urban_unit = record.get("uucr_nom")
        if urban_unit:
            zone_emploi.append(urban_unit)

        # Countries
        country = record.get("country")
        country_alpha3 = localisation.get("iso3")
        if country:
            country_alpha2 = get_alpha2_from_french(country)

        name_acronym_city[current_id]["acronym"] = clean_list(data=acronyms, ignored=ACRONYM_IGNORED, min_character=2)
        name_acronym_city[current_id]["name"] = clean_list(data=names, stopwords=FRENCH_STOP, min_token=2)
        name_acronym_city[current_id]["country"] = clean_list([country]) if country else []
        name_acronym_city[current_id]["country_alpha2"] = clean_list([country_alpha2]) if country_alpha2 else []
        name_acronym_city[current_id]["country_alpha3"] = clean_list([country_alpha3]) if country_alpha2 else []
        name_acronym_city[current_id]["city"] = clean_list([city]) if city else []
        name_acronym_city[current_id]["zone_emploi"] = clean_list(zone_emploi)

    logger.debug("Transform records to elastic indexes")
    es_paysages = []
    for record in records:
        paysage_id = record.get("id")
        es_paysage = {"id": paysage_id}
        # Acronyms & names
        es_paysage["acronym"] = name_acronym_city[paysage_id]["acronym"]
        names = name_acronym_city[paysage_id]["name"]
        es_paysage["name"] = list(set(names) - set(es_paysage["acronym"]))
        # Addresses
        es_paysage["city"] = name_acronym_city[paysage_id]["city"]
        es_paysage["country_alpha2"] = name_acronym_city[paysage_id]["country_alpha2"]
        es_paysage["country_code"] = [name_acronym_city[paysage_id]["country_alpha2"]]
        # Zone emploi
        es_paysage["zone_emploi"] = name_acronym_city[paysage_id]["zone_emploi"]
        # Wikidata
        wikidata = record.get("identifiant_wikidata")
        if wikidata:
            es_paysage["wikidata"] = wikidata
        # Dates
        last_year = f"{datetime.date.today().year}"
        start_date = record.get("date_creation")
        if not start_date:
            start_date = "2010"
        start = int(start_date[0:4])
        end_date = record.get("date_fermeture")
        if not end_date:
            end_date = last_year
        end = int(end_date[0:4])
        # Start date one year before official as it can be used before sometimes
        es_paysage["year"] = [str(y) for y in list(range(start - 1, end + 1))]
        # Url
        url = record.get("url")
        if isinstance(url, list):
            raise Exception("Found list url", url)
        if url:
            es_paysage["web_url"] = clean_url(url)
            es_paysage["web_domain"] = get_url_domain(url)

        es_paysages.append(es_paysage)
    return es_paysages
