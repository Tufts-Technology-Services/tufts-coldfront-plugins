import logging
from time import sleep
from coldfront_utils import ttl_cache
from storage.utils import get_client_config

logger = logging.getLogger(__name__)

@ttl_cache(timeout=60*60)
def get_starfish_usage_data_by_volume(volume: str, client_key: str) -> list:
    """
    Helper function to query Starfish API for usage data for all subfolders of a given volume. 
    Caches results to avoid redundant API calls.
    """
    sf = get_starfish_client(client_key)
    subfolder_response = sf.request_subfolder_query(volume)
    # we only need certain fields from the response, so we will extract those and store them in a list of dictionaries
    retained_fields = ['vol_path', 'logical_size', 'sync', 'username', 'groupname', 'tags_explicit']
    subfolder_response = [{field: i[field] for field in retained_fields} for i in subfolder_response]
    return subfolder_response


def get_starfish_data_by_vol_path(vol_path: str, client_key: str) -> dict:
    """
    Helper function to query Starfish API for usage data for a specific subfolder by its volume path. 
    Caches results to avoid redundant API calls.
    """
    volume, _ = vol_path.split(":", 1)
    subfolder_response = get_starfish_usage_data_by_volume(volume, client_key)
    sf_entry = next((item for item in subfolder_response if item['vol_path'] == vol_path), None)
    if not sf_entry:
        logger.warning(f"Subfolder with vol_path '{vol_path}' not found in Starfish.")
        return None
    return sf_entry


def get_starfish_client(client_key: str):
    """
    Helper function to get a Starfish API client instance.
    """
    from starfish_api_client import StarfishAPIClient # pylint: ignore=import-outside-toplevel
    client_config = get_client_config(client_key)
    return StarfishAPIClient(host=client_config['host'], token=client_config['api_key'])


def add_directory_to_index(client_key, vol_path, wait=5, retries=12):
    """
    Add a top level directory to the index by initiating a scan of depth 0. 
    This is useful when a new project directory is created and needs to be tagged.
    """
    client = get_starfish_client(client_key)
    volume, path = vol_path.split(":", 1)
    r = client.scan_new(volume, path)
    scan_id = r['id']
    for _ in range(retries):
        sleep(wait)
        status = client.get_scan(scan_id)
        if not status['state']['is_running']:
            if status['state']['is_successful']:
                sleep(wait)
                return status
            else:
                raise RuntimeError(f"Scan {scan_id} failed with error: {status['reason']}")

    raise TimeoutError(f"Scan {scan_id} did not complete in time.")


def flatten_tags(tags: dict) -> list:
    """
    Flatten a dictionary of tags into a list of strings in the format 'key:value'.
    """
    flattened = []
    for key, values in tags.items():
        for value in values:
            flattened.append(f"{key}:{value}")
    return flattened


def parse_tags(tags: list) -> dict:
    """
    Parse a list of tags into a dictionary where keys are tag types and values are a set of tag values
    """
    parsed_tags = {}
    for tag in tags:
        if ':' in tag:
            key, value = tag.split(':', 1)
            parsed_tags.setdefault(key.strip(), set()).add(value.strip())
        else:
            raise ValueError(f"Tag '{tag}' does not contain a colon ':' to separate key and value.")
    return parsed_tags


def sync_approver_tags(client_key, vol_path, approvers: list):
    """
    Synchronize approver tags for a directory indexed by Starfish.
    This function will add or remove approver tags based on the current approvers in Coldfront.
    It will not add tags that are already present, and it will remove tags 
    that are not in the provided list. 
    It will ignore tag types that are not represented in the provided list.
    If the directory is not indexed, it will raise a ValueError
    """
    sf_data = get_starfish_data_by_vol_path(vol_path, client_key)  # raises ValueError if not found
    existing_tags = parse_tags(sf_data.get('tags_explicit', ''))
    existing_approvers = existing_tags.get('Approver', set())
    new_approvers = set(approvers)
    tags_to_add = {'Approver': new_approvers - existing_approvers}
    tags_to_remove = {'Approver': existing_approvers - new_approvers}
    client = get_starfish_client(client_key)
    tags_to_add = flatten_tags(tags_to_add)
    if tags_to_add:
        client.add_tag(vol_path, tags_to_add)
    tags_to_remove = flatten_tags(tags_to_remove)
    if tags_to_remove:
        client.detach_tag(vol_path, tags_to_remove)


def sync_tags(client_key, vol_path, tags: list):
    """
    Synchronize tags for a directory indexed by Starfish.
    This function will add or remove tags based on the current tags in Starfish.
    It will not add tags that are already present, and it will remove tags 
    that are not in the provided list. 
    It will ignore tag types that are not represented in the provided list.
    If the directory is not indexed, it will raise a ValueError
    """
    sf_data = get_starfish_data_by_vol_path(vol_path, client_key)  # raises ValueError if not found
    existing_tags = parse_tags(sf_data.get('tags_explicit', ''))
    project_tags = parse_tags(tags)
    tags_to_add = {}
    tags_to_remove = {}
    for key, value in project_tags.items():
        if key in existing_tags:
            # match values
            tags_to_add[key] = value - existing_tags[key]
            tags_to_remove[key] = existing_tags[key] - value
        else:
            # add all values for this key
            tags_to_add[key] = value
    client = get_starfish_client(client_key)
    tags_to_add = flatten_tags(tags_to_add)
    if tags_to_add:
        client.add_tag(vol_path, tags_to_add)
    tags_to_remove = flatten_tags(tags_to_remove)
    if tags_to_remove:
        client.detach_tag(vol_path, tags_to_remove)


def set_owner_tag(client_key, vol_path, owner: str):
    # valid tags: Owner, Group, LabGroup, Approver, Reporting
    sync_tags(client_key, vol_path, [f"Owner:{owner.lower()}"])


def set_approver_tags(client_key, vol_path, approvers: list):
    # valid tags: Owner, Group, LabGroup, Approver, Reporting
    sync_tags(client_key, vol_path, [f"Approver:{approver.lower()}" for approver in approvers])