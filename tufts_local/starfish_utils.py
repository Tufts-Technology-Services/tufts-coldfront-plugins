import logging

from coldfront_utils import ttl_cache
from storage.utils import get_client_config

logger = logging.getLogger(__name__)

@ttl_cache(timeout=60*60)
def get_starfish_usage_data_by_volume(volume: str, client_key: str) -> list:
    """
    Helper function to query Starfish API for usage data for all subfolders of a given volume. 
    Caches results to avoid redundant API calls.
    """
    from starfish_api_client import StarfishAPIClient # pylint: ignore=import-outside-toplevel
    client_config = get_client_config(client_key)
    sf = StarfishAPIClient(host=client_config['host'], token=client_config['api_key'])
    subfolder_response = sf.request_subfolder_query(volume)
    # we only need certain fields from the response, so we will extract those and store them in a list of dictionaries
    #retained_fields = ['vol_path', 'logical_size', 'sync', 'username', 'groupname', 'tags_explicit']
    #subfolder_response = [{field: i[field] for field in retained_fields} for i in subfolder_response]
    return subfolder_response
